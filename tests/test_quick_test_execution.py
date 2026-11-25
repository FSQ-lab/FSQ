# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
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
async def test_quick_test_runs_keep_distinct_target_provenance_and_report_links(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from fsq_agent.adapters.cli import main
    from fsq_agent.config import create_workspace_at_root
    from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
    from fsq_agent.models import WorkspacePlatformCreateInput

    user_root = tmp_path / ".fsq"
    user_root.mkdir()
    original = prepared(user_root)
    root = original.root_path
    browser = tmp_path / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_bytes(b"not launched")
    browser.chmod(0o755)
    default = WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=browser)
    create_workspace_at_root(root_path=root, name="quick-test", platforms=[WorkspacePlatformCreateInput(platform="web", target=default, env={})], user_config_root=user_root)
    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Observed the fixture."))

    runtime_urls = []

    def agent_factory(settings):
        runtime_urls.append(settings.harness.web.base_url)
        return Agent()

    persisted = []
    runs = (
        ("msedge", "https://first.example.test/?section=one&access_token=URL_SECRET#top"),
        ("chrome", "https://second.example.test/?ref=UNIT%5FPRIVATE"),
        ("msedge-beta", "https://third.example.test/o'clock#sig=SIGNATURE_VALUE"),
        ("chromium", None),
    )
    for channel, url in runs:
        settings = original.settings.model_copy(deep=True)
        settings.harness.web.channel = channel
        settings.harness.web.base_url = url
        settings.agent_runtime.user_config_root = user_root
        item = original.model_copy(
            update={"target_id": channel, "target": WebWorkspaceTarget(browser_channel=channel, browser_executable_path=browser), "settings": settings, "requires_web_search_evidence": url is None}
        )
        result = await application.execute_prepared_quick_test(item, "Open the fixture and read the heading.", agent_factory=agent_factory)
        assert result.status == ("inconclusive" if url is None else "success")
        metadata_path = root / ".fsq/runs/web" / result.run_id / "run.json"
        raw = metadata_path.read_text(encoding="utf-8")
        facts = json.loads(raw)["provenance"]["configuration"]["quick_test_target"]
        assert facts["target_id"] == facts["browser_channel"] == channel
        if url is None:
            assert facts["base_url"] is None
        elif channel == "chrome":
            assert facts["base_url"] == "[redacted URL]"
        elif channel == "msedge-beta":
            assert facts["base_url"].startswith("https://third.example.test/o'clock#sig=")
        else:
            assert facts["base_url"].startswith("https://first.example.test/?section=one&access_token=")
        assert runtime_urls[-1] == url
        assert "URL_SECRET" not in raw
        assert "UNIT%5FPRIVATE" not in raw
        assert "SIGNATURE_VALUE" not in raw
        assert str(browser) not in raw
        persisted.append((result.run_id, metadata_path, raw))

    assert len({run_id for run_id, _, _ in persisted}) == len(runs)
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False, user_config_root=user_root))
    status, detail, _ = server.handle_get("/api/control-plane/workspaces/quick-test/platforms/web")
    assert status == 200
    assert detail["target"]["browserChannel"] == "chrome"

    browser.unlink()  # Historical reads do not depend on the saved default still being usable.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(root)
    opened = []

    def open_report(address):
        opened.append(address)
        return True

    monkeypatch.setattr("fsq_agent.adapters.cli._main.webbrowser.open", open_report)
    for run_id, metadata_path, raw in persisted:
        status, _, _ = server.handle_get(f"/api/control-plane/history/web/{run_id}", {"workspace": ["quick-test"]})
        assert status == 200
        shown = application.show_run(application.ShowRunRequest(current_directory=root, user_config_root=user_root, platform="web", run_id=run_id))
        assert shown.run.run_id == run_id
        cli = CliRunner().invoke(main, ["runs", "show", run_id, "--platform", "web", "--open"])
        assert cli.exit_code == 0, cli.output
        report_path = metadata_path.with_name("report.html")
        assert opened[-1] == report_path.as_uri()
        assert report_path.is_file()
        assert metadata_path.read_text(encoding="utf-8") == raw


@pytest.mark.asyncio
async def test_quick_test_windows_target_provenance_distinguishes_same_basename(monkeypatch, tmp_path):
    from fsq_agent.models import WindowsWorkspaceTarget

    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Observed the fixture."))

    original = prepared(tmp_path)
    cases = (
        ("Stable", "Microsoft Edge", "Microsoft Edge"),
        ("Beta", "Microsoft Edge Beta", "Microsoft Edge Beta"),
        ("Canary", "Microsoft Edge Canary", "Microsoft Edge Canary"),
        ("One", "msedge", "msedge"),
        ("Two", "msedge", "msedge"),
        ("One", r"C:\Private\Other\msedge.exe", "msedge.exe"),
        ("UNIT_PRIVATE", "msedge", "msedge"),
        ("one", "msedge", "msedge"),
        ("PrivatePathValue", "msedge", "msedge"),
    )
    identities = []
    for directory, label, expected_label in cases:
        settings = original.settings.model_copy(deep=True)
        settings.harness.platform = "windows"
        settings.output.runs_dir = original.root_path / ".fsq/runs/windows"
        exe = tmp_path / "Private" / directory / "msedge.exe"
        if directory == "PrivatePathValue":
            settings.runtime_secrets.set_values({"PRIVATE": str(exe).replace("/", "\\").upper()})
        settings.harness.windows.app_path = exe
        item = original.model_copy(update={"target_id": "windows-app", "target": WindowsWorkspaceTarget(app_path=exe), "application": label, "settings": settings})
        result = await application.execute_prepared_quick_test(item, "Open the fixture and read the heading.", agent_factory=lambda settings: Agent())
        raw = (settings.output.runs_dir / result.run_id / "run.json").read_text(encoding="utf-8")
        facts = json.loads(raw)["provenance"]["configuration"]["quick_test_target"]
        assert facts["application_name"] == "msedge.exe"
        assert facts["target_id"] == "windows-app"
        assert "Private" not in raw
        assert "UNIT_PRIVATE" not in raw
        assert facts["application_label"] == expected_label
        identities.append(facts.get("application_identity"))
    assert all(identities[:5])
    assert len(set(identities[:5])) == 5
    assert identities[3] == identities[5]  # Same resolved executable, different label.
    assert identities[6] is None
    assert identities[7] is not None
    assert identities[7] != identities[3]  # Same label and basename; only the directory's case differs.
    assert identities[8] is None  # The complete path is a private env value in Windows spelling.


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


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["6510", "Title A\nTitle B", "UNIT_PRIVATE answer", "   ", None])
async def test_primary_answer_survives_inconclusive_verification_without_leaking_secrets(monkeypatch, tmp_path, answer):
    from fsq_agent.models import AgentFinalOutput, StepResult

    item = prepared(tmp_path)
    raw = AgentFinalOutput(status="inconclusive", summary=answer).model_dump_json() if answer is not None else "unvalidated answer"
    verification = VerificationResult(status="inconclusive", summary="Collection coverage remains unproven.", diagnostics=["Partial coverage"])

    async def execute(self, request):
        return SimpleNamespace(
            task_result=TaskResult(
                task_id=request.task.id,
                status="inconclusive",
                steps=[StepResult(step_id=1, status="success", tool_name="agent_runtime.runner", actual_outcome=raw)],
                verification=verification,
                report=ReportArtifact(run_id="answer", path=item.settings.output.runs_dir / "answer" / "report.md"),
            ),
            recording=None,
        )

    monkeypatch.setattr(
        "fsq_agent.application._quick_test_execution.diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready")))
    )
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.validate_runtime_settings", lambda settings: None)
    monkeypatch.setattr("fsq_agent.application._quick_test_execution.DynamicExecutionService.execute", execute)
    result = await application.execute_prepared_quick_test(item, "Return only the requested answer", agent_factory=lambda settings: object())
    if answer and answer.strip():
        if "UNIT_PRIVATE" in answer:
            assert result.summary.endswith(" answer")
        else:
            assert result.summary == answer
    else:
        assert result.summary == verification.summary
    assert result.status == result.verification.status == "inconclusive"
    assert result.verification.summary == verification.summary
    assert result.verification.diagnostics == ["Partial coverage"]
    assert "UNIT_PRIVATE" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_case_manager_live_summary_delivers_answer_without_upgrading_verdict(monkeypatch, tmp_path):
    from fsq_agent.adapters.control_plane._execution import PreparedRun, _run_explore
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.models import AgentFinalOutput, StepResult

    item = prepared(tmp_path)
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="quick-test", platform="web", target_id="chrome", mode="explore", source={"goal": "Read distance"})
    request = PreparedRun(request_id=request_id, settings=item.settings, workspace_name="quick-test", platform_revision="exact", target_id="chrome", mode="explore", goal="Read distance")
    raw = AgentFinalOutput(status="inconclusive", summary="6510").model_dump_json()
    task_result = TaskResult(
        task_id="distance",
        status="inconclusive",
        steps=[StepResult(step_id=1, status="success", tool_name="agent_runtime.runner", actual_outcome=raw)],
        verification=VerificationResult(status="inconclusive", summary="Coverage remains unproven."),
        report=ReportArtifact(run_id="answer", path=item.settings.output.runs_dir / "answer" / "report.md"),
    )

    async def execute(self, request):
        return SimpleNamespace(task_result=task_result, recording=None)

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings", lambda *a: object())
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.DynamicExecutionService.execute", execute)
    await _run_explore(request, state)
    snapshot = state.snapshot(request_id)
    assert snapshot["summary"] == "6510"
    assert snapshot["status"] == snapshot["result"]["status"] == "inconclusive"
