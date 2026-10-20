# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli import main
from fsq_agent.application import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode


@pytest.mark.parametrize(("platform", "target"), [("web", "chrome"), ("android", "device-1"), ("macos", None), ("windows", None)])
def test_standalone_run_maps_request_and_streams_one_terminal(monkeypatch, tmp_path, platform, target):
    seen = []

    async def run(request, *, agent_factory, event_sink, **kwargs):
        seen.append(request)
        event_sink({"phase": "preparing", "message": "Preparing Quick Test."})
        event_sink({"phase": "running", "message": "Running the goal."})
        return SimpleNamespace(status="success", model_dump=lambda **kw: {"status": "success", "run_id": "exact-run", "root_path": str(tmp_path / "quick-test")})

    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_quick_test", run)
    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_control_plane", lambda *a: pytest.fail("No HTTP service"))
    monkeypatch.setattr("fsq_agent.adapters.cli._main.require_initialized_workspace", lambda *a: pytest.fail("No current-directory Workspace"))
    argv = ["--output", "jsonl", "--non-interactive", "quick-test", "run", "--platform", platform, "--goal", "Verify the exact application outcome."]
    if target is not None:
        argv += ["--target-id", target]
    result = CliRunner().invoke(main, argv)
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.output.splitlines()]
    assert [r["type"] for r in records] == ["event", "event", "result"]
    assert {r["operation"] for r in records} == {"quick-test.run"}
    assert {r["schema_version"] for r in records} == {"fsq.machine/v1"}
    assert len(seen) == 1
    assert seen[0].platform == platform
    assert seen[0].target_id == target
    assert seen[0].url is None


@pytest.mark.parametrize("mode", ["json", "jsonl"])
def test_web_url_option_preserves_target_and_goal_in_machine_request(monkeypatch, mode):
    url = "HTTPS://[::1]:8443/a(b)?q=%2f#part!"
    goal = "Verify heading, then visit https://later.test."
    seen = []

    async def run(request, **kwargs):
        seen.append(request)
        return SimpleNamespace(status="success", model_dump=lambda **kw: {"status": "success", "run_id": "exact-run"})

    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_quick_test", run)
    result = CliRunner().invoke(main, ["--output", mode, "--non-interactive", "quick-test", "run", "--platform", "web", "--target-id", "chrome", "--url", url, "--goal", goal])
    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert seen[0].url == url
    assert seen[0].goal == goal
    record = json.loads(result.output)
    assert record["operation"] == "quick-test.run"
    assert record["type"] == "result"


@pytest.mark.parametrize("platform", ["android", "macos", "windows"])
def test_url_option_rejected_for_native_before_submission(monkeypatch, platform):
    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_quick_test", lambda *a, **k: pytest.fail("Native URL submitted"))
    args = ["--platform", platform, "--goal", "Calculator: 1+1=2", "--url", "https://example.test"]
    if platform == "android":
        args += ["--target-id", "device"]
    result = CliRunner().invoke(main, ["--output", "json", "quick-test", "run", *args])
    assert result.exit_code == 2
    assert json.loads(result.output)["type"] == "error"


@pytest.mark.parametrize("url", ["", " ", " https://example.test", "ftp://example.test", "https://user:UNIT_URL_SECRET@example.test", "https://example.test/%GG"])
def test_invalid_web_url_has_safe_terminal_without_inference_or_config(monkeypatch, url):
    monkeypatch.setattr("fsq_agent.application._quick_test.load_platform_settings", lambda *a, **k: pytest.fail("Invalid URL reached configuration"))
    result = CliRunner().invoke(main, ["--output", "jsonl", "quick-test", "run", "--platform", "web", "--target-id", "chrome", "--url", url, "--goal", "Verify heading"])
    assert result.exit_code == 2, result.output
    records = [json.loads(line) for line in result.output.splitlines()]
    assert records[-1]["type"] == "error"
    assert sum(item["type"] != "event" for item in records) == 1
    assert records[-1]["operation"] == "quick-test.run"
    assert "UNIT_URL_SECRET" not in result.output


@pytest.mark.parametrize(
    "argv", [[], ["--platform", "web", "--goal", "Test"], ["--platform", "macos", "--goal", "Test", "--target-id", "override"], ["--platform", "web", "--goal", " ", "--target-id", "chrome"]]
)
def test_invalid_run_is_structured_and_never_submitted(monkeypatch, argv):
    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_quick_test", lambda *a, **k: pytest.fail("Invalid submission"))
    result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", "quick-test", "run", *argv])
    assert result.exit_code == 2
    record = json.loads(result.output)
    assert record["type"] == "error"
    assert record["operation"] == "quick-test.run"


@pytest.mark.parametrize(("status", "exit_code"), [("success", 0), ("failed", 1), ("inconclusive", 1), ("error", 5), ("cancelled", 130)])
def test_terminal_execution_status_controls_exit(monkeypatch, status, exit_code):
    async def run(*a, **k):
        return SimpleNamespace(status=status, model_dump=lambda **kw: {"status": status, "run_id": "exact"})

    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_quick_test", run)
    result = CliRunner().invoke(main, ["--output", "json", "quick-test", "run", "--platform", "macos", "--goal", "Calculator: verify 1+1=2"])
    assert result.exit_code == exit_code
    assert json.loads(result.output)["result"]["status"] == status


def test_context_unavailable_is_valid_result_without_submission(monkeypatch):
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.get_quick_test_context", lambda request: SimpleNamespace(can_start=False, model_dump=lambda **kw: {"can_start": False, "platform": request.platform})
    )
    result = CliRunner().invoke(main, ["--output", "json", "quick-test", "context", "--platform", "web"])
    assert result.exit_code == 4
    record = json.loads(result.output)
    assert record["type"] == "result"
    assert record["operation"] == "quick-test.context"
    assert record["result"]["can_start"] is False


def test_preparation_error_retains_classified_facts_without_run(monkeypatch):
    async def run(*a, **k):
        raise ApplicationError(code=ApplicationErrorCode.QUICK_TEST_PREREQUISITES, category=ApplicationErrorCategory.UNAVAILABLE, message="Runtime unavailable.")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_quick_test", run)
    result = CliRunner().invoke(main, ["--output", "json", "quick-test", "run", "--platform", "macos", "--goal", "Calculator: verify 1+1=2"])
    assert result.exit_code == 4
    assert json.loads(result.output)["error"]["code"] == "quick_test.prerequisites"


@pytest.mark.parametrize(
    ("code", "exit_code"),
    [("quick_test.prerequisites", 4), ("quick_test.inference_failed", 4), ("quick_test.discovery_failed", 4), ("quick_test.workspace_conflict", 3), ("quick_test.persistence_failed", 5)],
)
def test_real_preparation_codes_use_operation_exit_categories(monkeypatch, code, exit_code):
    from fsq_agent.application import _quick_test_execution as operation

    def fail(*a, **kw):
        raise ApplicationError(code=ApplicationErrorCode(code), category=ApplicationErrorCategory.REQUEST_VALIDATION, message="Classified preparation failure.")

    monkeypatch.setattr(operation, "prepare_quick_test", fail)
    result = CliRunner().invoke(main, ["--output", "json", "quick-test", "run", "--platform", "macos", "--goal", "Calculator: verify 1+1=2"])
    assert result.exit_code == exit_code
    assert json.loads(result.output)["error"]["code"] == code


def test_cli_finalization_failure_exits_internal_without_losing_successful_verification(monkeypatch, tmp_path):
    from fsq_agent.application import QuickTestPrepareResult
    from fsq_agent.application import _quick_test_execution as operation
    from fsq_agent.config import Settings
    from fsq_agent.execution import RunLifecycleService
    from fsq_agent.models import DynamicAgentOutcome, VerificationResult, WebWorkspaceTarget

    root = tmp_path / "quick-test"
    settings = Settings(harness={"platform": "web"})
    settings.workspace.root_dir = root
    settings.output.runs_dir = root / ".fsq/runs/web"
    item = QuickTestPrepareResult(
        workspace_name="quick-test",
        root_path=root,
        platform_revision="exact",
        target_id="chrome",
        target=WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=root / "browser"),
        application="https://example.test",
        settings=settings,
    )
    monkeypatch.setattr(operation, "prepare_quick_test", lambda *a, **kw: item)
    monkeypatch.setattr(operation, "diagnose_platform_settings", lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status="ready"))))
    monkeypatch.setattr(operation, "validate_runtime_settings", lambda settings: None)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Verified existing state."))

    def fail(*a, **kw):
        raise OSError("Metadata failure")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case_agent", lambda settings: Agent())
    monkeypatch.setattr(RunLifecycleService, "finalize", fail)
    result = CliRunner().invoke(main, ["--output", "json", "quick-test", "run", "--platform", "web", "--target-id", "chrome", "--goal", "Verify https://example.test outcome"])
    assert result.exit_code == 5
    record = json.loads(result.stdout)
    assert record["type"] == "error"
    assert record["error"]["details"]["execution_status"] == "success"
    assert record["error"]["details"]["verification"]["status"] == "success"
    assert record["error"]["details"]["execution_result_path"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT integration; platform-independent cancellation is covered separately.")
def test_sigint_finalizes_same_run_and_emits_cancelled_terminal(tmp_path):
    source = """
import asyncio, sys
from pathlib import Path
from types import SimpleNamespace
from fsq_agent.adapters.cli import _main
from fsq_agent.application import QuickTestPrepareResult
from fsq_agent.application import _quick_test_execution as operation
from fsq_agent.config import Settings
from fsq_agent.models import RunEvent, WebWorkspaceTarget
root = Path(sys.argv[1]) / 'quick-test'
settings = Settings(harness={'platform': 'web'})
settings.workspace.root_dir = root
settings.output.runs_dir = root / '.fsq/runs/web'
settings.cases.dir = root / 'cases/web'
prepared = QuickTestPrepareResult(workspace_name='quick-test', root_path=root, platform_revision='exact', target_id='chrome', target=WebWorkspaceTarget(browser_channel='chrome', browser_executable_path=root/'browser'), application='https://example.test', settings=settings)
operation.prepare_quick_test = lambda *a, **kw: prepared
operation.diagnose_platform_settings = lambda settings: SimpleNamespace(commands=SimpleNamespace(case_create=SimpleNamespace(status='ready')))
operation.validate_runtime_settings = lambda settings: None
class Agent:
    async def run_in_context(self, task, context, event_sink=None, **kwargs):
        await event_sink(RunEvent(run_id=context.run_id, task_id=task.id, type='run_started', title='Allocated'))
        await asyncio.sleep(30)
_main.create_case_agent = lambda settings: Agent()
_main.main(['--output','jsonl','--non-interactive','quick-test','run','--platform','web','--target-id','chrome','--goal','Verify https://example.test outcome'])
"""
    process = subprocess.Popen([sys.executable, "-u", "-c", source, str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # noqa: S603 -- controlled local fixture, no external actions.
    records = []
    try:
        while True:
            line = process.stdout.readline()
            assert line, process.stderr.read()
            record = json.loads(line)
            records.append(record)
            if record.get("event", {}).get("type") == "run_started":
                run_id = record["event"]["run_id"]
                process.send_signal(signal.SIGINT)
                break
        output, error = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    records.extend(json.loads(line) for line in output.splitlines())
    assert process.returncode == 130, (records, error)
    assert records[-1]["type"] == "result"
    assert records[-1]["result"]["status"] == "cancelled"
    assert records[-1]["result"]["run_id"] == run_id
    assert sum(record["type"] != "event" for record in records) == 1
    assert Path(records[-1]["result"]["execution_result_path"]).is_file()
