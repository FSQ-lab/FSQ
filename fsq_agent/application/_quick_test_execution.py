# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from fsq_agent.application._quick_test import prepare_quick_test
from fsq_agent.application.contracts.shared import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode
from fsq_agent.application.contracts.workspace import QuickTestPrepareRequest, QuickTestPrepareResult, QuickTestRunResult
from fsq_agent.application.doctor import diagnose_platform_settings
from fsq_agent.config import validate_runtime_settings
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, RunLifecycleService
from fsq_agent.models import Task, VerificationResult

if TYPE_CHECKING:
    from collections.abc import Callable


async def run_quick_test(request: QuickTestPrepareRequest, *, agent_factory, event_sink=None, check_cancelled: Callable[[], None] | None = None) -> QuickTestRunResult:
    if check_cancelled:
        check_cancelled()
    await _emit(event_sink, {"phase": "preparing", "message": "Checking prerequisites and identifying the goal's application."})
    try:
        prepared = prepare_quick_test(request, check_cancelled=check_cancelled)
    except ApplicationError as error:
        categories = {
            ApplicationErrorCode.QUICK_TEST_PREREQUISITES: ApplicationErrorCategory.UNAVAILABLE,
            ApplicationErrorCode.QUICK_TEST_INFERENCE_FAILED: ApplicationErrorCategory.UNAVAILABLE,
            ApplicationErrorCode.QUICK_TEST_DISCOVERY_FAILED: ApplicationErrorCategory.UNAVAILABLE,
            ApplicationErrorCode.QUICK_TEST_WORKSPACE_CONFLICT: ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            ApplicationErrorCode.QUICK_TEST_PERSISTENCE_FAILED: ApplicationErrorCategory.INTERNAL,
        }
        raise ApplicationError(code=error.code, category=categories.get(error.code, error.category), message=error.message, action=error.action, details=error.details) from None
    return await execute_prepared_quick_test(prepared, request.goal, agent_factory=agent_factory, event_sink=event_sink, check_cancelled=check_cancelled)


async def execute_prepared_quick_test(prepared: QuickTestPrepareResult, goal: str, *, agent_factory, event_sink=None, check_cancelled: Callable[[], None] | None = None) -> QuickTestRunResult:
    if check_cancelled:
        check_cancelled()
    settings = prepared.settings.model_copy(deep=True)
    secrets = (*settings.runtime_secrets.private_values().values(), settings.agent_runtime.api_key)

    def safe(text):
        return _safe_text(str(text), secrets)

    try:
        diagnosis = diagnose_platform_settings(settings)
        validate_runtime_settings(settings)
    except Exception:  # noqa: BLE001 -- do not expose configuration or backend details.
        raise ApplicationError(
            code=ApplicationErrorCode.QUICK_TEST_PREREQUISITES,
            category=ApplicationErrorCategory.UNAVAILABLE,
            message="Quick Test execution prerequisites could not be validated.",
            action="Recheck Provider and platform readiness.",
        ) from None
    if diagnosis.commands.case_create.status != "ready":
        raise ApplicationError(
            code=ApplicationErrorCode.QUICK_TEST_PREREQUISITES,
            category=ApplicationErrorCategory.UNAVAILABLE,
            message="Quick Test execution prerequisites are not ready.",
            action="Recheck Provider, platform, and the selected device or browser.",
        )
    if check_cancelled:
        check_cancelled()
    if agent_factory is None:
        raise ApplicationError(code=ApplicationErrorCode.CONFIGURATION_INVALID, category=ApplicationErrorCategory.CONFIGURATION, message="A Quick Test Agent factory is required.")

    async def progress(event):
        # Existing Run events are sanitized again at the transport-neutral boundary.
        value = event.model_dump(mode="json")
        value = RunLifecycleService.safe_value(value, secrets)
        value = _safe_encoded_values(value, secrets)
        await _emit(event_sink, value)

    task = Task(id=re.sub(r"[^a-z0-9]+", "-", goal.casefold()).strip("-")[:80] or "quick-test", name=goal, description=goal, planning_reference_kind="goal", planning_reference_text=goal)
    base = {
        "workspace_name": prepared.workspace_name,
        "root_path": prepared.root_path.resolve(),
        "platform": settings.harness.platform,
        "platform_revision": prepared.platform_revision,
        "target_id": prepared.target_id,
        "target": prepared.target,
        "application": safe(prepared.application),
    }
    await _emit(event_sink, {"phase": "running", "message": "Executing the prepared Quick Test goal.", "application": base["application"]})
    try:
        execution = await DynamicExecutionService(agent=agent_factory(settings)).execute(
            DynamicExecutionRequest(
                task=task,
                settings=settings,
                event_sink=progress,
                record=True,
                allow_recording_failure=True,
                cancellation_check=check_cancelled,
                requires_web_search_evidence=prepared.requires_web_search_evidence,
            )
        )
    except (asyncio.CancelledError, KeyboardInterrupt, Exception) as error:
        context = getattr(error, "context", {})
        run_id = getattr(error, "run_id", None) or (context.get("run_id") if isinstance(context, dict) else None)
        cancelled = isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)) or type(error).__name__ in {"TaskCancelledError", "ExecutionCancelled", "RunCancelled"}
        if not run_id:
            if cancelled:
                raise
            raise ApplicationError(code=ApplicationErrorCode.RUN_EXECUTION_FAILED, category=ApplicationErrorCategory.INTERNAL, message="Quick Test execution could not start.") from None
        paths = _paths(settings.output.runs_dir, run_id)
        frozen = None
        if paths["execution_result_path"]:
            try:
                frozen = RunLifecycleService.load_result(paths["execution_result_path"].parent)
            except (OSError, ValueError):
                pass
        verification = None
        if frozen and frozen.verification.get("status") in {"success", "failed", "inconclusive", "cancelled", "error"}:
            verification = VerificationResult.model_validate(_safe_encoded_values(RunLifecycleService.safe_value(frozen.verification, secrets), secrets))
        recovered = QuickTestRunResult(
            **base,
            run_id=run_id,
            status=frozen.outcome if frozen else ("cancelled" if cancelled else "error"),
            summary=safe(frozen.summary) if frozen else ("Quick Test cancelled." if cancelled else "Quick Test execution stopped."),
            **paths,
            verification=verification,
            duration_ms=frozen.duration_ms if frozen else None,
            warnings=["quick_test.execution_processing_stopped"],
        )
        if cancelled or (frozen and frozen.outcome == "error"):
            return recovered
        raise ApplicationError(
            code=ApplicationErrorCode.RUN_EXECUTION_FAILED,
            category=ApplicationErrorCategory.INTERNAL,
            message="Quick Test execution processing failed; retained execution facts are available.",
            details={**recovered.model_dump(mode="json"), "execution_status": frozen.outcome if frozen else None},
        ) from None
    result = execution.task_result
    await _emit(event_sink, {"phase": "finalizing", "message": "Collecting completed execution and artifact facts.", "run_id": result.report.run_id})
    recording = execution.recording
    paths = _paths(settings.output.runs_dir, result.report.run_id, report=result.report.path, candidate=recording.recorded_case_path if recording else None)
    verification = VerificationResult.model_validate(_safe_encoded_values(RunLifecycleService.safe_value(result.verification.model_dump(), secrets), secrets))
    warnings = [safe(item) for item in recording.warnings] if recording else ["case.recording_unavailable"]
    if recording and recording.status != "recorded":
        warnings.append("case.recording_failed")
        warnings.extend(safe(item) for item in recording.errors)
    if paths["report_path"] is None:
        warnings.append("quick_test.rich_report_unavailable")
    if paths["evidence_manifest_path"] is None:
        warnings.append("quick_test.evidence_manifest_unavailable")
    return QuickTestRunResult(
        **base,
        run_id=result.report.run_id,
        status=result.status,
        summary=verification.summary,
        verification=verification,
        duration_ms=result.duration_ms,
        **paths,
        case_name=safe(recording.case_name) if recording and recording.case_name else None,
        recording_draft=recording.draft if recording else None,
        warnings=warnings,
    )


async def _emit(sink, value) -> None:
    if sink:
        result = sink(value)
        if inspect.isawaitable(result):
            await result


def _safe_text(text: str, secrets: tuple[str, ...]) -> str:
    def url(match):
        value = match.group()
        decoded = unquote(value)
        if any(secret and secret in decoded for secret in secrets):
            return "[redacted URL]"
        return value

    return RunLifecycleService.safe_text(re.sub(r"https?://[^\s<>\"']+", url, text, flags=re.IGNORECASE), secrets)


def _safe_encoded_values(value, secrets):
    if isinstance(value, str):
        return _safe_text(value, secrets)
    if isinstance(value, dict):
        return {key: _safe_encoded_values(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_encoded_values(item, secrets) for item in value]
    return value


def _paths(runs_dir: Path, run_id: str, *, report: Path | None = None, candidate: Path | None = None) -> dict:
    root = Path(runs_dir).resolve()
    directory = root / run_id
    if not run_id or Path(run_id).name != run_id or directory.resolve().parent != root:
        raise ApplicationError(code=ApplicationErrorCode.RUN_METADATA_INVALID, category=ApplicationErrorCategory.INTERNAL, message="Quick Test Run identity is invalid.")

    def available(path):
        if path is None:
            return None
        path = Path(path).absolute()
        try:
            path.relative_to(directory)
            if path.resolve().is_relative_to(directory) and not any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(directory)) and path.is_file():
                return path
        except (OSError, ValueError):
            pass
        return None

    return {
        "report_path": available(report) if report and Path(report).name != "execution-result.json" else available(directory / "report.md"),
        "execution_result_path": available(directory / "execution-result.json"),
        "evidence_manifest_path": available(directory / "evidence-manifest.json"),
        "candidate_case_path": available(candidate or directory / "recorded.fsq.yaml"),
    }
