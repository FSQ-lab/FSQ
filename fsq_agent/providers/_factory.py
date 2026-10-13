# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from dataclasses import dataclass
from typing import Literal

from fsq_agent.config import Settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._azure_openai import build_azure_openai_client_config
from fsq_agent.providers._deepseek import build_deepseek_client_config
from fsq_agent.providers._github_copilot import build_github_copilot_client_config, refresh_github_copilot_client_config
from fsq_agent.providers._google_gemini import build_google_gemini_client_config
from fsq_agent.providers._kimi import build_kimi_client_config
from fsq_agent.providers._openai import build_openai_client_config
from fsq_agent.providers._session import ModelProviderSession


class ModelProviderFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_session(self) -> ModelProviderSession:
        provider = self.settings.agent_runtime.provider
        if provider == "deepseek":
            return ModelProviderSession(build_deepseek_client_config(self.settings))
        if provider == "google_gemini":
            return ModelProviderSession(build_google_gemini_client_config(self.settings))
        if provider == "kimi":
            return ModelProviderSession(build_kimi_client_config(self.settings))
        if provider == "github_copilot":
            return ModelProviderSession(build_github_copilot_client_config(self.settings))
        if provider == "azure_openai":
            return ModelProviderSession(build_azure_openai_client_config(self.settings))
        if provider == "openai":
            return ModelProviderSession(build_openai_client_config(self.settings))
        from fsq_agent.models import ConfigurationError

        raise ConfigurationError("Model Provider is not configured. Add a Provider in Control Plane Config.")

    def refresh_session(self) -> ModelProviderSession:
        provider = self.settings.agent_runtime.provider
        if provider == "github_copilot":
            return ModelProviderSession(refresh_github_copilot_client_config(self.settings))
        return self.build_session()


def build_model_provider_session(settings: Settings) -> ModelProviderSession:
    return ModelProviderFactory(settings).build_session()


def prepare_model_provider_session(settings: Settings) -> ModelProviderSession:
    return ModelProviderFactory(settings).build_session()


def refresh_model_provider_session(settings: Settings) -> ModelProviderSession:
    return ModelProviderFactory(settings).refresh_session()


@dataclass(frozen=True)
class ProviderReadiness:
    ready: bool
    session_status: Literal["session_available", "sign_in_required", "temporarily_unavailable"]
    code: str
    message: str
    action: str


def check_provider_readiness(settings: Settings) -> ProviderReadiness:
    session = None
    try:
        session = prepare_model_provider_session(settings)
    except ConfigurationError as exc:
        reason = exc.context.get("reason")
        message = str(exc).casefold()
        sign_in = reason == "authentication" or "authentication is not configured" in message
        if sign_in:
            return ProviderReadiness(
                ready=False,
                session_status="sign_in_required",
                code="provider_sign_in_required",
                message="The saved Provider session requires sign-in.",
                action="Sign in again from Control Plane Settings.",
            )
        return ProviderReadiness(
            ready=False,
            session_status="temporarily_unavailable",
            code="provider_temporarily_unavailable",
            message="The saved Provider session is temporarily unavailable.",
            action="Retry without changing the saved Provider.",
        )
    except Exception:  # noqa: BLE001 - readiness returns a safe classified result.
        return ProviderReadiness(
            ready=False,
            session_status="temporarily_unavailable",
            code="provider_temporarily_unavailable",
            message="The saved Provider session is temporarily unavailable.",
            action="Retry without changing the saved Provider.",
        )
    finally:
        if session is not None:
            session.close_sync()
    return ProviderReadiness(
        ready=True,
        session_status="session_available",
        code="provider_session_available",
        message="Model Provider is ready for non-interactive use.",
        action="",
    )
