# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import time
from datetime import UTC, datetime
from email.utils import format_datetime
from functools import partial
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from fsq_agent.ai_services import build_ai_assertion_evaluator
from fsq_agent.config import Settings, activate_github_copilot_provider
from fsq_agent.models import AgentRuntimeSettings, ConfigurationError
from fsq_agent.providers import _github_copilot as copilot
from fsq_agent.providers import (
    build_model_provider_session,
    prepare_model_provider_session,
    refresh_model_provider_session,
)


def _fake_copilot_token(prefix: str = "copilot") -> str:
    return f"{prefix}-token"


def _fake_github_token(suffix: str) -> str:
    return f"ghu_{suffix}"


def _authorization() -> copilot.GitHubCopilotAuthorization:
    return copilot.GitHubCopilotAuthorization(
        github_access_token=_fake_github_token("pending"),
        github_expires_at=time.time() + 3600,
        copilot_token=_fake_copilot_token("pending"),
        copilot_expires_at=time.time() + 1800,
        plan="business",
    )


def _github_settings(
    tmp_path,
    *,
    github_token: dict[str, object] | None = None,
    provider_token: dict[str, object] | None = None,
) -> Settings:
    settings = Settings(agent_runtime=AgentRuntimeSettings(provider="github_copilot"))
    settings.agent_runtime.model = "gpt-5.5"
    settings.agent_runtime.github_token = github_token
    settings.agent_runtime.provider_token = provider_token
    settings.agent_runtime.user_config_root = tmp_path
    return settings


class _AsyncOpenAI:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_build_github_copilot_client_config_uses_plan_endpoint(tmp_path) -> None:
    settings = _github_settings(
        tmp_path,
        provider_token={
            "token": _fake_copilot_token(),
            "expires_at": time.time() + 3600,
            "plan": "business",
        },
    )
    with patch.object(copilot, "_get_copilot_plan") as get_plan, patch.object(copilot, "_get_copilot_token") as get_token:
        config = copilot.build_github_copilot_client_config(settings)

    get_plan.assert_not_called()
    get_token.assert_not_called()
    assert config.api_key == _fake_copilot_token()
    assert config.base_url == "https://api.business.githubcopilot.com"
    assert config.default_headers["copilot-integration-id"] == "vscode-chat"
    assert config.model == "gpt-5.5"


def test_prepare_model_provider_session_exchanges_and_caches_copilot_provider_token(tmp_path) -> None:
    provider_cache_path = tmp_path / "auth" / "github-copilot-provider-token.json"
    settings = _github_settings(
        tmp_path,
        github_token={"access_token": _fake_github_token("test"), "expires_at": time.time() + 3600},
    )

    with (
        patch.object(copilot, "_get_copilot_plan", return_value="business") as get_plan,
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token(), expires_at=time.time() + 3600),
        ) as get_token,
    ):
        session = prepare_model_provider_session(settings)

    get_plan.assert_called_once_with(_fake_github_token("test"))
    get_token.assert_called_once_with(_fake_github_token("test"))
    assert session.client_config.api_key == _fake_copilot_token()
    data = json.loads(provider_cache_path.read_text(encoding="utf-8"))
    assert data["token"] == _fake_copilot_token()
    assert data["plan"] == "business"


def test_runtime_copilot_provider_session_requires_cached_provider_or_github_token(tmp_path) -> None:
    settings = _github_settings(tmp_path)

    with (
        patch.object(copilot, "request_github_copilot_device_code") as request_device_code,
        patch.object(copilot, "_get_copilot_token") as get_token,
        pytest.raises(ConfigurationError, match="Control Plane Config"),
    ):
        build_model_provider_session(settings)

    request_device_code.assert_not_called()
    get_token.assert_not_called()


def test_runtime_copilot_provider_session_refreshes_expired_provider_token_from_cached_github_token(tmp_path) -> None:
    provider_cache_path = tmp_path / "auth" / "github-copilot-provider-token.json"
    settings = _github_settings(
        tmp_path,
        github_token={"access_token": _fake_github_token("test"), "expires_at": time.time() + 3600},
        provider_token={"token": _fake_copilot_token("old-provider"), "expires_at": time.time() - 1, "plan": "enterprise"},
    )

    with (
        patch.object(
            copilot,
            "_get_copilot_plan",
            return_value="enterprise",
        ) as get_plan,
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token("new-provider"), expires_at=time.time() + 3600),
        ) as get_token,
    ):
        session = build_model_provider_session(settings)

    get_plan.assert_called_once_with(_fake_github_token("test"))
    get_token.assert_called_once_with(_fake_github_token("test"))
    assert session.client_config.api_key == _fake_copilot_token("new-provider")
    data = json.loads(provider_cache_path.read_text(encoding="utf-8"))
    assert data["token"] == _fake_copilot_token("new-provider")


@pytest.mark.parametrize("factory", [prepare_model_provider_session, build_model_provider_session, refresh_model_provider_session])
def test_copilot_session_factories_reuse_cache_without_auth_or_writes(tmp_path, auth_transport, factory) -> None:
    requests, waits = auth_transport([])
    provider_token = {"token": _fake_copilot_token("still-valid-provider"), "expires_at": time.time() + 3600, "plan": "business"}
    activate_github_copilot_provider(
        model="gpt-5.5",
        github_token={"access_token": _fake_github_token("saved")},
        provider_token=provider_token,
        user_config_root=tmp_path,
    )
    settings = _github_settings(tmp_path, provider_token=provider_token)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()}

    with patch.object(copilot, "activate_github_copilot_provider") as activate:
        session = factory(settings)

    assert session.client_config.api_key == provider_token["token"]
    assert session.client_config.model == "gpt-5.5"
    assert session.client_config.base_url == "https://api.business.githubcopilot.com"
    assert settings.agent_runtime.provider_token == provider_token
    assert requests == []
    assert waits == []
    activate.assert_not_called()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()} == before


def test_runtime_copilot_provider_session_and_ai_assertion_use_cached_provider_token(tmp_path) -> None:
    settings = _github_settings(
        tmp_path,
        provider_token={
            "token": _fake_copilot_token(),
            "expires_at": time.time() + 3600,
            "plan": "enterprise",
        },
    )

    with patch.object(copilot, "_get_copilot_token") as get_token:
        session = build_model_provider_session(settings)
        evaluator = build_ai_assertion_evaluator(settings)

    get_token.assert_not_called()
    assert session.client_config.api_key == _fake_copilot_token()
    assert session.client_config.base_url == "https://api.enterprise.githubcopilot.com"
    assert evaluator.session.client_config.api_key == _fake_copilot_token()


def test_load_cached_token_returns_none_when_expired(tmp_path) -> None:
    assert copilot._load_github_token({"access_token": _fake_github_token("old"), "expires_at": time.time() - 1}) is None


def test_load_github_token_returns_saved_nonexpired_token() -> None:
    token = copilot._load_github_token({"access_token": _fake_github_token("new")})

    assert token == _fake_github_token("new")


def test_request_device_code_uses_explicit_copilot_scope() -> None:
    response = MagicMock()
    response.json.return_value = {
        "device_code": "device",
        "user_code": "user",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 600,
        "interval": 5,
    }
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "post", return_value=response) as post:
        copilot.request_github_copilot_device_code()

    assert post.call_args.kwargs["data"]["scope"] == copilot.GITHUB_OAUTH_SCOPE


def test_complete_device_flow_returns_pending_authorization_without_activating_config() -> None:
    response = MagicMock()
    response.json.return_value = {"access_token": _fake_github_token("pending"), "expires_in": 3600}
    response.raise_for_status = MagicMock()
    device_code = copilot.GitHubDeviceCode(
        device_code="device",
        user_code="user",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )

    with (
        patch.object(copilot.time, "sleep"),
        patch.object(copilot.httpx, "post", return_value=response),
        patch.object(copilot, "_get_copilot_plan", return_value="business"),
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token("pending"), expires_at=time.time() + 1800),
        ),
        patch.object(copilot, "activate_github_copilot_provider") as activate,
    ):
        authorization = copilot.complete_github_copilot_device_flow(device_code, cancel_requested=lambda: False)

    activate.assert_not_called()
    assert authorization.plan == "business"
    assert authorization.github_access_token == _fake_github_token("pending")
    assert authorization.copilot_token == _fake_copilot_token("pending")
    assert "pending-token" not in repr(authorization)


def test_list_copilot_models_requests_plan_endpoint_and_filters_general_gpt_five_plus() -> None:
    response = MagicMock()
    response.json.return_value = {
        "data": [
            {"id": "gpt-4o", "name": "GPT 4o", "capabilities": {"type": "chat"}},
            {"id": "gpt-5", "name": "GPT 5", "capabilities": {"type": "chat"}},
            {"id": "gpt-5.1", "name": "GPT 5.1", "capabilities": {"type": "chat"}},
            {"id": "gpt-5-mini", "name": "GPT 5 Mini", "capabilities": {"type": "chat"}},
            {"id": "gpt-5.1-codex", "name": "GPT 5.1 Codex", "capabilities": {"type": "chat"}},
            {"id": "gpt-6", "name": "GPT 6", "capabilities": {"type": "chat"}},
            {"id": "GPT-5", "name": "duplicate", "capabilities": {"type": "chat"}},
            {"id": "gpt-5.2", "name": "disabled", "model_picker_enabled": False, "capabilities": {"type": "chat"}},
            {"id": "gpt-5.3", "name": "completion", "capabilities": {"type": "completion"}},
        ]
    }
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response) as get:
        models = copilot.list_github_copilot_models(_authorization())

    assert models == (
        copilot.GitHubCopilotModel(id="gpt-5", name="GPT 5"),
        copilot.GitHubCopilotModel(id="gpt-5.1", name="GPT 5.1"),
        copilot.GitHubCopilotModel(id="gpt-6", name="GPT 6"),
    )
    assert get.call_args.args[0] == "https://api.business.githubcopilot.com/models"
    assert get.call_args.kwargs["headers"]["authorization"] == "Bearer pending-token"
    assert get.call_args.kwargs["timeout"] == copilot.COPILOT_AUTH_TIMEOUT_SECONDS


def test_copilot_reasoning_effort_filters_preserve_service_order() -> None:
    eligible = ["gpt-7-pro", "gpt-5", "gpt-5-2025-08-07", "gpt-5.1", "gpt-5.2-pro", "gpt-5.4-pro-2026-03-05", "gpt-5.7", "gpt-5.10-pro", "gpt-6", "gpt-7"]
    excluded = ["gpt-5-pro", "gpt-5-pro-2025-10-06", "gpt-5.1-pro", "gpt-5-chat-latest", "gpt-7-chat", "gpt-7-mini", "gpt-7-codex", "claude-4", "gemini-4-pro"]
    response = MagicMock()
    response.json.return_value = {
        "data": [{"id": name, "capabilities": {"type": "chat"}} for name in eligible + excluded + eligible]
        + [
            {"id": "gpt-7.1", "name": "GPT 7.1 Chat"},
            {"id": "gpt-5.0", "name": "GPT 5 Pro"},
            {"id": "gpt-7.2", "model_picker_enabled": False},
            {"id": "gpt-7.3", "capabilities": {"type": "completion"}},
        ]
    }

    with patch.object(copilot.httpx, "get", return_value=response) as get:
        assert [model.id for model in copilot.list_github_copilot_models(_authorization())] == eligible
    get.assert_called_once()


def test_list_copilot_models_rejects_malformed_envelope_without_exposing_token() -> None:
    response = MagicMock()
    response.json.return_value = {"data": "not-a-list", "token": _fake_copilot_token("pending")}
    response.raise_for_status = MagicMock()

    with (
        patch.object(copilot.httpx, "get", return_value=response),
        pytest.raises(ConfigurationError) as captured,
    ):
        copilot.list_github_copilot_models(_authorization())

    assert _fake_copilot_token("pending") not in str(captured.value)
    assert captured.value.context["reason"] == "malformed_response"


def test_saved_copilot_models_refreshes_and_retries_once_after_auth_rejection(tmp_path) -> None:
    activate_github_copilot_provider(
        model="gpt-5",
        github_token={"access_token": _fake_github_token("saved"), "expires_at": time.time() + 3600},
        provider_token={"token": _fake_copilot_token("cached"), "expires_at": time.time() + 1800, "plan": "individual"},
        user_config_root=tmp_path,
    )
    authentication_error = ConfigurationError(
        "rejected",
        context={"provider": "github_copilot", "reason": "authentication"},
    )

    with (
        patch.object(
            copilot,
            "list_github_copilot_models",
            side_effect=[authentication_error, (copilot.GitHubCopilotModel(id="gpt-5.2", name="GPT 5.2"),)],
        ) as discover,
        patch.object(copilot, "_get_copilot_plan", return_value="business") as get_plan,
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token("fresh"), expires_at=time.time() + 1800),
        ) as get_token,
    ):
        models = copilot.list_saved_github_copilot_models(user_config_root=tmp_path)

    assert models == (copilot.GitHubCopilotModel(id="gpt-5.2", name="GPT 5.2"),)
    assert discover.call_count == 2
    get_plan.assert_called_once_with(_fake_github_token("saved"))
    get_token.assert_called_once_with(_fake_github_token("saved"))


def test_saved_copilot_models_does_not_refresh_temporary_failure(tmp_path) -> None:
    activate_github_copilot_provider(
        model="gpt-5",
        github_token={"access_token": _fake_github_token("saved"), "expires_at": time.time() + 3600},
        provider_token={"token": _fake_copilot_token("cached"), "expires_at": time.time() + 1800, "plan": "individual"},
        user_config_root=tmp_path,
    )
    timeout_error = ConfigurationError(
        "timeout",
        context={"provider": "github_copilot", "reason": "timeout"},
    )

    with (
        patch.object(copilot, "list_github_copilot_models", side_effect=timeout_error),
        patch.object(copilot, "_get_copilot_plan") as get_plan,
        pytest.raises(ConfigurationError) as captured,
    ):
        copilot.list_saved_github_copilot_models(user_config_root=tmp_path)

    assert captured.value.context["reason"] == "timeout"
    get_plan.assert_not_called()


def test_saved_copilot_models_does_not_refresh_access_denied(tmp_path) -> None:
    activate_github_copilot_provider(
        model="gpt-5",
        github_token={"access_token": _fake_github_token("saved"), "expires_at": time.time() + 3600},
        provider_token={"token": _fake_copilot_token("cached"), "expires_at": time.time() + 1800, "plan": "individual"},
        user_config_root=tmp_path,
    )
    denied = ConfigurationError(
        "denied",
        context={"provider": "github_copilot", "reason": "access_denied"},
    )

    with (
        patch.object(copilot, "list_github_copilot_models", side_effect=denied),
        patch.object(copilot, "_get_copilot_plan") as get_plan,
        pytest.raises(ConfigurationError) as captured,
    ):
        copilot.list_saved_github_copilot_models(user_config_root=tmp_path)

    assert captured.value.context["reason"] == "access_denied"
    get_plan.assert_not_called()


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [(401, "authentication"), (403, "access_denied"), (429, "rate_limited"), (503, "network")],
)
def test_copilot_http_failure_reason_preserves_auth_and_access_distinction(status_code: int, reason: str) -> None:
    assert copilot._http_failure_reason(status_code) == reason


def test_activate_copilot_authorization_commits_exact_credentials_and_model(tmp_path) -> None:
    authorization = _authorization()

    with patch.object(copilot, "activate_github_copilot_provider", return_value=MagicMock()) as activate:
        result = copilot.activate_github_copilot_authorization(
            authorization,
            model="gpt-5.1",
            user_config_root=tmp_path,
        )

    assert result is activate.return_value
    activate.assert_called_once_with(
        model="gpt-5.1",
        github_token={"access_token": authorization.github_access_token, "expires_at": authorization.github_expires_at},
        provider_token={
            "token": authorization.copilot_token,
            "expires_at": authorization.copilot_expires_at,
            "plan": authorization.plan,
        },
        user_config_root=tmp_path,
    )


def test_get_copilot_token_uses_copilot_exchange_headers() -> None:
    response = MagicMock()
    response.json.return_value = {
        "token": _fake_copilot_token(),
        "expires_at": 9999999999,
    }
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response) as get:
        token = copilot._get_copilot_token(_fake_github_token("test"))

    headers = get.call_args.kwargs["headers"]
    assert headers["authorization"] == "token ghu_test"
    assert headers["x-github-api-version"] == copilot.GITHUB_API_VERSION
    assert get.call_args.kwargs["timeout"] == copilot.COPILOT_AUTH_TIMEOUT_SECONDS
    assert token.token == _fake_copilot_token()


def test_prepare_model_provider_session_noninteractive_rejects_missing_provider_token(tmp_path) -> None:
    settings = _github_settings(tmp_path)

    with (
        patch.object(copilot, "request_github_copilot_device_code") as request_device_code,
        patch.object(copilot, "_get_copilot_token") as get_token,
        pytest.raises(ConfigurationError, match="Control Plane Config"),
    ):
        prepare_model_provider_session(settings)

    request_device_code.assert_not_called()
    get_token.assert_not_called()


def test_get_copilot_plan_rejects_unknown_plan() -> None:
    response = MagicMock()
    response.json.return_value = {"copilot_plan": "unknown"}
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response), pytest.raises(ConfigurationError, match="Unknown GitHub Copilot plan"):
        copilot._get_copilot_plan(_fake_github_token("test"))


def test_get_copilot_token_requires_token_field() -> None:
    response = MagicMock()
    response.json.return_value = {}
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response), pytest.raises(ConfigurationError, match="did not include a token"):
        copilot._get_copilot_token(_fake_github_token("test"))


@pytest.fixture
def auth_transport(monkeypatch):
    clients = []
    monkeypatch.setattr(copilot.time, "time", lambda: 1_700_000_000.0)

    def install(outcomes):
        requests = []
        waits = []
        pending = iter(outcomes)

        def handle(request):
            requests.append(request)
            outcome = next(pending)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        client = httpx.Client(transport=httpx.MockTransport(handle))
        clients.append(client)
        monkeypatch.setattr(copilot.httpx, "get", client.get)
        monkeypatch.setattr(copilot.time, "sleep", waits.append)
        return requests, waits

    yield install
    for client in clients:
        client.close()


def _plan_response():
    return httpx.Response(200, json={"copilot_plan": "enterprise"})


def _token_response():
    return httpx.Response(200, json={"token": _fake_copilot_token("refreshed"), "expires_at": time.time() + 3600})


@pytest.mark.parametrize("remaining,refresh", [(60.001, False), (60, True), (59.999, True), (-1, True)])
@pytest.mark.parametrize("factory", [prepare_model_provider_session, build_model_provider_session, refresh_model_provider_session])
def test_copilot_factories_observe_exact_expiry_safety_boundary(tmp_path, auth_transport, remaining, refresh, factory) -> None:
    requests, waits = auth_transport([_plan_response(), _token_response()])
    settings = _github_settings(
        tmp_path,
        github_token={"access_token": _fake_github_token("saved")},
        provider_token={"token": _fake_copilot_token("cached"), "expires_at": time.time() + remaining, "plan": "business"},
    )

    session = factory(settings)

    assert session.client_config.api_key == _fake_copilot_token("refreshed" if refresh else "cached")
    assert [str(request.url) for request in requests] == ([copilot.COPILOT_USER_URL, copilot.COPILOT_TOKEN_URL] if refresh else [])
    assert waits == []
    assert (tmp_path / "auth" / "github-copilot-provider-token.json").exists() is refresh


@pytest.mark.parametrize("expires_at", [None, True, False, float("nan"), float("inf"), -float("inf"), "1700003600"])
def test_invalid_cached_expiry_requires_refresh(tmp_path, auth_transport, expires_at) -> None:
    requests, waits = auth_transport([_plan_response(), _token_response()])
    settings = _github_settings(
        tmp_path,
        github_token={"access_token": _fake_github_token("saved")},
        provider_token={"token": _fake_copilot_token("cached"), "expires_at": expires_at, "plan": "business"},
    )

    session = refresh_model_provider_session(settings)

    assert session.client_config.api_key == _fake_copilot_token("refreshed")
    assert len(requests) == 2
    assert waits == []


@pytest.mark.parametrize("expires_at", [True, False, float("nan"), float("inf"), -float("inf"), "1700003600", 1_700_000_060])
def test_invalid_or_near_expiry_oauth_does_not_start_auth(tmp_path, auth_transport, expires_at) -> None:
    requests, waits = auth_transport([])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved"), "expires_at": expires_at})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "authentication"}
    assert requests == []
    assert waits == []


def test_runtime_refresh_shares_retry_budget_and_retains_successful_plan(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([httpx.Response(503), _plan_response(), httpx.Response(429), httpx.Response(502), _token_response()])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    session = refresh_model_provider_session(settings)

    assert session.client_config.api_key == _fake_copilot_token("refreshed")
    assert [str(request.url) for request in requests] == [copilot.COPILOT_USER_URL] * 2 + [copilot.COPILOT_TOKEN_URL] * 3
    assert waits == [1, 2, 4]
    assert all(request.extensions["timeout"] == {"connect": 20.0, "read": 20.0, "write": 20.0, "pool": 20.0} for request in requests)
    assert json.loads((tmp_path / "auth" / "github-copilot-provider-token.json").read_text())["plan"] == "enterprise"


def test_runtime_refresh_exhausts_shared_retries_without_old_token_fallback(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([httpx.Response(503), _plan_response(), httpx.Response(503), httpx.Response(503), httpx.Response(503)])
    cached = {"token": _fake_copilot_token("near-expiry"), "expires_at": time.time() + 30, "plan": "business"}
    activate_github_copilot_provider(model="gpt-5.5", github_token={"access_token": _fake_github_token("saved")}, provider_token=cached, user_config_root=tmp_path)
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")}, provider_token=cached)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with patch.object(copilot, "activate_github_copilot_provider") as activate, pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "network", "status_code": 503}
    assert [str(request.url) for request in requests] == [copilot.COPILOT_USER_URL] * 2 + [copilot.COPILOT_TOKEN_URL] * 3
    assert waits == [1, 2, 4]
    assert settings.agent_runtime.provider_token == cached
    activate.assert_not_called()
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("failure_type,reason", [(httpx.ConnectError, "network"), (httpx.ReadError, "network"), (httpx.ConnectTimeout, "timeout"), (httpx.ReadTimeout, "timeout")])
@pytest.mark.parametrize("recover", [False, True])
def test_runtime_transport_failure_retries_and_preserves_final_safe_errno(tmp_path, auth_transport, failure_type, reason, recover) -> None:
    failures = []
    for _ in range(3 if recover else 4):
        failure = failure_type("private-network-text ghu_saved")
        failure.__cause__ = OSError(61, "private-host-and-path")
        failures.append(failure)
    requests, waits = auth_transport([_plan_response(), *failures, *([_token_response()] if recover else [])])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    if recover:
        session = refresh_model_provider_session(settings)
        assert session.client_config.api_key == _fake_copilot_token("refreshed")
    else:
        with pytest.raises(ConfigurationError) as captured:
            refresh_model_provider_session(settings)
        assert captured.value.context == {"provider": "github_copilot", "reason": reason, "errno": 61}
        assert captured.value.__cause__ is failures[-1]
        assert "private-network-text" not in str(captured.value)
        assert "ghu_saved" not in str(captured.value)
        assert settings.agent_runtime.provider_token is None
        assert not (tmp_path / "auth" / "github-copilot-provider-token.json").exists()
    assert [str(request.url) for request in requests] == [copilot.COPILOT_USER_URL] + [copilot.COPILOT_TOKEN_URL] * 4
    assert waits == [1, 2, 4]


@pytest.mark.parametrize("status,reason", [(401, "authentication"), (403, "access_denied"), (400, "internal"), (404, "internal"), (302, "internal")])
@pytest.mark.parametrize("endpoint", ["plan", "token"])
def test_nonretryable_auth_status_fails_without_activation(tmp_path, auth_transport, status, reason, endpoint) -> None:
    requests, waits = auth_transport([*([_plan_response()] if endpoint == "token" else []), httpx.Response(status, text="private-token-and-body")])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with patch.object(copilot, "activate_github_copilot_provider") as activate, pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": reason, "status_code": status}
    assert len(requests) == (2 if endpoint == "token" else 1)
    assert waits == []
    activate.assert_not_called()
    assert "private-token-and-body" not in str(captured.value)


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("plan", []),
        ("plan", None),
        ("plan", {}),
        ("plan", {"copilot_plan": []}),
        ("plan", {"copilot_plan": "unknown"}),
        ("token", []),
        ("token", None),
        ("token", {}),
        ("token", {"token": True}),
        ("token", {"token": " "}),
        *[("token", {"token": "candidate", "expires_at": expiry}) for expiry in [None, True, False, "later"]],
    ],
)
def test_malformed_auth_payload_does_not_retry_or_activate(tmp_path, auth_transport, endpoint, payload) -> None:
    response = httpx.Response(200, content=json.dumps(payload).encode())
    requests, waits = auth_transport([*([_plan_response()] if endpoint == "token" else []), response])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with patch.object(copilot, "activate_github_copilot_provider") as activate, pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "malformed_response"}
    assert len(requests) == (2 if endpoint == "token" else 1)
    assert waits == []
    activate.assert_not_called()


@pytest.mark.parametrize(
    "body", [b"not-json private-token", b'{"token":"candidate","expires_at":NaN}', b'{"token":"candidate","expires_at":Infinity}', b'{"token":"candidate","expires_at":-Infinity}']
)
def test_invalid_token_json_or_nonfinite_expiry_fails_without_retry(tmp_path, auth_transport, body) -> None:
    requests, waits = auth_transport([_plan_response(), httpx.Response(200, content=body)])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "malformed_response"}
    assert len(requests) == 2
    assert waits == []
    assert settings.agent_runtime.provider_token is None


@pytest.mark.parametrize(
    "retry_after,expected",
    [("0", 1), ("0000", 1), ("0003", 3), ("3", 3), ("9" * 5000, 4), ("invalid", 1), ("-2", 1), ("1.5", 1), ("past", 1), ("date", 4)],
)
def test_runtime_retry_after_increases_delay_with_four_second_cap(tmp_path, auth_transport, retry_after, expected) -> None:
    if retry_after in {"past", "date"}:
        offset = -10 if retry_after == "past" else 20
        retry_after = format_datetime(datetime.fromtimestamp(1_700_000_000 + offset, tz=UTC), usegmt=True)
    requests, waits = auth_transport([_plan_response(), httpx.Response(429, headers={"Retry-After": retry_after}), _token_response()])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    session = refresh_model_provider_session(settings)

    assert session.client_config.api_key == _fake_copilot_token("refreshed")
    assert len(requests) == 3
    assert waits == [expected]


def test_retry_after_cannot_reduce_later_ordinary_delays(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([httpx.Response(503, headers={"Retry-After": "0"}) for _ in range(3)] + [_plan_response(), _token_response()])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    assert refresh_model_provider_session(settings).client_config.api_key == _fake_copilot_token("refreshed")
    assert len(requests) == 5
    assert waits == [1, 2, 4]


def test_storage_failure_preserves_old_state_and_safe_errno_without_retry(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([_plan_response(), _token_response()])
    root = tmp_path / "not-a-directory"
    root.write_text("existing-data")
    settings = _github_settings(root, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "storage", "errno": 17}
    assert str(root) not in str(captured.value)
    assert root.read_text() == "existing-data"
    assert settings.agent_runtime.provider_token is None
    assert len(requests) == 2
    assert waits == []


@pytest.mark.parametrize("errno", [True, False, 0, -1, "61", None])
def test_runtime_failure_drops_nonnumeric_or_invalid_errno(tmp_path, auth_transport, errno) -> None:
    failure = httpx.ConnectError("private-error")
    cause = OSError()
    cause.errno = errno
    failure.__cause__ = cause
    requests, waits = auth_transport([failure] * 4)
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "network"}
    assert len(requests) == 4
    assert waits == [1, 2, 4]


@pytest.mark.parametrize("os_cause", [False, True])
def test_runtime_failure_never_promotes_non_os_errno(tmp_path, auth_transport, os_cause) -> None:
    failure = httpx.ConnectError("CANARY_NETWORK_TEXT")
    imposter = RuntimeError("CANARY_NON_OS_TEXT")
    imposter.errno = 99
    failure.__cause__ = imposter
    imposter.__cause__ = OSError(61, "CANARY_OS_TEXT") if os_cause else failure
    requests, waits = auth_transport([failure] * 4)
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    expected = {"provider": "github_copilot", "reason": "network"}
    if os_cause:
        expected["errno"] = 61
    assert captured.value.context == expected
    assert "CANARY" not in str(captured.value)
    assert len(requests) == 4
    assert waits == [1, 2, 4]


@pytest.mark.parametrize("field,value", [("token", None), ("token", True), ("token", " "), ("plan", None), ("plan", []), ("plan", "unknown")])
def test_incomplete_or_unsupported_cache_requires_full_refresh(tmp_path, auth_transport, field, value) -> None:
    requests, waits = auth_transport([_plan_response(), _token_response()])
    cached = {"token": _fake_copilot_token("cached"), "expires_at": time.time() + 3600, "plan": "business"}
    cached[field] = value
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")}, provider_token=cached)

    session = prepare_model_provider_session(settings)

    assert session.client_config.api_key == _fake_copilot_token("refreshed")
    assert len(requests) == 2
    assert waits == []


@pytest.mark.parametrize("status,reason", [(429, "rate_limited"), (500, "network"), (502, "network"), (599, "network")])
@pytest.mark.parametrize("endpoint", ["plan", "token"])
def test_retryable_http_status_exhaustion_is_bounded(tmp_path, auth_transport, status, reason, endpoint) -> None:
    responses = [httpx.Response(status, text="private-response-body") for _ in range(4)]
    requests, waits = auth_transport([*([_plan_response()] if endpoint == "token" else []), *responses])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": reason, "status_code": status}
    assert len(requests) == (5 if endpoint == "token" else 4)
    assert waits == [1, 2, 4]
    assert "private-response-body" not in str(captured.value)
    assert settings.agent_runtime.provider_token is None
    assert not (tmp_path / "auth" / "github-copilot-provider-token.json").exists()


def test_plan_invalid_json_fails_immediately(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([httpx.Response(200, content=b"invalid-json private-token")])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "malformed_response"}
    assert len(requests) == 1
    assert waits == []


def test_refresh_preserves_existing_default_token_lifetime(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([_plan_response(), httpx.Response(200, json={"token": _fake_copilot_token("refreshed")})])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    session = refresh_model_provider_session(settings)

    assert session.client_config.api_key == _fake_copilot_token("refreshed")
    assert settings.agent_runtime.provider_token["expires_at"] == time.time() + 600
    assert len(requests) == 2
    assert waits == []


@pytest.mark.parametrize("shape", ["nested", "cycle", "too_deep"])
def test_safe_errno_walk_is_bounded_and_handles_implicit_context(tmp_path, auth_transport, shape) -> None:
    failure = httpx.ConnectError("private-error")
    middle = RuntimeError("private-middle")
    failure.__cause__ = middle
    if shape == "nested":
        middle.__context__ = OSError(61, "private-host")
    elif shape == "cycle":
        middle.__cause__ = failure
    else:
        for _ in range(10):
            following = RuntimeError("private-middle")
            middle.__cause__ = following
            middle = following
        middle.__cause__ = OSError(61, "private-host")
    requests, waits = auth_transport([failure] * 4)
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    expected = {"provider": "github_copilot", "reason": "network"}
    if shape == "nested":
        expected["errno"] = 61
    assert captured.value.context == expected
    assert len(requests) == 4
    assert waits == [1, 2, 4]


def test_nontransport_http_error_does_not_receive_retry_budget(tmp_path, auth_transport) -> None:
    failure = httpx.TooManyRedirects("private-redirect-url")
    requests, waits = auth_transport([failure])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})

    with pytest.raises(ConfigurationError) as captured:
        refresh_model_provider_session(settings)

    assert captured.value.context == {"provider": "github_copilot", "reason": "network"}
    assert captured.value.__cause__ is failure
    assert len(requests) == 1
    assert waits == []


def test_refresh_does_not_intercept_owner_cancellation_during_backoff(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([httpx.Response(503)])
    settings = _github_settings(tmp_path, github_token={"access_token": _fake_github_token("saved")})
    cancellation = KeyboardInterrupt()

    with patch.object(copilot.time, "sleep", side_effect=cancellation), pytest.raises(KeyboardInterrupt) as captured:
        refresh_model_provider_session(settings)

    assert captured.value is cancellation
    assert len(requests) == 1
    assert waits == []
    assert settings.agent_runtime.provider_token is None


def test_device_flow_token_exchange_does_not_gain_runtime_retries(tmp_path, auth_transport, monkeypatch) -> None:
    requests, waits = auth_transport([_plan_response(), httpx.Response(503)])
    oauth_response = httpx.Response(200, json={"access_token": _fake_github_token("saved")}, request=httpx.Request("POST", copilot.ACCESS_TOKEN_URL))
    monkeypatch.setattr(copilot.httpx, "post", lambda *args, **kwargs: oauth_response)
    device = copilot.GitHubDeviceCode(device_code="device", user_code="user", verification_uri="https://github.com/login/device", expires_at=time.time() + 600, poll_interval_seconds=5)

    with pytest.raises(ConfigurationError) as captured:
        copilot.complete_github_copilot_device_flow(device, cancel_requested=lambda: False)

    assert captured.value.context["status_code"] == 503
    assert [str(request.url) for request in requests] == [copilot.COPILOT_USER_URL, copilot.COPILOT_TOKEN_URL]
    assert waits == [5]


def test_model_discovery_does_not_gain_runtime_retries(tmp_path, auth_transport) -> None:
    requests, waits = auth_transport([httpx.Response(503)])

    with pytest.raises(ConfigurationError) as captured:
        copilot.list_github_copilot_models(_authorization())

    assert captured.value.context["reason"] == "network"
    assert len(requests) == 1
    assert waits == []


@pytest.mark.parametrize("stage", ["device_code", "oauth_poll", "copilot_plan", "copilot_token", "model_discovery"])
def test_initial_auth_http_failure_reports_safe_stage_without_retry(monkeypatch, stage):
    requests = []

    def handle(request):
        requests.append(request)
        failures = {"device_code": copilot.DEVICE_CODE_URL, "oauth_poll": copilot.ACCESS_TOKEN_URL, "copilot_plan": copilot.COPILOT_USER_URL, "copilot_token": copilot.COPILOT_TOKEN_URL}
        if str(request.url) == failures.get(stage) or stage == "model_discovery":
            return httpx.Response(503, text="CANARY_RESPONSE_TOKEN")
        if str(request.url) == copilot.ACCESS_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "CANARY_OAUTH_TOKEN"})
        return _plan_response()

    monkeypatch.setattr(copilot.time, "sleep", lambda _: None)
    device = copilot.GitHubDeviceCode("CANARY_DEVICE_TOKEN", "USER", "https://github.com/login/device", time.time() + 600, 2)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(copilot.httpx, "post", client.post)
        monkeypatch.setattr(copilot.httpx, "get", client.get)
        if stage == "device_code":
            operation = copilot.request_github_copilot_device_code
        elif stage == "model_discovery":
            operation = partial(copilot.list_github_copilot_models, _authorization())
        else:
            operation = partial(copilot.complete_github_copilot_device_flow, device, cancel_requested=lambda: False)
        with pytest.raises(ConfigurationError) as caught:
            operation()
    diagnostic = caught.value.context["diagnostic"]
    assert diagnostic["stage"] == stage
    assert diagnostic["error_code"] == "http_error"
    assert diagnostic["http_status"] == 503
    assert diagnostic["elapsed_seconds"] >= 0
    assert caught.value.__suppress_context__ is True
    assert "CANARY" not in str(caught.value)
    assert len(requests) == {"copilot_plan": 2, "copilot_token": 3}.get(stage, 1)


@pytest.mark.parametrize("terminal", ["expired_token", "access_denied", "CANARY_UNRESTRICTED_ERROR"])
def test_poll_diagnostics_preserve_slowdown_counts_and_normalize_oauth_errors(monkeypatch, terminal):
    responses = iter(({"error": "authorization_pending"}, {"error": "slow_down", "interval": 7}, {"error": terminal}))
    waits = []
    monkeypatch.setattr(copilot.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(copilot.time, "sleep", waits.append)
    device = copilot.GitHubDeviceCode("CANARY_DEVICE_TOKEN", "USER", "https://github.com/login/device", 1700000600.0, 2, native_expires_at=1700000600.0, native_poll_interval_seconds=2)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses)))) as client:
        monkeypatch.setattr(copilot.httpx, "post", client.post)
        with pytest.raises(ConfigurationError) as caught:
            copilot.complete_github_copilot_device_flow(device, cancel_requested=lambda: False)
    diagnostic = caught.value.context["diagnostic"]
    assert diagnostic["stage"] == "oauth_poll"
    assert diagnostic["error_code"] == diagnostic["last_oauth_error"] == ("unknown_oauth_error" if terminal.startswith("CANARY") else terminal)
    assert diagnostic["poll_attempts"] == 3
    assert diagnostic["poll_interval_seconds"] == 7
    assert diagnostic["last_http_status"] == 200
    assert diagnostic["remaining_seconds"] == 600
    assert waits == [2, 2, 7]
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("cancelled", [False, True])
def test_poll_termination_without_transport_has_explicit_diagnostics(monkeypatch, cancelled):
    monkeypatch.setattr(copilot.time, "time", lambda: 1700000000.0)
    device = copilot.GitHubDeviceCode("device", "USER", "https://github.com/login/device", 1700000600.0 if cancelled else 1699999999.0, 2)
    with patch.object(copilot.httpx, "post") as post, pytest.raises(ConfigurationError) as caught:
        copilot.complete_github_copilot_device_flow(device, cancel_requested=lambda: cancelled)
    assert caught.value.context["diagnostic"]["error_code"] == ("cancelled" if cancelled else "local_expiry")
    assert caught.value.context["diagnostic"]["poll_attempts"] == 0
    post.assert_not_called()
