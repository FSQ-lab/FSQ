# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

from pydantic import ValidationError

from fsq_agent.application.contracts import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode, WorkspaceInitializeRequest, WorkspaceInitializeResult
from fsq_agent.config import add_workspace_platform as persist_workspace_platform
from fsq_agent.config import create_workspace as persist_workspace
from fsq_agent.config import inspect_registered_workspace, list_workspace_registry, load_registered_workspace, workspace_revision
from fsq_agent.config import update_workspace_platform as persist_workspace_platform_update
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.models import (
    AndroidWorkspaceTarget,
    ConfigurationError,
    MacOSWorkspaceTarget,
    WebWorkspaceTarget,
    WindowsWorkspaceTarget,
    WorkspacePlatformCreateInput,
)


def initialize_workspace(request: WorkspaceInitializeRequest) -> WorkspaceInitializeResult:
    target = resolve_workspace_target(request)
    try:
        selected = request.current_directory.expanduser().resolve()
        entries = list_workspace_registry(request.user_config_root)
        if request.name is not None:
            name = request.name.strip()
            entry = next((item for item in entries if item.name.casefold() == name.casefold()), None)
        else:
            entry = next((item for item in entries if item.root_path.expanduser().resolve() == selected), None)
            if entry is not None:
                name = entry.name
            else:
                base = selected.name
                registered_names = {item.name.casefold() for item in entries}
                name = base
                suffix = 2
                while name.casefold() in registered_names:
                    name = f"{base}-{suffix}"
                    suffix += 1
        if entry is None:
            status = persist_workspace(
                selected_path=selected,
                name=name,
                platforms=[WorkspacePlatformCreateInput(platform=request.platform, target=target, env=request.env)],
                user_config_root=request.user_config_root,
            )
            result_status = "initialized"
            result_name = status.name
            result_root = status.root_path
        else:
            status = inspect_registered_workspace(entry.name, request.user_config_root)
            if status.status == "unavailable":
                raise ConfigurationError("Registered workspace is unavailable.", context={"name": entry.name})  # noqa: TRY301
            config_path = entry.root_path / ".fsq" / "config" / f"config.{request.platform}.yaml"
            if not config_path.exists() and not config_path.is_symlink():
                added = persist_workspace_platform(name=entry.name, platform=request.platform, target=target, env=request.env, user_config_root=request.user_config_root)
                result_status, result_name, result_root = "platform_added", added.name, added.root_path
            else:
                current = load_registered_workspace(entry.name, request.platform, request.user_config_root)
                if current.target == target and current.env == request.env:
                    result_status, result_name, result_root = "unchanged", current.name, current.root_path
                elif not request.update_existing:
                    raise ConfigurationError(  # noqa: TRY301
                        "Workspace platform configuration differs; use --update-existing to replace target and env values."
                    )
                else:
                    updated = persist_workspace_platform_update(
                        name=current.name,
                        platform=current.platform,
                        target=target,
                        env=request.env,
                        expected_revision=workspace_revision(config_path),
                        user_config_root=request.user_config_root,
                    )
                    result_status, result_name, result_root = "updated", updated.name, updated.root_path
    except (ConfigurationError, ValidationError, ValueError) as exc:
        raise _configuration_error(exc) from exc
    browser_path = target.browser_executable_path if isinstance(target, WebWorkspaceTarget) else None
    return WorkspaceInitializeResult(
        status=result_status,
        name=result_name,
        root_path=result_root,
        platform=request.platform,
        driver_status="ready",
        browser_executable_path=browser_path,
    )


def resolve_workspace_target(request: WorkspaceInitializeRequest):
    """Check platform readiness and return one complete persistence-ready target."""
    runtime = PlatformRuntimeService()
    try:
        target = _target(request, runtime)
    except (ConfigurationError, ValidationError, ValueError) as exc:
        raise _configuration_error(exc) from exc
    check = runtime.check(request.platform)
    if not check.ready:
        raise ApplicationError(
            code=ApplicationErrorCode.ENVIRONMENT_UNAVAILABLE, category=ApplicationErrorCategory.UNAVAILABLE, message=check.message, action=check.action, details={"platform": request.platform}
        )
    return target


def create_workspace(*, selected_path: Path, name: str, platforms: list[WorkspacePlatformCreateInput | dict[str, object]], user_config_root: Path | None = None):
    resolved: list[WorkspacePlatformCreateInput] = []
    for platform in platforms:
        if isinstance(platform, WorkspacePlatformCreateInput):
            platform_name = platform.platform
            target_input = platform.target.model_dump()
            env = dict(platform.env)
        else:
            platform_name = str(platform["platform"])
            target_input = platform["target"]
            env = platform["env"]
            if not isinstance(target_input, dict) or not isinstance(env, dict):
                raise TypeError("Workspace target and environment must be objects.")
        request = _request_from_target(selected_path, platform_name, target_input, env)  # type: ignore[arg-type]
        target = resolve_workspace_target(request)
        resolved.append(WorkspacePlatformCreateInput(platform=platform_name, target=target, env=env))  # type: ignore[arg-type]
    return persist_workspace(selected_path=selected_path, name=name, platforms=resolved, user_config_root=user_config_root)


def add_workspace_platform(*, name: str, root_path: Path, platform: str, target: dict[str, object], env: dict[str, str], user_config_root: Path | None = None):
    resolved = resolve_workspace_target(_request_from_target(root_path, platform, target, env))
    return persist_workspace_platform(name=name, platform=platform, target=resolved, env=env, user_config_root=user_config_root)


def update_workspace_platform(*, name: str, root_path: Path, platform: str, target: dict[str, object], env: dict[str, str], expected_revision: str, user_config_root: Path | None = None):
    resolved = resolve_workspace_target(_request_from_target(root_path, platform, target, env))
    return persist_workspace_platform_update(name=name, platform=platform, target=resolved, env=env, expected_revision=expected_revision, user_config_root=user_config_root)


def _request_from_target(root: Path, platform: str, target: dict[str, object], env: dict[str, str]) -> WorkspaceInitializeRequest:
    return WorkspaceInitializeRequest.model_validate({"current_directory": root, "platform": platform, "env": env, **target})


def _configuration_error(exc: ConfigurationError | ValidationError | ValueError) -> ApplicationError:
    details = exc.context if isinstance(exc, ConfigurationError) else {}
    return ApplicationError(
        code=ApplicationErrorCode.CONFIGURATION_INVALID,
        category=ApplicationErrorCategory.CONFIGURATION,
        message=str(exc).splitlines()[0],
        action="Correct the workspace target and retry.",
        details=details,
    )


def _target(request: WorkspaceInitializeRequest, runtime: PlatformRuntimeService):
    _validate_platform_options(request)
    if request.platform == "android":
        return AndroidWorkspaceTarget(app_id=request.app_id)
    if request.platform == "web":
        if request.browser_channel is None:
            raise ValueError("Web workspace initialization requires --browser-channel.")
        path = request.browser_executable_path
        if path is None:
            candidates = runtime.discover_web_executables(request.browser_channel)
            if not candidates:
                raise ValueError("No compatible Web browser executable was found; install the selected channel or pass --browser-executable-path.")
            if len(candidates) != 1:
                raise ValueError("Multiple compatible Web browser executables were found; pass --browser-executable-path.")
            path = candidates[0]
        resolved = Path(path).expanduser().resolve()
        _validate_local_file(resolved, "Web browser executable path", executable=True)
        if not runtime.web_executable_matches_channel(request.browser_channel, resolved):
            raise ValueError("Web browser executable path does not match the selected channel.")
        return WebWorkspaceTarget(browser_channel=request.browser_channel, browser_executable_path=resolved)
    if request.platform == "windows":
        target = WindowsWorkspaceTarget(app_path=request.app_path, window_title_re=request.window_title_re, launch_args=request.launch_args or "")
        resolved = target.app_path.expanduser().resolve()
        _validate_local_file(resolved, "Windows application path", executable=True)
        return target.model_copy(update={"app_path": resolved})
    target = MacOSWorkspaceTarget(bundle_id=request.bundle_id, app_path=request.app_path)
    if target.app_path is not None:
        resolved = target.app_path.expanduser().resolve()
        if not resolved.exists() or not ((resolved.is_dir() and resolved.suffix.casefold() == ".app") or resolved.is_file()):
            raise ValueError("macOS application path must identify an existing app bundle or executable.")
        target = target.model_copy(update={"app_path": resolved})
    return target


def _validate_local_file(path: Path, description: str, *, executable: bool) -> None:
    if not path.exists():
        raise ValueError(f"{description} does not exist.")
    if not path.is_file():
        raise ValueError(f"{description} must be a file.")
    if executable and not __import__("os").access(path, __import__("os").X_OK):
        raise ValueError(f"{description} must be executable.")


def _validate_platform_options(request: WorkspaceInitializeRequest) -> None:
    supplied = {
        "app_id": request.app_id,
        "browser_channel": request.browser_channel,
        "browser_executable_path": request.browser_executable_path,
        "app_path": request.app_path,
        "window_title_re": request.window_title_re,
        "launch_args": request.launch_args,
        "bundle_id": request.bundle_id,
    }
    allowed = {
        "android": {"app_id"},
        "web": {"browser_channel", "browser_executable_path"},
        "windows": {"app_path", "window_title_re", "launch_args"},
        "macos": {"app_path", "bundle_id"},
    }[request.platform]
    invalid = sorted(name for name, value in supplied.items() if value is not None and name not in allowed)
    if invalid:
        options = ", ".join(f"--{name.replace('_', '-')}" for name in invalid)
        raise ValueError(f"{options} cannot be used with --platform {request.platform}.")
