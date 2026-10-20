# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, get_args

from fsq_agent.ai_services import QuickTestWebAddressParser, build_quick_test_intent_analyzer
from fsq_agent.application.contracts.shared import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode
from fsq_agent.application.contracts.workspace import QuickTestCheck, QuickTestChoice, QuickTestContextRequest, QuickTestContextResult, QuickTestPrepareRequest, QuickTestPrepareResult
from fsq_agent.application.doctor import PlatformDiagnosticContext, diagnose_platform_settings
from fsq_agent.config import (
    Settings,
    add_workspace_platform,
    create_workspace_at_root,
    inspect_registered_workspace,
    list_workspace_registry,
    load_platform_settings,
    load_registered_workspace,
    load_workspace_platform_settings,
    update_workspace_platform,
    validate_runtime_settings,
    workspace_revision,
)
from fsq_agent.environments import AndroidDeviceDiscovery, PlatformRuntimeService
from fsq_agent.models import AndroidWorkspaceTarget, ConfigurationError, MacOSWorkspaceTarget, WebWorkspaceTarget, WindowsWorkspaceTarget, WorkspacePlatformCreateInput

if TYPE_CHECKING:
    from collections.abc import Callable

_ERRORS = {
    "insufficient": ("The goal does not supply sufficient test intent.", "State an action and a checkable outcome."),
    "application_missing": ("The goal does not identify an application.", "Identify the intended application in the current goal."),
    "application_not_found": ("No matching application was found in supported local discovery.", "Use an installed application's exact identity or path in the goal."),
    "application_ambiguous": ("The goal matches multiple applications.", "Use the intended application's exact identity or path in a new goal."),
    "invalid_web_address": ("The goal contains an invalid or unresolved Web address.", "Use valid HTTP(S) addresses, or state a clear address-free search goal."),
    "discovery_failed": ("Application discovery could not be completed.", "Check the selected host/device and use a supported explicit application identity."),
    "inference_failed": ("Quick Test intent analysis could not be completed.", "Check Provider availability and submit a new request."),
    "prerequisites": ("Quick Test prerequisites are not ready.", "Check Provider, platform, and device or browser readiness."),
    "workspace_conflict": ("The dedicated Quick Test Workspace is conflicting or unavailable.", "Inspect the quick-test Workspace in Case Manager; no alternate Workspace was used."),
    "persistence_failed": ("The dedicated Quick Test Workspace could not be persisted.", "Check local storage availability and permissions, then retry."),
}


def _error(kind: str, *, reason: str | None = None) -> ApplicationError:
    message, action = _ERRORS[kind]
    return ApplicationError(
        code=ApplicationErrorCode(f"quick_test.{kind}"),
        category=ApplicationErrorCategory.UNAVAILABLE if kind == "persistence_failed" else ApplicationErrorCategory.REQUEST_VALIDATION,
        message=message,
        action=action,
        details={"stage": "workspace_persistence", "reason": reason} if reason else None,
    )


def get_quick_test_context(request: QuickTestContextRequest) -> QuickTestContextResult:
    try:
        settings = load_platform_settings(request.platform, user_config_root=request.user_config_root)
    except Exception:  # noqa: BLE001 -- never expose provider credentials or host probe output.
        settings = None
    return _context(request, settings)


def _context(request: QuickTestContextRequest, settings: Settings | None) -> QuickTestContextResult:
    runtime = PlatformRuntimeService()
    choices = []
    if settings is not None and request.platform == "android":
        settings.harness.android.serial = request.target_id
        try:
            discovery = AndroidDeviceDiscovery().discover()
            choices.extend(
                QuickTestChoice(id=device.serial, label=device.metadata.get("model", device.serial), selectable=device.state == "device", status="ready" if device.state == "device" else "unavailable")
                for device in discovery.devices
            )
        except Exception:  # noqa: BLE001 -- shared diagnosis supplies the failure facts; choices are only selection data.
            choices = []
    elif settings is not None and request.platform == "web":
        settings.harness.web.browser_executable_path = None
        if request.target_id is not None:
            settings.harness.web.channel = request.target_id
        for channel in get_args(WebWorkspaceTarget.model_fields["browser_channel"].annotation):
            try:
                paths = runtime.discover_web_executables(channel)
                valid = len(paths) == 1 and runtime.web_executable_matches_channel(channel, paths[0])
                choices.append(QuickTestChoice(id=channel, label=channel, selectable=valid, status="ready" if valid else "unavailable"))
                if valid and channel == request.target_id:
                    settings.harness.web.browser_executable_path = paths[0]
            except Exception:  # noqa: BLE001 -- isolate channel discovery.
                choices.append(QuickTestChoice(id=channel, label=channel, selectable=False, status="error"))
    diagnosis = diagnose_platform_settings(
        settings,
        context=PlatformDiagnosticContext(
            platform=request.platform,
            application_pending=True,
            workspace_pending=True,
            require_explicit_selection=True,
            target_id=request.target_id if request.platform == "android" else None,
        ),
    )
    # Retain the original compact fields as a compatibility projection, not a
    # second diagnostic graph or eligibility calculation.
    checks = [
        QuickTestCheck(id=name, status=detail.status, message=detail.message or "", action=detail.action or "")
        for name in type(diagnosis.checks).model_fields
        for detail in (getattr(diagnosis.checks, name),)
    ]
    checks.extend(QuickTestCheck(id=item.identifier, status=item.status, message=item.message, action=item.action or "") for item in diagnosis.prerequisites)
    if diagnosis.identification is not None:
        detail = diagnosis.identification
        checks.append(QuickTestCheck(id="application", status=detail.status, message=detail.message or "", action=detail.action or ""))
    return QuickTestContextResult(
        platform=request.platform,
        target_id=request.target_id,
        can_start=diagnosis.preparation is not None and diagnosis.preparation.status == "ready",
        choices=tuple(choices),
        checks=tuple(checks),
        diagnosis=diagnosis,
    )


def prepare_quick_test(request: QuickTestPrepareRequest, *, check_cancelled: Callable[[], None] | None = None) -> QuickTestPrepareResult:
    cancel = check_cancelled or (lambda: None)
    cancel()
    address = None
    addresses = ()
    if request.platform == "web":
        if request.url is not None:
            try:
                address = QuickTestWebAddressParser().validate(request.url)
            except ValueError:
                raise _error("invalid_web_address") from None
    try:
        settings = load_platform_settings(request.platform, user_config_root=request.user_config_root)
        context = _context(request, settings)
    except Exception:  # noqa: BLE001 -- configuration and host errors become safe prerequisite facts.
        raise _error("prerequisites") from None
    if not context.can_start:
        raise _error("prerequisites")
    cancel()
    if request.platform == "web" and request.url is None:
        addresses = _web_addresses(request.goal)
    try:
        options = {"url": request.url} if request.url is not None else {}
        intent = build_quick_test_intent_analyzer(settings).analyze(platform=request.platform, goal=request.goal, **options)
    except Exception:  # noqa: BLE001 -- preserve failure, never return raw provider/model output.
        raise _error("inference_failed") from None
    cancel()
    if not intent.sufficient:
        raise _error({"insufficient": "insufficient", "missing_application": "application_missing", "ambiguous_application": "application_ambiguous"}[intent.reason])
    runtime = PlatformRuntimeService()
    requires_web_search_evidence = False
    if request.platform == "web":
        if request.url is not None:
            application = address
        elif addresses:
            if intent.explicit_reference not in addresses:
                raise _error("invalid_web_address")
            address = intent.explicit_reference
            application = address
        else:
            if intent.reason != "web_search" or not intent.application_name or intent.explicit_reference is not None or intent.inferred_hint is not None:
                raise _error("invalid_web_address")
            address = None
            application = intent.application_name
            requires_web_search_evidence = True
        paths = runtime.discover_web_executables(request.target_id)
        if len(paths) != 1 or not runtime.web_executable_matches_channel(request.target_id, paths[0]):
            raise _error("prerequisites")
        target = WebWorkspaceTarget(browser_channel=request.target_id, browser_executable_path=paths[0])
        settings.harness.web.base_url = address
    else:
        name = intent.application_name or intent.explicit_reference
        if not name:
            raise _error("application_missing")
        lookup = runtime.lookup_application(platform=request.platform, name=name, explicit_reference=intent.explicit_reference, target_id=request.target_id, inferred_hint=intent.inferred_hint)
        if lookup.status != "complete":
            raise _error("discovery_failed")
        if not lookup.candidates:
            raise _error("application_not_found")
        if len(lookup.candidates) != 1:
            raise _error("application_ambiguous")
        candidate = lookup.candidates[0]
        try:
            if request.platform == "android":
                target = AndroidWorkspaceTarget(app_id=candidate.app_id)
            elif request.platform == "macos":
                target = MacOSWorkspaceTarget(app_path=candidate.app_path, bundle_id=candidate.bundle_id)
            else:
                target = WindowsWorkspaceTarget(app_path=candidate.app_path)
        except ValueError:
            raise _error("discovery_failed") from None
        application = candidate.label
    cancel()
    _bind_target(settings, target, request.target_id)
    try:
        validate_runtime_settings(settings)
        available, _, _ = runtime.check_target_availability(settings)
    except Exception:  # noqa: BLE001 -- target checks never leak host or credential values.
        raise _error("prerequisites") from None
    if not available:
        raise _error("prerequisites")
    cancel()
    root, name, revision, frozen = _persist(request, target)
    # Loading never authorizes a changed target: _persist verifies target, identity and revision.
    _bind_target(frozen, target, request.target_id)
    if request.platform == "web":
        frozen.harness.web.base_url = address
    cancel()
    return QuickTestPrepareResult(
        workspace_name=name,
        root_path=root,
        platform_revision=revision,
        target_id=request.target_id if request.platform in {"android", "web"} else f"{request.platform}-app",
        target=target,
        application=application,
        settings=frozen.model_copy(deep=True),
        requires_web_search_evidence=requires_web_search_evidence,
    )


def _web_addresses(goal: str) -> tuple[str, ...]:
    try:
        return QuickTestWebAddressParser().extract(goal)
    except ValueError:
        raise _error("invalid_web_address") from None


def _bind_target(settings, target, target_id):
    if isinstance(target, AndroidWorkspaceTarget):
        settings.harness.android.app_id = target.app_id
        settings.harness.android.serial = target_id
    elif isinstance(target, WebWorkspaceTarget):
        settings.harness.web.channel = target.browser_channel
        settings.harness.web.browser_executable_path = target.browser_executable_path
    elif isinstance(target, MacOSWorkspaceTarget):
        settings.harness.macos.app_path = target.app_path
        settings.harness.macos.bundle_id = target.bundle_id
    else:
        settings.harness.windows.app_path = target.app_path
        settings.harness.windows.window_title_re = target.window_title_re
        settings.harness.windows.launch_args = []


class _WorkspaceConflictError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _has_io_cause(error: BaseException) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, OSError):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


def _persist(request, target):
    try:
        return _persist_target(request, target)
    except _WorkspaceConflictError as error:
        raise _error("workspace_conflict", reason=error.reason) from None
    except ConfigurationError as error:
        if _has_io_cause(error):
            raise _error("persistence_failed", reason="io_error") from None
        if error.context.get("error_code") == "workspace_conflict":
            raise _error("workspace_conflict", reason="revision_conflict") from None
        reason = error.context.get("workspace_failure")
        if isinstance(reason, str) and reason in {"identity_conflict", "identity_unavailable", "configuration_conflict", "unsafe_root"}:
            raise _error("workspace_conflict", reason=reason) from None
        raise _error("persistence_failed", reason="storage_error") from None
    except Exception as error:  # noqa: BLE001 -- storage and unexpected persistence failures must not masquerade as identity conflicts.
        raise _error("persistence_failed", reason="io_error" if _has_io_cause(error) else "storage_error") from None


def _persist_target(request, target):
    user_root = (request.user_config_root or Path.home() / ".fsq").expanduser()
    root = user_root / "quick-test"
    if not user_root.is_absolute() or any(path.is_symlink() for path in (root, *root.parents)):
        raise _WorkspaceConflictError("unsafe_root")
    root = root.resolve()
    entries = list_workspace_registry(user_root)
    matching = [entry for entry in entries if entry.name.casefold() == "quick-test" or entry.root_path.resolve() == root]
    if any(entry.name.casefold() != "quick-test" or entry.root_path.resolve() != root for entry in matching):
        raise _WorkspaceConflictError("identity_conflict")
    name = matching[0].name if matching else "quick-test"
    config_path = root / ".fsq/config" / f"config.{request.platform}.yaml"
    if not matching:
        create_workspace_at_root(root_path=root, name=name, platforms=[WorkspacePlatformCreateInput(platform=request.platform, target=target, env={})], user_config_root=user_root)
    else:
        if inspect_registered_workspace(name, user_root).status == "unavailable":
            raise _WorkspaceConflictError("identity_unavailable")
        if config_path.exists() or config_path.is_symlink():
            revision = workspace_revision(config_path)
            current = load_registered_workspace(name, request.platform, user_root)
            if current.root_path.resolve() != root:
                raise _WorkspaceConflictError("identity_conflict")
            if current.target != target:
                update_workspace_platform(name=name, platform=request.platform, target=target, env=current.env, expected_revision=revision, user_config_root=user_root)
            elif workspace_revision(config_path) != revision:
                raise _WorkspaceConflictError("revision_conflict")
        else:
            add_workspace_platform(name=name, platform=request.platform, target=target, env={}, user_config_root=user_root)
    revision = workspace_revision(config_path)
    current = load_registered_workspace(name, request.platform, user_root)
    frozen = load_workspace_platform_settings(root, request.platform, user_config_root=user_root)
    if current.root_path.resolve() != root or current.target != target or workspace_revision(config_path) != revision:
        raise _WorkspaceConflictError("revision_conflict")
    # A concurrent change during loading must not silently replace validated target.
    comparison = frozen.model_copy(deep=True)
    _bind_target(comparison, target, None)
    if frozen.harness != comparison.harness:
        raise _WorkspaceConflictError("revision_conflict")
    return root, name, revision, frozen
