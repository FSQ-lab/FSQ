# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from fsq_agent.config import list_workspace_registry
from fsq_agent.core.evidence import EvidenceRecorder
from fsq_agent.models import (
    ConfigurationError,
    DynamicAgentOutcome,
    EvidenceBundle,
    ReportArtifact,
    ReportGenerationError,
    RunExecutionContext,
    RunnerEvent,
    RunRuntime,
    RunSource,
    TaskResult,
    ToolExecutionError,
)
from fsq_agent.report import ReportGenerator, RunReportService

from .recording import RecordingResult, RecordingService
from .runs import RunLifecycleService, allocate_run, transition_run

if TYPE_CHECKING:
    from collections.abc import Callable

    from fsq_agent.config import Settings
    from fsq_agent.models import EvidenceArtifactRef, RunEventSink, Task


class DynamicAgent(Protocol):
    async def run_in_context(self, task: Task, context: RunExecutionContext, event_sink: RunEventSink | None = None, *, evidence_sink, cancellation_check=None) -> DynamicAgentOutcome: ...


@dataclass(frozen=True)
class DynamicExecutionRequest:
    task: Task
    settings: Settings
    event_sink: RunEventSink | None = None
    record: bool = False
    case_name: str | None = None
    allow_recording_failure: bool = False
    publication_directory: Path | None = None
    cancellation_check: Callable[[], None] | None = None
    report_coordinator: Callable[[TaskResult], None] | None = None
    recording_error_sink: Callable[[Exception], None] | None = None
    requires_web_search_evidence: bool = False


@dataclass(frozen=True)
class DynamicExecutionResult:
    task_result: TaskResult
    recording: RecordingResult | None = None


class DynamicExecutionService:
    def __init__(self, *, agent: DynamicAgent, recording_service: RecordingService | None = None) -> None:
        self._agent = agent
        self._recording_service = recording_service or RecordingService()

    async def execute(self, request: DynamicExecutionRequest) -> DynamicExecutionResult:
        if request.cancellation_check is not None:
            request.cancellation_check()
        if not callable(getattr(self._agent, "run_in_context", None)):
            raise ConfigurationError("Dynamic execution requires a run_in_context collaborator before actions.")
        settings = request.settings
        if request.requires_web_search_evidence and settings.harness.platform != "web":
            raise ConfigurationError("Web search evidence requires Web execution settings before actions.")
        started = time.perf_counter()
        lifecycle = RunLifecycleService()
        secret_values = tuple(sorted(set(settings.runtime_secrets.private_values().values()), key=len, reverse=True))
        root = Path(settings.workspace.root_dir or Path(settings.output.runs_dir).parent.parent.parent)
        safe_task = request.task.model_copy(update={key: _redact_metadata_text(value, secret_values, root) for key, value in request.task.model_dump().items() if isinstance(value, str)})
        safe_source_id = safe_task.id
        for value in secret_values:
            slug_value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
            if slug_value:
                safe_source_id = re.sub(re.escape(slug_value), "***", safe_source_id, flags=re.IGNORECASE)
        metadata = allocate_run(
            workspace=root,
            workspace_name=_workspace_name(root, settings.agent_runtime.user_config_root),
            platform=settings.harness.platform,
            source_id=safe_source_id,
            mode="explore",
            source=RunSource(kind="goal", goal_summary=safe_task.name[:500]),
            platform_runs_dir=Path(settings.output.runs_dir),
        )
        run_dir = Path(settings.output.runs_dir) / metadata.run_id
        processing = {}
        active_processing = None
        checking_cancellation = False

        def check_cancellation():
            nonlocal checking_cancellation
            if request.cancellation_check is not None:
                checking_cancellation = True
                request.cancellation_check()
                checking_cancellation = False

        recording = None
        heartbeat = None
        primary_failure = False
        try:
            metadata = lifecycle.snapshot_sources(
                run_dir, metadata, sources={"goal": request.task.planning_reference_text or request.task.description}, secret_values=secret_values, configuration=lifecycle.safe_configuration(settings)
            )
            metadata = transition_run(run_dir, metadata, "running")
            lifecycle.heartbeat(run_dir)
            heartbeat = asyncio.create_task(self._heartbeat(run_dir))
            recorder = EvidenceRecorder(run_id=metadata.run_id, output_dir=run_dir, secret_values=secret_values)
            outcome = await self._agent.run_in_context(safe_task, lifecycle.context(run_dir, metadata), request.event_sink, evidence_sink=recorder, cancellation_check=check_cancellation)
            _validate_outcome_identity(outcome, safe_task)
            check_cancellation()
            try:
                bundle = lifecycle.read_evidence(run_dir)
            except FileNotFoundError:
                bundle = recorder.build_bundle()
                if not bundle.steps and not bundle.events and not bundle.planned_steps:
                    recorder.record_event(RunnerEvent(run_id=metadata.run_id, event_type="session_finish", payload={"capability_execution": "not_requested"}))
                    recorder.write_manifest()
                    bundle = lifecycle.read_evidence(run_dir)
            web_evidence_gap = request.requires_web_search_evidence and outcome.verification.status == "success" and not _has_web_search_evidence(bundle, run_dir)
            if web_evidence_gap:
                outcome = outcome.model_copy(
                    update={
                        "verification": outcome.verification.model_copy(
                            update={
                                "status": "inconclusive",
                                "summary": "Web search could not be confirmed: recorded browser activity with usable nonblank HTTP(S) page observations is missing.",
                                "satisfied_criteria": [],
                                "diagnostics": [*outcome.verification.diagnostics, "web_search_evidence_missing"],
                            }
                        )
                    }
                )
            frozen = lifecycle.freeze(
                run_dir,
                metadata,
                bundle=bundle,
                verification=outcome.verification.model_copy(update={"summary": _redact_metadata_text(outcome.verification.summary, secret_values, root)}),
                summary=_redact_metadata_text(outcome.verification.summary, secret_values, root),
                fatal_errors=outcome.errors,
                preserve_evidence_error=web_evidence_gap,
                secret_values=secret_values,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            active_processing = "report"
            processing[active_processing] = {"status": "pending"}
            try:
                report = ReportGenerator(settings.output.runs_dir, secret_values=secret_values).generate(metadata.run_id, outcome.task, outcome.steps, outcome.verification)
                processing["report"] = {"status": "success"}
            except Exception as exc:  # noqa: BLE001 - isolate derived processing failures from frozen execution.
                report = ReportArtifact(run_id=metadata.run_id, path=run_dir / "execution-result.json", evidence_manifest_path=bundle.manifest_path)
                processing["report"] = {"status": "failed", "error_type": type(exc).__name__}
            result = TaskResult(
                task_id=request.task.id,
                status=frozen.outcome,
                steps=outcome.steps,
                verification=outcome.verification,
                report=report,
                duration_ms=frozen.duration_ms,
            )
            active_processing = None
            if request.report_coordinator is not None:
                active_processing = "report"
                try:
                    request.report_coordinator(result.model_copy(deep=True))
                except Exception as exc:  # noqa: BLE001 - isolate derived processing failures from frozen execution.
                    processing["report"] = {"status": "failed", "error_type": type(exc).__name__}
            active_processing = None
            if request.record:
                active_processing = "recording"
                processing[active_processing] = {"status": "pending"}
                try:
                    recording = self._recording_service.record(
                        run_dir=run_dir,
                        task=outcome.task,
                        result=result,
                        settings=settings,
                        allow_failure=request.allow_recording_failure,
                        publication_directory=request.publication_directory,
                        case_name=request.case_name,
                    )
                    processing["recording"] = {"status": "success" if recording.status == "recorded" else "failed"}
                    processing["publication"] = {"status": recording.publication_status, "errors": list(recording.publication_errors)}
                    if recording.published_case_path is not None:
                        processing["publication"]["published_case_path"] = recording.published_case_path.name
                except Exception as exc:  # noqa: BLE001 - isolate derived processing failures from frozen execution.
                    processing["recording"] = {"status": "failed", "error_type": type(exc).__name__}
                    if request.recording_error_sink is not None:
                        request.recording_error_sink(exc)
            active_processing = None
            lifecycle.finalize(run_dir, metadata, execution_result=frozen, processing=processing, runtime=RunRuntime(provider=settings.agent_runtime.provider, model=settings.agent_runtime.model))
            return DynamicExecutionResult(task_result=result, recording=recording)
        except BaseException as exc:
            primary_failure = True
            if active_processing is not None:
                processing[active_processing] = {"status": "cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else "failed", "error_type": type(exc).__name__}
            try:
                try:
                    bundle = lifecycle.read_evidence(run_dir)
                except (OSError, ValueError):
                    bundle = EvidenceBundle(bundle_id=f"{metadata.run_id}-evidence", run_id=metadata.run_id)
                if (run_dir / "execution-result.json").is_file():
                    from fsq_agent.models import RunExecutionResult

                    frozen = RunExecutionResult.model_validate_json((run_dir / "execution-result.json").read_text())
                else:
                    cancelled = (
                        checking_cancellation or isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) or type(exc).__name__ in {"ExecutionCancelled", "RunCancelled", "TaskCancelledError"}
                    )
                    frozen = lifecycle.freeze(
                        run_dir, metadata, bundle=bundle, status="cancelled" if cancelled else "error", summary="Execution cancelled." if cancelled else f"Execution stopped ({type(exc).__name__})."
                    )
                lifecycle.finalize(run_dir, metadata, execution_result=frozen, processing=processing)
            except BaseException as persistence_error:  # noqa: BLE001 - preserve the original execution failure.
                logging.getLogger(__name__).warning("Run finalization failed (%s)", type(persistence_error).__name__)
            cancelled = checking_cancellation or isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) or type(exc).__name__ in {"ExecutionCancelled", "RunCancelled", "TaskCancelledError"}
            if cancelled:
                exc.run_id = metadata.run_id
                exc.platform = metadata.platform
                raise
            raise ToolExecutionError("Run execution failed.", context={"run_id": metadata.run_id, "platform": metadata.platform, "exception_type": type(exc).__name__}) from exc
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    with suppress(asyncio.CancelledError):
                        await heartbeat
                except BaseException as cleanup_error:
                    if not primary_failure:
                        raise ToolExecutionError(
                            "Run heartbeat failed.", context={"run_id": metadata.run_id, "platform": metadata.platform, "exception_type": type(cleanup_error).__name__}
                        ) from cleanup_error
                    logging.getLogger(__name__).warning("Run heartbeat cleanup failed (%s)", type(cleanup_error).__name__)

    @staticmethod
    async def _heartbeat(run_dir: Path) -> None:
        while True:
            await asyncio.sleep(5)
            RunLifecycleService.heartbeat(run_dir)


def _validate_outcome_identity(outcome: DynamicAgentOutcome, task: Task) -> None:
    if outcome.task.id != task.id:
        raise ValueError("Agent task identity does not match the allocated Run task.")


def _has_web_search_evidence(bundle: EvidenceBundle, run_dir: Path) -> bool:
    """Minimum observed browser support, not a semantic verdict about a site."""
    if bundle.run_id != run_dir.name:
        return False
    activities = {
        event.step_execution_id
        for event in bundle.events
        if event.run_id == bundle.run_id and event.event_type == "action_result" and event.phase == "invoke" and event.payload.get("status") == "passed" and event.step_execution_id
    }
    artifacts = {ref.artifact_id: ref for ref in bundle.artifacts}
    for step in bundle.steps:
        identity = step.step_execution_id
        if identity not in activities or step.action_status != "passed":
            continue
        for phase in step.phase_reports:
            for ref in phase.artifact_refs:
                if (
                    ref.kind == "ui_snapshot"
                    and ref.availability == "available"
                    and ref.step_id == ref.step_execution_id == identity
                    and ref.phase == phase.phase
                    and artifacts.get(ref.artifact_id) == ref
                    and _usable_web_observation(ref, run_dir)
                ):
                    return True
    return False


def _usable_web_observation(ref: EvidenceArtifactRef, run_dir: Path) -> bool:
    relative = ref.path
    if relative is None or relative.is_absolute() or ".." in relative.parts or ref.size_bytes is None or not ref.sha256 or run_dir.is_symlink():
        return False
    root = run_dir.resolve()
    path = root / relative
    try:
        if not path.resolve().is_relative_to(root) or any(item.is_symlink() for item in (path, *path.parents) if item != root and item.is_relative_to(root)):
            return False
        data = RunReportService.read_fact_bytes(path)
        if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            return False
        observation = json.loads(data)
        if not isinstance(observation, dict):
            return False
        url = observation.get("url")
        if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url) or "\\" in url:
            return False
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port == 0:
            return False
        # These are the two WebUI snapshot layouts captured by the Web harness.
        # A URL, screenshot or title alone does not supply usable page content.
        kind = observation.get("snapshot_type")
        content = observation.get("snapshot") if kind == "aria" else observation.get("text") if kind == "text" else None
        return isinstance(content, str) and bool(content.strip())
    except (OSError, ValueError, ReportGenerationError):
        return False


def _redact_metadata_text(text: str, private_values: tuple[str, ...], workspace: Path) -> str:
    roots = {str(workspace.resolve()), workspace.resolve().as_posix()}
    if workspace.is_absolute():
        roots.update((str(workspace), workspace.as_posix()))
    roots.update(root.replace("\\", "\\\\") for root in tuple(roots))
    for root in sorted(roots, key=len, reverse=True):
        text = re.sub(re.escape(root), "<workspace>", text, flags=re.IGNORECASE if workspace.drive else 0)
    for value in private_values:
        if value:
            text = text.replace(value, "***")
    return text


def _workspace_name(workspace_root: Path, user_config_root: Path | None) -> str:
    resolved = workspace_root.resolve()
    entry = next((item for item in list_workspace_registry(user_config_root=user_config_root) if item.root_path.resolve() == resolved), None)
    return entry.name if entry is not None else workspace_root.name
