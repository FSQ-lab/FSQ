# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from fsq_agent.ai_services import build_quick_test_intent_analyzer, validate_quick_test_web_url
from fsq_agent.application.contracts.shared import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode
from fsq_agent.application.contracts.workspace import QuickTestCheck, QuickTestChoice, QuickTestContextRequest, QuickTestContextResult, QuickTestPrepareRequest, QuickTestPrepareResult
from fsq_agent.application.doctor import PlatformDiagnosticContext, diagnose_platform_settings
from fsq_agent.config import Settings, load_platform_settings, load_quick_test_workspace_settings, load_windows_application_catalogue, validate_runtime_settings
from fsq_agent.environments import AndroidDeviceDiscovery, IOSDeviceDiscovery, PlatformRuntimeService
from fsq_agent.models import AndroidWorkspaceTarget, ConfigurationError, IOSWorkspaceTarget, MacOSWorkspaceTarget, WebWorkspaceTarget, WindowsWorkspaceTarget

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fsq_agent.models import ApplicationCandidate

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
_IOS_SIGNING_ACTION = (
    "Configure target.xcode_org_id and target.xcode_signing_id in the dedicated Quick Test Workspace's .fsq/config/config.ios.yaml. "
    "If absent, initialize iOS locally with fsq init --platform ios from the exact dedicated quick-test root (normally ~/.fsq/quick-test)."
)
_IOS_SIGNING_FIELDS = ("xcode_org_id", "xcode_signing_id", "updated_wda_bundle_id")
_IOS_EXPLICIT_BUNDLE_ACTION = "Put the exact installed application's iOS bundle ID in the goal; this host cannot verify installation on the selected physical device."


def _safe_url_diagnostic(url: str) -> str | None:
    # Never echo userinfo/credentials or control characters in diagnostic details.
    if "@" in url or any(ord(char) < 32 or ord(char) == 127 for char in url):
        return None
    return url[:256]


def _safe_candidate_diagnostics(candidates: Sequence[ApplicationCandidate], *, limit: int = 8) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for candidate in candidates[:limit]:
        entry: dict[str, str] = {"label": candidate.label}
        if candidate.candidate_id:
            entry["candidate_id"] = candidate.candidate_id
        if candidate.app_id:
            entry["app_id"] = candidate.app_id
        elif candidate.bundle_id:
            entry["bundle_id"] = candidate.bundle_id
        elif candidate.app_path is not None:
            entry["app_path"] = str(candidate.app_path)
        items.append(entry)
    return items


def _safe_choice_diagnostics(choices: Sequence[QuickTestChoice], *, limit: int = 8) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for choice in choices[:limit]:
        entry: dict[str, str] = {"id": choice.id, "label": choice.label}
        if choice.kind:
            entry["kind"] = choice.kind
        if choice.state:
            entry["state"] = choice.state
        items.append(entry)
    return items


def _error(
    kind: str,
    *,
    reason: str | None = None,
    stage: str | None = None,
    target_name: str | None = None,
    candidates: Sequence[ApplicationCandidate] | None = None,
    choice_candidates: Sequence[QuickTestChoice] | None = None,
    url: str | None = None,
    action: str | None = None,
) -> ApplicationError:
    message, default_action = _ERRORS[kind]
    details: dict[str, object] = {}
    if stage:
        details["stage"] = stage
    if target_name:
        details["target_name"] = target_name
    if reason:
        details["reason"] = reason
    if url:
        safe_url = _safe_url_diagnostic(url)
        if safe_url:
            details["url"] = safe_url
    if candidates:
        details["candidates"] = _safe_candidate_diagnostics(candidates)
    elif choice_candidates:
        details["candidates"] = _safe_choice_diagnostics(choice_candidates)
    return ApplicationError(
        code=ApplicationErrorCode(f"quick_test.{kind}"),
        category=ApplicationErrorCategory.UNAVAILABLE if kind == "persistence_failed" else ApplicationErrorCategory.REQUEST_VALIDATION,
        message=message,
        action=action or default_action,
        details=details or None,
    )


def get_quick_test_context(request: QuickTestContextRequest) -> QuickTestContextResult:
    try:
        settings = load_platform_settings(request.platform, user_config_root=request.user_config_root)
    except Exception:  # noqa: BLE001 -- never expose provider credentials or host probe output.
        settings = None
    return _context(request, settings)


def _context(request: QuickTestContextRequest | QuickTestPrepareRequest, settings: Settings | None) -> QuickTestContextResult:
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
    elif request.platform == "ios":
        choices = _ios_choices()
        if settings is not None:
            settings.harness.ios.udid = request.target_id
            if any(choice.id == request.target_id and choice.selectable and choice.kind == "device" for choice in choices):
                try:
                    _, _, configured = load_quick_test_workspace_settings("ios", None, request.user_config_root)
                except ConfigurationError:
                    # The shared signing fact reports missing/invalid configuration without input values.
                    pass
                else:
                    for field in _IOS_SIGNING_FIELDS:
                        setattr(settings.harness.ios, field, getattr(configured.harness.ios, field))
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
            sandbox=getattr(request, "sandbox", None),
            target_id=request.target_id if request.platform in {"android", "ios"} else None,
        ),
    )
    if request.platform == "ios":
        prerequisites = tuple(
            item.model_copy(update={"action": _IOS_SIGNING_ACTION}) if item.identifier == "wda_signing" and item.status in {"unavailable", "error"} else item for item in diagnosis.prerequisites
        )
        diagnosis = diagnosis.model_copy(update={"prerequisites": prerequisites})
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
        can_start=(
            diagnosis.preparation is not None
            and diagnosis.preparation.status == "ready"
            and (request.platform != "ios" or any(choice.id == request.target_id and choice.selectable for choice in choices))
        ),
        choices=tuple(choices),
        checks=tuple(checks),
        diagnosis=diagnosis,
    )


def _ios_choices() -> list[QuickTestChoice]:
    try:
        discovery = IOSDeviceDiscovery().discover()
    except Exception:  # noqa: BLE001 -- shared diagnosis owns the safe discovery failure.
        return []
    if discovery.error_code:
        return []
    choices = []
    for device in discovery.devices:
        selectable = (device.kind, device.state) in {("simulator", "booted"), ("device", "connected")}
        choices.append(QuickTestChoice(id=device.udid, label=device.name, kind=device.kind, state=device.state, selectable=selectable, status="ready" if selectable else "unavailable"))
    return choices


def _build_native_target(platform: str, candidate: ApplicationCandidate):
    if platform == "android":
        return AndroidWorkspaceTarget(app_id=candidate.app_id)
    if platform == "macos":
        return MacOSWorkspaceTarget(app_path=candidate.app_path, bundle_id=candidate.bundle_id)
    if platform == "ios":
        return IOSWorkspaceTarget(bundle_id=candidate.bundle_id, app_path=candidate.app_path)
    return WindowsWorkspaceTarget(app_path=candidate.app_path, window_title_re=candidate.window_title_re)


def _selected_candidates(inventory: Sequence[ApplicationCandidate], candidate_ids: Sequence[str]) -> list[ApplicationCandidate]:
    if not candidate_ids:
        return []
    by_id = {item.candidate_id: item for item in inventory if item.candidate_id}
    return [by_id[cid] for cid in candidate_ids if cid in by_id]


def prepare_quick_test(request: QuickTestPrepareRequest, *, check_cancelled: Callable[[], None] | None = None) -> QuickTestPrepareResult:
    cancel = check_cancelled or (lambda: None)
    cancel()
    address = None
    if request.platform == "web" and request.url is not None:
        try:
            address = validate_quick_test_web_url(request.url)
        except ValueError:
            raise _error("invalid_web_address", reason="invalid_explicit_url", url=request.url) from None
    try:
        settings = load_platform_settings(request.platform, user_config_root=request.user_config_root)
        context = _context(request, settings)
    except Exception:  # noqa: BLE001 -- configuration and host errors become safe prerequisite facts.
        raise _error("prerequisites", reason="context_unavailable") from None
    effective_target_id = request.target_id
    if request.platform in {"android", "ios", "web"} and effective_target_id is None:
        selectable = [choice for choice in context.choices if choice.selectable and choice.status == "ready"]
        if len(selectable) == 1:
            effective_target_id = selectable[0].id
            resolved_request = request.model_copy(update={"target_id": effective_target_id})
            try:
                settings = load_platform_settings(request.platform, user_config_root=request.user_config_root)
                context = _context(resolved_request, settings)
            except Exception:  # noqa: BLE001 -- configuration and host errors become safe prerequisite facts.
                raise _error("prerequisites", reason="context_unavailable") from None
        elif len(selectable) > 1:
            noun = "browser channels" if request.platform == "web" else "devices"
            raise _error(
                "prerequisites",
                reason="target_ambiguous",
                choice_candidates=selectable,
                action=f"Multiple available {request.platform} {noun} were found. Specify an explicit target_id.",
            )
        else:
            noun = "browser channel" if request.platform == "web" else "device"
            raise _error(
                "prerequisites",
                reason="target_unavailable",
                action=f"No available {request.platform} {noun} was found. Connect or start one, or check host prerequisites.",
            )
    if not context.can_start:
        if request.platform == "ios":
            signing = next((item for item in context.checks if item.id == "wda_signing" and item.status in {"unavailable", "error"}), None)
            if signing is not None:
                raise _error("prerequisites", reason="ios_wda_signing_required", action=signing.action)
        raise _error("prerequisites", reason="prerequisites_not_ready")
    physical_ios = request.platform == "ios" and any(choice.id == effective_target_id and choice.kind == "device" for choice in context.choices)
    if physical_ios:
        _check_ios_signing(settings, request.goal)
    cancel()

    runtime = PlatformRuntimeService()
    catalogue = None
    inventory_result = None
    installation_unverified = False
    if request.platform != "web":
        if request.platform == "windows":
            try:
                catalogue = load_windows_application_catalogue()
            except ConfigurationError:
                raise _error("discovery_failed", reason="windows_catalogue_invalid") from None
        inventory_result = runtime.list_applications(
            platform=request.platform,
            target_id=effective_target_id,
            windows_catalogue=catalogue,
            force_refresh=False,
        )
        installation_unverified = physical_ios and inventory_result.status == "unsupported"
        if inventory_result.status != "complete" and not installation_unverified:
            raise _error("discovery_failed", reason=inventory_result.code)
        cancel()

    def _analyze_once(candidates: Sequence[ApplicationCandidate] | None):
        try:
            options: dict[str, object] = {}
            if request.url is not None:
                options["url"] = address
            if candidates is not None:
                options["candidates"] = candidates
            return build_quick_test_intent_analyzer(settings).analyze(platform=request.platform, goal=request.goal, **options)
        except ValueError:
            if request.platform == "web":
                raise _error("invalid_web_address", reason="invalid_extracted_web_url") from None
            raise _error("inference_failed", reason="intent_analysis_failed") from None
        except Exception:  # noqa: BLE001 -- preserve failure, never return raw provider/model output.
            raise _error("inference_failed", reason="intent_analysis_failed") from None

    intent = _analyze_once(inventory_result.candidates if inventory_result is not None else None)
    cancel()

    # A cached miss earns one live refresh, except on physical iOS devices.
    if not physical_ios and inventory_result is not None and inventory_result.from_cache and not intent.sufficient and intent.reason == "application_not_found":
        refreshed = runtime.list_applications(
            platform=request.platform,
            target_id=effective_target_id,
            windows_catalogue=catalogue,
            force_refresh=True,
        )
        if refreshed.status != "complete":
            raise _error("discovery_failed", reason=refreshed.code)
        cancel()
        if refreshed.fingerprint != inventory_result.fingerprint:
            inventory_result = refreshed
            intent = _analyze_once(inventory_result.candidates)
            cancel()

    if not intent.sufficient:
        if installation_unverified and intent.reason in {"missing_application", "application_not_found", "ambiguous_application"}:
            raise _error("application_missing", reason="ios_physical_inventory_unsupported", action=_IOS_EXPLICIT_BUNDLE_ACTION)
        kind = {
            "insufficient": "insufficient",
            "missing_application": "application_missing",
            "application_not_found": "application_not_found",
            "ambiguous_application": "application_ambiguous",
        }[intent.reason]
        diag_candidates = _selected_candidates(inventory_result.candidates, intent.candidate_ids) if inventory_result is not None else []
        raise _error(
            kind,
            target_name=intent.target_name,
            reason=intent.explanation or intent.reason,
            candidates=diag_candidates or None,
        )

    requires_web_search_evidence = False
    if request.platform == "web":
        if request.url is not None:
            application = address
        elif intent.reason == "web_search":
            if not intent.target_name or intent.base_url is not None:
                raise _error("invalid_web_address", reason="invalid_web_search_decision")
            address = None
            application = intent.target_name
            requires_web_search_evidence = True
        else:
            if not intent.base_url:
                raise _error("invalid_web_address", target_name=intent.target_name, reason="missing_web_url")
            try:
                address = validate_quick_test_web_url(intent.base_url)
            except ValueError:
                raise _error(
                    "invalid_web_address",
                    target_name=intent.target_name,
                    reason="invalid_extracted_url",
                    url=intent.base_url,
                ) from None
            application = address
        paths = runtime.discover_web_executables(effective_target_id)
        if len(paths) != 1 or not runtime.web_executable_matches_channel(effective_target_id, paths[0]):
            raise _error("prerequisites", reason="browser_executable_unavailable")
        target = WebWorkspaceTarget(browser_channel=effective_target_id, browser_executable_path=paths[0])
        settings.harness.web.base_url = address
    else:
        if inventory_result is None:
            raise _error("discovery_failed", reason="inventory_unavailable")
        if intent.candidate_id is not None:
            by_id = {item.candidate_id: item for item in inventory_result.candidates if item.candidate_id}
            candidate = by_id.get(intent.candidate_id)
            if candidate is None:
                raise _error("inference_failed", target_name=intent.target_name, reason="unknown_candidate_id")
            try:
                target = _build_native_target(request.platform, candidate)
            except ValueError:
                raise _error("discovery_failed", target_name=intent.target_name or candidate.label, reason="invalid_candidate_target") from None
            application = candidate.label
        elif intent.explicit_reference is not None:
            if physical_ios and intent.explicit_reference.startswith("/"):
                raise _error("application_not_found", reason="ios_physical_bundle_id_required", action=_IOS_EXPLICIT_BUNDLE_ACTION)
            name = intent.target_name or intent.explicit_reference
            lookup = runtime.lookup_application(
                platform=request.platform,
                name=name,
                explicit_reference=intent.explicit_reference,
                target_id=effective_target_id,
            )
            if lookup.status != "complete":
                raise _error("discovery_failed", target_name=name, reason=lookup.code)
            if not lookup.candidates:
                raise _error(
                    "application_not_found",
                    target_name=name,
                    reason=intent.explanation or "explicit_reference_not_found",
                )
            if len(lookup.candidates) != 1:
                raise _error(
                    "application_ambiguous",
                    target_name=name,
                    reason="multiple_candidates_matched",
                    candidates=lookup.candidates,
                )
            candidate = lookup.candidates[0]
            try:
                target = _build_native_target(request.platform, candidate)
            except ValueError:
                raise _error("discovery_failed", target_name=name, reason="invalid_candidate_target") from None
            application = candidate.label
        else:
            raise _error("application_missing", target_name=intent.target_name, reason=intent.explanation or "missing_application")

    cancel()
    _bind_target(settings, target, effective_target_id)
    try:
        validate_runtime_settings(settings)
        available, _, _ = runtime.check_target_availability(settings)
    except Exception:  # noqa: BLE001 -- target checks never leak host or credential values.
        raise _error("prerequisites", target_name=application, reason="target_validation_failed") from None
    if not available:
        raise _error("prerequisites", target_name=application, reason="target_unavailable")
    cancel()
    name, revision, frozen = _load_workspace_settings(request, None if physical_ios else target)
    if physical_ios:
        _check_ios_signing(frozen, request.goal)
    elif request.platform == "ios":
        for field in _IOS_SIGNING_FIELDS:
            setattr(frozen.harness.ios, field, None)
    _bind_target(frozen, target, effective_target_id)
    if request.platform == "web":
        frozen.harness.web.base_url = address
    cancel()
    return QuickTestPrepareResult(
        workspace_name=name,
        root_path=frozen.workspace.root_dir,
        platform_revision=revision,
        target_id=effective_target_id or f"{request.platform}-app",
        target=target,
        application=application,
        settings=frozen,
        requires_web_search_evidence=requires_web_search_evidence,
        installation_unverified=installation_unverified,
    )


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
    elif isinstance(target, IOSWorkspaceTarget):
        settings.harness.ios.app_path = target.app_path
        settings.harness.ios.bundle_id = target.bundle_id
        settings.harness.ios.udid = target_id
    else:
        settings.harness.windows.app_path = target.app_path
        settings.harness.windows.window_title_re = target.window_title_re
        settings.harness.windows.launch_args = []


def _check_ios_signing(settings: Settings, goal: str) -> None:
    ios = settings.harness.ios
    if not ios.xcode_org_id or not ios.xcode_signing_id:
        raise _error("prerequisites", reason="ios_wda_signing_required", action=_IOS_SIGNING_ACTION)
    if any((value := getattr(ios, field)) and value in goal for field in _IOS_SIGNING_FIELDS):
        raise _error("insufficient", reason="ios_signing_in_goal", action="Remove WDA signing values from the goal and keep them in local configuration.")


def _has_io_cause(error: BaseException) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, OSError):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


def _load_workspace_settings(request, target):
    try:
        return load_quick_test_workspace_settings(request.platform, target, request.user_config_root)
    except ConfigurationError as error:
        if _has_io_cause(error):
            raise _error("persistence_failed", stage="workspace_persistence", reason="io_error") from None
        reason = error.context.get("workspace_failure")
        if isinstance(reason, str) and reason in {"identity_conflict", "identity_unavailable", "configuration_conflict", "unsafe_root"}:
            raise _error("workspace_conflict", stage="workspace_persistence", reason=reason) from None
        raise _error("persistence_failed", stage="workspace_persistence", reason="storage_error") from None
    except Exception as error:  # noqa: BLE001 -- storage and unexpected persistence failures must not masquerade as identity conflicts.
        raise _error("persistence_failed", stage="workspace_persistence", reason="io_error" if _has_io_cause(error) else "storage_error") from None
