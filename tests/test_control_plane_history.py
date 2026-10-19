# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions


@pytest.mark.parametrize(
    "fragment",
    [
        "access%5F%74oken=AUDIT_SENTINEL",
        "access%5F%74oken=AUDIT_SENTINEL?part=private",
        "client%5Fsecret=AUDIT_SENTINEL&state=public",
        "/callback?access%5F%74oken=AUDIT_SENTINEL&state=public",
        "/callback?access%5F%74oken=AUDIT_SENTINEL&token=SECOND_PRIVATE",
        "/route?sig=AUDIT_SENTINEL",
    ],
)
def test_public_redaction_boundary_masks_encoded_fragment_credentials(fragment):
    from fsq_agent.execution import RunLifecycleService

    url = f"https://example.test/callback?q=FSQ%20github#{fragment}"
    for redact in (RunLifecycleService.safe_text, RunLifecycleService.safe_value, RunLifecycleService.safe_source_text):
        result = redact(url)
        assert "AUDIT_SENTINEL" not in result
        assert "SECOND_PRIVATE" not in result
        assert "https://example.test/callback?q=FSQ%20github#" in result


@pytest.mark.parametrize("fragment", ["installation", "access_token", "token", "/docs/getting-started?tab=python", "section=public&tab=examples", "section%20one"])
def test_public_redaction_boundary_preserves_benign_fragments(fragment):
    from fsq_agent.execution import RunLifecycleService

    url = f"https://example.test/docs?q=FSQ%20github#{fragment}"
    assert RunLifecycleService.safe_text(url) == url


@pytest.mark.parametrize("fragment", ["access%5F%74oken=AUDIT_SENTINEL", "/callback?access%5F%74oken=AUDIT_SENTINEL&state=public"])
async def test_control_plane_event_and_terminal_redact_unknown_fragment_credentials(tmp_path, monkeypatch, fragment):
    import json

    from fsq_agent.adapters.control_plane._execution import PreparedRun, _run_explore
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.config import Settings
    from fsq_agent.models import DynamicAgentOutcome, RunEvent, VerificationResult

    url = f"https://example.test/callback#{fragment}"

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            event_sink(RunEvent(run_id=context.run_id, task_id=task.id, type="run_started", title="Browser result", message=url))
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary=url))

    settings = Settings(harness={"platform": "web"})
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    settings.agent_runtime.user_config_root = tmp_path / "user"
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="quick-test", platform="web", target_id="chrome", mode="explore", source={"goal": "Search FSQ"})
    prepared = PreparedRun(request_id, settings, "quick-test", "revision", "explore", "chrome", goal="Search FSQ")
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings", lambda *args: Agent())
    await _run_explore(prepared, state)
    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == "success"
    assert snapshot["terminal"]
    assert snapshot["summary"].startswith("https://example.test/callback#")
    assert "AUDIT_SENTINEL" not in json.dumps(snapshot)
    assert any(event["message"].startswith("https://example.test/callback#") for event in snapshot["events"])


@pytest.mark.parametrize(
    "message",
    [
        "Found https://fsq-lab.github.io/FSQ/",
        "Source: https://github.com/FSQ-lab/FSQ",
        "Official site: (https://fsq-lab.github.io/FSQ/).",
        "See https://example.test/a-/b.c/(d)/?q=FSQ%20github&lang=zh-CN#links.",
        "http://localhost:8080/search?q=FSQ&next=%2Fdocs%2F",
        "https://[::1]:8443/path/",
    ],
)
def test_safe_text_preserves_normal_http_urls_and_punctuation(message):
    from fsq_agent.adapters.control_plane._evidence import safe_text

    assert safe_text(message) == message


@pytest.mark.parametrize("path", ["/Users/alice/private/run.json", r"C:\Users\alice\private\run.json", r"\\host\private\run.json"])
@pytest.mark.parametrize("url_first", [False, True])
def test_safe_text_preserves_url_but_masks_local_paths_in_same_message(path, url_first):
    from fsq_agent.adapters.control_plane._evidence import safe_text

    url = "https://github.com/FSQ-lab/FSQ"
    message = f"Found {url}; saved {path}" if url_first else f"Saved {path}; found {url}"
    result = safe_text(message)
    assert url in result
    assert "[local path]" in result
    assert "alice" not in result
    assert "private" not in result


@pytest.mark.parametrize(
    "url",
    [
        "https://user:URL_PRIVATE@example.test/docs/",
        "https://example.test/docs/?password=URL_PRIVATE",
        "https://example.test/docs/?access_token=URL_PRIVATE",
        "https://example.test/docs/?access%5Ftoken=URL_PRIVATE",
        "https://example.test/docs/?signature=URL_PRIVATE",
        "https://example.test/docs/?cookie=URL_PRIVATE",
        "https://example.test/docs/#access_token=URL_PRIVATE",
    ],
)
def test_safe_text_does_not_expose_url_credentials_or_sensitive_values(url):
    from fsq_agent.adapters.control_plane._evidence import safe_text

    result = safe_text(f"Found {url}")
    assert "URL_PRIVATE" not in result
    assert "user:" not in result
    assert "https://example.test/docs/" in result


def test_safe_text_keeps_private_values_and_header_redaction_with_urls():
    from fsq_agent.adapters.control_plane._evidence import safe_text

    result = safe_text(
        "Found https://fsq-lab.github.io/FSQ/\nPrivate https://example.test/PRIVATE_VALUE/path\nAuthorization: Bearer HEADER_PRIVATE\nCookie: session=COOKIE_PRIVATE\nOther api_key=KEY_PRIVATE",
        secret_values=("PRIVATE_VALUE",),
    )
    assert "https://fsq-lab.github.io/FSQ/" in result
    for secret in ("PRIVATE_VALUE", "HEADER_PRIVATE", "COOKIE_PRIVATE", "KEY_PRIVATE"):
        assert secret not in result
    assert "[redacted]" in result


@pytest.mark.parametrize("prefix", ["api_key=", "access_token=", "Bearer ", "Basic "])
def test_safe_text_masks_credentials_even_when_the_value_is_a_url(prefix):
    from fsq_agent.adapters.control_plane._evidence import safe_text

    assert "private.example" not in safe_text(prefix + "https://private.example/secret/")


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("private https://example.test/secret/", "private https://example.test/secret/"),
        ("https://example.test/PRIVATE%20VALUE/", "PRIVATE VALUE"),
    ],
)
def test_safe_text_does_not_leak_private_values_across_url_boundaries(message, secret):
    from fsq_agent.adapters.control_plane._evidence import safe_text

    result = safe_text(message, secret_values=(secret,))
    assert "example.test" not in result
    assert "PRIVATE%20VALUE" not in result


@pytest.mark.parametrize("url", ["https://", "https://[broken/path/", "https://example.test:bad/path/", r"https://example.test\private\path"])
def test_safe_text_does_not_exempt_invalid_http_addresses_from_redaction(url):
    from fsq_agent.adapters.control_plane._evidence import safe_text

    result = safe_text(url)
    assert result != url


@pytest.mark.parametrize("value", ["file:///Users/alice/private/run.json", "ftp://host/private/run.json", r"file:C:\Users\alice\private\run.json"])
def test_safe_text_does_not_exempt_unsupported_schemes_from_path_masking(value):
    from fsq_agent.adapters.control_plane._evidence import safe_text

    result = safe_text(value)
    assert "[local path]" in result
    assert "private" not in result


def test_history_requires_local_access_before_application_calls(monkeypatch):
    from fsq_agent.adapters.control_plane import _server as _history

    monkeypatch.setattr(_history, "_list_history", lambda *args: (_ for _ in ()).throw(AssertionError("must not read")))
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, _, headers = server.handle_get("/api/control-plane/history", {"workspace": ["demo"]}, peer_host="192.0.2.1")
    assert status == 403
    assert headers["Cache-Control"] == "no-store"


def test_history_list_does_not_require_live_request(monkeypatch):
    from fsq_agent.adapters.control_plane import _server as _history

    calls = []
    monkeypatch.setattr(_history, "_list_history", lambda query, user_root: calls.append(query) or {"workspace": "demo", "runs": [], "truncated": False})
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, result, headers = server.handle_get("/api/control-plane/history", {"workspace": ["demo"], "status": ["failed"], "limit": ["10"]})
    assert status == 200
    assert result["workspace"] == "demo"
    assert calls[0]["status"] == ["failed"]
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_history_export_rejects_cross_origin_before_writing(monkeypatch):
    from fsq_agent.adapters.control_plane import _server as _history

    monkeypatch.setattr(_history, "_export_history", lambda *args: (_ for _ in ()).throw(AssertionError("must not write")))
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, payload = server.handle_post("/api/control-plane/history/web/run-1/exports", {"workspaceName": "demo", "format": "html"}, origin="https://other.example", host="127.0.0.1:8879")
    assert status == 403
    assert payload["code"] == "cross_origin_forbidden"


def test_history_download_retains_validated_file_mapping(tmp_path, monkeypatch):
    from fsq_agent.adapters.control_plane import _server as _history

    artifact = tmp_path / "report.html"
    artifact.write_text("<!doctype html><p>evidence</p>", encoding="utf-8")
    monkeypatch.setattr(_history, "_resolve_history_file", lambda *args: SimpleNamespace(path=artifact, size=artifact.stat().st_size, mime_type="text/html"))
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, body, headers = server.handle_get("/api/control-plane/history/web/run-1/exports/export-1/files/html", {"workspace": ["demo"]})
    assert status == 200
    assert isinstance(body, _history._HistoryFile)
    assert headers["Content-Disposition"].startswith("attachment;")
    assert str(tmp_path) not in str(headers)


def test_history_report_and_export_survive_server_restart(tmp_path, monkeypatch):
    from fsq_agent.application import runs
    from fsq_agent.execution import RunLifecycleService, RunSource, allocate_run
    from fsq_agent.models import EvidenceBundle, RunnerStepResult

    monkeypatch.setattr(runs, "list_workspace_registry", lambda *args: [SimpleNamespace(name="demo", root_path=tmp_path)])
    monkeypatch.setattr(runs, "inspect_registered_workspace", lambda *args, **kwargs: SimpleNamespace(platforms=[SimpleNamespace(platform="web")]))
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="checkout", mode="strict", source=RunSource(kind="case", case_id="checkout"))
    directory = tmp_path / ".fsq" / "runs" / "web" / metadata.run_id
    lifecycle = RunLifecycleService()
    metadata = lifecycle.snapshot_sources(directory, metadata, sources={"case": b"name: checkout\n"})
    result = lifecycle.freeze(
        directory,
        metadata,
        bundle=EvidenceBundle(
            bundle_id="evidence",
            run_id=metadata.run_id,
            steps=[RunnerStepResult(step_id="assert-heading", status="failed", failure_category="assertion_error", error_message="Missing Checkout heading")],
        ),
    )
    lifecycle.finalize(directory, metadata, execution_result=result)
    before = (directory / "run.json").read_bytes()
    path = f"/api/control-plane/history/web/{metadata.run_id}"
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, detail, _ = server.handle_get(path, {"workspace": ["demo"]})
    assert status == 200
    assert detail["report"]["execution"]["outcome"] != "success"
    status, exported = server.handle_post(path + "/exports", {"workspaceName": "demo", "format": "json"})
    assert status == 201
    assert "output_path" not in exported
    assert str(tmp_path) not in str(exported)
    download = urlsplit(exported["files"][0]["download_url"])
    restarted = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, body, _ = restarted.handle_get(download.path, parse_qs(download.query))
    assert status == 200
    assert body.path.is_file()
    assert (directory / "run.json").read_bytes() == before


def test_history_export_rejects_host_paths(tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, error = server.handle_post("/api/control-plane/history/web/run-1/exports", {"workspaceName": "demo", "format": "html", "output_path": str(tmp_path / "unsafe.html")})
    assert status == 400
    assert error["code"] == "invalid_export"
    assert not (tmp_path / "unsafe.html").exists()


def test_root_action_maps_source_identity_without_nested_overwrite(tmp_path):
    from fsq_agent.adapters.control_plane._evidence import EvidenceProjection
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.models import RunnerStepResult

    state = ControlPlaneState()
    request = state.reserve(
        workspace_name="demo", platform="web", target_id="chrome", mode="strict", source={"casePath": "checkout.fsq.yaml", "caseSteps": [{"stepId": "source-1", "sourceStepId": "source-1"}]}
    )
    projection = EvidenceProjection(state, request, tmp_path)
    projection.project_step_result(RunnerStepResult(step_id="nested-execution", source_step_id="source-1", status="failed", metadata={"root_invocation": False}))
    assert "status" not in state.snapshot(request)["source"]["caseSteps"][0]
    projection.project_step_result(RunnerStepResult(step_id="root-execution", source_step_id="source-1", status="passed", metadata={"root_invocation": True}))
    step = state.snapshot(request)["source"]["caseSteps"][0]
    assert step["status"] == "passed"
    assert step["stepId"] == "root-execution"


def test_failed_pathless_captures_keep_identity_and_reason(tmp_path):
    import json

    from fsq_agent.adapters.control_plane._evidence import read_step_artifacts

    (tmp_path / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "artifact_id": "screen-failed",
                        "step_id": "exec-1",
                        "kind": "screenshot",
                        "path": None,
                        "phase": "finalize",
                        "capture_reason": "failure",
                        "availability": "failed",
                        "unavailable_reason": "capture_timeout",
                    },
                    {
                        "artifact_id": "tree-failed",
                        "step_id": "exec-1",
                        "kind": "ui_snapshot",
                        "path": None,
                        "phase": "finalize",
                        "capture_reason": "failure",
                        "availability": "failed",
                        "unavailable_reason": "tree_unavailable",
                    },
                ]
            }
        )
    )
    result = read_step_artifacts(tmp_path, "exec-1")
    assert result["available"] is True
    assert [item["artifactId"] for item in result["artifacts"]] == ["screen-failed", "tree-failed"]
    assert result["artifacts"][0]["availability"] == "failed"
    assert result["artifacts"][0]["unavailableReason"] == "capture_timeout"


def test_step_projection_preserves_skipped_invocation_and_attempt(tmp_path):
    from fsq_agent.adapters.control_plane._evidence import EvidenceProjection
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.models import RunnerStepResult

    state = ControlPlaneState()
    request = state.reserve(workspace_name="demo", platform="web", target_id="chrome", mode="strict", source={"caseSteps": [{"stepId": "authored", "sourceStepId": "authored"}]})
    projection = EvidenceProjection(state, request, tmp_path)
    result = RunnerStepResult(
        step_id="authored",
        source_step_id="authored",
        status="skipped",
        skip_reason="blocked",
        blocked_by_step="failed-execution",
        invocation_path=("root", "body", "2"),
        attempt_index=1,
        max_attempts=3,
        metadata={"root_invocation": True},
    )
    projection.project_step_result(result)
    row = state.snapshot(request)["source"]["caseSteps"][0]
    assert row["skipReason"] == "blocked"
    assert row["blockedByStep"] == "failed-execution"
    assert row["invocationPath"] == ["root", "body", "2"]
    assert row["attemptIndex"] == 1
    assert row["maxAttempts"] == 3


def test_live_action_outcome_keeps_capture_failure_separate(tmp_path):
    from fsq_agent.adapters.control_plane._evidence import EvidenceProjection
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.models import RunnerStepResult

    state = ControlPlaneState()
    request = state.reserve(workspace_name="demo", platform="web", target_id="chrome", mode="strict", source={"caseSteps": [{"stepId": "s1", "sourceStepId": "s1"}]})
    EvidenceProjection(state, request, tmp_path).project_step_result(
        RunnerStepResult(
            step_id="s1", source_step_id="s1", status="failed", action_status="passed", evidence_errors=[{"kind": "screenshot", "message": "capture failed"}], metadata={"root_invocation": True}
        )
    )
    row = state.snapshot(request)["source"]["caseSteps"][0]
    assert row["status"] == "passed"
    assert row["aggregateStatus"] == "failed"
    assert row["evidenceErrors"][0]["message"] == "capture failed"


def test_passed_attempt_clears_previous_failure_but_preserves_attempt_history(tmp_path):
    from fsq_agent.adapters.control_plane._evidence import EvidenceProjection
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.models import RunnerStepResult

    state = ControlPlaneState()
    request = state.reserve(workspace_name="demo", platform="web", target_id="chrome", mode="strict", source={"caseSteps": [{"stepId": "source", "sourceStepId": "source"}]})
    projection = EvidenceProjection(state, request, tmp_path)
    projection.project_step_result(
        RunnerStepResult(
            step_id="attempt-1",
            step_execution_id="attempt-1",
            source_step_id="source",
            status="failed",
            action_status="failed",
            failure_category="assertion_error",
            error_message="old failure",
            duration_ms=15,
            metadata={"root_invocation": True},
        )
    )
    projection.project_step_result(
        RunnerStepResult(
            step_id="attempt-2", step_execution_id="attempt-2", source_step_id="source", status="passed", action_status="passed", duration_ms=None, metadata={"root_invocation": True}, attempt_index=2
        )
    )
    row = state.snapshot(request)["source"]["caseSteps"][0]
    assert row["status"] == "passed"
    assert row["message"] is None
    assert row["failureCategory"] is None
    assert row["durationMs"] is None
    assert row["attempts"][0]["message"] == "old failure"


def test_history_errors_preserve_safe_action_and_reason(monkeypatch):
    from fsq_agent.adapters.control_plane import _server
    from fsq_agent.application import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode

    def fail(*args):
        raise ApplicationError(
            code=ApplicationErrorCode.RUN_REPORT_GENERATION_FAILED,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message="Run report operation failed.",
            action="Choose a Run with matching source.",
            details={"reason": "incompatible_source"},
        )

    monkeypatch.setattr(_server, "_get_history", fail)
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    status, payload, _ = server.handle_get("/api/control-plane/history/web/run", {"workspace": ["demo"]})
    assert status == 400
    assert payload["details"]["reason"] == "incompatible_source"
    assert payload["action"] == "Choose a Run with matching source."


def test_selected_capture_preserves_qualifiers_and_capture_identity(tmp_path):
    import json

    from fsq_agent.adapters.control_plane._evidence import read_step_artifacts

    (tmp_path / "tree.json").write_text('{"xml":"<node/>"}')
    (tmp_path / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "artifact_id": "tree-2",
                        "kind": "ui_snapshot",
                        "path": "tree.json",
                        "step_execution_id": "attempt-2",
                        "phase": "finalize",
                        "capture_occurrence": 3,
                        "capture_reason": "failure",
                        "metadata": {
                            "attempt_index": 2,
                            "redacted": True,
                            "transformed": True,
                            "display_transformed": True,
                            "coverage": {"status": "partial", "reason": "bounded_tree"},
                            "compaction": {"text_limit": 50},
                        },
                    }
                ]
            }
        )
    )
    capture = read_step_artifacts(tmp_path, "attempt-2")["artifacts"][0]
    assert capture["redacted"] is True
    assert capture["transformed"] is True
    assert capture["displayTransformed"] is True
    assert capture["coverage"] == {"status": "partial", "reason": "bounded_tree"}
    assert capture["compaction"] == {"text_limit": 50}
    assert capture["captureOccurrence"] == 3
    assert capture["stepExecutionId"] == "attempt-2"


def test_cp_safe_text_redacts_complete_authorization_and_cookie_values():
    from fsq_agent.adapters.control_plane._evidence import safe_text

    result = safe_text("Authorization: Bearer CANARY_SECRET\nCookie: a=FIRST_SECRET; b=SECOND_SECRET")
    assert "CANARY_SECRET" not in result
    assert "FIRST_SECRET" not in result
    assert "SECOND_SECRET" not in result


def test_explore_failure_before_first_event_preserves_allocated_identity(tmp_path, monkeypatch):
    import asyncio

    from fsq_agent.adapters.control_plane._execution import _run_explore
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.config import Settings
    from fsq_agent.execution import RunSource, allocate_run
    from fsq_agent.models import ToolExecutionError

    settings = Settings(harness={"platform": "web"})
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="goal", mode="explore", source=RunSource(kind="goal", goal_summary="g"))
    state = ControlPlaneState()
    request = state.reserve(workspace_name="demo", platform="web", target_id="chrome", mode="explore", source={"goal": "g"})

    async def fail(*args, **kwargs):
        raise ToolExecutionError("Run execution failed.", context={"run_id": metadata.run_id, "platform": "web", "exception_type": "OSError"})

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings", lambda *a, **kw: object())
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.DynamicExecutionService.execute", fail)
    asyncio.run(_run_explore(SimpleNamespace(settings=settings, request_id=request, goal="g", requires_web_search_evidence=False), state))
    assert state.snapshot(request)["runId"] == metadata.run_id
    assert state.snapshot(request)["status"] == "error"


def test_cp_bare_basic_and_bearer_credentials_are_redacted():
    from fsq_agent.adapters.control_plane._evidence import safe_text

    result = safe_text("Bearer BARE_TOKEN; Basic BASIC_TOKEN")
    assert "BARE_TOKEN" not in result
    assert "BASIC_TOKEN" not in result


def test_explore_processing_failure_keeps_frozen_outcome_and_report_action(tmp_path, monkeypatch):
    import asyncio

    from fsq_agent.adapters.control_plane._execution import _run_explore
    from fsq_agent.adapters.control_plane._state import ControlPlaneState
    from fsq_agent.config import Settings
    from fsq_agent.execution import RunLifecycleService, RunSource, allocate_run
    from fsq_agent.models import EvidenceBundle, ToolExecutionError, VerificationResult

    settings = Settings(harness={"platform": "web"})
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="goal", mode="explore", source=RunSource(kind="goal", goal_summary="g"))
    directory = settings.output.runs_dir / metadata.run_id
    lifecycle = RunLifecycleService()
    lifecycle.freeze(
        directory, metadata, bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete"), verification=VerificationResult(status="success", summary="Verified existing state")
    )
    state = ControlPlaneState()
    request = state.reserve(workspace_name="demo", platform="web", target_id="chrome", mode="explore", source={"goal": "g"})

    async def fail(*args, **kwargs):
        raise ToolExecutionError("Processing failed.", context={"run_id": metadata.run_id, "platform": "web"})

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings", lambda *a, **kw: object())
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.DynamicExecutionService.execute", fail)
    asyncio.run(_run_explore(SimpleNamespace(settings=settings, request_id=request, goal="g", requires_web_search_evidence=False), state))
    snapshot = state.snapshot(request)
    assert snapshot["runId"] == metadata.run_id
    assert snapshot["status"] == "success"
    assert snapshot["reportAvailable"] is True
