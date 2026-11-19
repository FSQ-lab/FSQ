# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli._main import main
from fsq_agent.application import ProviderConfigurationResult, ProviderStatusResult
from fsq_agent.application.providers import complete_github_configuration, configure_azure_openai, provider_status
from fsq_agent.config import activate_github_copilot_provider, load_user_provider_config
from fsq_agent.providers import GitHubCopilotModel, ProviderReadiness


def test_azure_application_configuration_is_visible_to_ui_config_api(tmp_path: Path) -> None:
    configure_azure_openai(base_url="https://sample.openai.azure.com", model="gpt-5", api_key="top-secret", user_config_root=tmp_path)

    saved = load_user_provider_config(tmp_path)
    assert saved.provider is not None
    assert saved.provider.type == "azure_openai"
    assert saved.provider.model == "gpt-5"
    assert saved.api_key == "top-secret"


@pytest.mark.parametrize("output", ["human", "json", "jsonl"])
def test_openai_cli_non_interactive_configures_shared_user_store(tmp_path, monkeypatch, output):
    from fsq_agent.providers import OpenAIModel

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", lambda **kwargs: (OpenAIModel(id="gpt-5", name="gpt-5"),))
    result = CliRunner().invoke(main, ["--output", output, "--non-interactive", "providers", "configure", "openai", "--model", "gpt-5", "--api-key", "cli-secret"])
    assert result.exit_code == 0, result.output
    saved = load_user_provider_config(tmp_path / ".fsq")
    assert saved.provider.type == "openai"
    assert saved.api_key == "cli-secret"
    assert "cli-secret" not in result.output
    status = provider_status(user_config_root=tmp_path / ".fsq")
    assert status.status == "ready"
    assert status.provider == "openai"


def test_openai_cli_interactive_requires_explicit_model_choice_and_hides_key(tmp_path, monkeypatch):
    from fsq_agent.providers import OpenAIModel

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", lambda **kwargs: (OpenAIModel(id="gpt-5", name="gpt-5"),))
    result = CliRunner().invoke(main, ["providers", "configure", "openai"], input="cli-secret\n1\n")
    assert result.exit_code == 0, result.output
    assert "Select model" in result.output
    assert "cli-secret" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").provider.model == "gpt-5"


@pytest.mark.parametrize("options", [[], ["--model", "gpt-5"], ["--api-key", "cli-secret"], ["--base-url", "https://untrusted.example", "--model", "gpt-5", "--api-key", "cli-secret"]])
def test_openai_cli_rejects_missing_or_custom_endpoint_options(options):
    result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", "providers", "configure", "openai", *options])
    assert result.exit_code == 2
    assert "cli-secret" not in result.output


def test_openai_cli_empty_models_does_not_save_or_prompt_for_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", lambda **kwargs: ())
    result = CliRunner().invoke(main, ["providers", "configure", "openai", "--api-key", "cli-secret"])
    assert result.exit_code == 4
    assert "Select model" not in result.output
    assert "cli-secret" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").provider is None


def test_status_reads_github_configuration_written_by_shared_ui_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    activate_github_copilot_provider(
        model="gpt-5", github_token={"access_token": "oauth-secret"}, provider_token={"token": "provider-secret", "expires_at": 9999999999, "plan": "individual"}, user_config_root=tmp_path
    )
    monkeypatch.setattr(
        "fsq_agent.application.providers.check_provider_readiness",
        lambda _settings: ProviderReadiness(
            ready=True,
            session_status="session_available",
            code="provider_session_available",
            message="Provider is ready.",
            action="",
        ),
    )

    result = provider_status(user_config_root=tmp_path)

    assert result.status == "ready"
    assert result.provider == "github_copilot"
    assert result.model == "gpt-5"
    assert "secret" not in result.model_dump_json()


def test_failed_github_replacement_preserves_existing_azure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_azure_openai(base_url="https://sample.openai.azure.com", model="old-model", api_key="old-secret", user_config_root=tmp_path)
    monkeypatch.setattr("fsq_agent.application.providers.complete_github_copilot_device_flow", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("fsq_agent.application.providers.list_github_copilot_models", lambda _authorization: (GitHubCopilotModel(id="gpt-5", name="GPT-5"),))
    monkeypatch.setattr("fsq_agent.application.providers.activate_github_copilot_authorization", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("secret failure")))

    with pytest.raises(Exception, match="GitHub Copilot configuration failed"):
        complete_github_configuration(object(), model="gpt-5", select_model=lambda _models: "gpt-5", cancel_requested=lambda: False, user_config_root=tmp_path)

    saved = load_user_provider_config(tmp_path)
    assert saved.provider is not None
    assert saved.provider.type == "azure_openai"
    assert saved.provider.model == "old-model"
    assert saved.api_key == "old-secret"


def test_azure_cli_non_interactive_maps_complete_request_without_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def configure(**kwargs):
        captured.update(kwargs)
        return ProviderConfigurationResult(provider="azure_openai", model=kwargs["model"])

    monkeypatch.setattr("fsq_agent.adapters.cli._main.configure_azure_openai", configure)
    result = CliRunner().invoke(
        main, ["--output", "json", "--non-interactive", "providers", "configure", "azure_openai", "--base-url", "https://sample.openai.azure.com", "--model", "gpt-5", "--api-key", "cli-secret"]
    )

    assert result.exit_code == 0
    assert captured["api_key"] == "cli-secret"
    assert "cli-secret" not in result.output


@pytest.mark.parametrize("output", ["json", "jsonl"])
def test_provider_status_machine_modes_emit_one_safe_terminal_result(output: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.provider_status", lambda: ProviderStatusResult(status="ready", configured=True, provider="azure_openai", model="gpt-5", authenticated=True, message="Ready.")
    )

    result = CliRunner().invoke(main, ["--output", output, "providers", "status"])

    assert result.exit_code == 0
    records = [json.loads(line) for line in result.output.splitlines()]
    assert len(records) == 1
    assert records[0]["result"]["provider"] == "azure_openai"


def test_provider_status_unavailable_is_result_with_exit_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.provider_status",
        lambda: ProviderStatusResult(status="unavailable", configured=False, authenticated=False, message="Not configured.", action="Configure a Provider."),
    )

    result = CliRunner().invoke(main, ["--output", "json", "providers", "status"])

    assert result.exit_code == 4
    assert json.loads(result.output)["status"] == "unavailable"


@pytest.mark.parametrize("expires_at,interval,shown", [(1700000600.0, 7, True), (float("nan"), True, False), (10**400, float("inf"), False)])
def test_copilot_cli_displays_only_valid_native_timing(monkeypatch, expires_at, interval, shown):
    from fsq_agent.providers import GitHubDeviceCode

    device = GitHubDeviceCode("CANARY_DEVICE_TOKEN", "ABCD-EFGH", "https://github.com/login/device", expires_at, interval, native_expires_at=expires_at, native_poll_interval_seconds=interval)
    monkeypatch.setattr("fsq_agent.adapters.cli._main.request_github_device_code", lambda: device)
    monkeypatch.setattr("fsq_agent.adapters.cli._main.complete_github_configuration", lambda *args, **kwargs: ProviderConfigurationResult(provider="github_copilot", model="gpt-5"))

    result = CliRunner().invoke(main, ["providers", "configure", "github_copilot", "--model", "gpt-5"])

    assert result.exit_code == 0
    assert "ABCD-EFGH" in result.stdout
    if shown:
        assert "2023-11-14T22:23:20+00:00" in result.stdout
        assert "Polling interval: 7 seconds" in result.stdout
    else:
        assert "Expires at:" not in result.stdout
        assert "Polling interval:" not in result.stdout
    assert "CANARY" not in result.output


def test_copilot_cli_renders_application_filtered_auth_diagnostic(monkeypatch):
    from fsq_agent.models import ConfigurationError

    def fail():
        raise ConfigurationError(
            "CANARY_EXCEPTION",
            context={"provider": "github_copilot", "diagnostic": {"stage": "device_code", "error_code": "http_error", "http_status": 503, "elapsed_seconds": 1.25, "token": "CANARY_TOKEN"}},
        )

    monkeypatch.setattr("fsq_agent.application.providers.request_github_copilot_device_code", fail)
    result = CliRunner().invoke(main, ["providers", "configure", "github_copilot"])

    assert result.exit_code == 4
    assert "stage: device_code" in result.stderr
    assert "error_code: http_error" in result.stderr
    assert "http_status: 503" in result.stderr
    assert "elapsed_seconds: 1.25" in result.stderr
    assert "CANARY" not in result.output


@pytest.mark.parametrize("options", [["--output", "json"], ["--output", "jsonl"], ["--non-interactive"]])
def test_copilot_machine_or_noninteractive_login_never_starts_authorization(monkeypatch, options):
    def forbidden():
        pytest.fail("Device flow must not start")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.request_github_device_code", forbidden)
    result = CliRunner().invoke(main, [*options, "providers", "configure", "github_copilot"])
    assert result.exit_code == 2


@pytest.mark.parametrize("timing", [{}, {"expires_in": 600}, {"interval": 2.75}, {"expires_in": 600, "interval": 7}])
def test_copilot_cli_distinguishes_native_timing_from_effective_defaults(monkeypatch, timing):
    import httpx

    from fsq_agent.providers import _github_copilot as copilot

    def handle(request):
        if str(request.url) == copilot.DEVICE_CODE_URL:
            return httpx.Response(200, json={"device_code": "PRIVATE_DEVICE", "user_code": "USER-CODE", "verification_uri": "https://github.com/login/device", **timing})
        return httpx.Response(200, json={"error": "expired_token"})

    waits = []
    monkeypatch.setattr(copilot.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(copilot.time, "sleep", waits.append)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(copilot.httpx, "post", client.post)
        result = CliRunner().invoke(main, ["providers", "configure", "github_copilot"])
    assert result.exit_code == 4
    assert ("Expires at:" in result.stdout) is ("expires_in" in timing)
    assert ("expires_at:" in result.stderr) is ("expires_in" in timing)
    if "interval" in timing:
        assert f"Polling interval: {timing['interval']} seconds" in result.stdout
        assert f"poll_interval_seconds: {timing['interval']}" in result.stderr
    else:
        assert "Polling interval:" not in result.stdout
        assert "poll_interval_seconds:" not in result.stderr
    assert waits == [max(1, int(timing.get("interval", 5)))]
    assert "PRIVATE_DEVICE" not in result.output


def test_copilot_cli_preserves_unknown_plan_classification(monkeypatch):
    import httpx

    from fsq_agent.providers import _github_copilot as copilot

    def handle(request):
        if str(request.url) == copilot.DEVICE_CODE_URL:
            return httpx.Response(200, json={"device_code": "PRIVATE_DEVICE", "user_code": "USER-CODE", "verification_uri": "https://github.com/login/device"})
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "PRIVATE_OAUTH"})
        return httpx.Response(200, json={"copilot_plan": "PRIVATE_UNSUPPORTED_PLAN"})

    monkeypatch.setattr(copilot.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(copilot.httpx, "post", client.post)
        monkeypatch.setattr(copilot.httpx, "get", client.get)
        result = CliRunner().invoke(main, ["providers", "configure", "github_copilot", "--model", "gpt-5"])
    assert result.exit_code == 4
    assert "stage: copilot_plan" in result.stderr
    assert "error_code: unknown_plan" in result.stderr
    assert "PRIVATE_" not in result.output
