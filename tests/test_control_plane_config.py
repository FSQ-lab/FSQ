# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import time
from pathlib import Path
from threading import Event, Lock, Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fsq_agent.config import Settings, activate_github_copilot_provider
from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.models import ConfigurationError
from fsq_agent.providers import (
    GitHubCopilotAuthorization,
    GitHubCopilotModel,
    GitHubDeviceCode,
    ProviderConnectionTestResult,
)


def _server(tmp_path: Path, *, host: str = "127.0.0.1") -> ControlPlaneServer:
    return ControlPlaneServer(
        ControlPlaneServerOptions(
            host=host,
            static_path=tmp_path / "static",
            user_config_root=tmp_path / "user",
            open_browser=False,
        )
    )


def _device_code() -> GitHubDeviceCode:
    return GitHubDeviceCode(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=5,
    )


def _authorization() -> GitHubCopilotAuthorization:
    return GitHubCopilotAuthorization(
        github_access_token="github-secret",  # noqa: S106 - synthetic test credential.
        github_expires_at=time.time() + 3600,
        copilot_token="provider-secret",  # noqa: S106 - synthetic test credential.
        copilot_expires_at=time.time() + 1800,
        plan="individual",
    )


def test_config_get_initializes_unconfigured_state_with_no_store(tmp_path: Path) -> None:
    server = _server(tmp_path)

    status, payload, headers = server.handle_get("/api/control-plane/config", peer_host="127.0.0.1")

    assert status == 200
    assert payload == {"configured": False, "provider": None}
    assert headers["Cache-Control"] == "no-store"


def test_config_azure_put_and_get_return_complete_local_projection(tmp_path: Path) -> None:
    server = _server(tmp_path)

    status, saved = server.handle_put(
        "/api/control-plane/config/azure",
        {"baseUrl": "https://example.openai.azure.com", "modelName": "gpt-5.4", "apiKey": "complete-key"},
        peer_host="127.0.0.1",
    )
    read_status, loaded, _ = server.handle_get("/api/control-plane/config", peer_host="127.0.0.1")

    assert status == read_status == 200
    assert saved == loaded
    assert loaded == {
        "configured": True,
        "provider": {
            "type": "azure_openai",
            "modelName": "gpt-5.4",
            "baseUrl": "https://example.openai.azure.com/openai/v1/",
            "apiKey": "complete-key",
        },
    }


def test_config_github_projection_and_status_do_not_assume_authentication(tmp_path: Path) -> None:
    server = _server(tmp_path)
    activate_github_copilot_provider(
        model="gpt-5",
        github_token={"access_token": "oauth", "expires_at": 1},
        provider_token={"token": "provider", "expires_at": 1, "plan": "individual"},
        user_config_root=tmp_path / "user",
    )

    config_status, config, _ = server.handle_get("/api/control-plane/config", peer_host="127.0.0.1")
    status_status, provider_status, status_headers = server.handle_get("/api/control-plane/config/status", peer_host="127.0.0.1")

    assert config_status == status_status == 200
    assert config["provider"] == {
        "type": "github_copilot",
        "modelName": "gpt-5",
        "sessionStatus": "configured",
    }
    assert provider_status["configured"] is True
    assert provider_status["sessionStatus"] == "sign_in_required"
    assert provider_status["authenticated"] is False
    assert status_headers["Cache-Control"] == "no-store"


def test_saved_github_model_endpoints_validate_and_return_authoritative_config(tmp_path: Path) -> None:
    server = _server(tmp_path)
    activate_github_copilot_provider(
        model="gpt-5",
        github_token={"access_token": "oauth"},
        provider_token={"token": "provider", "expires_at": 9999999999, "plan": "individual"},
        user_config_root=tmp_path / "user",
    )
    offered = (
        GitHubCopilotModel(id="gpt-5", name="GPT 5"),
        GitHubCopilotModel(id="gpt-5.2", name="GPT 5.2"),
    )

    with (
        patch("fsq_agent.adapters.control_plane._config.list_saved_github_copilot_models", return_value=offered),
        patch("fsq_agent.application.providers._discover_saved_github_copilot_models", return_value=offered),
    ):
        list_status, listed = server.handle_post("/api/control-plane/config/github/models", {}, peer_host="127.0.0.1")
        invalid_status, invalid = server.handle_post("/api/control-plane/config/github/models", {"extra": True}, peer_host="127.0.0.1")
        save_status, saved = server.handle_put(
            "/api/control-plane/config/github/model",
            {"modelName": "gpt-5.2"},
            peer_host="127.0.0.1",
        )

    assert list_status == save_status == 200
    assert listed["modelName"] == "gpt-5"
    assert listed["sessionStatus"] == "session_available"
    assert listed["models"] == [{"id": "gpt-5", "name": "GPT 5"}, {"id": "gpt-5.2", "name": "GPT 5.2"}]
    assert invalid_status == 400
    assert invalid["code"] == "invalid_request"
    assert saved["provider"]["modelName"] == "gpt-5.2"
    assert saved["provider"]["sessionStatus"] == "configured"


def test_saved_github_models_maps_access_denied_without_requesting_sign_in(tmp_path: Path) -> None:
    from fsq_agent.application import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode

    server = _server(tmp_path)
    denied = ApplicationError(
        code=ApplicationErrorCode.PROVIDER_UNAVAILABLE,
        category=ApplicationErrorCategory.UNAVAILABLE,
        message="GitHub Copilot model access was denied.",
        action="Check the account or organization Copilot permissions, then retry.",
        details={"provider": "github_copilot", "reason": "access_denied"},
    )

    with patch("fsq_agent.adapters.control_plane._config.list_saved_github_copilot_models", side_effect=denied):
        status, error = server.handle_post("/api/control-plane/config/github/models", {}, peer_host="127.0.0.1")

    assert status == 403
    assert error["code"] == "provider_access_denied"
    assert "sign in" not in str(error).casefold()


def test_config_rejects_nonloopback_bind_peer_and_cross_origin_write(tmp_path: Path) -> None:
    nonloopback_server = _server(tmp_path / "bind", host="0.0.0.0")  # noqa: S104 - validates the rejection gate without binding.
    bind_status, bind_error, _ = nonloopback_server.handle_get("/api/control-plane/config", peer_host="127.0.0.1")
    server = _server(tmp_path / "peer")
    peer_status, peer_error, _ = server.handle_get("/api/control-plane/config", peer_host="192.0.2.10")
    origin_status, origin_error = server.handle_put(
        "/api/control-plane/config/azure",
        {"baseUrl": "https://example.openai.azure.com", "modelName": "model", "apiKey": "key"},
        peer_host="127.0.0.1",
        origin="https://evil.example",
        host="127.0.0.1:8879",
    )

    assert bind_status == peer_status == origin_status == 403
    assert bind_error["code"] == peer_error["code"] == "config_unavailable"
    assert origin_error["code"] == "cross_origin_forbidden"
    assert "key" not in str(origin_error)


def test_device_flow_is_independent_from_run_state_and_cancels_idempotently(tmp_path: Path) -> None:
    server = _server(tmp_path)
    server.state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "active"})
    worker_started = Event()

    def complete(_device_code, *, cancel_requested):
        worker_started.set()
        while not cancel_requested():
            worker_started.wait(0.01)
        raise ConfigurationError("GitHub device-code authentication was cancelled.")

    with (
        patch("fsq_agent.adapters.control_plane._provider_auth.request_github_copilot_device_code", return_value=_device_code()),
        patch("fsq_agent.adapters.control_plane._provider_auth.complete_github_copilot_device_flow", side_effect=complete),
    ):
        status, started = server.handle_post(
            "/api/control-plane/config/github/device-flow",
            {},
            peer_host="127.0.0.1",
        )
        worker_started.wait(1)
        busy_status, busy = server.handle_post(
            "/api/control-plane/config/github/device-flow",
            {},
            peer_host="127.0.0.1",
        )
        cancel_status, cancelled = server.handle_delete(
            f"/api/control-plane/config/github/device-flow/{started['authRequestId']}",
            peer_host="127.0.0.1",
        )
        repeat_status, repeated = server.handle_delete(
            f"/api/control-plane/config/github/device-flow/{started['authRequestId']}",
            peer_host="127.0.0.1",
        )

    assert status == 202
    assert started["status"] == "waiting"
    assert busy_status == 409
    assert busy["code"] == "device_flow_busy"
    assert cancel_status == repeat_status == 200
    assert cancelled["status"] == repeated["status"] == "cancelled"
    server.stop()


def test_device_flow_start_rejects_legacy_model_field(tmp_path: Path) -> None:
    server = _server(tmp_path)

    status, payload = server.handle_post(
        "/api/control-plane/config/github/device-flow",
        {"modelName": "gpt-5"},
        peer_host="127.0.0.1",
    )

    assert status == 400
    assert payload["code"] == "invalid_request"


def test_device_flow_discovers_models_and_saves_only_an_offered_selection(tmp_path: Path) -> None:
    server = _server(tmp_path)

    def activate(authorization, *, model, user_config_root):
        assert authorization.github_access_token == "github-secret"  # noqa: S105 - synthetic test credential.
        assert authorization.copilot_token == "provider-secret"  # noqa: S105 - synthetic test credential.
        assert authorization.plan == "individual"
        return activate_github_copilot_provider(
            model=model,
            github_token={"access_token": authorization.github_access_token},
            provider_token={"token": authorization.copilot_token, "plan": authorization.plan},
            user_config_root=user_config_root,
        )

    with (
        patch("fsq_agent.adapters.control_plane._provider_auth.request_github_copilot_device_code", return_value=_device_code()),
        patch("fsq_agent.adapters.control_plane._provider_auth.complete_github_copilot_device_flow", return_value=_authorization()),
        patch(
            "fsq_agent.adapters.control_plane._provider_auth.list_github_copilot_models",
            return_value=(GitHubCopilotModel(id="gpt-5", name="GPT 5"), GitHubCopilotModel(id="gpt-5.1", name="GPT 5.1")),
        ),
        patch("fsq_agent.adapters.control_plane._provider_auth.activate_github_copilot_authorization", side_effect=activate),
    ):
        status, started = server.handle_post(
            "/api/control-plane/config/github/device-flow",
            {},
            peer_host="127.0.0.1",
        )
        deadline = time.monotonic() + 1
        auth_payload = None
        while time.monotonic() < deadline:
            _, auth_payload, _ = server.handle_get(
                f"/api/control-plane/config/github/device-flow/{started['authRequestId']}",
                peer_host="127.0.0.1",
            )
            if auth_payload["status"] == "ready":
                break
            time.sleep(0.01)
        _, config_before, _ = server.handle_get("/api/control-plane/config", peer_host="127.0.0.1")
        rejected_status, rejected = server.handle_put(
            f"/api/control-plane/config/github/device-flow/{started['authRequestId']}",
            {"modelName": "gpt-6"},
            peer_host="127.0.0.1",
        )
        save_status, saved = server.handle_put(
            f"/api/control-plane/config/github/device-flow/{started['authRequestId']}",
            {"modelName": "gpt-5.1"},
            peer_host="127.0.0.1",
        )

    assert status == 202
    assert auth_payload is not None
    assert auth_payload["status"] == "ready"
    assert auth_payload["models"] == [{"id": "gpt-5", "name": "GPT 5"}, {"id": "gpt-5.1", "name": "GPT 5.1"}]
    assert "secret" not in str(auth_payload)
    assert config_before == {"configured": False, "provider": None}
    assert rejected_status == 400
    assert rejected["code"] == "model_not_offered"
    assert save_status == 200
    assert saved["provider"] == {"type": "github_copilot", "modelName": "gpt-5.1", "sessionStatus": "configured"}


def test_device_flow_save_failure_preserves_retryable_ready_state(tmp_path: Path) -> None:
    server = _server(tmp_path)
    models = (GitHubCopilotModel(id="gpt-5", name="GPT 5"),)
    with (
        patch("fsq_agent.adapters.control_plane._provider_auth.request_github_copilot_device_code", return_value=_device_code()),
        patch("fsq_agent.adapters.control_plane._provider_auth.complete_github_copilot_device_flow", return_value=_authorization()),
        patch("fsq_agent.adapters.control_plane._provider_auth.list_github_copilot_models", return_value=models),
        patch(
            "fsq_agent.adapters.control_plane._provider_auth.activate_github_copilot_authorization",
            side_effect=ConfigurationError("GitHub Provider activation failed."),
        ),
    ):
        _, started = server.handle_post("/api/control-plane/config/github/device-flow", {}, peer_host="127.0.0.1")
        deadline = time.monotonic() + 1
        payload = None
        while time.monotonic() < deadline:
            _, payload, _ = server.handle_get(f"/api/control-plane/config/github/device-flow/{started['authRequestId']}", peer_host="127.0.0.1")
            if payload["status"] == "ready":
                break
            time.sleep(0.01)
        save_status, _ = server.handle_put(
            f"/api/control-plane/config/github/device-flow/{started['authRequestId']}",
            {"modelName": "gpt-5"},
            peer_host="127.0.0.1",
        )
        _, after, _ = server.handle_get(f"/api/control-plane/config/github/device-flow/{started['authRequestId']}", peer_host="127.0.0.1")

    assert save_status == 400
    assert after["status"] == "ready"
    assert after["models"] == [{"id": "gpt-5", "name": "GPT 5"}]


def test_pending_authorization_expires_after_ten_minutes_and_releases_busy_slot(tmp_path: Path) -> None:
    server = _server(tmp_path)
    clock = [1_000.0]
    with (
        patch("fsq_agent.adapters.control_plane._provider_auth.time.time", side_effect=lambda: clock[0]),
        patch("fsq_agent.adapters.control_plane._provider_auth.request_github_copilot_device_code", return_value=_device_code()),
        patch("fsq_agent.adapters.control_plane._provider_auth.complete_github_copilot_device_flow", return_value=_authorization()),
        patch("fsq_agent.adapters.control_plane._provider_auth.list_github_copilot_models", return_value=()),
    ):
        _, started = server.handle_post("/api/control-plane/config/github/device-flow", {}, peer_host="127.0.0.1")
        deadline = time.monotonic() + 1
        payload = None
        while time.monotonic() < deadline:
            _, payload, _ = server.handle_get(f"/api/control-plane/config/github/device-flow/{started['authRequestId']}", peer_host="127.0.0.1")
            if payload["status"] == "ready":
                break
            time.sleep(0.01)
        clock[0] += 601
        _, expired, _ = server.handle_get(f"/api/control-plane/config/github/device-flow/{started['authRequestId']}", peer_host="127.0.0.1")
        restart_status, restarted = server.handle_post("/api/control-plane/config/github/device-flow", {}, peer_host="127.0.0.1")

    assert expired["status"] == "expired"
    assert restart_status == 202
    assert restarted["status"] in {"waiting", "loading_models", "ready"}
    server.stop()


def test_model_discovery_failure_retries_without_reauthorizing(tmp_path: Path) -> None:
    server = _server(tmp_path)
    with (
        patch("fsq_agent.adapters.control_plane._provider_auth.request_github_copilot_device_code", return_value=_device_code()) as request_code,
        patch("fsq_agent.adapters.control_plane._provider_auth.complete_github_copilot_device_flow", return_value=_authorization()) as complete,
        patch(
            "fsq_agent.adapters.control_plane._provider_auth.list_github_copilot_models",
            side_effect=[ConfigurationError("GitHub Copilot model discovery failed."), (GitHubCopilotModel(id="gpt-5", name="GPT 5"),)],
        ),
    ):
        _, started = server.handle_post("/api/control-plane/config/github/device-flow", {}, peer_host="127.0.0.1")
        deadline = time.monotonic() + 1
        payload = None
        while time.monotonic() < deadline:
            _, payload, _ = server.handle_get(f"/api/control-plane/config/github/device-flow/{started['authRequestId']}", peer_host="127.0.0.1")
            if payload["status"] == "model_error":
                break
            time.sleep(0.01)
        retry_status, _ = server.handle_post(f"/api/control-plane/config/github/device-flow/{started['authRequestId']}/models", {}, peer_host="127.0.0.1")
        while time.monotonic() < deadline:
            _, payload, _ = server.handle_get(f"/api/control-plane/config/github/device-flow/{started['authRequestId']}", peer_host="127.0.0.1")
            if payload["status"] == "ready":
                break
            time.sleep(0.01)

    assert retry_status == 202
    assert payload is not None
    assert payload["status"] == "ready"
    request_code.assert_called_once()
    complete.assert_called_once()


def test_connection_endpoint_accepts_no_fields_and_returns_saved_result(tmp_path: Path) -> None:
    server = _server(tmp_path)
    result = ProviderConnectionTestResult(provider="azure_openai", model="saved-model", duration_seconds=0.125)

    with patch("fsq_agent.adapters.control_plane._config.test_model_provider_connection", return_value=result) as test_connection:
        status, payload = server.handle_post(
            "/api/control-plane/config/test-connection",
            {},
            peer_host="127.0.0.1",
        )
        invalid_status, invalid = server.handle_post(
            "/api/control-plane/config/test-connection",
            {"apiKey": "draft-key"},
            peer_host="127.0.0.1",
        )

    assert status == 200
    assert payload == {"success": True, "provider": "azure_openai", "modelName": "saved-model", "durationMs": 125}
    assert invalid_status == 400
    assert invalid["code"] == "invalid_request"
    test_connection.assert_called_once_with(user_config_root=tmp_path / "user")


def test_control_plane_run_start_loads_latest_provider_from_configured_user_root(tmp_path: Path, monkeypatch) -> None:
    server = _server(tmp_path)
    settings = Settings(harness={"platform": "web"})
    captured: dict[str, object] = {}

    def load(workspace_name, platform, user_config_root=None):
        captured.update(workspace_name=workspace_name, platform=platform, user_config_root=user_config_root)
        return settings

    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", load)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.prepare_run", lambda **kwargs: kwargs)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.start_execution", lambda prepared, state: object())

    status, _ = server.handle_post(
        "/api/control-plane/runs",
        {"mode": "explore", "workspaceName": "checkout", "platform": "web", "targetId": "chrome", "goal": "Do it"},
    )

    assert status == 202
    assert captured == {
        "workspace_name": "checkout",
        "platform": "web",
        "user_config_root": tmp_path / "user",
    }


def test_actual_http_config_transport_supports_put_and_rejects_cross_origin(tmp_path: Path) -> None:
    static = tmp_path / "static" / "control-plane"
    static.mkdir(parents=True)
    (static / "index.html").write_text("control plane", encoding="utf-8")
    server = _server(tmp_path)
    server.options = ControlPlaneServerOptions(
        host="127.0.0.1",
        port=0,
        static_path=tmp_path / "static",
        user_config_root=tmp_path / "user",
        open_browser=False,
    )
    server.start()
    payload = b'{"baseUrl":"https://example.openai.azure.com","modelName":"model","apiKey":"local-key"}'
    try:
        request = Request(  # noqa: S310 - URL is the in-process loopback server.
            f"{server.url}/api/control-plane/config/azure",
            method="PUT",
            data=payload,
            headers={"Content-Type": "application/json", "Origin": server.url},
        )
        with urlopen(request, timeout=5) as response:  # noqa: S310 - in-process loopback server.
            saved = response.read()
            cache_control = response.headers["Cache-Control"]
        with urlopen(f"{server.url}/api/control-plane/config", timeout=5) as response:  # noqa: S310 - in-process loopback server.
            loaded = response.read()
        cross_origin = Request(  # noqa: S310 - URL is the in-process loopback server.
            f"{server.url}/api/control-plane/config/azure",
            method="PUT",
            data=payload,
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        )
        try:
            urlopen(cross_origin, timeout=5)  # noqa: S310 - in-process loopback server.
        except HTTPError as exc:
            cross_origin_status = exc.code
            cross_origin_body = exc.read()
    finally:
        server.stop()

    assert saved == loaded
    assert b"local-key" in loaded
    assert cache_control == "no-store"
    assert cross_origin_status == 403
    assert b"cross_origin_forbidden" in cross_origin_body
    assert b"local-key" not in cross_origin_body


def test_concurrent_device_flow_starts_reserve_before_requesting_code(tmp_path: Path) -> None:
    server = _server(tmp_path)
    request_entered = Event()
    release_request = Event()
    count_lock = Lock()
    request_count = 0
    results: list[tuple[int, dict[str, object]]] = []

    def request_code() -> GitHubDeviceCode:
        nonlocal request_count
        with count_lock:
            request_count += 1
        request_entered.set()
        release_request.wait(1)
        return _device_code()

    def start_flow() -> None:
        results.append(
            server.handle_post(
                "/api/control-plane/config/github/device-flow",
                {},
                peer_host="127.0.0.1",
            )
        )

    with (
        patch("fsq_agent.adapters.control_plane._provider_auth.request_github_copilot_device_code", side_effect=request_code),
        patch("fsq_agent.adapters.control_plane._provider_auth.complete_github_copilot_device_flow", side_effect=ConfigurationError("cancelled")),
    ):
        first = Thread(target=start_flow)
        second = Thread(target=start_flow)
        first.start()
        request_entered.wait(1)
        second.start()
        second.join(timeout=0.1)
        release_request.set()
        first.join(timeout=1)
        second.join(timeout=1)

    assert request_count == 1
    assert sorted(status for status, _ in results) == [202, 409]


def test_server_stop_cancels_active_device_flow_worker(tmp_path: Path) -> None:
    server = _server(tmp_path)
    worker_started = Event()
    worker_stopped = Event()

    def complete(_device_code, *, cancel_requested):
        worker_started.set()
        while not cancel_requested():
            worker_stopped.wait(0.01)
        worker_stopped.set()
        raise ConfigurationError("cancelled")

    with (
        patch("fsq_agent.adapters.control_plane._provider_auth.request_github_copilot_device_code", return_value=_device_code()),
        patch("fsq_agent.adapters.control_plane._provider_auth.complete_github_copilot_device_flow", side_effect=complete),
    ):
        server.handle_post(
            "/api/control-plane/config/github/device-flow",
            {},
            peer_host="127.0.0.1",
        )
        worker_started.wait(1)
        server.stop()

    assert worker_stopped.is_set()
