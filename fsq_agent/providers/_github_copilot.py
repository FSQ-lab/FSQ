# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import math
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import httpx

from fsq_agent.config import Settings, UserProviderConfig, activate_github_copilot_provider, load_user_provider_config
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._client_config import ProviderClientConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DEVICE_CODE_URL = "https://github.com/login/device/code"
# This is a public OAuth endpoint URL, not a credential or secret token.
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_OAUTH_SCOPE = "read:user"
GITHUB_API_VERSION = "2022-11-28"
# This is a public Copilot service endpoint URL, not a credential or secret token.
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"  # noqa: S105
COPILOT_USER_URL = "https://api.github.com/copilot_internal/user"
COPILOT_AUTH_TIMEOUT_SECONDS = 20.0
_COPILOT_REFRESH_RETRY_DELAYS = (1.0, 2.0, 4.0)

COPILOT_BASE_URLS: dict[str, str] = {
    "individual": "https://api.githubcopilot.com",
    "business": "https://api.business.githubcopilot.com",
    "enterprise": "https://api.enterprise.githubcopilot.com",
}

COPILOT_EDITOR_HEADERS: dict[str, str] = {
    "editor-version": "vscode/1.99.0",
    "editor-plugin-version": "copilot-chat/0.38.2",
    "user-agent": "GitHubCopilotChat/0.38.2",
    "x-github-api-version": GITHUB_API_VERSION,
}

COPILOT_MODEL_HEADERS: dict[str, str] = {
    "copilot-integration-id": "vscode-chat",
    "editor-version": "vscode/1.99.0",
    "editor-plugin-version": "copilot-chat/0.38.2",
    "user-agent": "GitHubCopilotChat/0.38.2",
    "openai-intent": "conversation-agent",
}


_OAUTH_ERRORS = frozenset(
    {"authorization_pending", "slow_down", "expired_token", "access_denied", "unsupported_grant_type", "incorrect_client_credentials", "incorrect_device_code", "device_flow_disabled"}
)
_AUTH_FAILURE_REASONS = frozenset({"authentication", "access_denied", "rate_limited", "timeout", "network", "malformed_response", "storage", "internal"})


def _positive_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _oauth_error(value: object) -> str:
    return value if isinstance(value, str) and value in _OAUTH_ERRORS else "unknown_oauth_error"


class _UnknownCopilotPlanError(ConfigurationError):
    """Keep the owned plan distinction without widening runtime error context."""


class _AuthorizationDiagnostic:
    def __init__(self, stage: str) -> None:
        self.started = time.monotonic()
        self.stage = stage
        self.code = "internal_error"
        self.message = "GitHub Copilot authentication failed."
        self.facts: dict[str, object] = {}
        self.device: GitHubDeviceCode | None = None

    def observe(self, response: httpx.Response) -> None:
        status = response.status_code
        if type(status) is int and 100 <= status <= 599:
            self.facts["http_status"] = status
            if self.device is not None and self.stage == "oauth_poll":
                self.facts["last_http_status"] = status

    def fail(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        raise ConfigurationError(message) from None

    def context(self) -> dict[str, object]:
        facts = dict(self.facts)
        facts["elapsed_seconds"] = max(0.0, time.monotonic() - self.started)
        if self.device is not None and _positive_number(self.device.native_expires_at):
            facts["expires_at"] = self.device.native_expires_at
            facts["remaining_seconds"] = max(0.0, self.device.native_expires_at - time.time())
        return {"provider": "github_copilot", "diagnostic": {"stage": self.stage, "error_code": self.code, **facts}}


@contextmanager
def _initial_auth_diagnostic(stage: str):
    state = _AuthorizationDiagnostic(stage)
    try:
        yield state
    except Exception as exc:  # noqa: BLE001 - initial authorization exposes only bounded diagnostics.
        original = exc.context if isinstance(exc, ConfigurationError) else {}
        reason = original.get("reason")
        status = original.get("status_code")
        if isinstance(exc, httpx.HTTPStatusError):
            state.observe(exc.response)
            state.code = "http_error"
        elif isinstance(exc, httpx.TimeoutException):
            state.code = "timeout"
        elif isinstance(exc, httpx.HTTPError):
            state.code = "network_error"
        elif type(status) is int and 100 <= status <= 599:
            state.facts["http_status"] = status
            state.code = "http_error"
        elif isinstance(exc, _UnknownCopilotPlanError):
            state.code = "unknown_plan"
            state.message = "Unknown GitHub Copilot plan."
        elif isinstance(reason, str) and reason in _AUTH_FAILURE_REASONS:
            state.code = {"network": "network_error", "internal": "internal_error", "authentication": "access_denied", "rate_limited": "http_error"}.get(reason, reason)
        elif isinstance(exc, (ValueError, TypeError, OverflowError)):
            state.code = "malformed_response"
        context = state.context()
        # Discovery is shared with saved sessions; retain their classification facts.
        if isinstance(reason, str) and reason in _AUTH_FAILURE_REASONS:
            context["reason"] = reason
        if type(status) is int and 100 <= status <= 599:
            context["status_code"] = status
        errno = original.get("errno")
        if type(errno) is int and errno > 0:
            context["errno"] = errno
        raise ConfigurationError(state.message, context=context) from None


@dataclass(frozen=True)
class CopilotToken:
    token: str
    expires_at: float


@dataclass(frozen=True)
class CachedCopilotProviderToken:
    token: str
    expires_at: float
    plan: str


@dataclass(frozen=True)
class GitHubDeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float
    poll_interval_seconds: int
    native_expires_at: float | None = None
    native_poll_interval_seconds: float | None = None


@dataclass(frozen=True)
class GitHubCopilotAuthorization:
    github_access_token: str = field(repr=False)
    github_expires_at: float | None
    copilot_token: str = field(repr=False)
    copilot_expires_at: float
    plan: str


@dataclass(frozen=True)
class GitHubCopilotModel:
    id: str
    name: str


def build_github_copilot_client_config(settings: Settings) -> ProviderClientConfig:
    return refresh_github_copilot_client_config(settings)


def refresh_github_copilot_client_config(settings: Settings) -> ProviderClientConfig:
    cached_provider_token = _load_provider_token(settings.agent_runtime.provider_token)
    if cached_provider_token is not None:
        return _client_config_from_cached_provider_token(settings, cached_provider_token)
    github_payload = settings.agent_runtime.github_token
    github_token = _load_github_token(github_payload)
    if github_token is None:
        raise _copilot_error("GitHub Copilot authentication is not configured. Authenticate in Control Plane Config.", "authentication")
    user_config_root = settings.agent_runtime.user_config_root
    if user_config_root is None:
        raise _copilot_error("GitHub Copilot user configuration root is unavailable.", "internal")
    if not settings.agent_runtime.model.strip():
        raise _copilot_error("GitHub Copilot model name is required.", "invalid_candidate")
    plan = None
    retries = 0
    while True:
        try:
            if plan is None:
                plan = _get_copilot_plan(github_token)
            copilot_token = _get_copilot_token(github_token)
            break
        except ConfigurationError as exc:
            if retries == len(_COPILOT_REFRESH_RETRY_DELAYS):
                raise
            delay = _copilot_retry_delay(exc.__cause__, retries)
            if delay is None:
                raise
            time.sleep(delay)
            retries += 1
    provider_payload: dict[str, object] = {
        "token": copilot_token.token,
        "expires_at": copilot_token.expires_at,
        "plan": plan,
    }
    try:
        activate_github_copilot_provider(
            model=settings.agent_runtime.model,
            github_token=github_payload or {},
            provider_token=provider_payload,
            user_config_root=user_config_root,
        )
    except (ConfigurationError, OSError) as exc:
        reason = "internal" if isinstance(exc, ConfigurationError) and exc.context.get("reason") == "internal" else "storage"
        raise _copilot_error("GitHub Copilot credentials could not be saved.", reason, cause=exc) from exc
    settings.agent_runtime.provider_token = provider_payload
    return _client_config_from_cached_provider_token(
        settings,
        CachedCopilotProviderToken(token=copilot_token.token, expires_at=copilot_token.expires_at, plan=plan),
    )


def _client_config_from_cached_provider_token(
    settings: Settings,
    provider_token: CachedCopilotProviderToken,
) -> ProviderClientConfig:
    model = settings.agent_runtime.model.strip()
    if not model:
        raise _copilot_error("GitHub Copilot model name is required.", "invalid_candidate")
    return ProviderClientConfig(
        provider="github_copilot",
        model=model,
        api_key=provider_token.token,
        base_url=COPILOT_BASE_URLS[provider_token.plan],
        default_headers=COPILOT_MODEL_HEADERS,
        metadata={"endpoint_family": "github_copilot", "copilot_plan": provider_token.plan},
    )


def _load_github_token(data: dict[str, object] | None) -> str | None:
    if data is None:
        return None
    expires_at = data.get("expires_at")
    if expires_at is not None and (not _is_finite_expiry(expires_at) or time.time() >= expires_at - 60):
        return None
    token = data.get("access_token")
    return token if isinstance(token, str) and token.strip() else None


def _load_provider_token(data: dict[str, object] | None) -> CachedCopilotProviderToken | None:
    if data is None:
        return None
    expires_at = data.get("expires_at")
    if not _is_finite_expiry(expires_at) or time.time() >= expires_at - 60:
        return None
    token = data.get("token")
    plan = data.get("plan")
    if not isinstance(token, str) or not token.strip() or not isinstance(plan, str) or plan not in COPILOT_BASE_URLS:
        return None
    return CachedCopilotProviderToken(token=token, expires_at=expires_at, plan=plan)


def _is_finite_expiry(value: object) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and math.isfinite(value))


def _copilot_error(message: str, reason: str, *, cause: BaseException | None = None, status_code: int | None = None) -> ConfigurationError:
    context: dict[str, object] = {"provider": "github_copilot", "reason": reason}
    if isinstance(status_code, int) and not isinstance(status_code, bool) and 100 <= status_code <= 599:
        context["status_code"] = status_code
    seen: set[int] = set()
    for _ in range(8):
        if cause is None or id(cause) in seen:
            break
        seen.add(id(cause))
        if isinstance(cause, OSError):
            errno = cause.errno
            if type(errno) is int and errno > 0:
                context["errno"] = errno
                break
        cause = cause.__cause__ or cause.__context__
    return ConfigurationError(message, context=context)


def _copilot_retry_delay(cause: BaseException | None, retry_index: int) -> float | None:
    delay = _COPILOT_REFRESH_RETRY_DELAYS[retry_index]
    if isinstance(cause, httpx.TransportError):
        return delay
    if not isinstance(cause, httpx.HTTPStatusError):
        return None
    status = cause.response.status_code
    if status != 429 and not 500 <= status <= 599:
        return None
    retry_after = cause.response.headers.get("retry-after", "").strip()
    try:
        if re.fullmatch(r"[0-9]+", retry_after):
            seconds = retry_after.lstrip("0") or "0"
            requested_delay = 4 if len(seconds) > 1 else min(4, int(seconds))
        else:
            date = parsedate_to_datetime(retry_after)
            if date.tzinfo is None:
                return delay
            requested_delay = date.timestamp() - time.time()
    except (TypeError, ValueError, OverflowError):
        return delay
    if not math.isfinite(requested_delay) or requested_delay <= 0:
        return delay
    return max(delay, min(4.0, requested_delay))


def request_github_copilot_device_code() -> GitHubDeviceCode:
    with _initial_auth_diagnostic("device_code") as state:
        response = httpx.post(
            DEVICE_CODE_URL,
            data={"client_id": CLIENT_ID, "scope": GITHUB_OAUTH_SCOPE},
            headers={"Accept": "application/json"},
            timeout=COPILOT_AUTH_TIMEOUT_SECONDS,
        )
        state.observe(response)
        response.raise_for_status()
        data = response.json()
        try:
            return _parse_device_code(data)
        except ConfigurationError:
            state.fail(_oauth_error(data["error"]) if isinstance(data, dict) and "error" in data else "malformed_response", "GitHub device-code response was invalid.")


def _parse_device_code(data: object) -> GitHubDeviceCode:
    if not isinstance(data, dict):
        raise ConfigurationError("GitHub device-code response was invalid.")
    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri")
    interval = data.get("interval", 5)
    expires_in = data.get("expires_in", 900)
    if not all(isinstance(value, str) and value for value in (device_code, user_code, verification_uri)):
        raise ConfigurationError("GitHub device-code response was invalid.")
    if not _positive_number(interval):
        raise ConfigurationError("GitHub device-code response was invalid.")
    if not _positive_number(expires_in):
        raise ConfigurationError("GitHub device-code response was invalid.")
    expires_at = time.time() + float(expires_in)
    return GitHubDeviceCode(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        expires_at=expires_at,
        poll_interval_seconds=max(1, int(interval)),
        native_expires_at=expires_at if "expires_in" in data else None,
        native_poll_interval_seconds=interval if "interval" in data else None,
    )


def complete_github_copilot_device_flow(
    device_code: GitHubDeviceCode,
    *,
    cancel_requested: Callable[[], bool],
) -> GitHubCopilotAuthorization:
    with _initial_auth_diagnostic("oauth_poll") as state:
        state.device = device_code
        state.facts["poll_attempts"] = 0
        wait = device_code.poll_interval_seconds
        if _positive_number(device_code.native_poll_interval_seconds):
            state.facts["poll_interval_seconds"] = device_code.native_poll_interval_seconds

        def check_cancelled() -> None:
            if cancel_requested():
                state.fail("cancelled", "GitHub device-code authentication was cancelled.")

        while time.time() < device_code.expires_at:
            check_cancelled()
            time.sleep(wait)
            check_cancelled()
            state.facts["poll_attempts"] += 1
            state.facts.pop("http_status", None)
            response = httpx.post(
                ACCESS_TOKEN_URL,
                data={"client_id": CLIENT_ID, "device_code": device_code.device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                headers={"Accept": "application/json"},
                timeout=COPILOT_AUTH_TIMEOUT_SECONDS,
            )
            state.observe(response)
            response.raise_for_status()
            check_cancelled()
            data = response.json()
            if not isinstance(data, dict):
                state.fail("malformed_response", "GitHub device-code response was invalid.")
            github_token = data.get("access_token")
            if isinstance(github_token, str) and github_token.strip():
                github_expires_at = None
                if "expires_in" in data:
                    expires_in = data["expires_in"]
                    if not _positive_number(expires_in):
                        state.fail("malformed_response", "GitHub OAuth response had an invalid expiration.")
                    github_expires_at = time.time() + float(expires_in)
                    if not _positive_number(github_expires_at):
                        state.fail("malformed_response", "GitHub OAuth response had an invalid expiration.")
                state.stage = "copilot_plan"
                state.facts.pop("http_status", None)
                plan = _get_copilot_plan(github_token)
                check_cancelled()
                state.stage = "copilot_token"
                copilot_token = _get_copilot_token(github_token)
                check_cancelled()
                return GitHubCopilotAuthorization(
                    github_access_token=github_token,
                    github_expires_at=github_expires_at,
                    copilot_token=copilot_token.token,
                    copilot_expires_at=copilot_token.expires_at,
                    plan=plan,
                )
            if "error" not in data:
                state.fail("malformed_response", "GitHub device-code response was invalid.")
            error = _oauth_error(data.get("error"))
            state.facts["last_oauth_error"] = error
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                updated_wait = data.get("interval", wait + 5)
                wait = max(1, int(updated_wait)) if _positive_number(updated_wait) else wait + 5
                if "interval" in data and _positive_number(updated_wait):
                    state.facts["poll_interval_seconds"] = updated_wait
                else:
                    state.facts.pop("poll_interval_seconds", None)
                continue
            if error == "expired_token":
                state.fail(error, "GitHub device code expired. Please try again.")
            if error == "access_denied":
                state.fail(error, "GitHub device-code authorization was denied.")
            state.fail(error, "GitHub device-code OAuth failed.")
        state.fail("local_expiry", "GitHub device code expired. Please try again.")


def list_github_copilot_models(authorization: GitHubCopilotAuthorization) -> tuple[GitHubCopilotModel, ...]:
    with _initial_auth_diagnostic("model_discovery"):
        return _discover_copilot_models(authorization)


def _discover_copilot_models(authorization: GitHubCopilotAuthorization) -> tuple[GitHubCopilotModel, ...]:
    try:
        response = httpx.get(
            f"{COPILOT_BASE_URLS[authorization.plan]}/models",
            headers={"authorization": f"Bearer {authorization.copilot_token}", **COPILOT_MODEL_HEADERS},
            timeout=COPILOT_AUTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        reason = _http_failure_reason(exc.response.status_code)
        raise ConfigurationError(
            "GitHub Copilot model discovery failed.",
            context={"provider": "github_copilot", "reason": reason, "status_code": exc.response.status_code},
        ) from exc
    except httpx.TimeoutException as exc:
        raise ConfigurationError(
            "GitHub Copilot model discovery timed out.",
            context={"provider": "github_copilot", "reason": "timeout"},
        ) from exc
    except httpx.HTTPError as exc:
        raise ConfigurationError(
            "GitHub Copilot model discovery failed.",
            context={"provider": "github_copilot", "reason": "network"},
        ) from exc
    try:
        data = response.json()
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "GitHub Copilot model response was invalid.",
            context={"provider": "github_copilot", "reason": "malformed_response"},
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ConfigurationError(
            "GitHub Copilot model response was invalid.",
            context={"provider": "github_copilot", "reason": "malformed_response"},
        )
    models: list[GitHubCopilotModel] = []
    seen: set[str] = set()
    for candidate in data["data"]:
        model = _parse_github_copilot_model(candidate)
        if model is None or model.id.casefold() in seen:
            continue
        seen.add(model.id.casefold())
        models.append(model)
    return tuple(models)


def list_saved_github_copilot_models(
    *,
    user_config_root: str | Path | None = None,
) -> tuple[GitHubCopilotModel, ...]:
    try:
        configured = load_user_provider_config(user_config_root)
    except ConfigurationError as exc:
        raise _saved_session_error("authentication") from exc
    if configured.provider is None or configured.provider.type != "github_copilot":
        raise _saved_session_error("authentication")

    authorization = _saved_authorization(configured)
    if authorization is None:
        authorization = _refresh_saved_authorization(configured, user_config_root)
    try:
        return list_github_copilot_models(authorization)
    except ConfigurationError as exc:
        if exc.context.get("reason") != "authentication":
            raise
    refreshed = _refresh_saved_authorization(configured, user_config_root)
    return list_github_copilot_models(refreshed)


def _saved_authorization(configured: UserProviderConfig) -> GitHubCopilotAuthorization | None:
    cached = _load_provider_token(configured.provider_token)
    if cached is None:
        return None
    github = configured.github_token or {}
    expires_at = github.get("expires_at")
    return GitHubCopilotAuthorization(
        github_access_token="",
        github_expires_at=float(expires_at) if isinstance(expires_at, int | float) else None,
        copilot_token=cached.token,
        copilot_expires_at=cached.expires_at,
        plan=cached.plan,
    )


def _refresh_saved_authorization(
    configured: UserProviderConfig,
    user_config_root: str | Path | None,
) -> GitHubCopilotAuthorization:
    github_payload = configured.github_token
    github_token = _load_github_token(github_payload)
    if github_token is None:
        raise _saved_session_error("authentication")
    try:
        plan = _get_copilot_plan(github_token)
        token = _get_copilot_token(github_token)
    except ConfigurationError as exc:
        reason = exc.context.get("reason")
        if reason in {"authentication", "rate_limited", "timeout", "network", "malformed_response"}:
            raise
        raise _saved_session_error("internal") from exc
    authorization = GitHubCopilotAuthorization(
        github_access_token=github_token,
        github_expires_at=None,
        copilot_token=token.token,
        copilot_expires_at=token.expires_at,
        plan=plan,
    )
    activate_github_copilot_provider(
        model=configured.provider.model,
        github_token=github_payload or {},
        provider_token={"token": token.token, "expires_at": token.expires_at, "plan": plan},
        user_config_root=user_config_root,
    )
    return authorization


def _saved_session_error(reason: str) -> ConfigurationError:
    messages = {
        "authentication": "GitHub Copilot sign-in is required.",
        "internal": "GitHub Copilot saved session could not be used.",
    }
    return ConfigurationError(
        messages.get(reason, messages["internal"]),
        context={"provider": "github_copilot", "reason": reason},
    )


def _http_failure_reason(status_code: int) -> str:
    if status_code == 401:
        return "authentication"
    if status_code == 403:
        return "access_denied"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "network"
    return "internal"


def activate_github_copilot_authorization(
    authorization: GitHubCopilotAuthorization,
    *,
    model: str,
    user_config_root: str | Path | None = None,
) -> UserProviderConfig:
    normalized_model = model.strip()
    if not normalized_model:
        raise ConfigurationError("GitHub Copilot model name is required.")
    github_token: dict[str, object] = {"access_token": authorization.github_access_token}
    if authorization.github_expires_at is not None:
        github_token["expires_at"] = authorization.github_expires_at
    return activate_github_copilot_provider(
        model=normalized_model,
        github_token=github_token,
        provider_token={
            "token": authorization.copilot_token,
            "expires_at": authorization.copilot_expires_at,
            "plan": authorization.plan,
        },
        user_config_root=user_config_root,
    )


def _parse_github_copilot_model(candidate: object) -> GitHubCopilotModel | None:
    if not isinstance(candidate, dict):
        return None
    model_id = candidate.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    model_id = model_id.strip()
    version = re.match(r"^gpt-(\d+)(?:\.(\d+))?(?=-|$)", model_id, flags=re.IGNORECASE)
    if version is None or int(version.group(1)) < 5:
        return None
    name_value = candidate.get("name")
    name = name_value.strip() if isinstance(name_value, str) and name_value.strip() else model_id
    specialist_tokens = {
        "mini",
        "nano",
        "codex",
        "embedding",
        "audio",
        "realtime",
        "image",
        "search",
        "transcribe",
        "transcription",
        "tts",
    }
    tokens = set(re.findall(r"[a-z0-9]+", f"{model_id} {name}".casefold()))
    if tokens & specialist_tokens:
        return None
    # GPT reasoning/model docs inform these floors; Copilot endpoint support remains separate live evidence.
    if "chat" in tokens or ("pro" in tokens and (int(version[1]), int(version[2] or 0)) < (5, 2)):
        return None
    if candidate.get("model_picker_enabled") is False:
        return None
    capabilities = candidate.get("capabilities")
    if isinstance(capabilities, dict) and "type" in capabilities:
        capability_type = capabilities["type"]
        if not isinstance(capability_type, str) or capability_type.casefold() != "chat":
            return None
    return GitHubCopilotModel(id=model_id, name=name)


def _get_copilot_token(github_token: str) -> CopilotToken:
    try:
        response = httpx.get(
            COPILOT_TOKEN_URL,
            headers={
                "authorization": f"token {github_token}",
                "accept": "application/json",
                **COPILOT_EDITOR_HEADERS,
            },
            timeout=COPILOT_AUTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _copilot_error(
            "GitHub Copilot token exchange failed. Confirm the signed-in account has Copilot access.",
            _http_failure_reason(exc.response.status_code),
            cause=exc,
            status_code=exc.response.status_code,
        ) from exc
    except httpx.TimeoutException as exc:
        raise _copilot_error("GitHub Copilot token exchange timed out.", "timeout", cause=exc) from exc
    except httpx.HTTPError as exc:
        raise _copilot_error("GitHub Copilot token exchange failed.", "network", cause=exc) from exc
    try:
        data = response.json()
    except (TypeError, ValueError) as exc:
        raise _copilot_error("GitHub Copilot token response was invalid.", "malformed_response", cause=exc) from exc
    if not isinstance(data, dict):
        raise _copilot_error("GitHub Copilot token response was invalid.", "malformed_response")
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise _copilot_error("GitHub Copilot token response did not include a token.", "malformed_response")
    expires_at = data.get("expires_at") if "expires_at" in data else time.time() + 600
    if not _is_finite_expiry(expires_at):
        raise _copilot_error("GitHub Copilot token response had an invalid expiration.", "malformed_response")
    return CopilotToken(token=token, expires_at=expires_at)


def _get_copilot_plan(github_token: str) -> str:
    try:
        response = httpx.get(
            COPILOT_USER_URL,
            headers={
                "authorization": f"token {github_token}",
                "accept": "application/json",
                **COPILOT_EDITOR_HEADERS,
            },
            timeout=COPILOT_AUTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _copilot_error(
            "GitHub Copilot plan detection failed. Confirm the signed-in account has Copilot access.",
            _http_failure_reason(exc.response.status_code),
            cause=exc,
            status_code=exc.response.status_code,
        ) from exc
    except httpx.TimeoutException as exc:
        raise _copilot_error("GitHub Copilot plan detection timed out.", "timeout", cause=exc) from exc
    except httpx.HTTPError as exc:
        raise _copilot_error("GitHub Copilot plan detection failed.", "network", cause=exc) from exc
    try:
        data = response.json()
    except (TypeError, ValueError) as exc:
        raise _copilot_error("GitHub Copilot plan response was invalid.", "malformed_response", cause=exc) from exc
    if not isinstance(data, dict):
        raise _copilot_error("GitHub Copilot plan response was invalid.", "malformed_response")
    plan = data.get("copilot_plan")
    if not isinstance(plan, str) or plan not in COPILOT_BASE_URLS:
        raise _UnknownCopilotPlanError(
            "Unknown GitHub Copilot plan.",
            context={"provider": "github_copilot", "reason": "malformed_response"},
        )
    return plan
