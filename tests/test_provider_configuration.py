# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fsq_agent.agent_engine import ModelRequest, ModelResult
from fsq_agent.application import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    list_saved_github_copilot_models,
)
from fsq_agent.config import (
    Settings,
    activate_github_copilot_provider,
    load_settings,
    load_user_provider_config,
    save_azure_openai_provider,
)
from fsq_agent.models import AgentRuntimeSettings, ConfigurationError
from fsq_agent.providers import (
    GitHubDeviceCode,
    activate_github_copilot_authorization,
    build_model_provider_session,
    complete_github_copilot_device_flow,
    request_github_copilot_device_code,
    test_model_provider_connection,
)
from fsq_agent.providers import _github_copilot as copilot


def _settings_path(tmp_path: Path) -> Path:
    path = tmp_path / "platform.yaml"
    path.write_text(
        f"""
workspace:
  root_dir: {tmp_path.as_posix()}/workspace
""",
        encoding="utf-8",
    )
    return path


def test_azure_client_uses_saved_private_key_instead_of_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_root = tmp_path / "user"
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "environment-key")
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="saved-model",
        api_key="saved-key",
        user_config_root=user_root,
    )
    settings = load_settings(_settings_path(tmp_path), user_config_root=user_root)

    session = build_model_provider_session(settings)

    assert session.client_config.api_key == "saved-key"
    assert session.model == "saved-model"


def test_github_client_uses_saved_custom_model_and_user_provider_token(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    activate_github_copilot_provider(
        model="custom-copilot-model",
        github_token={"access_token": "github-token"},
        provider_token={"token": "provider-token", "expires_at": time.time() + 3600, "plan": "business"},
        user_config_root=user_root,
    )
    settings = load_settings(_settings_path(tmp_path), user_config_root=user_root)

    session = build_model_provider_session(settings)

    assert session.model == "custom-copilot-model"
    assert session.client_config.api_key == "provider-token"
    assert session.client_config.base_url == "https://api.business.githubcopilot.com"


def test_runtime_missing_github_credentials_never_starts_device_flow(tmp_path: Path) -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings(provider="github_copilot"))
    settings.agent_runtime.model = "copilot-model"
    settings.agent_runtime.user_config_root = tmp_path

    with (
        patch.object(copilot, "request_github_copilot_device_code") as request_device_code,
        pytest.raises(ConfigurationError, match="Control Plane Config"),
    ):
        build_model_provider_session(settings)

    request_device_code.assert_not_called()


def test_application_preserves_saved_github_access_denied_without_sign_in_guidance(tmp_path: Path) -> None:
    denied = ConfigurationError(
        "denied",
        context={"provider": "github_copilot", "reason": "access_denied"},
    )

    with (
        patch(
            "fsq_agent.application.providers._discover_saved_github_copilot_models",
            side_effect=denied,
        ),
        pytest.raises(ApplicationError) as captured,
    ):
        list_saved_github_copilot_models(user_config_root=tmp_path)

    error = captured.value
    assert error.code == ApplicationErrorCode.PROVIDER_UNAVAILABLE
    assert error.category == ApplicationErrorCategory.UNAVAILABLE
    assert error.details == {"provider": "github_copilot", "reason": "access_denied"}
    assert "access was denied" in error.message
    assert "permission" in (error.action or "").casefold()
    assert "sign in" not in f"{error.message} {error.action}".casefold()


def test_request_github_device_code_returns_observable_values_without_printing() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "device_code": "device-secret",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://github.com/login/device",
        "interval": 7,
        "expires_in": 600,
    }

    with patch.object(copilot.httpx, "post", return_value=response) as post, patch("builtins.print") as print_mock:
        device_code = request_github_copilot_device_code()

    assert device_code.user_code == "ABCD-EFGH"
    assert device_code.verification_uri == "https://github.com/login/device"
    assert device_code.poll_interval_seconds == 7
    assert device_code.expires_at > time.time()
    assert post.call_args.kwargs["data"]["scope"] == copilot.GITHUB_OAUTH_SCOPE
    print_mock.assert_not_called()


def test_cancelled_github_device_flow_preserves_active_provider(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="azure-model",
        api_key="azure-key",
        user_config_root=user_root,
    )
    device_code = GitHubDeviceCode(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )

    with pytest.raises(ConfigurationError, match="cancelled"):
        complete_github_copilot_device_flow(
            device_code,
            cancel_requested=lambda: True,
        )

    assert load_user_provider_config(user_root).provider.type == "azure_openai"  # type: ignore[union-attr]


def test_github_device_flow_cancelled_during_poll_response_does_not_commit(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="azure-model",
        api_key="azure-key",
        user_config_root=user_root,
    )
    device_code = GitHubDeviceCode(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )
    cancelled = False
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"access_token": "github-token"}

    def poll(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return response

    with patch.object(copilot.time, "sleep"), patch.object(copilot.httpx, "post", side_effect=poll), pytest.raises(ConfigurationError, match="cancelled"):
        complete_github_copilot_device_flow(
            device_code,
            cancel_requested=lambda: cancelled,
        )

    assert load_user_provider_config(user_root).provider.type == "azure_openai"  # type: ignore[union-attr]


def test_completed_github_device_flow_requires_explicit_activation_to_commit(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    device_code = GitHubDeviceCode(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"access_token": "github-token"}

    with (
        patch.object(copilot.time, "sleep"),
        patch.object(copilot.httpx, "post", return_value=response),
        patch.object(copilot, "_get_copilot_plan", return_value="enterprise"),
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token="provider-token", expires_at=time.time() + 3600),  # noqa: S106 - synthetic test credential.
        ),
    ):
        authorization = complete_github_copilot_device_flow(
            device_code,
            cancel_requested=lambda: False,
        )
    assert load_user_provider_config(user_root).provider is None

    saved = activate_github_copilot_authorization(
        authorization,
        model="copilot-model",
        user_config_root=user_root,
    )

    assert saved.provider is not None
    assert saved.provider.type == "github_copilot"
    assert saved.provider.model == "copilot-model"
    assert saved.github_token == {"access_token": "github-token"}
    assert saved.provider_token is not None
    assert saved.provider_token["token"] == "provider-token"  # noqa: S105 - synthetic test credential.
    assert saved.provider_token["plan"] == "enterprise"


def test_connection_test_uses_saved_provider_and_always_closes_session(tmp_path: Path) -> None:
    from fsq_agent.config import Settings

    user_root = tmp_path / "user"
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="saved-model",
        api_key="saved-key",
        user_config_root=user_root,
    )
    session = MagicMock()
    session.provider = "azure_openai"
    session.model = "saved-model"
    session.complete_sync.return_value = ModelResult(text="FSQ_OK")

    settings = Settings()
    settings.agent_runtime.reasoning_effort = "high"
    with (
        patch("fsq_agent.providers._connection_test.build_model_provider_session", return_value=session),
        patch("fsq_agent.providers._connection_test.Settings", return_value=settings),
    ):
        result = test_model_provider_connection(user_config_root=user_root)

    assert result.provider == "azure_openai"
    assert result.model == "saved-model"
    assert result.duration_seconds >= 0
    session.complete_sync.assert_called_once_with(ModelRequest(input="Reply with FSQ_OK.", reasoning_effort="low"))
    session.close_sync.assert_called_once_with()


def test_initial_copilot_application_filters_untrusted_diagnostics(monkeypatch):
    from fsq_agent.application import providers

    diagnostic = {
        "stage": "oauth_poll",
        "error_code": "timeout",
        "http_status": 503,
        "poll_attempts": 2,
        "elapsed_seconds": 1.5,
        "remaining_seconds": 42.0,
        "expires_at": 1700000600.0,
        "poll_interval_seconds": 5,
        "last_http_status": True,
        "last_oauth_error": "CANARY_ERROR",
        "body": "CANARY_BODY",
    }
    failure = ConfigurationError("CANARY_EXCEPTION", context={"provider": "github_copilot", "diagnostic": diagnostic, "token": "CANARY_TOKEN"})
    with patch.object(providers, "request_github_copilot_device_code", side_effect=failure), pytest.raises(ApplicationError) as caught:
        providers.request_github_device_code()
    result = caught.value.details["diagnostic"]
    assert result == {key: value for key, value in diagnostic.items() if key not in {"last_http_status", "last_oauth_error", "body"}}
    assert caught.value.details["provider"] == "github_copilot"
    assert caught.value.__suppress_context__ is True
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), 10**400, "CANARY_NUMBER"])
def test_initial_copilot_application_omits_invalid_diagnostic_numbers(value):
    from fsq_agent.application import providers

    failure = ConfigurationError(
        "CANARY_EXCEPTION", context={"provider": "github_copilot", "diagnostic": {"stage": "CANARY_STAGE", "error_code": "CANARY_CODE", "elapsed_seconds": value, "poll_attempts": True}}
    )
    with patch.object(providers, "request_github_copilot_device_code", side_effect=failure), pytest.raises(ApplicationError) as caught:
        providers.request_github_device_code()
    diagnostic = caught.value.details["diagnostic"]
    assert diagnostic["stage"] == "device_code"
    assert diagnostic["error_code"] == "internal_error"
    assert "poll_attempts" not in diagnostic
    assert type(diagnostic["elapsed_seconds"]) is float
    assert diagnostic["elapsed_seconds"] >= 0
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("stage", ["model_discovery", "model_selection", "activation"])
def test_initial_copilot_configuration_failure_preserves_stage_and_saved_state(tmp_path, stage):
    from fsq_agent.application import providers

    save_azure_openai_provider(base_url="https://example.openai.azure.com", model="old-model", api_key="old-secret", user_config_root=tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    failure = OSError("CANARY_STORAGE_OR_CALLBACK")
    with (
        patch.object(providers, "complete_github_copilot_device_flow", return_value=object()),
        patch.object(
            providers,
            "list_github_copilot_models",
            return_value=() if stage == "model_selection" else (copilot.GitHubCopilotModel("gpt-5", "GPT-5"),),
            side_effect=failure if stage == "model_discovery" else None,
        ),
        patch.object(providers, "activate_github_copilot_authorization", side_effect=failure) as activate,
    ):
        select = MagicMock(return_value="gpt-5")
        with pytest.raises(ApplicationError) as caught:
            providers.complete_github_configuration(object(), model=None, select_model=select, cancel_requested=lambda: False, user_config_root=tmp_path)
    assert caught.value.details["diagnostic"]["stage"] == stage
    if stage == "model_selection":
        assert caught.value.details["diagnostic"]["error_code"] == "model_not_offered"
        select.assert_not_called()
    if stage != "activation":
        activate.assert_not_called()
    assert "CANARY" not in str(caught.value)
    assert caught.value.__suppress_context__ is True
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("lifetime", [0, -5, True, None, "3600", float("nan"), float("inf"), 10**400, "absent"])
def test_initial_oauth_lifetime_cannot_be_promoted_to_undated_credentials(tmp_path, monkeypatch, lifetime):
    import json

    import httpx

    from fsq_agent.application import providers

    save_azure_openai_provider(base_url="https://example.openai.azure.com", model="old-model", api_key="old-secret", user_config_root=tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    requests = []

    def handle(request):
        requests.append(request)
        if str(request.url) == copilot.ACCESS_TOKEN_URL:
            data = {"access_token": "OAUTH_TEST_CREDENTIAL"}
            if lifetime != "absent":
                data["expires_in"] = lifetime
            return httpx.Response(200, content=json.dumps(data).encode())
        if str(request.url) == copilot.COPILOT_USER_URL:
            return httpx.Response(200, json={"copilot_plan": "individual"})
        if str(request.url) == copilot.COPILOT_TOKEN_URL:
            return httpx.Response(200, json={"token": "PROVIDER_TEST_CREDENTIAL", "expires_at": time.time() + 3600})
        return httpx.Response(200, json={"data": [{"id": "gpt-5", "name": "GPT-5"}]})

    device = GitHubDeviceCode("device", "USER", "https://github.com/login/device", time.time() + 600, 1)
    monkeypatch.setattr(copilot.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(copilot.httpx, "post", client.post)
        monkeypatch.setattr(copilot.httpx, "get", client.get)
        if lifetime == "absent":
            providers.complete_github_configuration(device, model="gpt-5", select_model=lambda _: "gpt-5", cancel_requested=lambda: False, user_config_root=tmp_path)
        else:
            with pytest.raises(ApplicationError) as caught:
                providers.complete_github_configuration(device, model="gpt-5", select_model=lambda _: "gpt-5", cancel_requested=lambda: False, user_config_root=tmp_path)
            assert caught.value.details["diagnostic"]["error_code"] == "malformed_response"
    saved = load_user_provider_config(tmp_path)
    if lifetime == "absent":
        assert saved.provider.type == "github_copilot"
        assert "expires_at" not in saved.github_token
        assert copilot._load_github_token(saved.github_token) == "OAUTH_TEST_CREDENTIAL"
    else:
        assert saved.provider.type == "azure_openai"
        assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
        assert len(requests) == 1


def test_unknown_plan_retains_safe_code_through_provider_and_application(monkeypatch):
    import httpx

    from fsq_agent.application import providers

    def handle(request):
        return httpx.Response(200, json={"access_token": "OAUTH_TEST_CREDENTIAL"} if request.method == "POST" else {"copilot_plan": "UNSUPPORTED_PRIVATE_PLAN"})

    device = GitHubDeviceCode("device", "USER", "https://github.com/login/device", time.time() + 600, 1)
    monkeypatch.setattr(copilot.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(copilot.httpx, "post", client.post)
        monkeypatch.setattr(copilot.httpx, "get", client.get)
        with pytest.raises(ConfigurationError) as provider_error:
            complete_github_copilot_device_flow(device, cancel_requested=lambda: False)
        with pytest.raises(ApplicationError) as application_error:
            providers.complete_github_configuration(device, model="gpt-5", select_model=lambda _: "gpt-5", cancel_requested=lambda: False)
    assert provider_error.value.context["reason"] == "malformed_response"
    for error, diagnostic in ((provider_error.value, provider_error.value.context["diagnostic"]), (application_error.value, application_error.value.details["diagnostic"])):
        assert diagnostic["stage"] == "copilot_plan"
        assert diagnostic["error_code"] == "unknown_plan"
        assert "UNSUPPORTED_PRIVATE_PLAN" not in str(error)
