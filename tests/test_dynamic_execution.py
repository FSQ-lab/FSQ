# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.config import Settings
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, load_run_metadata
from fsq_agent.models import DynamicAgentOutcome, ExecutableStep, ReportArtifact, RunnerEvent, RunnerStepResult, Task, ToolExecutionError, VerificationResult


def _settings(root: Path) -> Settings:
    settings = Settings()
    settings.workspace.root_dir = root
    settings.output.runs_dir = root / ".fsq" / "runs" / "web"
    settings.harness.platform = "web"
    settings.agent_runtime.user_config_root = root / "user"
    return settings


class _SearchAgent:
    """Controlled Core journal facts using the Web driver's actual snapshot shape."""

    def __init__(self, variant="aria", status="success", errors=()):
        self.variant = variant
        self.status = status
        self.errors = errors

    async def run_in_context(self, task, context, event_sink=None, *, evidence_sink, **kwargs):
        from fsq_agent.core.evidence import ArtifactStore
        from fsq_agent.models import StepPhaseReport, StepResult

        variant = self.variant
        step = evidence_sink.allocate_step_identity(ExecutableStep(step_id="browser-fact", action_name="arbitrary_capability", kind="observation", params={}))
        if variant == "plan":
            evidence_sink.record_planned_step(step)
            evidence_sink.write_manifest()
        elif variant != "model_only":
            evidence_sink.record_event(
                RunnerEvent(
                    run_id=context.run_id,
                    event_type="step_start",
                    step_id=step.step_id,
                    step_execution_id=step.step_execution_id,
                    source_step_id=step.source_step_id,
                    invocation_path=step.invocation_path,
                )
            )
            if variant != "no_activity":
                evidence_sink.record_event(
                    RunnerEvent(
                        run_id=context.run_id,
                        event_type="action_result",
                        step_id=step.step_id,
                        step_execution_id=step.step_execution_id,
                        source_step_id=step.source_step_id,
                        invocation_path=step.invocation_path,
                        phase="invoke",
                        payload={"status": "passed"},
                    )
                )
            payload = {
                "url": "https://github.com/FSQ-lab/FSQ",
                "snapshot_type": "aria",
                "snapshot": '- link "Official website":\n  - /url: https://fsq.test',
                "coverage": {"status": "complete", "scope": "observed_aria_snapshot"},
                "truncated": False,
            }
            if variant == "text":
                payload = {
                    "url": "https://fsq.test",
                    "snapshot_type": "text",
                    "title": "FSQ",
                    "text": "FSQ website discovered via repository link",
                    "coverage": {"status": "partial", "scope": "visible_body_text"},
                    "truncated": False,
                }
            elif variant == "blank":
                payload["url"] = "about:blank"
            elif variant == "empty":
                payload["snapshot"] = " "
            elif variant == "wrong_shape":
                payload = {"url": "https://fsq.test", "content": "invented fixture shape"}
            ref = ArtifactStore(evidence_sink.output_dir).write_json(kind="ui_snapshot", step_id=step.step_id, phase="finalize", name="after-action", payload=payload)
            path = evidence_sink.output_dir / ref.path
            if variant == "missing":
                path.unlink()
            elif variant == "corrupt":
                path.write_text("{}")
            elif variant == "malformed":
                ref = ArtifactStore(evidence_sink.output_dir).write_bytes(kind="ui_snapshot", step_id=step.step_id, phase="finalize", name="invalid-json", data=b"{")
            elif variant == "outside":
                ref = ref.model_copy(update={"path": Path("../outside.json")})
            elif variant == "symlink":
                original = path.read_bytes()
                outside = evidence_sink.output_dir.parent / "outside.json"
                outside.write_bytes(original)
                path.unlink()
                path.symlink_to(outside)
            elif variant == "no_digest":
                ref = ref.model_copy(update={"sha256": None})
            elif variant == "wrong_size":
                ref = ref.model_copy(update={"size_bytes": 1})
            if variant == "unassociated":
                detached_ref = ref.model_copy(update={"step_id": None, "step_execution_id": None})
                evidence_sink.record_event(RunnerEvent(run_id=context.run_id, event_type="artifact_captured", payload={"artifact": detached_ref.model_dump(mode="json")}))
            evidence_sink.record_step_result(
                RunnerStepResult(
                    step_id=step.step_id,
                    step_execution_id=step.step_execution_id,
                    source_step_id=step.source_step_id,
                    invocation_path=step.invocation_path,
                    status="passed",
                    action_status="passed" if variant != "no_activity" else None,
                    action_name="arbitrary_capability",
                    kind="observation",
                    phase_reports=[] if variant == "unassociated" else [StepPhaseReport(step_id=step.step_id, phase="finalize", status="passed", artifact_refs=[ref])],
                )
            )
        return DynamicAgentOutcome(
            task=task,
            steps=[StepResult(step_id=1, actual_outcome="Claimed search", status="success")],
            verification=VerificationResult(status=self.status, summary="Model claims the official site was found."),
            errors=list(self.errors),
        )


@pytest.mark.parametrize(
    "variant",
    ["model_only", "plan", "blank", "empty", "wrong_shape", "missing", "corrupt", "malformed", "outside", "symlink", "no_digest", "wrong_size", "unassociated", "no_activity", "aria", "text"],
)
async def test_url_free_search_requires_current_run_browser_facts_before_every_consumer(tmp_path, variant):
    settings = _settings(tmp_path)
    coordinated = []
    result = await DynamicExecutionService(agent=_SearchAgent(variant)).execute(
        DynamicExecutionRequest(task=Task(description="Find FSQ official site https://not-evidence.test"), settings=settings, requires_web_search_evidence=True, report_coordinator=coordinated.append)
    )
    expected = "success" if variant in {"aria", "text"} else "inconclusive"
    task_result = result.task_result
    directory = settings.output.runs_dir / task_result.report.run_id
    frozen = json.loads((directory / "execution-result.json").read_text())
    for actual in [task_result, *coordinated]:
        assert actual.status == expected
        assert actual.verification.status == expected
    assert frozen["outcome"] == frozen["verification"]["status"] == expected
    assert load_run_metadata(directory).status == expected
    assert task_result.report.path.read_text() == task_result.verification.summary
    if expected == "inconclusive":
        assert "web_search_evidence_missing" in task_result.verification.diagnostics
        assert "Model claims" not in task_result.verification.summary


@pytest.mark.parametrize("status", ["failed", "inconclusive"])
@pytest.mark.parametrize("variant", ["model_only", "aria"])
async def test_search_evidence_gate_preserves_nonpositive_verdicts(tmp_path, status, variant):
    result = await DynamicExecutionService(agent=_SearchAgent(variant, status=status)).execute(
        DynamicExecutionRequest(task=Task(description="Search FSQ"), settings=_settings(tmp_path), requires_web_search_evidence=True)
    )
    assert result.task_result.status == status
    assert result.task_result.verification.status == status


async def test_search_gate_preserves_fatal_error_precedence(tmp_path):
    result = await DynamicExecutionService(agent=_SearchAgent("model_only", errors=[{"message": "Fatal fixture"}])).execute(
        DynamicExecutionRequest(task=Task(description="Search FSQ"), settings=_settings(tmp_path), requires_web_search_evidence=True)
    )
    assert result.task_result.status == "error"


async def test_search_requirement_rejects_nonweb_before_allocation(tmp_path):
    from fsq_agent.models import ConfigurationError

    settings = _settings(tmp_path)
    settings.harness.platform = "android"
    with pytest.raises(ConfigurationError):
        await DynamicExecutionService(agent=_SearchAgent()).execute(DynamicExecutionRequest(task=Task(description="Search FSQ"), settings=settings, requires_web_search_evidence=True))
    assert not settings.output.runs_dir.exists()


async def test_default_dynamic_keeps_legitimate_zero_activity_success(tmp_path):
    result = await DynamicExecutionService(agent=_SearchAgent("model_only")).execute(DynamicExecutionRequest(task=Task(description="No browser work required"), settings=_settings(tmp_path)))
    assert result.task_result.status == "success"


async def test_real_core_runner_observation_associations_qualify_without_named_command_rules(tmp_path):
    from fsq_agent.core import StepRunner
    from fsq_agent.core.evidence import ArtifactStore
    from fsq_agent.models import HarnessActionResult, HarnessArtifactRef, HarnessContext

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, *, evidence_sink, **kwargs):
            store = ArtifactStore(evidence_sink.output_dir)

            class Harness:
                def get_context(self):
                    return HarnessContext(platform="web")

                def before_action(self, step, context):
                    pass

                def after_action(self, step, context, result):
                    pass

                def invoke_action(self, step, context):
                    ref = store.write_json(
                        kind="ui_snapshot",
                        step_id=step.step_id,
                        phase="invoke",
                        name="observed-page",
                        payload={"url": "https://github.com/FSQ-lab/FSQ", "snapshot_type": "aria", "snapshot": '- link "Official website"'},
                    )
                    artifact = HarnessArtifactRef.model_validate(ref.model_dump(exclude={"step_id", "step_execution_id", "phase"}))
                    return HarnessActionResult(status="passed", action_name=step.action_name, artifact_refs=[artifact])

                def classify_error(self, error, phase, step):
                    return "harness_error"

            result = StepRunner(Harness(), evidence_sink=evidence_sink).run_step(context.run_id, ExecutableStep(step_id="page", action_name="neutral_browser_fact", kind="observation", params={}))
            assert result.status == "passed"
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Observed source satisfies the goal."))

    result = await DynamicExecutionService(agent=Agent()).execute(
        DynamicExecutionRequest(task=Task(description="Find FSQ official site"), settings=_settings(tmp_path), requires_web_search_evidence=True)
    )
    assert result.task_result.status == "success"


@pytest.mark.parametrize(
    ("required", "model_status", "status", "verification_status", "action_status"),
    [
        (False, "success", "error", "success", "passed"),
        (True, "success", "error", "inconclusive", "passed"),
        (True, "failed", "failed", "failed", "passed"),
        (True, "inconclusive", "inconclusive", "inconclusive", "passed"),
        (True, "success", "cancelled", "inconclusive", "cancelled"),
        (True, "success", "inconclusive", "inconclusive", "incomplete"),
    ],
)
async def test_real_core_capture_failure_retains_precedence_through_terminal(tmp_path, monkeypatch, required, model_status, status, verification_status, action_status):
    from dataclasses import replace

    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.adapters.control_plane._execution import PreparedRun, _run_explore
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.core import StepRunner
    from fsq_agent.models import HarnessActionResult, HarnessContext

    class Harness:
        def get_context(self):
            return HarnessContext(platform="web")

        def before_action(self, step, context):
            pass

        def invoke_action(self, step, context):
            return HarnessActionResult(status=action_status, action_name=step.action_name)

        def after_action(self, step, context, result):
            pass

        def capture_artifact(self, **kwargs):
            raise RuntimeError("Required browser observation capture failed.")

        def classify_error(self, error, phase, step):
            return "observation_error"

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, *, evidence_sink, **kwargs):
            runner = StepRunner(Harness(), capability_registry=build_capability_registry(platform="web"), evidence_sink=evidence_sink)
            result = runner.run_step(context.run_id, ExecutableStep(step_id="navigate", kind="action", action_name="navigate_to", params={"url": "https://example.test"}))
            assert result.action_status == action_status
            if action_status == "passed":
                assert result.failure_category == "artifact_error"
            else:
                assert result.status == action_status
            assert result.evidence_errors
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status=model_status, summary="Model verification fixture"))

    settings = _settings(tmp_path)
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="quick-test", platform="web", target_id="chrome", mode="explore", source={"goal": "Search FSQ"})
    prepared = PreparedRun(request_id, settings, "quick-test", "revision", "explore", "chrome", goal="Search FSQ", requires_web_search_evidence=required)
    returned = []
    coordinated = []
    execute = DynamicExecutionService.execute

    async def observed_execute(self, request):
        result = await execute(self, replace(request, report_coordinator=coordinated.append))
        returned.append(result.task_result)
        return result

    monkeypatch.setattr(DynamicExecutionService, "execute", observed_execute)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings", lambda *args: Agent())
    await _run_explore(prepared, state)
    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == status
    assert snapshot["result"]["status"] == status
    assert len(returned) == len(coordinated) == 1
    for result in [*returned, *coordinated]:
        assert result.status == status
        assert result.verification.status == verification_status
    result = returned[0]
    directory = settings.output.runs_dir / result.report.run_id
    frozen = json.loads((directory / "execution-result.json").read_text())
    assert frozen["outcome"] == load_run_metadata(directory).status == status
    assert frozen["verification"]["status"] == verification_status
    assert result.report.path.read_text() == result.verification.summary == snapshot["summary"]
    if required and model_status == "success":
        assert "web_search_evidence_missing" in result.verification.diagnostics
        assert "Model verification fixture" not in snapshot["summary"]


@pytest.fixture(autouse=True)
def _reporter(monkeypatch):
    def generate(reporter, run_id, task, results, verification):
        directory = reporter.runs_dir / run_id
        path = directory / "report.md"
        path.write_text(verification.summary, encoding="utf-8")
        path.with_suffix(".json").write_text("{}", encoding="utf-8")
        return ReportArtifact(run_id=run_id, path=path)

    monkeypatch.setattr("fsq_agent.execution.dynamic.ReportGenerator.generate", generate)


class _Agent:
    def __init__(self, settings: Settings, *, status: str = "success", error: BaseException | None = None, wrong_id: bool = False) -> None:
        self.settings = settings
        self.status = status
        self.error = error
        self.wrong_id = wrong_id
        self.run_ids: list[str] = []

    async def run_in_context(self, task: Task, context, event_sink=None, *, evidence_sink, cancellation_check=None) -> DynamicAgentOutcome:
        run_id = context.run_id
        self.run_ids.append(run_id)
        directory = self.settings.output.runs_dir / run_id
        assert load_run_metadata(directory).status == "running"
        if self.error is not None:
            raise self.error
        step = evidence_sink.allocate_step_identity(ExecutableStep(step_id="observed", action_name="observe", kind="observation", params={}))
        evidence_sink.record_event(
            RunnerEvent(
                run_id=run_id, event_type="step_start", step_id=step.step_id, source_step_id=step.source_step_id, step_execution_id=step.step_execution_id, invocation_path=step.invocation_path
            )
        )
        evidence_sink.record_step_result(
            RunnerStepResult(step_id=step.step_id, source_step_id=step.source_step_id, step_execution_id=step.step_execution_id, invocation_path=step.invocation_path, status="passed")
        )
        return DynamicAgentOutcome(
            task=task.model_copy(update={"id": "wrong-task"}) if self.wrong_id else task,
            steps=[],
            verification=VerificationResult(status=self.status, summary="Execution evidence reviewed."),
        )


@pytest.mark.parametrize("status", ["success", "failed", "inconclusive"])
async def test_execution_allocates_one_identity_and_finalizes_after_agent(tmp_path: Path, status: str) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings, status=status)
    request = DynamicExecutionRequest(task=Task(id="task", description="Task"), settings=settings)

    result = await DynamicExecutionService(agent=agent).execute(request)

    assert agent.run_ids == [result.task_result.report.run_id]
    assert re.fullmatch(r"task-\d{8}T\d{6}Z-[0-9a-f]{6}", agent.run_ids[0])
    metadata = load_run_metadata(settings.output.runs_dir / agent.run_ids[0])
    assert metadata.status == status
    assert metadata.result.summary == "Execution evidence reviewed."
    assert metadata.artifacts.report_markdown == "report.md"
    assert len(list(settings.output.runs_dir.glob("*/run.json"))) == 1


async def test_execution_repeated_tasks_allocate_distinct_runs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings)
    service = DynamicExecutionService(agent=agent)
    request = DynamicExecutionRequest(task=Task(id="same-task", description="Task"), settings=settings)
    first = await service.execute(request)
    second = await service.execute(request)
    assert first.task_result.report.run_id != second.task_result.report.run_id
    assert len(list(settings.output.runs_dir.glob("*/run.json"))) == 2


@pytest.mark.parametrize("callback_fails", [False, True])
@pytest.mark.parametrize("status", ["success", "failed"])
async def test_report_callback_cannot_mutate_authoritative_result(tmp_path: Path, callback_fails: bool, status: str) -> None:
    settings = _settings(tmp_path)
    recorded = []

    def mutate(result):
        result.status = "success" if status == "failed" else "failed"
        result.verification.summary = "mutated"
        result.verification.status = "success" if status == "failed" else "failed"
        result.steps.append(object())
        result.report.path = tmp_path / "wrong-report.md"
        if callback_fails:
            raise RuntimeError("callback failure")

    def record(**kwargs):
        recorded.append(kwargs["result"].model_copy(deep=True))
        return SimpleNamespace(status="recorded", publication_status="not_requested", publication_errors=[], published_case_path=None)

    result = await DynamicExecutionService(agent=_Agent(settings, status=status), recording_service=SimpleNamespace(record=record)).execute(
        DynamicExecutionRequest(task=Task(id="task", description="Task"), settings=settings, record=True, report_coordinator=mutate)
    )
    for actual in [result.task_result, *recorded]:
        assert actual.status == status
        assert actual.verification.status == status
        assert actual.verification.summary == "Execution evidence reviewed."
        assert actual.steps == []
        assert actual.report.path.name == "report.md"
    assert len(recorded) == 1
    directory = settings.output.runs_dir / result.task_result.report.run_id
    assert load_run_metadata(directory).status == status


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("requires_web_search_evidence", [False, True])
async def test_execution_finalizes_agent_error_or_cancellation_and_preserves_exception(tmp_path: Path, cancelled: bool, requires_web_search_evidence: bool) -> None:
    settings = _settings(tmp_path)
    error = asyncio.CancelledError() if cancelled else RuntimeError("primary failure")
    agent = _Agent(settings, error=error)
    with pytest.raises(asyncio.CancelledError if cancelled else ToolExecutionError) as failure:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings, requires_web_search_evidence=requires_web_search_evidence))
    assert (failure.value if cancelled else failure.value.__cause__) is error
    assert load_run_metadata(settings.output.runs_dir / agent.run_ids[0]).status == ("cancelled" if cancelled else "error")


async def test_execution_error_freezes_steps_recovered_from_deep_evidence_journal(tmp_path: Path) -> None:
    from fsq_agent.core import StepRunner
    from fsq_agent.models import HarnessActionResult, HarnessContext

    settings = _settings(tmp_path)

    class Harness:
        def get_context(self):
            return HarnessContext(platform="web")

        def before_action(self, step, context):
            pass

        def invoke_action(self, step, context):
            output = {"attributes": {"value": "A"}}
            for _ in range(10):
                output = {"children": [output]}
            return HarnessActionResult(status="passed", action_name=step.action_name, output={"snapshot": output})

        def after_action(self, step, context, result):
            pass

        def classify_error(self, error, phase, step):
            return "harness_error"

    class FailingAfterEvidenceAgent(_Agent):
        async def run_in_context(self, task, context, event_sink=None, *, evidence_sink, **kwargs):
            self.run_ids.append(context.run_id)
            StepRunner(Harness(), evidence_sink=evidence_sink).run_step(
                context.run_id,
                ExecutableStep(step_id="deep-observation", kind="observation", action_name="ui_snapshot", params={}),
            )
            raise RuntimeError("failure after durable evidence")

    agent = FailingAfterEvidenceAgent(settings)

    with pytest.raises(ToolExecutionError):
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))

    run_dir = settings.output.runs_dir / agent.run_ids[0]
    frozen = json.loads((run_dir / "execution-result.json").read_text())
    assert frozen["counts"]["total"] == 1
    assert frozen["counts"]["passed"] == 1


@pytest.mark.parametrize("before_allocation", [False, True])
async def test_execution_callback_cancellation_has_owned_lifecycle(tmp_path: Path, before_allocation: bool) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings)
    calls = []

    def check_cancelled() -> None:
        calls.append(True)
        if before_allocation or len(calls) == 2:
            raise RuntimeError("transport cancellation")

    with pytest.raises(RuntimeError, match="transport cancellation"):
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings, cancellation_check=check_cancelled))
    if before_allocation:
        assert not agent.run_ids
        assert not settings.output.runs_dir.exists()
    else:
        assert load_run_metadata(settings.output.runs_dir / agent.run_ids[0]).status == "cancelled"


async def test_execution_rejects_mismatched_task_identity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings, wrong_id=True)
    with pytest.raises(ToolExecutionError) as failure:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert isinstance(failure.value.__cause__, ValueError)
    assert load_run_metadata(settings.output.runs_dir / agent.run_ids[0]).status == "error"


@pytest.mark.parametrize("event_type", ["action_result", "step_error", "phase_finish"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_journal_phase_failure_keeps_primary_cancellation_and_run_identity(tmp_path, monkeypatch, caplog, event_type, cancelled):
    from fsq_agent.core import EvidenceRecorder, StepRunner
    from fsq_agent.models import HarnessActionResult, HarnessContext

    settings = _settings(tmp_path)
    primary = asyncio.CancelledError("primary cancellation")
    disk_error = OSError("private journal error")
    invoked, cleanup = [], []
    original_record = EvidenceRecorder.record_event

    def record(recorder, event):
        if event.event_type == event_type and event.phase == "invoke":
            raise disk_error
        return original_record(recorder, event)

    monkeypatch.setattr(EvidenceRecorder, "record_event", record)

    class Harness:
        def get_context(self):
            return HarnessContext(platform="web")

        def before_action(self, step, context):
            pass

        def invoke_action(self, step, context):
            invoked.append(step.step_id)
            if cancelled:
                raise primary
            return HarnessActionResult(action_name=step.action_name, status="failed" if event_type == "step_error" else "passed")

        def classify_error(self, *args):
            return "cancelled" if cancelled else "action_error"

    class Agent(_Agent):
        async def run_in_context(self, task, context, event_sink=None, *, evidence_sink, **kwargs):
            self.run_ids.append(context.run_id)
            runner = StepRunner(Harness(), evidence_sink=evidence_sink)
            try:
                runner.run_step(context.run_id, ExecutableStep(step_id="one", kind="action", action_name="test", params={}))
            finally:
                cleanup.append(True)
                with pytest.raises(OSError, match="evidence is unavailable"):
                    runner.run_step(context.run_id, ExecutableStep(step_id="two", kind="action", action_name="test", params={}))

    agent = Agent(settings)
    with pytest.raises(asyncio.CancelledError if cancelled else ToolExecutionError) as raised:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Test"), settings=settings))
    assert (raised.value if cancelled else raised.value.__cause__) is (primary if cancelled else disk_error)
    if cancelled:
        assert raised.value.run_id == agent.run_ids[0]
        assert raised.value.platform == "web"
        assert "Interrupted phase persistence failed (OSError)" in caplog.text
    assert len(invoked) == 1
    assert cleanup == [True]
    assert "private journal error" not in caplog.text
    assert load_run_metadata(settings.output.runs_dir / agent.run_ids[0]).status == ("cancelled" if cancelled else "error")


async def test_execution_initial_metadata_failure_prevents_agent_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings)

    def fail_allocation(**kwargs):
        raise OSError("initial metadata failure")

    monkeypatch.setattr("fsq_agent.execution.dynamic.allocate_run", fail_allocation)
    with pytest.raises(OSError, match="initial metadata failure"):
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert agent.run_ids == []


async def test_execution_failure_finalization_does_not_mask_original_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.execution import dynamic

    settings = _settings(tmp_path)
    primary = RuntimeError("original agent failure")
    agent = _Agent(settings, error=primary)

    def fail_terminal(*args, **kwargs):
        raise OSError("secondary metadata failure")

    monkeypatch.setattr(dynamic.RunLifecycleService, "finalize", fail_terminal)
    with pytest.raises(ToolExecutionError) as failure:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert failure.value.__cause__ is primary


@pytest.mark.parametrize("failure_kind", ["none", "error", "cancelled"])
async def test_failed_heartbeat_preserves_local_primary_failure(tmp_path: Path, monkeypatch, caplog, failure_kind: str) -> None:
    settings = _settings(tmp_path)
    primary = asyncio.CancelledError("cancelled") if failure_kind == "cancelled" else RuntimeError("primary") if failure_kind == "error" else None
    heartbeat_finished = asyncio.Event()
    secondary = OSError("private heartbeat failure")

    async def heartbeat(run_dir):
        heartbeat_finished.set()
        raise secondary

    class WaitingAgent(_Agent):
        async def run_in_context(self, *args, **kwargs):
            await heartbeat_finished.wait()
            return await super().run_in_context(*args, **kwargs)

    monkeypatch.setattr(DynamicExecutionService, "_heartbeat", staticmethod(heartbeat))
    agent = WaitingAgent(settings, error=primary)
    try:
        raise ValueError("unrelated caller exception")  # noqa: TRY301 - reproduce an ambient handled exception.
    except ValueError:
        with pytest.raises(asyncio.CancelledError if failure_kind == "cancelled" else ToolExecutionError) as raised:
            await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert (raised.value if failure_kind == "cancelled" else raised.value.__cause__) is (primary or secondary)
    if primary is not None:
        assert "Run heartbeat cleanup failed (OSError)" in caplog.text
        assert "private heartbeat failure" not in caplog.text


async def test_execution_terminal_metadata_failure_preserves_report_and_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.execution import dynamic

    settings = _settings(tmp_path)
    agent = _Agent(settings)

    def fail_success(*args, **kwargs):
        raise OSError("terminal metadata failure")

    monkeypatch.setattr(dynamic.RunLifecycleService, "finalize", fail_success)
    with pytest.raises(ToolExecutionError) as failure:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert isinstance(failure.value.__cause__, OSError)
    directory = settings.output.runs_dir / agent.run_ids[0]
    assert (directory / "report.md").read_text(encoding="utf-8") == "Execution evidence reviewed."
    assert (directory / "execution-result.json").is_file()
    assert load_run_metadata(directory).status == "running"


def test_agent_has_no_execution_import_even_inside_functions() -> None:
    package = Path(__file__).parents[1] / "fsq_agent" / "agent"
    imports = []
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("fsq_agent.execution"):
                imports.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import) and any(alias.name.startswith("fsq_agent.execution") for alias in node.names):
                imports.append(f"{path.name}:{node.lineno}")
    assert imports == []


async def test_dynamic_metadata_redacts_configured_values_before_truncation_and_slugging(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    private_value = "Private_Value_2026"
    settings.runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": private_value})

    class SensitiveSummaryAgent(_Agent):
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            result = await super().run_in_context(task, context, event_sink, **kwargs)
            result.verification.summary = f"Observed {private_value} during the task."
            return result

    agent = SensitiveSummaryAgent(settings)
    source_id = "login-with-private-value-2026"
    task = Task(id=source_id, name="x" * 192 + private_value, description="Task")
    result = await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=settings))
    directory = settings.output.runs_dir / result.task_result.report.run_id
    serialized = (directory / "run.json").read_text(encoding="utf-8")
    metadata = load_run_metadata(directory)
    assert private_value not in serialized
    assert "Private_" not in metadata.source.goal_summary
    assert "private-value-2026" not in directory.name
    assert metadata.source.goal_summary == "x" * 192 + "***"
    assert metadata.result.summary == "Observed *** during the task."
    assert task.id == source_id


@pytest.mark.parametrize("path_style", ["native", "posix", "escaped", "case_variant"])
async def test_dynamic_metadata_removes_absolute_workspace_paths(tmp_path: Path, path_style: str) -> None:
    workspace = tmp_path / "workspace folder"
    settings = _settings(workspace)
    root_text = str(workspace.resolve())
    if path_style == "posix":
        root_text = workspace.resolve().as_posix()
    elif path_style == "escaped":
        root_text = root_text.replace("\\", "\\\\")
    elif path_style == "case_variant" and workspace.drive:
        root_text = root_text.upper()

    class PathSummaryAgent(_Agent):
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            result = await super().run_in_context(task, context, event_sink, **kwargs)
            result.verification.summary = f"Read {root_text}/cases/input.yaml."
            return result

    agent = PathSummaryAgent(settings)
    task = Task(id="workspace-task", name=f"Inspect {root_text}", description="Task")
    result = await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.source.goal_summary == "Inspect <workspace>"
    assert metadata.result.summary == "Read <workspace>/cases/input.yaml."
    assert result.task_result.verification.summary == f"Read {root_text}/cases/input.yaml."


@pytest.mark.parametrize(("report_format", "filename"), [("markdown", "report.md"), ("json", "report-fallback.json"), ("html", "report.html")])
async def test_dynamic_report_index_respects_declared_format(tmp_path: Path, monkeypatch, report_format: str, filename: str) -> None:
    settings = _settings(tmp_path)

    def generate(reporter, run_id, task, results, verification):
        report = settings.output.runs_dir / run_id / filename
        report.write_text("report content", encoding="utf-8")
        if report_format == "markdown":
            report.with_suffix(".json").write_text("{}", encoding="utf-8")
        return ReportArtifact(run_id=run_id, path=report, format=report_format)

    monkeypatch.setattr("fsq_agent.execution.dynamic.ReportGenerator.generate", generate)
    result = await DynamicExecutionService(agent=_Agent(settings)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    index = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id).artifacts
    assert index.report_markdown == (filename if report_format == "markdown" else None)
    assert index.html_report == (filename if report_format == "html" else None)
    assert index.report == ("report.json" if report_format == "markdown" else filename if report_format == "json" else "execution-result.json")


@pytest.mark.parametrize("status", ["success", "failed", "inconclusive"])
@pytest.mark.parametrize("summary_length", [2000, 2001, 5000])
async def test_dynamic_metadata_bounds_summary_without_changing_business_result(tmp_path: Path, status: str, summary_length: int) -> None:
    settings = _settings(tmp_path)
    summary = "v" * summary_length

    class LongSummaryAgent(_Agent):
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            result = await super().run_in_context(task, context, event_sink, **kwargs)
            result.verification.summary = summary
            return result

    result = await DynamicExecutionService(agent=LongSummaryAgent(settings, status=status)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.status == status
    assert metadata.result.summary == summary[:2000]
    assert result.task_result.verification.summary == summary
    assert result.task_result.report.path.read_text(encoding="utf-8") == summary


@pytest.mark.parametrize("sensitive_kind", ["secret", "workspace"])
async def test_dynamic_summary_redacts_before_metadata_length_limit(tmp_path: Path, sensitive_kind: str) -> None:
    settings = _settings(tmp_path)
    private_value = "private-value-across-metadata-boundary"
    settings.runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": private_value})
    sensitive_value = private_value if sensitive_kind == "secret" else str(tmp_path)
    replacement = "***" if sensitive_kind == "secret" else "<workspace>"
    summary = "v" * 1990 + sensitive_value + " result" * 100

    class BoundarySummaryAgent(_Agent):
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            result = await super().run_in_context(task, context, event_sink, **kwargs)
            result.verification.summary = summary
            return result

    result = await DynamicExecutionService(agent=BoundarySummaryAgent(settings)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.status == "success"
    assert metadata.result.summary == ("v" * 1990 + replacement + " result" * 100)[:2000]
    assert result.task_result.verification.summary == summary


async def test_dynamic_workspace_identity_uses_configured_user_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path / "different-directory-name")
    selected_root = tmp_path / "selected-config"
    settings.agent_runtime.user_config_root = selected_root
    observed_roots = []

    def list_registry(user_config_root=None):
        observed_roots.append(user_config_root)
        return [SimpleNamespace(root_path=settings.workspace.root_dir, name="checkout" if user_config_root == selected_root else "wrong-default-name")]

    monkeypatch.setattr("fsq_agent.execution.dynamic.list_workspace_registry", list_registry)
    result = await DynamicExecutionService(agent=_Agent(settings)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.workspace == {"name": "checkout"}
    assert observed_roots == [selected_root]
