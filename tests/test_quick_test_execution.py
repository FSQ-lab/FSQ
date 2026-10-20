# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
from types import SimpleNamespace

import pytest

from fsq_agent import application
from fsq_agent.application import QuickTestPrepareRequest, QuickTestPrepareResult
from fsq_agent.config import Settings
from fsq_agent.models import DynamicAgentOutcome, ExecutableStep, ReportArtifact, RunEvent, RunnerEvent, RunnerStepResult, TaskResult, VerificationResult, WebWorkspaceTarget


def prepared(tmp_path, *, search=False):
    root = tmp_path / "quick-test"
    settings = Settings(harness={"platform": "web"})
    settings.workspace.root_dir = root
    settings.output.runs_dir = root / ".fsq/runs/web"
    settings.cases.dir = root / "cases/web"
    settings.runtime_secrets.set_values({"PRIVATE": "UNIT_PRIVATE"})
    return QuickTestPrepareResult(
        workspace_name="quick-test",
        root_path=root,
        platform_revision="revision",
        target_id="chrome",
        target=WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=tmp_path / "browser"),
        application="https://example.test",
        settings=settings,
        requires_web_search_evidence=search,
    )


@pytest.mark.asyncio
async def test_shared_execution_freezes_inputs_and_never_publishes(monkeypatch, tmp_path):
    item = prepared(tmp_path, search=True)
    seen = []
    events = []
    run_dir = item.settings.output.runs_dir / "run-exact"
    run_dir.mkdir(parents=True)
    report = run_dir / "report.md"
    report.write_text("Actual report")

    async def execute(self, request):
        seen.append(request)
        assert request.settings is not item.settings
        assert request.task.planning_reference_text == "Search FSQ and verify its website"
        return SimpleNamespace(
            task_result=TaskResult(
                task_id="task",
                status="inconclusive",
                steps=[],
                verification=VerificationResult(status="inconclusive", summary="UNIT_PRIVATE insufficient evidence"),
                report=ReportArtifact(run_id="run-exact", path=report),
            ),
            recording=None,
        )

    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.DynamicExecutionService.execute", execute)
    result = await application.execute_prepared_quick_test(item, "Search FSQ and verify its website", agent_factory=lambda settings: object(), event_sink=events.append)
    assert len(seen) == 1
    assert seen[0].requires_web_search_evidence
    assert seen[0].publication_directory is None
    assert seen[0].record
    assert result.status == "inconclusive"
    assert result.target == item.target
    assert result.report_path == report.resolve()
    assert "UNIT_PRIVATE" not in result.model_dump_json()
    assert not item.settings.cases.dir.exists()


@pytest.mark.asyncio
async def test_cancellation_before_prepare_has_no_side_effects(monkeypatch, tmp_path):
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.prepare_quick_test", lambda *a, **k: pytest.fail("Cancelled before preparation"))

    def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await application.run_quick_test(QuickTestPrepareRequest(platform="macos", goal="Calculator: verify 1+1=2"), agent_factory=lambda settings: object(), check_cancelled=cancel)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_real_execution_cancellation_preserves_allocated_run(monkeypatch, tmp_path):
    item = prepared(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            raise asyncio.CancelledError

    result = await application.execute_prepared_quick_test(item, "Verify a page outcome", agent_factory=lambda settings: Agent())
    assert result.status == "cancelled"
    assert result.run_id
    assert result.execution_result_path.is_file()
    assert result.root_path == item.root_path
    assert not item.settings.cases.dir.exists()


def test_quick_test_exports_share_canonical_identity():
    from fsq_agent.application import contracts, workspace

    assert workspace.run_quick_test is application.run_quick_test
    assert workspace.execute_prepared_quick_test is application.execute_prepared_quick_test
    assert workspace.QuickTestRunResult is contracts.QuickTestRunResult is application.QuickTestRunResult


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_real_execution_keeps_evidence_and_result_on_success_or_error(monkeypatch, tmp_path, failure):
    item = prepared(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)
    seen = []

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            seen.append(context.run_id)
            recorder = kwargs["evidence_sink"]
            step = recorder.allocate_step_identity(ExecutableStep(step_id="observe", action_name="observe", kind="observation", params={}))
            recorder.record_event(
                RunnerEvent(
                    run_id=context.run_id,
                    event_type="step_start",
                    step_id=step.step_id,
                    source_step_id=step.source_step_id,
                    step_execution_id=step.step_execution_id,
                    invocation_path=step.invocation_path,
                )
            )
            recorder.record_step_result(
                RunnerStepResult(step_id=step.step_id, source_step_id=step.source_step_id, step_execution_id=step.step_execution_id, invocation_path=step.invocation_path, status="passed")
            )
            await event_sink(RunEvent(run_id=context.run_id, task_id=task.id, type="step_completed", title="Observed UNIT_PRIVATE", payload={"api_key": "UNIT_PRIVATE"}))
            if failure:
                raise RuntimeError("UNIT_PRIVATE raw backend failure")
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Observed the requested outcome."))

    events = []
    result = await application.execute_prepared_quick_test(item, "Verify the page outcome", agent_factory=lambda settings: Agent(), event_sink=events.append)
    assert len(seen) == 1
    assert result.run_id == seen[0]
    assert result.status == ("error" if failure else "success")
    assert result.execution_result_path.is_file()
    assert result.evidence_manifest_path.is_file()
    assert "UNIT_PRIVATE" not in str(events) + result.model_dump_json()
    assert not item.settings.cases.dir.exists()
    if not failure:
        assert result.report_path.is_file()
        assert "case.recording_failed" in result.warnings


@pytest.mark.asyncio
async def test_frozen_minimal_result_is_not_report_and_missing_paths_are_absent(monkeypatch, tmp_path):
    item = prepared(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)
    directory = item.settings.output.runs_dir / "run-exact"
    directory.mkdir(parents=True)
    minimal = directory / "execution-result.json"
    minimal.write_text("{}")

    async def execute(self, request):
        return SimpleNamespace(
            task_result=TaskResult(
                task_id="task", status="success", steps=[], verification=VerificationResult(status="success", summary="Goal verified."), report=ReportArtifact(run_id="run-exact", path=minimal)
            ),
            recording=None,
        )

    monkeypatch.setattr("fsq_agent.application._quick_test_execution.DynamicExecutionService.execute", execute)
    result = await application.execute_prepared_quick_test(item, "Verify page", agent_factory=lambda settings: object())
    assert result.status == "success"
    assert result.report_path is None
    assert result.execution_result_path == minimal.resolve()
    assert result.evidence_manifest_path is None
    assert result.candidate_case_path is None
    assert "quick_test.rich_report_unavailable" in result.warnings


@pytest.mark.asyncio
async def test_cli_cancellation_callback_preserves_same_allocated_run(monkeypatch, tmp_path):
    item = prepared(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)
    active = []
    requested = False

    def cancel():
        if requested:
            raise asyncio.CancelledError

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            nonlocal requested
            active.append(context.run_id)
            requested = True
            kwargs["cancellation_check"]()
            pytest.fail("No external action after cancellation")

    result = await application.execute_prepared_quick_test(item, "Verify page", agent_factory=lambda settings: Agent(), check_cancelled=cancel)
    assert active == [result.run_id]
    assert result.status == "cancelled"
    assert result.execution_result_path.is_file()


def test_cli_full_operation_from_unregistered_directory(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from fsq_agent.adapters.cli import main

    item = prepared(tmp_path)
    calls = []
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.prepare_quick_test", lambda request, **kwargs: calls.append(request) or item)
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="inconclusive", summary="Evidence unavailable."))

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case_agent", lambda settings: Agent())
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["--output", "jsonl", "--non-interactive", "quick-test", "run", "--platform", "web", "--target-id", "chrome", "--goal", "Verify https://example.test outcome"])
    import json

    records = [json.loads(line) for line in result.output.splitlines()]
    assert result.exit_code == 1, result.output
    assert records[-1]["type"] == "result"
    assert records[-1]["result"]["status"] == "inconclusive"
    assert records[-1]["result"]["execution_result_path"]
    assert sum(record["type"] != "event" for record in records) == 1
    assert len(calls) == 1
    assert not (tmp_path / ".fsq").exists()


@pytest.mark.asyncio
async def test_finalization_failure_is_operation_error_with_frozen_verification(monkeypatch, tmp_path):
    from fsq_agent.execution import RunLifecycleService

    item = prepared(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Verified existing page.", diagnostics=["retained_diagnostic"]))

    def fail(*args, **kwargs):
        raise OSError("Metadata write failed")

    monkeypatch.setattr(RunLifecycleService, "finalize", fail)
    with pytest.raises(application.ApplicationError) as error:
        await application.execute_prepared_quick_test(item, "Verify page", agent_factory=lambda settings: Agent())
    assert error.value.category == application.ApplicationErrorCategory.INTERNAL
    details = error.value.details
    assert details["execution_status"] == "success"
    assert details["verification"]["status"] == "success"
    assert details["verification"]["diagnostics"] == ["retained_diagnostic"]
    assert details["duration_ms"] is not None
    assert details["execution_result_path"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/?q=%55%4E%49%54%5F%50%52%49%56%41%54%45",
        "HTTPS://example.test/?q=%55%4E%49%54%5F%50%52%49%56%41%54%45",
        "https://example.test/?a=UNIT_PRIVATE&q=%55%4E%49%54%5F%50%52%49%56%41%54%45",
    ],
)
async def test_encoded_private_application_never_reaches_public_result(monkeypatch, tmp_path, url):
    item = prepared(tmp_path).model_copy(update={"application": url})
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="inconclusive", summary="No evidence"))

    events = []
    result = await application.execute_prepared_quick_test(item, "Verify page", agent_factory=lambda settings: Agent(), event_sink=events.append)
    assert result.application == "[redacted URL]"
    assert "%55%4E%49%54" not in str(events) + result.model_dump_json()


@pytest.mark.asyncio
async def test_browser_shared_execution_keeps_recording_failure_diagnostic(monkeypatch, tmp_path):
    from fsq_agent.adapters.control_plane._execution import PreparedRun, _run_explore
    from fsq_agent.adapters.control_plane._state import ControlPlaneState

    item = prepared(tmp_path)
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="quick-test", platform="web", target_id="chrome", mode="explore", source={"goal": "Verify page"}, workflow="quick-test")
    request = PreparedRun(
        request_id=request_id, settings=item.settings, workspace_name="quick-test", platform_revision="exact", target_id="chrome", mode="explore", goal="Verify page", quick_test_preparation=item
    )
    result = application.QuickTestRunResult(
        workspace_name="quick-test",
        root_path=item.root_path,
        platform="web",
        platform_revision="exact",
        target_id="chrome",
        target=item.target,
        application=item.application,
        status="success",
        summary="Goal verified.",
        warnings=["case.recording_failed", "No replayable commands"],
    )

    async def execute(*a, **kw):
        return result

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.execute_prepared_quick_test", execute)
    await _run_explore(request, state)
    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == "success"
    assert any(event["message"] == "case.recording_failed" and event["level"] == "warning" for event in snapshot["events"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnostic",
    [
        "retained_diagnostic",
        "https://example.test/?q=%55%4E%49%54%5F%50%52%49%56%41%54%45",
        "HTTPS://example.test/?q=%55%4E%49%54%5F%50%52%49%56%41%54%45",
        "https://example.test/?a=UNIT_PRIVATE&q=%55%4E%49%54%5F%50%52%49%56%41%54%45",
    ],
)
@pytest.mark.parametrize("has_verification_details", [False, True])
async def test_browser_processing_error_consumes_application_details_without_earlier_run_event(monkeypatch, tmp_path, diagnostic, has_verification_details):
    from fsq_agent.adapters.control_plane._execution import PreparedRun, _run_explore
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.execution import RunLifecycleService, RunSource, allocate_run
    from fsq_agent.models import EvidenceBundle

    item = prepared(tmp_path)
    metadata = allocate_run(
        workspace=item.root_path,
        workspace_name="quick-test",
        platform="web",
        source_id="goal",
        mode="explore",
        source=RunSource(kind="goal", goal_summary="Verify page"),
        platform_runs_dir=item.settings.output.runs_dir,
    )
    run_dir = item.settings.output.runs_dir / metadata.run_id
    RunLifecycleService().freeze(
        run_dir,
        metadata,
        bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete"),
        verification=VerificationResult(status="success", summary="Verified page.", diagnostics=[diagnostic]),
        duration_ms=123,
    )
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="quick-test", platform="web", target_id="chrome", mode="explore", source={"goal": "Verify page"}, workflow="quick-test")
    request = PreparedRun(
        request_id=request_id, settings=item.settings, workspace_name="quick-test", platform_revision="exact", target_id="chrome", mode="explore", goal="Verify page", quick_test_preparation=item
    )

    async def fail(*a, **kw):
        details = {"run_id": metadata.run_id, "platform": "web", "execution_status": "success"}
        if has_verification_details:
            details["verification"] = {"status": "success", "summary": "Verified page.", "diagnostics": ["retained_diagnostic" if diagnostic == "retained_diagnostic" else "[redacted URL]"]}
        raise application.ApplicationError(
            code=application.ApplicationErrorCode.RUN_EXECUTION_FAILED,
            category=application.ApplicationErrorCategory.INTERNAL,
            message="Processing failed.",
            details=details,
        )

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.execute_prepared_quick_test", fail)
    await _run_explore(request, state)
    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == snapshot["result"]["status"] == "error"
    assert snapshot["runId"] == metadata.run_id
    assert snapshot["reportAvailable"]
    assert snapshot["result"]["executionStatus"] == "success"
    expected = "retained_diagnostic" if diagnostic == "retained_diagnostic" else "[redacted URL]"
    assert snapshot["result"]["verification"]["diagnostics"] == [expected]
    assert "%55%4E%49%54" not in str(snapshot)
    assert "UNIT_PRIVATE" not in str(snapshot)
    assert snapshot["result"]["durationMs"] == 123
