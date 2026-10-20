# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import threading
import time
from types import SimpleNamespace

import pytest

from fsq_agent.adapters.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.application import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode

PREFIX = "/api/control-plane"


def wait_terminal(server, request_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = server.state.snapshot(request_id)
        if snapshot["terminal"]:
            return snapshot
        time.sleep(0.01)
    pytest.fail("Task did not finish.")


def test_preparing_reservation_recovery_provenance_and_cancellation(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))

    def prepare(request, *, check_cancelled):
        entered.set()
        assert release.wait(3)
        check_cancelled()
        pytest.fail("Cancelled preparation continued.")

    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_quick_test", prepare)
    body = {"platform": "android", "targetId": "device", "goal": "Calculator: 1+1=2"}
    status, accepted = server.handle_post(f"{PREFIX}/quick-test/runs", body)
    assert status == 202
    request_id = accepted["requestId"]
    try:
        assert entered.wait(2)
        snapshot = server.state.snapshot(request_id)
        assert snapshot["workflow"] == "quick-test"
        assert snapshot["workspaceName"] is None
        assert snapshot["application"] is None
        assert snapshot["runId"] is None
        assert snapshot["status"] == "preparing"
        assert server.state.bootstrap()["activeTask"]["requestId"] == request_id
        assert server.handle_post(f"{PREFIX}/quick-test/runs", body)[0] == 409
        assert server.handle_post(f"{PREFIX}/runs", {"workspaceName": "normal", "platform": "android", "targetId": "device", "mode": "explore", "goal": "goal"})[0] == 409
        server.handle_post(f"{PREFIX}/runs/{request_id}/cancel", {})
    finally:
        release.set()
    final = wait_terminal(server, request_id)
    assert final["status"] == "cancelled"
    assert final["runId"] is None
    assert not server.state.bootstrap()["busy"]


def test_preparation_error_retains_safe_facts_without_run(monkeypatch, tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))

    def prepare(*args, **kwargs):
        raise ApplicationError(
            code=ApplicationErrorCode.QUICK_TEST_INSUFFICIENT, category=ApplicationErrorCategory.REQUEST_VALIDATION, message="Insufficient test intent.", action="State an action and expected outcome."
        )

    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_quick_test", prepare)
    status, accepted = server.handle_post(f"{PREFIX}/quick-test/runs", {"platform": "macos", "goal": "Calculator"})
    assert status == 202
    snapshot = wait_terminal(server, accepted["requestId"])
    assert snapshot["status"] == "error"
    assert snapshot["error"]["code"] == "quick_test.insufficient"
    assert snapshot["runId"] is None
    assert not snapshot["reportAvailable"]


def test_case_manager_in_dedicated_workspace_is_not_quick_test(tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    request_id = server.state.reserve(workspace_name="quick-test", platform="android", target_id="device", mode="explore", source={"goal": "goal"})
    assert server.state.snapshot(request_id)["workflow"] == "case-manager"


@pytest.mark.parametrize(
    "body",
    [
        {"platform": "macos", "goal": "Test", "targetId": "override"},
        {"platform": "android", "goal": "Test"},
        {"platform": "web", "goal": "Test", "targetId": "unknown"},
        {"platform": "macos", "goal": "Test", "workspaceName": "override"},
        {"platform": "macos", "goal": " "},
        {"platform": "macos", "goal": "a" * 16001},
        {"platform": "web", "goal": "Search FSQ", "targetId": "chrome", "requires_web_search_evidence": False},
        {"platform": "web", "goal": "Search FSQ", "targetId": "chrome", "requiresWebSearchEvidence": True},
        {"platform": "web", "goal": "Verify heading", "targetId": "chrome", "url": "https://example.test"},
    ],
)
def test_invalid_transport_fields_never_reserve(body, tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    assert server.handle_post(f"{PREFIX}/quick-test/runs", body)[0] == 400
    assert not server.state.bootstrap()["busy"]


def test_quick_test_gates_peer_bind_and_origin(tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    assert server.handle_get(f"{PREFIX}/quick-test/context", {"platform": ["web"]}, peer_host="192.0.2.1")[0] == 403
    assert server.handle_post(f"{PREFIX}/quick-test/runs", {"platform": "macos", "goal": "Test"}, origin="https://evil.test", host="127.0.0.1:8879")[0] == 403
    public = ControlPlaneServer(ControlPlaneServerOptions(host="0.0.0.0", user_config_root=tmp_path))  # noqa: S104 -- verify rejection of public binds.
    assert public.handle_get(f"{PREFIX}/quick-test/context", {"platform": ["web"]})[0] == 403


def test_reused_quick_task_routes_retain_trusted_local_gates(tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    request_id = server.state.reserve(workspace_name=None, platform="macos", target_id=None, mode="explore", source={}, workflow="quick-test")
    assert server.handle_get(f"{PREFIX}/runs/{request_id}", peer_host="192.0.2.1")[0] == 403
    assert server.handle_post(f"{PREFIX}/runs/{request_id}/cancel", {}, origin="https://evil.test", host="127.0.0.1:8879")[0] == 403
    assert not server.state.snapshot(request_id)["cancelRequested"]


def test_success_bridges_frozen_settings_to_existing_explore_without_reload(monkeypatch, tmp_path):
    from fsq_agent.adapters.control_plane._execution import ExecutionHandle
    from fsq_agent.application import QuickTestPrepareResult
    from fsq_agent.config import Settings
    from fsq_agent.models import AndroidWorkspaceTarget

    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    settings = Settings()
    settings.harness.android.app_id = "com.validated"
    settings.harness.android.serial = "selected"
    prepared = QuickTestPrepareResult(
        workspace_name="quick-test",
        root_path=tmp_path / "quick-test",
        platform_revision="validated-revision",
        target_id="selected",
        target=AndroidWorkspaceTarget(app_id="com.validated"),
        application="Calculator",
        settings=settings,
    )
    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_quick_test", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(server, "_load_settings", lambda *args: pytest.fail("Must not reread workspace and retarget"))
    seen = []

    def preflight(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(request_id=kwargs["request_id"])

    def execute(preflight, state):
        state.bind_run(preflight.request_id, "run-1", tmp_path / "run-1")
        state.finish(preflight.request_id, status="success", summary="Fixture completed.", report_available=True)
        return ExecutionHandle(request_id=preflight.request_id)

    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_run", preflight)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.start_execution", execute)
    status, accepted = server.handle_post(f"{PREFIX}/quick-test/runs", {"platform": "android", "targetId": "selected", "goal": "Calculator: 1+1=2"})
    assert status == 202
    snapshot = wait_terminal(server, accepted["requestId"])
    assert snapshot["workspaceName"] == "quick-test"
    assert snapshot["application"] == "Calculator"
    assert snapshot["runId"] == "run-1"
    assert snapshot["platformRevision"] == "validated-revision"
    assert snapshot["reportAvailable"]
    assert seen[0]["settings"] is prepared.settings
    assert seen[0]["settings"].harness.android.serial == "selected"
    assert seen[0]["frozen_revision"] == "validated-revision"
    assert seen[0]["body"]["mode"] == "explore"


@pytest.mark.parametrize(
    "address",
    [
        "https://example.test/docs?access_token=URL_PRIVATE",
        "https://example.test/docs?access%5Ftoken=URL_PRIVATE",
        "https://example.test/docs#access_token=URL_PRIVATE",
        "https://example.test/docs?q=FROZEN_PRIVATE",
        "https://example.test/docs?q=%46%52%4F%5A%45%4E%5F%50%52%49%56%41%54%45",
        "https://example.test/docs?q=public#section",
    ],
)
def test_application_presentation_is_safe_across_http_projections(monkeypatch, tmp_path, address):
    from http.client import HTTPConnection

    from fsq_agent.adapters.control_plane._execution import ExecutionHandle
    from fsq_agent.application import QuickTestPrepareResult
    from fsq_agent.config import Settings
    from fsq_agent.models import WebWorkspaceTarget

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("fixture")
    server = ControlPlaneServer(ControlPlaneServerOptions(port=0, static_path=static, user_config_root=tmp_path / "user"))
    mutable = Settings(harness={"platform": "web"})
    mutable.harness.web.base_url = address
    mutable.runtime_secrets.set_values({"FIXTURE_SECRET": "FROZEN_PRIVATE"})
    frozen = mutable.model_copy(deep=True)
    result = QuickTestPrepareResult(
        workspace_name="quick-test",
        root_path=tmp_path / "quick-test",
        platform_revision="frozen-revision",
        target_id="chrome",
        target=WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=tmp_path / "fixture-browser"),
        application=address,
        settings=frozen,
    )
    mutable.runtime_secrets.set_values({"FIXTURE_SECRET": "LATER_PRIVATE"})
    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_quick_test", lambda *args, **kwargs: result)
    monkeypatch.setattr(server, "_load_settings", lambda *args: pytest.fail("Projection must not reread mutable settings"))
    execution = []
    entered = threading.Event()

    def preflight(**kwargs):
        assert kwargs["settings"] is frozen
        return SimpleNamespace(request_id=kwargs["request_id"], settings=kwargs["settings"].model_copy(deep=True))

    def execute(prepared, state):
        execution.append(prepared)
        state.transition(prepared.request_id, "running", summary="Fixture running.")
        entered.set()
        return ExecutionHandle(request_id=prepared.request_id)

    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_run", preflight)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.start_execution", execute)

    def request_json(method, path, body=None):
        connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == (202 if method == "POST" else 200)
            return json.loads(response.read())
        finally:
            connection.close()

    def stream_snapshot(response):
        lines = [response.readline().decode() for _ in range(4)]
        assert lines[1] == "event: snapshot\n"
        assert lines[2].startswith("data: ")
        return json.loads(lines[2][6:])

    request_id = None
    stream = None
    connection = None
    server.start()
    try:
        accepted = request_json("POST", f"{PREFIX}/quick-test/runs", {"platform": "web", "targetId": "chrome", "goal": "Verify the fixture heading."})
        request_id = accepted["requestId"]
        assert entered.wait(3)
        snapshot = request_json("GET", f"{PREFIX}/runs/{request_id}")
        bootstrap = request_json("GET", f"{PREFIX}/bootstrap")
        assert bootstrap["activeTask"]["requestId"] == request_id
        connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
        connection.request("GET", f"{PREFIX}/runs/{request_id}/stream")
        stream = connection.getresponse()
        assert stream.status == 200
        assert stream.getheader("Content-Type").startswith("text/event-stream")
        active_stream = stream_snapshot(stream)
        server.state.finish(request_id, status="success", summary="Fixture completed.")
        terminal_stream = stream_snapshot(stream)
        terminal = request_json("GET", f"{PREFIX}/runs/{request_id}")
        assert not active_stream["terminal"]
        assert terminal_stream["terminal"]
        assert terminal["terminal"]
        public_values = [snapshot, bootstrap, active_stream, terminal_stream, terminal]
        for public in public_values:
            serialized = json.dumps(public)
            assert "URL_PRIVATE" not in serialized
            assert "FROZEN_PRIVATE" not in serialized
            assert "%46%52%4F%5A%45%4E%5F%50%52%49%56%41%54%45" not in serialized
        labels = [snapshot["application"], bootstrap["activeTask"]["application"], active_stream["application"], terminal_stream["application"], terminal["application"]]
        assert all(label == labels[0] for label in labels)
        if address.endswith("?q=public#section"):
            assert labels[0] == address
        else:
            assert "redacted" in labels[0].casefold()
        assert result.application == address
        assert frozen.harness.web.base_url == address
        assert frozen.runtime_secrets.private_values() == {"FIXTURE_SECRET": "FROZEN_PRIVATE"}
        assert execution[0].settings.harness.web.base_url == address
        assert execution[0].settings.runtime_secrets.private_values() == frozen.runtime_secrets.private_values()
        assert execution[0].goal == "Verify the fixture heading."
    finally:
        if request_id is not None:
            server.state.finish(request_id, status="cancelled", summary="Fixture cleanup.")
        if stream is not None:
            stream.close()
        if connection is not None:
            connection.close()
        server.stop()


def test_context_schema_and_unknown_query_rejection(monkeypatch, tmp_path):
    from fsq_agent.application import QuickTestCheck, QuickTestContextResult

    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._quick_test.get_quick_test_context",
        lambda request: QuickTestContextResult(
            platform=request.platform, target_id=None, can_start=True, choices=(), checks=(QuickTestCheck(id="application", status="pending", message="Awaiting identification."),)
        ),
    )
    status, payload, headers = server.handle_get(f"{PREFIX}/quick-test/context", {"platform": ["macos"]})
    assert status == 200
    assert set(payload) == {"platform", "targetId", "canStart", "choices", "checks", "diagnostics"}
    assert payload["checks"] == [{"id": "application", "status": "pending", "message": "Awaiting identification.", "action": ""}]
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert server.handle_get(f"{PREFIX}/quick-test/context", {"platform": ["macos"], "workspace": ["override"]})[0] == 400


def test_legacy_reservation_blocks_quick_test_before_inference(monkeypatch, tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    server.state.reserve(workspace_name="normal", platform="macos", target_id="macos-app", mode="strict", source={"casePath": "case.fsq.yaml"})
    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_quick_test", lambda *args, **kwargs: pytest.fail("Busy must prevent inference"))
    assert server.handle_post(f"{PREFIX}/quick-test/runs", {"platform": "macos", "goal": "Calculator: 1+1=2"})[0] == 409


def test_quick_body_byte_bound_is_enforced_before_reading_or_reserving(tmp_path):
    from http.client import HTTPConnection

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("fixture")
    server = ControlPlaneServer(ControlPlaneServerOptions(port=0, static_path=static, user_config_root=tmp_path / "user"))
    server.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
        connection.request("POST", f"{PREFIX}/quick-test/runs", body=b"", headers={"Content-Length": str(128 * 1024 + 1)})
        response = connection.getresponse()
        assert response.status == 413
        response.read()
        connection.close()
        assert not server.state.bootstrap()["busy"]
    finally:
        server.stop()


def test_shutdown_cancels_preparation_and_new_service_does_not_resume(monkeypatch, tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    entered, release = threading.Event(), threading.Event()

    def prepare(request, *, check_cancelled):
        entered.set()
        assert release.wait(3)
        check_cancelled()
        pytest.fail("Shutdown preparation proceeded to execution.")

    monkeypatch.setattr("fsq_agent.adapters.control_plane._quick_test.prepare_quick_test", prepare)
    status, accepted = server.handle_post(f"{PREFIX}/quick-test/runs", {"platform": "macos", "goal": "Calculator: 1+1=2"})
    assert status == 202
    try:
        assert entered.wait(2)
        server.stop()
    finally:
        release.set()
    assert wait_terminal(server, accepted["requestId"])["status"] == "cancelled"
    restarted = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    assert restarted.state.bootstrap()["activeTask"] is None
    assert not restarted.state.bootstrap()["busy"]


def test_quick_task_gate_does_not_change_legacy_unknown_endpoint_errors(tmp_path):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    assert server.handle_get(f"{PREFIX}/runs/missing/unknown")[1]["code"] == "not_found"
    assert server.handle_post(f"{PREFIX}/runs/missing/unknown", {})[1]["code"] == "not_found"
