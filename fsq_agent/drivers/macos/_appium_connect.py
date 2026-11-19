# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import logging
import os
import re
import socket
import time
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from itertools import pairwise
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, TypeVar
from xml.etree import ElementTree

from pydantic import ValidationError

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _macos_driver_tool
from fsq_agent.drivers.macos._elements import candidate
from fsq_agent.drivers.macos._visual_inspection import _run_visual_inspection
from fsq_agent.models import (
    ConfigurationError,
    MacOSAssertElementsOrderParams,
    MacOSAssertNotVisibleParams,
    MacOSAssertStateParams,
    MacOSAssertVisibleParams,
    MacOSAssertWithAIParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragEndpoint,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSInspectElementParams,
    MacOSInspectWithAIParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSLocator,
    MacOSPoint,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
    MacOSScrollOnParams,
    MacOSTakeScreenshotParams,
    MacOSTypeTextParams,
    MacOSUiSnapshotParams,
    ToolExecutionError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from pydantic import BaseModel

_T = TypeVar("_T")

DEFAULT_MACOS_PAGE_SOURCE_MAX_DEPTH = 12
DEFAULT_MACOS_ACTION_TIMEOUT_SECONDS = 10
DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS = 900.0
DEFAULT_MACOS_SNAPSHOT_SOURCE_PREVIEW_CHARS = 2000
DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS = 50
DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS = 15.0
DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS = 10.0
DEFAULT_IDLE_ENSURE_ALIVE_SECONDS = 45.0
DEFAULT_ENSURE_ALIVE_TIMEOUT_SECONDS = 25.0
MAX_PORT_BIND_ATTEMPTS = 3

MACOS_STATE_ASSERTION_FIELDS = ("enabled", "focused", "selected", "hittable", "displayed")
MACOS_MASK_GLYPHS = frozenset({"*", "•", "●"})
MACOS_MUTABLE_TEXT_SELECTOR_TOKEN = re.compile(r"\b(?:value|label|title|name)\b")
MACOS_SESSION_STATES = frozenset({"creating", "active", "uncertain", "disposing", "disposed", "closing", "closed"})
MACOS_SAFE_BACKEND_CODES = frozenset(
    {
        "invalid_argument",
        "invalid_selector",
        "command_failed",
        "operation_timeout",
        "condition_timeout",
        "operation_cancelled",
        "target_not_found",
        "target_ambiguous",
        "target_not_displayed",
        "target_not_enabled",
        "target_stale",
        "session_not_active",
        "session_invalid",
        "session_lost",
        "session_closed",
        "session_uncertain",
        "session_disposed",
        "session_busy",
        "session_create_unknown",
        "session_quit_unknown",
        "execution_unknown",
        "response_unknown",
        "connection_unavailable",
        "connection_closed",
        "connection_closing",
        "cleanup_failed",
        "source_settings_invalid",
        "source_invalid",
        "unsupported_operation",
        "unsupported_platform",
        "invalid_response",
        "pointer_mapping_unavailable",
        "invalid_visual_response",
        "frame_not_single",
        "observation_too_large",
    }
)
MACOS_SAFE_COMPLETED_STEPS = frozenset(
    {
        "target_resolved",
        "target_displayed",
        "target_enabled",
        "action_completed",
        "settings_changed",
        "settings_restored",
        "attributes_read",
        "display_size_read",
        "session_deleted",
        "observation_built",
        "source_parsed",
    }
)


class _AmbiguousResolutionError(Exception):
    def __init__(self, candidates: list[dict[str, object]], *, cause: Exception | None = None) -> None:
        super().__init__("macOS locator matches multiple elements; refine the locator.")
        self.candidates = candidates
        self.cause = cause


MACOS_KEY_MODIFIER_FLAGS = {
    "CAPS_LOCK": 1 << 0,
    "SHIFT": 1 << 1,
    "CONTROL": 1 << 2,
    "OPTION": 1 << 3,
    "ALT": 1 << 3,
    "COMMAND": 1 << 4,
    "FUNCTION": 1 << 5,
}
MACOS_KEY_NAMES = {
    "BACKSPACE": "XCUIKeyboardKeyDelete",
    "DELETE": "XCUIKeyboardKeyDelete",
    "FORWARD_DELETE": "XCUIKeyboardKeyForwardDelete",
    "DOWN": "XCUIKeyboardKeyDownArrow",
    "END": "XCUIKeyboardKeyEnd",
    "ENTER": "XCUIKeyboardKeyReturn",
    "ESC": "XCUIKeyboardKeyEscape",
    "ESCAPE": "XCUIKeyboardKeyEscape",
    "HOME": "XCUIKeyboardKeyHome",
    "LEFT": "XCUIKeyboardKeyLeftArrow",
    "PAGEDOWN": "XCUIKeyboardKeyPageDown",
    "PAGE_DOWN": "XCUIKeyboardKeyPageDown",
    "PAGEUP": "XCUIKeyboardKeyPageUp",
    "PAGE_UP": "XCUIKeyboardKeyPageUp",
    "RETURN": "XCUIKeyboardKeyReturn",
    "RIGHT": "XCUIKeyboardKeyRightArrow",
    "SPACE": " ",
    "TAB": "XCUIKeyboardKeyTab",
    "UP": "XCUIKeyboardKeyUpArrow",
    "CLEAR": "XCUIKeyboardKeyClear",
    "HELP": "XCUIKeyboardKeyHelp",
    "CAPS_LOCK": "XCUIKeyboardKeyCapsLock",
    **{f"F{index}": f"XCUIKeyboardKeyF{index}" for index in range(1, 20)},
}

DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS = frozenset(
    {
        "identifier",
        "name",
        "label",
        "value",
        "title",
        "type",
        "role",
        "enabled",
        "visible",
        "selected",
        "x",
        "y",
        "width",
        "height",
    }
)
MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS = frozenset({"name", "label", "value", "title", "placeholderValue"})
MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS = frozenset({"identifier", "name", "label", "value", "title"})
MACOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS = frozenset({*MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS, "type", "role"})
MACOS_SNAPSHOT_STATE_DEFAULTS = {"enabled": "true", "visible": "true", "selected": "false"}
MACOS_INTERACTIVE_SHORT_TYPES = frozenset(
    {
        "Button",
        "CheckBox",
        "RadioButton",
        "PopUpButton",
        "ComboBox",
        "TextField",
        "SecureTextField",
        "TextArea",
        "Slider",
        "Switch",
        "Link",
    }
)


def _is_strict_int(value: object) -> bool:
    return type(value) is int


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def _predicate_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve_host_executable(name: str) -> Path | None:
    raw_path = os.environ.get("PATH", "")
    for entry in raw_path.split(os.pathsep)[:64]:
        if not entry:
            continue
        candidate_dir = Path(entry)
        if not candidate_dir.is_absolute():
            continue
        candidate_path = candidate_dir / name
        try:
            resolved = candidate_path.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return resolved
        except OSError:
            continue
    return None


def _resolve_managed_local_settings(local_settings_cls: type[Any]) -> Any:
    return local_settings_cls(
        node_executable=_resolve_host_executable("node"),
        npm_executable=_resolve_host_executable("npm"),
    )


def _resolve_app_bundle_path(app_path: str) -> Path:
    path = Path(app_path)
    if path.suffix.casefold() == ".app":
        return path
    for parent in path.parents:
        if parent.suffix.casefold() == ".app":
            return parent
    return path


class AppiumConnectMacOSDriver(AIAssertionBackendToolMixin):
    backend = "appium_connect_agent"

    def __init__(
        self,
        *,
        bundle_id: str | None = None,
        app_path: str | Path | None = None,
        page_source_max_depth: int = DEFAULT_MACOS_PAGE_SOURCE_MAX_DEPTH,
        action_timeout_seconds: int = DEFAULT_MACOS_ACTION_TIMEOUT_SECONDS,
        new_command_timeout_seconds: float | None = DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS,
        session: object | None = None,
    ) -> None:
        self.bundle_id = bundle_id.strip() if isinstance(bundle_id, str) and bundle_id.strip() else None
        self.app_path = str(Path(app_path)) if app_path else None
        self.page_source_max_depth = page_source_max_depth
        self.action_timeout_seconds = action_timeout_seconds
        self.new_command_timeout_seconds = new_command_timeout_seconds
        self._borrowed_session = session is not None
        self._session: Any | None = session
        self._runtime: Any | None = None
        self._managed_scope: AbstractContextManager[Any] | None = None
        self._connection: Any | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._session_settled = False
        self._scope_exit_attempted = False
        self._cleanup_primary: BaseException | None = None
        self._closed = False
        self._cleanup_pending = False
        self._operation_cancellation: Event | None = None
        self._last_operation_finished_at: float | None = None
        self._terminal_disposal_diagnostic: dict[str, object] | None = None

    def close(self) -> None:
        if self._borrowed_session:
            self._shutdown_executor()
            self._closed = True
            return
        if self._session is not None or self._managed_scope is not None or self._runtime is not None or self._connection is not None:
            self._run_sync(self._close_owned_resources)
        try:
            self._shutdown_executor()
        except BaseException as shutdown_error:
            self._cleanup_pending = True
            primary_error = self._cleanup_primary
            if primary_error is not None:
                if isinstance(primary_error, (CancelledError, KeyboardInterrupt, SystemExit)):
                    raise primary_error from shutdown_error
                raise BaseExceptionGroup("macOS session and owner-thread shutdown failed.", [primary_error, shutdown_error]) from None
            raise
        primary_error = self._cleanup_primary
        self._cleanup_primary = None
        self._cleanup_pending = False
        self._closed = True
        if self._terminal_disposal_diagnostic is not None:
            if not self._terminal_disposal_diagnostic["terminal_disposal_completed"]:
                self._terminal_disposal_diagnostic["terminal_disposal_completed"] = True
                logging.getLogger(__name__).info("macOS terminal disposal settled an unconfirmed remote quit.", extra=self._terminal_disposal_diagnostic)
        elif primary_error is not None:
            raise primary_error

    def _close_owned_resources(self) -> None:
        self._cleanup_pending = True
        primary_error = self._cleanup_primary
        managed_settled = False
        try:
            session = self._session
            if session is not None and not self._session_settled:
                info_fn = getattr(session, "session_info", None)
                state = getattr(info_fn(), "state", "active") if callable(info_fn) else "active"
                if state == "active":
                    try:
                        session.quit(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                    except BaseException as exc:  # Retain quit failure while explicitly disposing uncertain resources.
                        if getattr(exc, "code", None) in {"session_closed", "session_disposed"}:
                            self._session_settled = True
                        else:
                            primary_error = self._cleanup_primary = exc
                            state = getattr(info_fn(), "state", "active") if callable(info_fn) else "active"
                            if state in {"uncertain", "disposing"} and getattr(exc, "code", None) == "session_quit_unknown" and type(exc).__name__ == "ResourceStateError":
                                req_state = getattr(exc, "request_state", None)
                                self._terminal_disposal_diagnostic = {
                                    "backend_code": "session_quit_unknown",
                                    "request_state": req_state if req_state in {"not_sent", "response_unknown", "response_received"} else "unknown",
                                    "remote_quit_confirmed": False,
                                    "terminal_disposal_completed": False,
                                }
                            if state not in {"uncertain", "disposing", "disposed"}:
                                raise
                    else:
                        self._session_settled = True
                if not self._session_settled:
                    dispose_fn = getattr(session, "dispose", None)
                    if state in {"uncertain", "disposing"} and callable(dispose_fn):
                        dispose_fn(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                        state = getattr(info_fn(), "state", "disposed") if callable(info_fn) else "disposed"
                        if state != "disposed":
                            raise ToolExecutionError("Owned Mac2 terminal disposal did not complete.", context={"state": state})  # noqa: TRY301
                        managed_settled = True
                    elif state == "disposed":
                        managed_settled = True
                    elif state != "closed":
                        raise ToolExecutionError("Owned Mac2 session is not available for terminal cleanup.", context={"state": state})  # noqa: TRY301
                    self._session_settled = True

            if self._scope_exit_attempted and self._runtime is not None and not managed_settled:
                self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
            elif self._managed_scope is not None and not self._scope_exit_attempted:
                self._scope_exit_attempted = True
                self._managed_scope.__exit__(None, None, None)
                if self._runtime is not None and bool(getattr(self._runtime, "managed_cleanup_pending", False)):
                    self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
            elif self._runtime is not None and not managed_settled and bool(getattr(self._runtime, "managed_cleanup_pending", False)):
                self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
        except BaseException as cleanup_error:
            if primary_error is not None and cleanup_error is not primary_error:
                if isinstance(primary_error, (CancelledError, KeyboardInterrupt, SystemExit)):
                    raise primary_error from cleanup_error
                raise BaseExceptionGroup("macOS session and owned-resource cleanup failed.", [primary_error, cleanup_error]) from None
            raise

        self._session = None
        self._managed_scope = None
        self._connection = None
        self._runtime = None
        self._session_settled = False
        self._scope_exit_attempted = False

    def _close_owned_resources_for_recreation(self) -> None:
        self._close_owned_resources()
        primary_error = self._cleanup_primary
        self._cleanup_primary = None
        self._cleanup_pending = False
        if self._terminal_disposal_diagnostic is not None:
            if not self._terminal_disposal_diagnostic["terminal_disposal_completed"]:
                self._terminal_disposal_diagnostic["terminal_disposal_completed"] = True
                logging.getLogger(__name__).info(
                    "macOS terminal disposal settled an unconfirmed remote quit before session transition.",
                    extra=self._terminal_disposal_diagnostic,
                )
            self._terminal_disposal_diagnostic = None
        elif primary_error is not None:
            raise primary_error

    def context(self) -> dict[str, object]:
        if self._session is None:
            return {
                "session_id": None,
                "current_url": None,
                "screen_size": None,
                "capabilities": {},
                "metadata": {
                    "backend": self.backend,
                    "bundle_id_configured": self.bundle_id is not None,
                    "app_path_configured": self.app_path is not None,
                },
            }
        return self._run_on_worker(self._context_impl)

    def _context_impl(self) -> dict[str, object]:
        session = self._session
        if session is None or self._closed:
            return {
                "session_id": None,
                "current_url": None,
                "screen_size": None,
                "capabilities": {},
                "metadata": {
                    "backend": self.backend,
                    "bundle_id_configured": self.bundle_id is not None,
                    "app_path_configured": self.app_path is not None,
                },
            }
        info_fn = getattr(session, "session_info", None)
        session_id: str | None = None
        if callable(info_fn):
            info = info_fn()
            state = getattr(info, "state", "active")
            if state != "active":
                session_id = None
            else:
                raw_id = getattr(info, "appium_session_id", None) or getattr(info, "id", None)
                session_id = str(raw_id) if raw_id else None
        else:
            raw_id = getattr(session, "session_id", None)
            session_id = raw_id if isinstance(raw_id, str) else None

        screen_size = self._viewport_size(session) if session_id is not None else None
        return {
            "session_id": session_id,
            "current_url": None,
            "screen_size": screen_size,
            "capabilities": {"platformName": "Mac", "automationName": "Mac2"} if session_id is not None else {},
            "metadata": {
                "backend": self.backend,
                "bundle_id_configured": self.bundle_id is not None,
                "app_path_configured": self.app_path is not None,
            },
        }

    @_macos_driver_tool(
        "launchApp",
        description="Create or reuse a managed Appium Mac2 session for the configured macOS application.",
    )
    def launch_app(self, params: MacOSLaunchAppParams) -> dict[str, object]:
        return self._run_tool(lambda: self._launch_app_impl(params), allow_cleanup_pending=bool(params.new_session))

    def _launch_app_impl(self, params: MacOSLaunchAppParams) -> dict[str, object]:
        session = self._ensure_session(params)
        info_fn = getattr(session, "session_info", None)
        if callable(info_fn):
            info = info_fn()
            session_id = getattr(info, "appium_session_id", None) or getattr(info, "id", None)
        else:
            session_id = getattr(session, "session_id", None)
        return self._passed(
            {
                "session_id": str(session_id) if session_id is not None else None,
                "bundle_id": self._effective_bundle_id(params.bundle_id),
                "app_path": self._effective_app_path(params.app_path),
            }
        )

    @_macos_driver_tool("killApp", description="Terminate the active macOS application or close the Mac2 session.")
    def kill_app(self, params: MacOSKillAppParams) -> dict[str, object]:
        return self._run_tool(lambda: self._kill_app_impl(params))

    def _kill_app_impl(self, params: MacOSKillAppParams) -> dict[str, object]:
        session = self._session
        if session is None:
            return self._passed()
        bundle_id = self._effective_bundle_id(params.bundle_id)
        if params.close_session:
            if self._borrowed_session:
                quit_fn = getattr(session, "quit", None)
                if callable(quit_fn):
                    quit_fn()
                self._session = None
            else:
                self._close_owned_resources_for_recreation()
            return self._passed({"bundle_id": bundle_id, "close_session": True})
        if not bundle_id:
            raise ConfigurationError("macOS application termination requires bundle_id or a configured bundle id.")
        try:
            session.device.terminate_app(bundle_id, control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="kill_app")
        return self._passed({"bundle_id": bundle_id, "close_session": False})

    @_macos_driver_tool("clickOn", description="Click a macOS element or point resolved through Appium Mac2.")
    def click_on(self, params: MacOSClickOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._click_on_impl(params))

    def _click_on_impl(self, params: MacOSClickOnParams) -> dict[str, object]:
        modifier_flags = self._modifier_flags(params.modifiers)
        element = self._resolve_element_or_none(params)
        if element is not None:
            try:
                element.click(
                    key_modifier_flags=modifier_flags,
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="click_on")
            return self._passed()
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_click(point, button="left", double=False, modifier_flags=modifier_flags)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool("doubleClickOn", description="Double-click a macOS element or point.")
    def double_click_on(self, params: MacOSDoubleClickOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._double_click_on_impl(params))

    def _double_click_on_impl(self, params: MacOSDoubleClickOnParams) -> dict[str, object]:
        modifier_flags = self._modifier_flags(params.modifiers)
        element = self._resolve_element_or_none(params)
        if element is not None:
            center = self._element_center(element)
            try:
                element.double_click(
                    key_modifier_flags=modifier_flags,
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="double_click_on")
            output: dict[str, object] = {"click_count": 2}
            if center is not None:
                output["point"] = center.model_dump(mode="json")
            return self._passed(output)
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_click(point, button="left", double=True, modifier_flags=modifier_flags)
        return self._passed({"point": point.model_dump(mode="json"), "click_count": 2})

    @_macos_driver_tool("rightClickOn", description="Right-click a macOS element or point.")
    def right_click_on(self, params: MacOSRightClickOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._right_click_on_impl(params))

    def _right_click_on_impl(self, params: MacOSRightClickOnParams) -> dict[str, object]:
        modifier_flags = self._modifier_flags(params.modifiers)
        element = self._resolve_element_or_none(params)
        if element is not None:
            center = self._element_center(element)
            try:
                element.right_click(
                    key_modifier_flags=modifier_flags,
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="right_click_on")
            return self._passed({"point": center.model_dump(mode="json")} if center is not None else None)
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_click(point, button="right", double=False, modifier_flags=modifier_flags)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool("typeText", description="Type text into the resolved or focused macOS element.")
    def type_text(self, params: MacOSTypeTextParams) -> dict[str, object]:
        return self._run_tool(lambda: self._type_text_impl(params))

    def _type_text_impl(self, params: MacOSTypeTextParams) -> dict[str, object]:
        self._require_session()
        element = self._resolve_element_or_none(params)
        if element is None and self._has_semantic_target(params):
            return self._target_missing()
        if element is not None:
            return self._type_text_into_element(element, params)

        if params.clear:
            return self._failed("action_error", "Untargeted macOS text entry cannot clear and verify an active element.")
        point = self._coordinate_only_point(params)
        if point is not None:
            self._perform_pointer_click(point, button="left", double=False, modifier_flags=None)
        self._send_macos_text(params.text, element=None)
        if point is not None:
            return self._passed({"point": point.model_dump(mode="json")})
        return self._passed()

    def _type_text_into_element(self, element: Any, params: MacOSTypeTextParams) -> dict[str, object]:
        session = self._require_session()
        pre_obs = self._inspect_obs_map(element, ("type", "id", "enabled", "displayed", "bounds"), stage="pre_entry_inspect")
        enabled_obs = pre_obs.get("enabled")
        displayed_obs = pre_obs.get("displayed")
        if getattr(enabled_obs, "state", None) != "value" or enabled_obs.value is not True or getattr(displayed_obs, "state", None) != "value" or displayed_obs.value is not True:
            return self._failed(
                "target_resolution_error",
                "Input target is not enabled and visible.",
                metadata={"reason": "invalid_input_target", "resolution_reason": "invalid_input_target"},
            )

        type_obs = pre_obs.get("type")
        elem_type = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) else None
        is_secure = self._is_secure_text_type(elem_type)

        id_obs = pre_obs.get("id")
        elem_id = id_obs.value if getattr(id_obs, "state", None) == "value" and isinstance(id_obs.value, str) and id_obs.value.strip() else None
        bounds_tuple = self._bounds_int_tuple(pre_obs.get("bounds"))

        initial_value: str | None = None
        if not is_secure:
            val_obs = self._inspect_obs_map(element, ("value",), stage="pre_entry_inspect").get("value")
            val_state = getattr(val_obs, "state", None)
            if val_state == "value" and isinstance(val_obs.value, str):
                if self._is_masked_string(val_obs.value):
                    return self._failed("action_error", "Input target value is masked and cannot be verified.", metadata={"reason": "verification_unavailable", "stage": "pre_entry_inspect"})
                initial_value = val_obs.value
            elif val_state == "missing":
                initial_value = ""
            else:
                return self._failed("action_error", "Input target value is unreadable before entry.", metadata={"reason": "verification_unavailable", "stage": "pre_entry_inspect"})

        bound_element = element
        bound_by_geometry_only = False
        text_dependent = self._target_depends_on_mutable_text(params)
        if elem_id is not None:
            bound_candidate = self._bind_element_by_id_or_geometry(session, elem_id=elem_id, elem_type=elem_type, bounds_tuple=bounds_tuple)
            if bound_candidate is not None:
                bound_element = bound_candidate
            elif text_dependent:
                return self._failed(
                    "action_error",
                    "Could not bind a text-independent identity for macOS text entry.",
                    metadata={"reason": "verification_unavailable", "stage": "identity_binding"},
                )
        elif text_dependent:
            if not elem_type or bounds_tuple is None:
                return self._failed(
                    "action_error",
                    "Could not bind a text-independent geometry anchor for macOS text entry.",
                    metadata={"reason": "verification_unavailable", "stage": "identity_binding"},
                )
            bound_candidate = self._bind_element_by_geometry(session, elem_type=elem_type, bounds_tuple=bounds_tuple)
            if bound_candidate is None:
                return self._failed(
                    "action_error",
                    "Could not uniquely bind target geometry before macOS text entry.",
                    metadata={"reason": "verification_unavailable", "stage": "identity_binding"},
                )
            bound_element = bound_candidate
            bound_by_geometry_only = True

        try:
            bound_element.click(
                wait_timeout_seconds=self._wait_timeout(),
                control=self._ActionControl(self),
            )
            if params.clear:
                bound_element.clear(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            bound_element.send_keys(
                params.text,
                wait_timeout_seconds=self._wait_timeout(),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="type_text")

        if is_secure:
            post_secure = self._verify_post_entry_target(
                bound_element,
                expected_type=elem_type,
                expected_id=elem_id,
                expected_bounds=bounds_tuple if bound_by_geometry_only else None,
                include_value=False,
            )
            if post_secure is None:
                return self._failed(
                    "action_error",
                    "Secure text was sent, but post-entry target identity could not be verified; inspect UI state before retrying.",
                    metadata={"reason": "verification_unavailable", "stage": "post_verification"},
                )
            return self._passed({"protected": True})

        post_check = self._verify_post_entry_target(
            bound_element,
            expected_type=elem_type,
            expected_id=elem_id,
            expected_bounds=bounds_tuple if bound_by_geometry_only else None,
            include_value=True,
        )
        if post_check is None:
            return self._failed(
                "action_error",
                "Text was sent, but target geometry or identity shifted during verification; inspect UI state before re-entering text.",
                metadata={"reason": "verification_unavailable", "stage": "post_verification"},
            )
        if self._value_matches(post_check, params.text, clear=bool(params.clear), initial_value=initial_value):
            return self._passed()

        try:
            if params.clear:
                bound_element.clear(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            self._send_macos_text(params.text, element=bound_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="type_text_fallback")

        fallback_check = self._verify_post_entry_target(
            bound_element,
            expected_type=elem_type,
            expected_id=elem_id,
            expected_bounds=bounds_tuple if bound_by_geometry_only else None,
            include_value=True,
        )
        if fallback_check is None:
            return self._failed(
                "action_error",
                "Text fallback was sent, but target geometry or identity shifted during verification; inspect UI state before re-entering text.",
                metadata={"reason": "verification_unavailable", "stage": "post_verification"},
            )
        if self._value_matches(fallback_check, params.text, clear=bool(params.clear), initial_value=initial_value):
            return self._passed()
        return self._failed(
            "action_error",
            "macOS text entry did not update the target element value after fallback.",
            metadata={"reason": "postcondition_failed", "stage": "post_verification"},
        )

    @_macos_driver_tool("pressKey", description="Send a keyboard shortcut or key to macOS.")
    def press_key(self, params: MacOSPressKeyParams) -> dict[str, object]:
        return self._run_tool(lambda: self._press_key_impl(params))

    def _press_key_impl(self, params: MacOSPressKeyParams) -> dict[str, object]:
        session = self._require_session()
        modifier_flags = self._modifier_flags(params.modifiers) or 0
        raw_key = params.key.strip()
        mapped_key = MACOS_KEY_NAMES.get(raw_key.upper(), raw_key)
        key_item = self._build_key_item(mapped_key, modifier_flags)
        try:
            session.device.send_keys([key_item], control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "InvalidRequestError":
                raise ConfigurationError(f"Unsupported macOS key: {params.key}.") from None
            return self._normalize_backend_exception(exc, stage="press_key")
        return self._passed({"key": params.key, "modifiers": params.modifiers or []})

    @_macos_driver_tool("hoverOn", description="Move the pointer over a macOS element or point.")
    def hover_on(self, params: MacOSHoverOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._hover_on_impl(params))

    def _hover_on_impl(self, params: MacOSHoverOnParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is not None:
            center = self._element_center(element)
            try:
                element.hover(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="hover_on")
            return self._passed({"point": center.model_dump(mode="json")} if center is not None else None)
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_hover(point)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool(
        "scrollOn",
        description="Scroll on a resolved macOS element center or explicit point using Appium Mac2 wheel deltas.",
    )
    def scroll_on(self, params: MacOSScrollOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._scroll_on_impl(params))

    def _scroll_on_impl(self, params: MacOSScrollOnParams) -> dict[str, object]:
        session = self._require_session()
        modifier_flags = self._modifier_flags(params.modifiers)
        if self._has_semantic_target(params):
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            center = self._element_center(element)
            if center is None:
                return self._failed("action_error", "Resolved macOS scroll target has no usable bounds.", metadata={"stage": "scroll_on"})
        else:
            center = self._coordinate_only_point(params)
            if center is None:
                return self._target_missing()
        try:
            session.device.scroll_at(
                center.x,
                center.y,
                delta_x=float(params.delta_x),
                delta_y=float(params.delta_y),
                key_modifier_flags=modifier_flags,
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="scroll_on")
        return self._passed(
            {
                "point": {"x": center.x, "y": center.y},
                "delta_x": params.delta_x,
                "delta_y": params.delta_y,
                "modifiers": params.modifiers or [],
            }
        )

    @_macos_driver_tool("dragTo", description="Drag from one macOS element or point to another.")
    def drag_to(self, params: MacOSDragToParams) -> dict[str, object]:
        return self._run_tool(lambda: self._drag_to_impl(params))

    def _drag_to_impl(self, params: MacOSDragToParams) -> dict[str, object]:
        source = self._point_from_endpoint(params.source)
        destination = self._point_from_endpoint(params.destination)
        if source is None or destination is None:
            return self._failed(
                "target_resolution_error",
                "Drag source or destination was not found.",
                metadata={"resolution_reason": "not_found", **self._safe_diagnostics(stage="drag_to")},
            )
        self._perform_pointer_drag(source, destination, duration_ms=params.duration_ms)
        return self._passed({"source": source.model_dump(mode="json"), "destination": destination.model_dump(mode="json")})

    @_macos_driver_tool("takeScreenshot", description="Capture a macOS screenshot through Appium Mac2.")
    def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes | dict[str, object]:
        try:
            return self.screenshot(params)
        except ToolExecutionError as exc:
            diagnostics = exc.context.get("capture_diagnostics")
            metadata = dict(diagnostics) if isinstance(diagnostics, dict) else self._safe_diagnostics(exc, stage="take_screenshot")
            return self._failed("observation_error", "Appium Mac2 screenshot capture failed.", metadata=metadata)

    @_macos_driver_tool(
        "uiSnapshot",
        description=("Read macOS accessibility evidence as a bounded compact control tree with explicit clipping markers. No-match is not proof of invisibility."),
    )
    def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        return self._run_tool(lambda: self._ui_snapshot_impl(params), failure_default="observation_error")

    def _ui_snapshot_impl(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        session = self._require_session()
        try:
            observation = session.observe_tree(control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        max_depth = params.max_depth or self.page_source_max_depth
        include_attributes = bool(params.include_attributes)
        return {
            "snapshot_type": "macos_accessibility_tree",
            "page_source": self._simplify_tree_observation(
                observation,
                max_depth=max_depth,
                include_attributes=include_attributes,
            ),
            "max_depth": max_depth,
            "include_attributes": include_attributes,
            "diagnostics": {
                "source": "appium_connect_agent_tree_observation",
                "coverage": getattr(observation, "coverage", "requested_scope"),
                "complete_for_requested_scope": bool(getattr(observation, "complete_for_requested_scope", True)),
                "atomic_with_screenshot": False,
                "absence_proves_invisibility": False,
            },
        }

    @_macos_driver_tool(
        "inspectElement",
        description="Inspect one uniquely resolved macOS element and return unclipped attributes with SecureTextField protection.",
    )
    def inspect_element(self, params: MacOSInspectElementParams) -> dict[str, object]:
        return self._run_tool(lambda: self._inspect_element_impl(params), failure_default="observation_error")

    def _inspect_element_impl(self, params: MacOSInspectElementParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is None:
            return self._target_missing()

        requested = tuple(params.attributes)
        needs_sensitive_gate = "text" in requested or "value" in requested
        attributes_out: dict[str, dict[str, object]] = {}

        if needs_sensitive_gate:
            type_obs = self._inspect_obs_map(element, ("type",), stage="inspect_element").get("type")
            type_state = getattr(type_obs, "state", "unknown")
            type_val = type_obs.value if type_state == "value" and isinstance(type_obs.value, str) else None
            safe_non_secure = type_state == "value" and bool(type_val) and not self._is_secure_text_type(type_val)
            if "type" in requested:
                attributes_out["type"] = self._serialize_attribute_observation("type", type_obs)
            remaining = tuple(attr for attr in requested if attr != "type" and (safe_non_secure or attr not in {"text", "value"}))
            if remaining:
                inspected_map = self._inspect_obs_map(element, remaining, stage="inspect_element")
                for attr in remaining:
                    attributes_out[attr] = self._serialize_attribute_observation(attr, inspected_map.get(attr))
            if not safe_non_secure:
                for protected_attr in ("text", "value"):
                    if protected_attr in requested:
                        attributes_out[protected_attr] = {"state": "unavailable", "value": None, "reason": "protected_text"}
        else:
            inspected_map = self._inspect_obs_map(element, requested, stage="inspect_element")
            for attr in requested:
                attributes_out[attr] = self._serialize_attribute_observation(attr, inspected_map.get(attr))

        ordered = {attr: attributes_out[attr] for attr in requested}
        return self._passed({"attributes": ordered})

    @_macos_driver_tool(
        "inspectWithAI",
        description="Read pixel-only content in the current macOS frame with mode='extract'. Read-only; never an assertion.",
    )
    def inspect_with_ai(self, params: MacOSInspectWithAIParams) -> dict[str, object]:
        if not isinstance(params, MacOSInspectWithAIParams):
            params = MacOSInspectWithAIParams.model_validate(params)
        return _run_visual_inspection(self, params)

    @_macos_driver_tool("assertVisible", description="Assert that a macOS element is visible.")
    def assert_visible(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_visible_impl(params))

    def _assert_visible_impl(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is None:
            return self._target_missing()
        if self._element_displayed(element):
            return self._passed()
        return self._failed("assertion_error", "Target is present but not visible.")

    @_macos_driver_tool("assertNotVisible", description="Assert that a macOS element is absent or not displayed.")
    def assert_not_visible(self, params: MacOSAssertNotVisibleParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_not_visible_impl(params))

    def _assert_not_visible_impl(self, params: MacOSAssertNotVisibleParams) -> dict[str, object]:
        locator = self._locator_from_params(params)
        deadline = time.monotonic() + self._wait_timeout()
        last_ambiguous: _AmbiguousResolutionError | None = None
        while True:
            try:
                element = self._element_from_locator(locator)
            except _AmbiguousResolutionError as amb:
                last_ambiguous = amb
                if time.monotonic() >= deadline:
                    return self._target_ambiguous_result(amb)
                time.sleep(0.25)
                continue
            if element is None:
                return self._passed({"visible": False})
            last_ambiguous = None
            try:
                displayed_obs = self._inspect_obs_map(element, ("displayed",), stage="assert_not_visible").get("displayed")
            except ToolExecutionError as exc:
                cause = exc.context.get("cause")
                if isinstance(cause, Exception) and type(cause).__name__ == "TargetError" and self._target_error_is_definitive(cause):
                    cause_code = getattr(cause, "code", None)
                    if cause_code == "target_not_found":
                        return self._passed({"visible": False})
                    if cause_code == "target_ambiguous":
                        session = self._require_session()
                        last_ambiguous = _AmbiguousResolutionError(self._collect_ambiguous_candidates(session, locator), cause=cause)
                        if time.monotonic() >= deadline:
                            return self._target_ambiguous_result(last_ambiguous)
                        time.sleep(0.25)
                        continue
                raise
            if getattr(displayed_obs, "state", None) == "value" and displayed_obs.value is False:
                return self._passed({"visible": False})
            if time.monotonic() >= deadline:
                return self._failed("assertion_error", "Target is visible.", output={"visible": True})
            time.sleep(0.25)
        if last_ambiguous is not None:
            return self._target_ambiguous_result(last_ambiguous)
        return self._failed("assertion_error", "Target is visible.", output={"visible": True})

    @_macos_driver_tool("assert", description="Assert existence, text, or boolean state on one uniquely located macOS element.")
    def assert_state(self, params: MacOSAssertStateParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_state_impl(params))

    def _assert_state_impl(self, params: MacOSAssertStateParams) -> dict[str, object]:
        locator = params.element.to_locator()
        element = self._poll_unique_element(locator)
        if element is None:
            return self._target_missing()

        if params.text is not None:
            return self._evaluate_text_assertion(element, params.text)

        expected_states = {field: getattr(params.element, field) for field in MACOS_STATE_ASSERTION_FIELDS if type(getattr(params.element, field)) is bool}
        if expected_states:
            ordered_fields = tuple(field for field in MACOS_STATE_ASSERTION_FIELDS if field in expected_states)
            observed_map = self._inspect_obs_map(element, ordered_fields, stage="assert_state")
            actual_states: dict[str, object] = {}
            mismatches: dict[str, dict[str, object]] = {}
            for field in ordered_fields:
                obs = observed_map.get(field)
                actual = obs.value if getattr(obs, "state", None) == "value" and type(obs.value) is bool else None
                actual_states[field] = actual
                expected = expected_states[field]
                if actual is not expected:
                    mismatches[field] = {"expected": expected, "actual": actual, "state": getattr(obs, "state", "unknown")}
            if mismatches:
                return self._failed(
                    "assertion_error",
                    "macOS element state did not match expected boolean values.",
                    output={"states": actual_states, "mismatches": mismatches},
                )
            return self._passed({"states": actual_states})

        self._inspect_obs_map(element, ("enabled",), stage="assert_state")
        return self._passed({"exists": True})

    @_macos_driver_tool("assertElementsOrder", description="Assert that macOS elements appear in the expected visual order.")
    def assert_elements_order(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_elements_order_impl(params))

    def _assert_elements_order_impl(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        missing: list[int] = []
        for index, element_ref in enumerate(params.elements):
            element = self._resolve_element_or_none(element_ref)
            if element is None:
                missing.append(index)
                continue
            rect = self._element_rect(element)
            if rect is None:
                missing.append(index)
                continue
            entries.append(
                {
                    "index": index,
                    "coordinate": self._order_coordinate(rect, params.direction),
                    "rect": rect,
                }
            )
        actual_order = [int(entry["index"]) for entry in sorted(entries, key=lambda entry: float(entry["coordinate"]))]
        expected_order = params.expected_order or list(range(len(params.elements)))
        if not params.require_all:
            expected_order = [index for index in expected_order if index in actual_order]
        output = {
            "direction": params.direction,
            "elements_found": len(entries),
            "elements_total": len(params.elements),
            "actual_order": actual_order,
            "expected_order": expected_order,
            "positions": entries,
        }
        if missing and params.require_all:
            return self._failed(
                "target_resolution_error",
                "One or more ordered elements were not found.",
                output=output,
                metadata={"missing_indexes": missing},
            )
        if self._order_matches(entries, expected_order, params.tolerance or 0.0):
            return self._passed(output)
        return self._failed(
            "assertion_error",
            "Elements are not in the expected order.",
            output=output,
        )

    @_macos_driver_tool("assertWithAI", description="Evaluate a scoped macOS image-space visual assertion with AI.")
    def assert_with_ai(self, params: MacOSAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        cancellation_check = None
        timeout_seconds: float | None = None
        if isinstance(params, dict):
            raw_check = params.get("cancellation_check")
            if callable(raw_check):
                cancellation_check = raw_check
            raw_timeout = params.get("timeout_seconds")
            if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool) and raw_timeout > 0:
                timeout_seconds = float(raw_timeout)
        cancellation_event = Event() if cancellation_check is not None else None
        failures: list[BaseException] = []
        backend_errors: list[BaseException] = []

        def check_on_caller() -> None:
            if cancellation_check is not None:
                try:
                    cancellation_check()
                except BaseException as err:
                    failures.append(err)
                    raise

        def run_capture() -> bytes:
            try:
                return self._screenshot_impl(timeout_seconds=timeout_seconds)
            except BaseException as err:
                backend_errors.append(err)
                raise

        try:
            data = self._run_on_worker(
                run_capture,
                cancellation_check=check_on_caller if cancellation_check is not None else None,
                cancellation_event=cancellation_event,
            )
            if cancellation_check is not None:
                check_on_caller()
        except BaseException as error:
            if cancellation_check is not None and not failures:
                with suppress(BaseException):
                    check_on_caller()
            if isinstance(error, ConfigurationError):
                raise
            diag_source = backend_errors[0] if backend_errors and isinstance(backend_errors[0], Exception) else (error if isinstance(error, Exception) else None)
            diagnostics = {key: value for key, value in self._safe_diagnostics(diag_source, stage="screenshot").items() if key in {"backend_code", "request_state", "session_state", "stage", "reason"}}
            if failures:
                interrupted = failures[0]
                interrupted.fsq_capture_diagnostics = diagnostics  # type: ignore[attr-defined]
                raise interrupted from None
            if isinstance(error, CancelledError) or type(error).__name__ == "OperationCancelledError" or getattr(error, "code", None) == "operation_cancelled":
                cancelled = CancelledError()
                cancelled.fsq_capture_diagnostics = diagnostics  # type: ignore[attr-defined]
                raise cancelled from None
            if isinstance(error, ToolExecutionError) and "capture_diagnostics" in error.context:
                raise
            if not isinstance(error, Exception):
                raise
            raise ToolExecutionError(
                "Appium Mac2 screenshot capture failed.",
                context={"capture_diagnostics": diagnostics},
            ) from None
        return data

    def _screenshot_impl(self, *, timeout_seconds: float | None = None) -> bytes:
        session = self._require_session()
        control = self._operation_control(timeout_seconds, cancellation=self._operation_cancellation) if timeout_seconds is not None else self._ActionControl(self)
        data = session.device.take_screenshot(control=control)
        if isinstance(data, bytes) and data:
            return data
        raise ToolExecutionError(
            "Appium Mac2 screenshot capture returned no image.",
            context={"capture_diagnostics": self._safe_diagnostics(stage="screenshot")},
        )

    def _ensure_session(self, params: MacOSLaunchAppParams | None = None) -> Any:
        if self._closed:
            raise ToolExecutionError("Appium Mac2 driver is closed.", context={"reason": "closed"})
        params = params or MacOSLaunchAppParams()
        if self._cleanup_pending and not params.new_session:
            raise ToolExecutionError(
                "Appium Mac2 session requires cleanup before reuse.",
                context={"reason": "cleanup_pending", **self._safe_diagnostics(stage="ensure_session")},
            )
        if self._session is not None and not self._session_is_active(self._session):
            if not params.new_session:
                if not self._borrowed_session:
                    self._cleanup_pending = True
                raise ToolExecutionError(
                    "Appium Mac2 session is no longer active.",
                    context={"reason": "session_unavailable", **self._safe_diagnostics(stage="ensure_session")},
                )
            if self._borrowed_session:
                self._session = None
            else:
                self._close_owned_resources_for_recreation()

        if self._session is None:
            if self._cleanup_pending:
                self._close_owned_resources_for_recreation()
            return self._create_session(params)
        if params.new_session:
            self._require_creation_identity(params)
            if self._borrowed_session:
                quit_fn = getattr(self._session, "quit", None)
                if callable(quit_fn):
                    quit_fn()
                self._session = None
            else:
                self._close_owned_resources_for_recreation()
            return self._create_session(params)
        if params.app_path is not None or params.arguments is not None:
            raise ConfigurationError("app_path and arguments require new_session=true when a Mac2 session already exists.")
        bundle_id = self._effective_bundle_id(params.bundle_id)
        if bundle_id is None:
            raise ConfigurationError("macOS application activation requires bundle_id or a configured bundle id.")
        self._ensure_alive_if_idle(self._session)
        try:
            self._session.device.activate_app(bundle_id, control=self._ActionControl(self))
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            raise ToolExecutionError(
                "macOS application activation failed.",
                context={"stage": "launch_app", "cause": exc},
            ) from exc
        return self._session

    @staticmethod
    def _session_is_active(session: Any) -> bool:
        info_fn = getattr(session, "session_info", None)
        if not callable(info_fn):
            return True
        state = getattr(info_fn(), "state", "active")
        return state == "active"

    def _create_session(self, params: MacOSLaunchAppParams) -> Any:
        bundle_id, app_path = self._require_creation_identity(params)
        resolved_app_path = _resolve_app_bundle_path(app_path) if app_path else None
        try:
            from appium_connect_agent.automation import create_mac2_session
            from appium_connect_agent.contracts import LocalSettings
            from appium_connect_agent.runtime import OperationControl, Runtime, ServiceLifecycleError
        except ImportError as exc:
            raise ConfigurationError(
                "appium-connect-agent is required for AppiumConnectMacOSDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc

        local_settings = _resolve_managed_local_settings(LocalSettings)
        runtime = Runtime(local_settings)
        self._borrowed_session = False
        self._runtime = runtime
        scope: AbstractContextManager[Any] | None = None
        connection: Any | None = None
        last_port_error: BaseException | None = None
        for _ in range(MAX_PORT_BIND_ATTEMPTS):
            port = _allocate_loopback_port()
            candidate_scope = runtime.start_managed(
                host="127.0.0.1",
                port=port,
                base_path="",
                control=OperationControl(timeout_seconds=DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS, cancellation=self._operation_cancellation),
            )
            self._managed_scope = candidate_scope
            self._scope_exit_attempted = False
            try:
                connection = candidate_scope.__enter__()
                scope = candidate_scope
                self._connection = connection
                break
            except BaseException as exc:
                self._cleanup_pending = True
                self._scope_exit_attempted = True
                if isinstance(exc, ServiceLifecycleError) and exc.code == "service_port_in_use":
                    try:
                        runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                    except BaseException as cleanup_error:  # noqa: BLE001 - preserve startup and cleanup failures.
                        raise BaseExceptionGroup("Managed Appium startup and cleanup failed.", [exc, cleanup_error]) from None
                    self._managed_scope = None
                    self._scope_exit_attempted = False
                    self._cleanup_pending = False
                    last_port_error = exc
                    continue
                raise

        if scope is None or connection is None:
            self._runtime = None
            if last_port_error is not None:
                raise last_port_error
            raise ConfigurationError("Failed to bind a managed Appium loopback port.")

        effective_new_command_timeout = 0.0 if self.new_command_timeout_seconds is None else float(self.new_command_timeout_seconds)
        if isinstance(self.new_command_timeout_seconds, bool) or not (0.0 <= effective_new_command_timeout <= 86_400.0):
            raise ConfigurationError("macOS new_command_timeout_seconds must be between 0 and 86400 seconds or None.")
        try:
            session = create_mac2_session(
                connection,
                bundle_id=bundle_id,
                app_path=resolved_app_path,
                arguments=params.arguments,
                no_reset=True,
                new_command_timeout_seconds=effective_new_command_timeout,
                control=OperationControl(timeout_seconds=DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS, cancellation=self._operation_cancellation),
            )
        except BaseException as exc:
            uncertain_session = getattr(exc, "session", None)
            if uncertain_session is not None:
                self._borrowed_session = False
                self._runtime = runtime
                self._managed_scope = scope
                self._connection = connection
                self._session = uncertain_session
                self._cleanup_pending = True
                raise
            try:
                self._scope_exit_attempted = True
                scope.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException as cleanup_exc:
                self._cleanup_pending = True
                logging.getLogger(__name__).warning(
                    "Managed Appium scope cleanup failed after Mac2 session creation error (%s)",
                    type(cleanup_exc).__name__,
                )
                raise
            if bool(getattr(runtime, "managed_cleanup_pending", False)):
                try:
                    runtime.cleanup_managed(control=OperationControl(timeout_seconds=DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                except BaseException as cleanup_exc:
                    self._cleanup_pending = True
                    logging.getLogger(__name__).debug("Secondary managed cleanup did not complete (%s)", type(cleanup_exc).__name__)
                    raise
            self._managed_scope = None
            self._connection = None
            self._runtime = None
            self._scope_exit_attempted = False
            self._cleanup_pending = False
            raise

        self._borrowed_session = False
        self._runtime = runtime
        self._managed_scope = scope
        self._connection = connection
        self._session = session
        self._session_settled = False
        self._scope_exit_attempted = False
        self._cleanup_primary = None
        self._cleanup_pending = False
        return session

    def _require_creation_identity(self, params: MacOSLaunchAppParams) -> tuple[str | None, str | None]:
        bundle_id = self._effective_bundle_id(params.bundle_id)
        app_path = self._effective_app_path(params.app_path)
        if bundle_id is None and app_path is None:
            raise ConfigurationError("macOS Appium Mac2 session requires bundle_id or app_path.")
        return bundle_id, app_path

    def _effective_bundle_id(self, bundle_id: str | None) -> str | None:
        if isinstance(bundle_id, str) and bundle_id.strip():
            return bundle_id.strip()
        return self.bundle_id

    def _effective_app_path(self, app_path: str | None) -> str | None:
        if isinstance(app_path, str) and app_path.strip():
            return app_path.strip()
        return self.app_path

    def _require_session(self) -> Any:
        if self._closed:
            raise ToolExecutionError("Appium Mac2 driver is closed.", context={"reason": "closed"})
        if self._cleanup_pending:
            raise ToolExecutionError(
                "Appium Mac2 session requires cleanup and cannot execute actions.",
                context={"reason": "cleanup_pending", **self._safe_diagnostics(stage="require_session")},
            )
        if self._session is None:
            raise ConfigurationError(
                "Appium Mac2 session is not available. Call launchApp before macOS Appium actions.",
                context={"resolution_reason": "session_unavailable"},
            )
        if not self._session_is_active(self._session):
            if not self._borrowed_session:
                self._cleanup_pending = True
            raise ToolExecutionError(
                "Appium Mac2 session is no longer active.",
                context={"reason": "session_unavailable", **self._safe_diagnostics(stage="require_session")},
            )
        self._ensure_alive_if_idle(self._session)
        return self._session

    def _ensure_alive_if_idle(self, session: Any) -> None:
        if self._borrowed_session:
            return
        if self._last_operation_finished_at is None or (time.monotonic() - self._last_operation_finished_at) < DEFAULT_IDLE_ENSURE_ALIVE_SECONDS:
            return
        ensure_alive = getattr(session, "ensure_alive", None)
        if not callable(ensure_alive):
            return
        try:
            ensure_alive(control=self._operation_control(DEFAULT_ENSURE_ALIVE_TIMEOUT_SECONDS, cancellation=self._operation_cancellation))
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            self._cleanup_pending = True
            raise ToolExecutionError(
                "Appium Mac2 session liveness probe failed; session requires cleanup or replacement.",
                context={"reason": "session_unavailable", "stage": "ensure_alive", "cause": exc},
            ) from exc

    @staticmethod
    def _has_semantic_target(params: BaseModel) -> bool:
        data = params.model_dump(mode="python", exclude_none=True)
        target = data.get("target")
        if isinstance(target, str) and target.strip():
            return True
        locator = data.get("locator")
        if isinstance(locator, dict):
            return any(bool(str(value).strip()) for key, value in locator.items() if key != "point" and value is not None)
        return False

    @staticmethod
    def _coordinate_only_point(params: BaseModel) -> MacOSPoint | None:
        data = params.model_dump(mode="python", exclude_none=True)
        point = data.get("point")
        if isinstance(point, dict):
            return MacOSPoint.model_validate(point)
        locator = data.get("locator")
        if isinstance(locator, dict) and isinstance(locator.get("point"), dict):
            return MacOSPoint.model_validate(locator["point"])
        return None

    def _resolve_element_or_none(self, params: BaseModel) -> Any | None:
        data = params.model_dump(mode="python", exclude_none=True)
        locator = data.get("locator")
        if isinstance(locator, dict):
            return self._element_from_locator(MacOSLocator.model_validate(locator))
        target = data.get("target")
        if isinstance(target, str) and target.strip():
            return self._element_from_locator(MacOSLocator(name=target.strip()))
        return None

    def _element_from_locator(self, locator: MacOSLocator) -> Any | None:
        session = self._require_session()
        if not any(
            isinstance(value, str) and value.strip()
            for value in (
                locator.accessibilityId,
                locator.name,
                locator.label,
                locator.value,
                locator.title,
                locator.role,
                locator.controlType,
                locator.className,
                locator.xpath,
                locator.predicate,
            )
        ):
            return None

        by, query_value, extra_predicate = self._build_session_locator_query(locator)
        try:
            element = session.element(by, query_value, predicate=extra_predicate)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        # When xpath or role is combined with raw predicate, intersect both queries via live inspection.
        if (locator.xpath or locator.role) and locator.predicate:
            return self._intersect_xpath_and_predicate(session, element, locator)

        try:
            element.inspect(attributes=("enabled",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            code = getattr(exc, "code", None)
            if type(exc).__name__ == "TargetError" and self._target_error_is_definitive(exc):
                if code == "target_not_found":
                    return None
                if code == "target_ambiguous":
                    candidates = self._collect_ambiguous_candidates(session, locator)
                    raise _AmbiguousResolutionError(candidates, cause=exc) from None
            self._raise_resolution_error(exc)
        return element

    def _intersect_xpath_and_predicate(self, session: Any, xpath_element: Any, locator: MacOSLocator) -> Any | None:
        if locator.predicate is None:
            return xpath_element
        if not self._probe_single_or_ambiguous(session, xpath_element, locator):
            return None
        try:
            scoped_element = session.element("-ios predicate string", locator.predicate, parent=xpath_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        try:
            scoped_inspection = scoped_element.inspect(
                attributes=("type", "id", "label", "title", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "TargetError":
                code = getattr(exc, "code", None)
                if code == "target_not_found":
                    return None
                if code == "target_ambiguous":
                    return self._confirm_self_matches_predicate_among_subtree(xpath_element, locator.predicate)
            self._raise_resolution_error(exc)
        if self._element_signature(xpath_element) != self._signature_from_inspection(scoped_inspection):
            return None
        return xpath_element

    def _confirm_self_matches_predicate_among_subtree(self, xpath_element: Any, predicate: str) -> Any | None:
        xpath_sig = self._element_signature(xpath_element)
        session = self._require_session()
        clauses = [f"({predicate})"]
        elem_type, elem_id, elem_label, elem_title, elem_value, bounds_tuple = xpath_sig
        if isinstance(elem_type, str) and elem_type:
            clauses.append(f"amType == {_predicate_literal(elem_type)}")
        if isinstance(elem_id, str) and elem_id:
            clauses.append(f"identifier == {_predicate_literal(elem_id)}")
        if isinstance(elem_label, str) and elem_label:
            clauses.append(f"label == {_predicate_literal(elem_label)}")
        if isinstance(elem_title, str) and elem_title:
            clauses.append(f"title == {_predicate_literal(elem_title)}")
        if isinstance(elem_value, str) and elem_value:
            clauses.append(f"value == {_predicate_literal(elem_value)}")
        if isinstance(bounds_tuple, tuple) and len(bounds_tuple) == 4 and all(type(coord) is int for coord in bounds_tuple):
            bx, by, bw, bh = bounds_tuple
            clauses.extend((f"amRect.x == {bx}", f"amRect.y == {by}", f"amRect.width == {bw}", f"amRect.height == {bh}"))
        try:
            narrowed = session.element("-ios predicate string", " AND ".join(clauses), parent=xpath_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        try:
            narrowed_inspection = narrowed.inspect(
                attributes=("type", "id", "label", "title", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "TargetError" and getattr(exc, "code", None) in {"target_not_found", "target_ambiguous"}:
                return None
            self._raise_resolution_error(exc)
        return xpath_element if xpath_sig == self._signature_from_inspection(narrowed_inspection) else None

    def _probe_single_or_ambiguous(self, session: Any, element: Any, locator: MacOSLocator) -> bool:
        try:
            element.inspect(attributes=("enabled",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            code = getattr(exc, "code", None)
            if type(exc).__name__ == "TargetError" and self._target_error_is_definitive(exc):
                if code == "target_not_found":
                    return False
                if code == "target_ambiguous":
                    candidates = self._collect_ambiguous_candidates(session, locator)
                    raise _AmbiguousResolutionError(candidates, cause=exc) from None
            self._raise_resolution_error(exc)
        return True

    def _element_signature(self, element: Any) -> tuple[object, ...]:
        try:
            inspection = element.inspect(
                attributes=("type", "id", "label", "title", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        return self._signature_from_inspection(inspection)

    @staticmethod
    def _signature_from_inspection(inspection: Any) -> tuple[object, ...]:
        attrs = {item.name: item.value if item.state == "value" else None for item in getattr(inspection, "attributes", ())}
        bounds = attrs.get("bounds")
        bounds_tuple = (bounds.x, bounds.y, bounds.width, bounds.height) if bounds is not None and hasattr(bounds, "x") else None
        return (attrs.get("type"), attrs.get("id"), attrs.get("label"), attrs.get("title"), attrs.get("value"), bounds_tuple)

    def _build_session_locator_query(self, locator: MacOSLocator) -> tuple[str, str, str | None]:
        if locator.xpath:
            constraints = self._xpath_constraints(locator)
            selector = f"({locator.xpath})"
            if constraints:
                selector += "[" + " and ".join(constraints) + "]"
            return "xpath", selector, None

        if locator.role:
            constraints = self._xpath_constraints(locator)
            selector = "//*[" + " and ".join(constraints) + "]"
            return "xpath", selector, None

        predicates = self._predicate_constraints(locator)
        if locator.predicate:
            combined_extra = " AND ".join(predicates) if predicates else None
            return "-ios predicate string", locator.predicate, combined_extra

        # Prefer exact single-field native strategies when unaccompanied.
        if locator.accessibilityId and not any((locator.name, locator.label, locator.value, locator.title, locator.controlType, locator.className)):
            return "accessibility id", locator.accessibilityId, None

        if not predicates:
            raise ConfigurationError("macOS locator requires at least one constraint.", context={"resolution_reason": "invalid_locator"})
        return "-ios predicate string", " AND ".join(predicates), None

    def _xpath_constraints(self, locator: MacOSLocator) -> list[str]:
        constraints: list[str] = []
        for field, attribute in (
            ("accessibilityId", "identifier"),
            ("label", "label"),
            ("value", "value"),
            ("title", "title"),
            ("role", "role"),
        ):
            raw = getattr(locator, field)
            if raw:
                constraints.append(f"@{attribute}={_xpath_literal(raw)}")
        if locator.name:
            literal = _xpath_literal(locator.name)
            constraints.append("(" + " or ".join(f"@{key}={literal}" for key in ("identifier", "name", "label", "value", "title")) + ")")
        for type_val in (locator.controlType, locator.className):
            if type_val:
                full = type_val if type_val.startswith("XCUIElementType") else f"XCUIElementType{type_val}"
                raw_lit = _xpath_literal(type_val)
                full_lit = _xpath_literal(full)
                constraints.append(f"(name()={raw_lit} or name()={full_lit} or @type={raw_lit})")
        return constraints

    def _predicate_constraints(self, locator: MacOSLocator) -> list[str]:
        clauses: list[str] = []
        if locator.accessibilityId:
            clauses.append(f"identifier == {_predicate_literal(locator.accessibilityId)}")
        if locator.label:
            clauses.append(f"label == {_predicate_literal(locator.label)}")
        if locator.value:
            clauses.append(f"value == {_predicate_literal(locator.value)}")
        if locator.title:
            clauses.append(f"title == {_predicate_literal(locator.title)}")
        if locator.name:
            lit = _predicate_literal(locator.name)
            clauses.append(f"(identifier == {lit} OR label == {lit} OR value == {lit} OR title == {lit})")
        for type_val in (locator.controlType, locator.className):
            if type_val:
                if type_val.startswith("XCUIElementType"):
                    clauses.append(f"amType == {_predicate_literal(type_val)}")
                else:
                    prefixed = f"XCUIElementType{type_val}"
                    clauses.append(f"(amType == {_predicate_literal(type_val)} OR amType == {_predicate_literal(prefixed)})")
        return clauses

    def _collect_ambiguous_candidates(self, session: Any, locator: MacOSLocator) -> list[dict[str, object]]:
        try:
            observation = session.observe_tree(control=self._ActionControl(self))
        except Exception:  # noqa: BLE001
            return []
        matches: list[dict[str, object]] = []
        stack = list(reversed(getattr(observation, "roots", ())))
        while stack and len(matches) < 5:
            node = stack.pop()
            xml_node = self._tree_node_to_xml_element(node)
            if self._xml_node_matches_locator(xml_node, locator):
                matches.append(candidate(xml_node))
            children = getattr(node, "children", ())
            if children:
                stack.extend(reversed(children))
        return matches

    @staticmethod
    def _xml_node_matches_locator(node: ElementTree.Element, locator: MacOSLocator) -> bool:
        attrs = node.attrib
        if locator.accessibilityId and attrs.get("identifier") != locator.accessibilityId:
            return False
        if locator.label and attrs.get("label") != locator.label:
            return False
        if locator.value and attrs.get("value") != locator.value:
            return False
        if locator.title and attrs.get("title") != locator.title:
            return False
        if locator.role and attrs.get("role") != locator.role:
            return False
        if locator.name and not any(attrs.get(key) == locator.name for key in ("identifier", "name", "label", "value", "title")):
            return False
        for type_val in (locator.controlType, locator.className):
            if type_val:
                prefixed = type_val if type_val.startswith("XCUIElementType") else f"XCUIElementType{type_val}"
                if node.tag not in {type_val, prefixed} and attrs.get("type") not in {type_val, prefixed}:
                    return False
        return True

    def _point_from_endpoint(self, endpoint: MacOSDragEndpoint) -> MacOSPoint | None:
        element = self._resolve_element_or_none(endpoint)
        if element is not None:
            return self._element_center(element)
        if self._has_semantic_target(endpoint):
            return None
        return self._coordinate_only_point(endpoint)

    def _perform_pointer_click(
        self,
        point: MacOSPoint,
        *,
        button: str = "left",
        double: bool = False,
        modifier_flags: int | None = None,
    ) -> None:
        session = self._require_session()
        try:
            session.device.click_at(
                point.x,
                point.y,
                button="right" if button == "right" else "left",
                double=double,
                key_modifier_flags=modifier_flags,
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

    def _perform_pointer_hover(self, point: MacOSPoint) -> None:
        session = self._require_session()
        try:
            session.device.hover_at(point.x, point.y, control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

    def _perform_pointer_drag(self, source: MacOSPoint, destination: MacOSPoint, *, duration_ms: int | None) -> None:
        session = self._require_session()
        effective_duration = max(1, min(duration_ms if duration_ms is not None else 250, 60_000))
        timeout_seconds = max(float(self.action_timeout_seconds), (effective_duration / 1000.0) + 5.0)
        try:
            session.device.swipe_between(
                source.x,
                source.y,
                destination.x,
                destination.y,
                duration_ms=effective_duration,
                control=self._operation_control(timeout_seconds, cancellation=self._operation_cancellation),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

    def _send_macos_text(self, text: str, *, element: Any | None = None) -> None:
        session = self._require_session()
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized_text:
            return
        keys: list[Any] = [self._build_key_item(MACOS_KEY_NAMES["ENTER"] if character == "\n" else character, 0) for character in normalized_text]
        for start in range(0, len(keys), 1024):
            chunk = keys[start : start + 1024]
            try:
                if element is not None:
                    element.send_macos_keys(
                        chunk,
                        wait_timeout_seconds=self._wait_timeout(),
                        control=self._ActionControl(self),
                    )
                else:
                    session.device.send_keys(chunk, control=self._ActionControl(self))
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                self._raise_resolution_error(exc)

    @staticmethod
    def _build_key_item(key: str, modifier_flags: int) -> Any:
        if modifier_flags == 0:
            return key
        try:
            from appium_connect_agent.automation import MacKeyStroke
        except ImportError:
            return {"key": key, "modifierFlags": modifier_flags}
        return MacKeyStroke(key=key, modifier_flags=modifier_flags)

    @staticmethod
    def _modifier_flags(modifiers: Sequence[str] | None) -> int | None:
        if not modifiers:
            return None
        flags = 0
        for modifier in modifiers:
            normalized = modifier.strip().upper()
            if normalized not in MACOS_KEY_MODIFIER_FLAGS:
                raise ConfigurationError(f"Unsupported macOS key modifier: {modifier}.")
            flags |= MACOS_KEY_MODIFIER_FLAGS[normalized]
        return flags

    def _element_displayed(self, element: Any) -> bool:
        obs = self._inspect_obs_map(element, ("displayed",), stage="assert_visible").get("displayed")
        if getattr(obs, "state", None) == "value" and type(obs.value) is bool:
            return bool(obs.value)
        raise ToolExecutionError("Mac2 element cannot report visibility.", context={"stage": "assert_visible"})

    def _element_center(self, element: Any | None) -> MacOSPoint | None:
        rect = self._element_rect(element)
        if rect is None:
            return None
        return MacOSPoint(
            x=int(rect["x"] + rect["width"] / 2.0),
            y=int(rect["y"] + rect["height"] / 2.0),
        )

    def _element_rect(self, element: Any | None) -> dict[str, float] | None:
        if element is None:
            return None
        try:
            inspection = element.inspect(attributes=("bounds",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        attrs = getattr(inspection, "attributes", ())
        if not attrs or getattr(attrs[0], "state", None) != "value":
            return None
        bounds = attrs[0].value
        if bounds is not None and all(hasattr(bounds, key) for key in ("x", "y", "width", "height")):
            return {
                "x": float(bounds.x),
                "y": float(bounds.y),
                "width": float(bounds.width),
                "height": float(bounds.height),
            }
        return None

    @staticmethod
    def _order_coordinate(rect: dict[str, float], direction: str) -> float:
        if direction == "horizontal":
            return rect["x"] + rect["width"] / 2.0
        return rect["y"] + rect["height"] / 2.0

    @staticmethod
    def _order_matches(entries: list[dict[str, object]], expected_order: list[int], tolerance: float) -> bool:
        coordinates_by_index = {int(entry["index"]): float(entry["coordinate"]) for entry in entries}
        if any(index not in coordinates_by_index for index in expected_order):
            return False
        expected_coordinates = [coordinates_by_index[index] for index in expected_order]
        return all(left <= right + tolerance for left, right in pairwise(expected_coordinates))

    def _simplify_tree_observation(
        self,
        observation: Any,
        *,
        max_depth: int,
        include_attributes: bool,
    ) -> dict[str, object]:
        roots = getattr(observation, "roots", ())
        if not roots:
            return {"format": "xml", "root": None, "source_length": 0}
        xml_root = self._tree_node_to_xml_element(roots[0])
        serialized = ElementTree.tostring(xml_root, encoding="unicode")
        try:
            return {
                "format": "xml",
                "source_length": len(serialized),
                "node_count": self._element_count(xml_root),
                "root": self._element_snapshot(
                    xml_root,
                    depth=1,
                    max_depth=max_depth,
                    include_attributes=include_attributes,
                ),
            }
        except Exception as exc:  # noqa: BLE001 - preserve observation availability on unexpected local compaction failure.
            self._propagate_if_cancelled(exc)
            preview = serialized[:DEFAULT_MACOS_SNAPSHOT_SOURCE_PREVIEW_CHARS]
            return {
                "format": "unparsed_xml",
                "source_preview": preview,
                "source_length": len(serialized),
                "truncated": len(serialized) > len(preview),
                "parse_error": type(exc).__name__,
            }

    def _tree_node_to_xml_element(self, node: Any) -> ElementTree.Element:
        type_obs = getattr(node, "type", None)
        tag = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) and type_obs.value else "XCUIElementTypeAny"
        attribs: dict[str, str] = {}
        for attr_field, xml_name in (
            ("id", "identifier"),
            ("label", "label"),
            ("title", "title"),
            ("value", "value"),
            ("placeholder_value", "placeholderValue"),
        ):
            obs = getattr(node, attr_field, None)
            if getattr(obs, "state", None) == "value" and isinstance(obs.value, str) and obs.value:
                attribs[xml_name] = obs.value

        element_type_obs = getattr(node, "element_type", None)
        if getattr(element_type_obs, "state", None) == "value" and _is_strict_int(element_type_obs.value):
            attribs["elementType"] = str(element_type_obs.value)

        for bool_field in ("enabled", "selected"):
            obs = getattr(node, bool_field, None)
            if getattr(obs, "state", None) == "value" and type(obs.value) is bool:
                attribs[bool_field] = "true" if obs.value else "false"

        visibility = getattr(node, "visibility", None)
        if visibility == "not_visible":
            attribs["visible"] = "false"
        elif visibility in {"visible", "partially_visible"}:
            attribs["visible"] = "true"

        bounds_obs = getattr(node, "bounds", None)
        bounds_val = getattr(bounds_obs, "value", None) if getattr(bounds_obs, "state", None) == "value" else None
        if bounds_val is not None and all(hasattr(bounds_val, key) for key in ("x", "y", "width", "height")):
            attribs["x"] = str(int(bounds_val.x))
            attribs["y"] = str(int(bounds_val.y))
            attribs["width"] = str(int(bounds_val.width))
            attribs["height"] = str(int(bounds_val.height))

        extra_attrs = getattr(node, "extra_attributes", None)
        if isinstance(extra_attrs, dict):
            for key, raw_val in extra_attrs.items():
                if isinstance(key, str) and key not in attribs and raw_val is not None:
                    attribs[key] = str(raw_val)

        xml_el = ElementTree.Element(tag, attribs)
        raw_text = getattr(node, "raw_text", None)
        if isinstance(raw_text, str) and raw_text:
            xml_el.text = raw_text
        for child in getattr(node, "children", ()):
            xml_el.append(self._tree_node_to_xml_element(child))
        return xml_el

    def _element_snapshot(
        self,
        element: ElementTree.Element,
        *,
        depth: int,
        max_depth: int,
        include_attributes: bool,
    ) -> dict[str, object]:
        snapshot = self._compact_element_snapshots(element, include_attributes=include_attributes, preserve_node=True)[0]
        self._bound_snapshot_depth(snapshot, depth=depth, max_depth=max_depth)
        return snapshot

    def _compact_element_snapshots(
        self,
        element: ElementTree.Element,
        *,
        include_attributes: bool,
        preserve_node: bool = False,
    ) -> list[dict[str, object]]:
        children = [child_snapshot for child in element for child_snapshot in self._compact_element_snapshots(child, include_attributes=include_attributes)]
        attributes = self._snapshot_attributes(element.attrib, include_attributes=include_attributes)
        text = (element.text or "").strip()[:DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS]
        interactive = self._xml_name(element.tag).removeprefix("XCUIElementType") in MACOS_INTERACTIVE_SHORT_TYPES
        if not preserve_node and not interactive and not self._has_snapshot_signal(attributes, text=text):
            return children

        snapshot: dict[str, object] = {"type": self._xml_name(element.tag)}
        if attributes:
            snapshot["attributes"] = attributes
        clipped = [
            key
            for key, value in element.attrib.items()
            if self._xml_name(key) in MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS
            and (include_attributes or self._xml_name(key) in DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS)
            and len(value.strip()) > DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS
        ]
        if clipped:
            snapshot["truncated_attributes"] = clipped
        if len((element.text or "").strip()) > DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS:
            snapshot["text_truncated"] = True
        if text:
            snapshot["text"] = text
        if children:
            snapshot["children"] = children
        return [snapshot]

    def _bound_snapshot_depth(self, snapshot: dict[str, object], *, depth: int, max_depth: int) -> None:
        children = snapshot.get("children")
        if not isinstance(children, list) or not children:
            return
        if depth >= max_depth:
            snapshot.pop("children")
            snapshot["children_truncated"] = len(children)
            return
        for child in children:
            if isinstance(child, dict):
                self._bound_snapshot_depth(child, depth=depth + 1, max_depth=max_depth)

    def _snapshot_attributes(self, attributes: dict[str, str], *, include_attributes: bool) -> dict[str, object]:
        selected: dict[str, object] = {}
        for key, raw_value in attributes.items():
            normalized_key = self._xml_name(key)
            value = str(raw_value).strip()
            if not value or (not include_attributes and normalized_key not in DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS):
                continue
            default_state = MACOS_SNAPSHOT_STATE_DEFAULTS.get(normalized_key)
            if default_state is not None and value.lower() == default_state:
                continue
            if normalized_key in MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS:
                value = value[:DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS]
            selected[normalized_key] = value
        return selected

    def _has_snapshot_signal(self, attributes: dict[str, object], *, text: str) -> bool:
        non_default_state = any(key in MACOS_SNAPSHOT_STATE_DEFAULTS for key in attributes)
        identity_or_text = bool(text) or any(key in MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS for key in attributes)
        if self._is_zero_size(attributes) or str(attributes.get("visible", "")).lower() == "false":
            return identity_or_text or non_default_state
        return identity_or_text or non_default_state or any(key in MACOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS for key in attributes)

    @staticmethod
    def _is_zero_size(attributes: dict[str, object]) -> bool:
        for key in ("width", "height"):
            value = attributes.get(key)
            if value is None:
                continue
            try:
                if float(str(value)) == 0:
                    return True
            except ValueError:
                continue
        return False

    def _element_count(self, element: ElementTree.Element) -> int:
        return 1 + sum(self._element_count(child) for child in list(element))

    @staticmethod
    def _xml_name(value: str) -> str:
        return value.rsplit("}", 1)[-1] if "}" in value else value

    def _viewport_size(self, session: Any) -> tuple[int, int] | None:
        observe_fn = getattr(session, "observe_tree", None)
        if not callable(observe_fn):
            return None
        try:
            observation = observe_fn(control=self._ActionControl(self))
        except Exception:  # noqa: BLE001
            return None
        viewport = getattr(observation, "viewport", None)
        width_obs = getattr(viewport, "width", None)
        height_obs = getattr(viewport, "height", None)
        if (
            getattr(width_obs, "state", None) == "value"
            and getattr(height_obs, "state", None) == "value"
            and _is_strict_int(width_obs.value)
            and _is_strict_int(height_obs.value)
            and width_obs.value > 0
            and height_obs.value > 0
        ):
            return int(width_obs.value), int(height_obs.value)
        return None

    @staticmethod
    def _is_secure_text_type(elem_type: str | None) -> bool:
        if not isinstance(elem_type, str) or not elem_type:
            return False
        return elem_type.removeprefix("XCUIElementType") == "SecureTextField"

    @staticmethod
    def _is_masked_string(value: str) -> bool:
        return bool(value) and all(ch in MACOS_MASK_GLYPHS for ch in value)

    @staticmethod
    def _bounds_int_tuple(bounds_obs: Any) -> tuple[int, int, int, int] | None:
        if getattr(bounds_obs, "state", None) != "value":
            return None
        bounds = getattr(bounds_obs, "value", None)
        if bounds is None or not all(hasattr(bounds, key) for key in ("x", "y", "width", "height")):
            return None
        bx, by, bw, bh = int(bounds.x), int(bounds.y), int(bounds.width), int(bounds.height)
        if bw <= 0 or bh <= 0:
            return None
        return (bx, by, bw, bh)

    def _serialize_attribute_observation(self, name: str, obs: Any) -> dict[str, object]:
        state = getattr(obs, "state", "unknown") if obs is not None else "unknown"
        if state not in {"value", "unknown", "not_applicable", "missing"}:
            state = "unknown"
        if state != "value":
            return {"state": state, "value": None}
        raw_val = getattr(obs, "value", None)
        if name == "bounds":
            if raw_val is not None and all(hasattr(raw_val, key) for key in ("x", "y", "width", "height")):
                return {
                    "state": "value",
                    "value": {
                        "x": int(raw_val.x),
                        "y": int(raw_val.y),
                        "width": int(raw_val.width),
                        "height": int(raw_val.height),
                    },
                }
            return {"state": "unknown", "value": None}
        if name in {"text", "value"} and isinstance(raw_val, str) and self._is_masked_string(raw_val):
            return {"state": "unavailable", "value": None, "reason": "protected_text"}
        return {"state": "value", "value": raw_val}

    def _inspect_obs_map(self, element: Any, attributes: tuple[str, ...], *, stage: str) -> dict[str, Any]:
        try:
            inspection = element.inspect(attributes=attributes, control=self._ActionControl(self))
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            raise ToolExecutionError(
                "macOS element inspection failed.",
                context={"stage": stage, "cause": exc},
            ) from exc
        return {item.name: item for item in getattr(inspection, "attributes", ())}

    @staticmethod
    def _locator_from_params(params: BaseModel) -> MacOSLocator:
        data = params.model_dump(mode="python", exclude_none=True)
        locator = data.get("locator")
        if isinstance(locator, dict):
            return MacOSLocator.model_validate(locator)
        target = data.get("target")
        if isinstance(target, str) and target.strip():
            return MacOSLocator(name=target.strip())
        raise ConfigurationError("macOS element lookup requires target or locator.", context={"resolution_reason": "invalid_locator"})

    def _poll_unique_element(self, locator: MacOSLocator) -> Any | None:
        deadline = time.monotonic() + self._wait_timeout()
        while True:
            element = self._element_from_locator(locator)
            if element is not None:
                return element
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)

    def _evaluate_text_assertion(self, element: Any, text_assertion: Any) -> dict[str, object]:
        attr_choice = text_assertion.attribute
        actual_text: str | None = None
        selected_attr: str | None = None

        if attr_choice in {"label", "title", "placeholder_value"}:
            obs = self._inspect_obs_map(element, (attr_choice,), stage="assert_state").get(attr_choice)
            if getattr(obs, "state", None) == "value" and isinstance(obs.value, str):
                actual_text = obs.value
                selected_attr = attr_choice
            elif getattr(obs, "state", None) == "missing":
                actual_text = ""
                selected_attr = attr_choice
            else:
                return self._failed(
                    "assertion_error",
                    f"Target attribute '{attr_choice}' is unavailable for text assertion.",
                    output={"attribute": attr_choice, "text": None},
                    metadata={"reason": "protected_or_unreadable_text"},
                )
        else:
            type_obs = self._inspect_obs_map(element, ("type",), stage="assert_state").get("type")
            type_val = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) else None
            if not type_val or self._is_secure_text_type(type_val):
                return self._failed(
                    "assertion_error",
                    "Target text is protected or unreadable.",
                    output={"attribute": attr_choice, "text": None},
                    metadata={"reason": "protected_or_unreadable_text"},
                )
            if attr_choice in {"value", "text"}:
                obs = self._inspect_obs_map(element, (attr_choice,), stage="assert_state").get(attr_choice)
                if getattr(obs, "state", None) == "value" and isinstance(obs.value, str) and not self._is_masked_string(obs.value):
                    actual_text = obs.value
                    selected_attr = attr_choice
                elif getattr(obs, "state", None) == "missing":
                    actual_text = ""
                    selected_attr = attr_choice
                else:
                    return self._failed(
                        "assertion_error",
                        f"Target attribute '{attr_choice}' is protected or unreadable.",
                        output={"attribute": attr_choice, "text": None},
                        metadata={"reason": "protected_or_unreadable_text"},
                    )
            else:
                candidates_map = self._inspect_obs_map(element, ("value", "label", "title", "text"), stage="assert_state")
                for candidate_attr in ("value", "label", "title", "text"):
                    obs = candidates_map.get(candidate_attr)
                    if getattr(obs, "state", None) == "value" and isinstance(obs.value, str):
                        if candidate_attr in {"value", "text"} and self._is_masked_string(obs.value):
                            return self._failed(
                                "assertion_error",
                                "Target text is masked.",
                                output={"attribute": candidate_attr, "text": None},
                                metadata={"reason": "protected_or_unreadable_text"},
                            )
                        actual_text = obs.value
                        selected_attr = candidate_attr
                        break
                if actual_text is None:
                    actual_text = ""
                    selected_attr = "auto"

        matched = actual_text == text_assertion.equals if text_assertion.equals is not None else (text_assertion.contains in actual_text if text_assertion.contains is not None else False)
        if matched:
            return self._passed({"attribute": selected_attr, "text": actual_text})
        return self._failed(
            "assertion_error",
            "macOS element text did not match expectation.",
            output={"attribute": selected_attr, "text": actual_text},
        )

    @staticmethod
    def _target_depends_on_mutable_text(params: MacOSTypeTextParams) -> bool:
        if isinstance(params.target, str) and params.target.strip():
            return True
        loc = params.locator
        if loc is None:
            return False
        if any(isinstance(val, str) and val.strip() for val in (loc.name, loc.value, loc.label, loc.title)):
            return True
        return any(isinstance(selector, str) and bool(MACOS_MUTABLE_TEXT_SELECTOR_TOKEN.search(selector)) for selector in (loc.xpath, loc.predicate))

    def _bind_element_by_id_or_geometry(
        self,
        session: Any,
        *,
        elem_id: str,
        elem_type: str | None,
        bounds_tuple: tuple[int, int, int, int] | None,
    ) -> Any | None:
        try:
            candidate_el = session.element("accessibility id", elem_id)
            obs_map = self._inspect_obs_map(candidate_el, ("type", "id", "bounds"), stage="identity_binding")
            obs_id = obs_map.get("id")
            obs_type = obs_map.get("type")
            if getattr(obs_id, "state", None) == "value" and obs_id.value == elem_id:
                if elem_type is None or (getattr(obs_type, "state", None) == "value" and obs_type.value == elem_type):
                    return candidate_el
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
        if elem_type and bounds_tuple is not None:
            bx, by, bw, bh = bounds_tuple
            predicate = (
                f"identifier == {_predicate_literal(elem_id)} AND amType == {_predicate_literal(elem_type)} "
                f"AND amRect.x == {bx} AND amRect.y == {by} AND amRect.width == {bw} AND amRect.height == {bh}"
            )
            return self._probe_bound_predicate_element(session, predicate, expected_id=elem_id, expected_type=elem_type, expected_bounds=bounds_tuple)
        return None

    def _bind_element_by_geometry(
        self,
        session: Any,
        *,
        elem_type: str,
        bounds_tuple: tuple[int, int, int, int],
    ) -> Any | None:
        bx, by, bw, bh = bounds_tuple
        predicate = f"amType == {_predicate_literal(elem_type)} AND amRect.x == {bx} AND amRect.y == {by} AND amRect.width == {bw} AND amRect.height == {bh}"
        return self._probe_bound_predicate_element(session, predicate, expected_id=None, expected_type=elem_type, expected_bounds=bounds_tuple)

    def _probe_bound_predicate_element(
        self,
        session: Any,
        predicate: str,
        *,
        expected_id: str | None,
        expected_type: str,
        expected_bounds: tuple[int, int, int, int],
    ) -> Any | None:
        try:
            candidate_el = session.element("-ios predicate string", predicate)
            obs_map = self._inspect_obs_map(candidate_el, ("type", "id", "bounds"), stage="identity_binding")
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return None
        obs_type = obs_map.get("type")
        if getattr(obs_type, "state", None) != "value" or obs_type.value != expected_type:
            return None
        if expected_id is not None:
            obs_id = obs_map.get("id")
            if getattr(obs_id, "state", None) != "value" or obs_id.value != expected_id:
                return None
        obs_bounds = self._bounds_int_tuple(obs_map.get("bounds"))
        if obs_bounds != expected_bounds:
            return None
        return candidate_el

    def _verify_post_entry_target(
        self,
        bound_element: Any,
        *,
        expected_type: str | None,
        expected_id: str | None,
        expected_bounds: tuple[int, int, int, int] | None,
        include_value: bool,
    ) -> str | None:
        attrs = ("type", "id", "enabled", "value", "bounds") if include_value else ("type", "id", "enabled", "bounds")
        try:
            obs_map = self._inspect_obs_map(bound_element, attrs, stage="post_verification")
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return None
        if expected_type is not None:
            obs_type = obs_map.get("type")
            if getattr(obs_type, "state", None) == "value" and obs_type.value != expected_type:
                return None
        if expected_id is not None:
            obs_id = obs_map.get("id")
            if getattr(obs_id, "state", None) == "value" and obs_id.value != expected_id:
                return None
        if expected_bounds is not None:
            if self._bounds_int_tuple(obs_map.get("bounds")) != expected_bounds:
                return None
        if not include_value:
            return ""
        val_obs = obs_map.get("value")
        val_state = getattr(val_obs, "state", None)
        if val_state == "value" and isinstance(val_obs.value, str) and not self._is_masked_string(val_obs.value):
            return val_obs.value
        if val_state == "missing":
            return ""
        return None

    @staticmethod
    def _value_matches(actual: str, expected_text: str, *, clear: bool, initial_value: str | None) -> bool:
        if clear:
            return actual == expected_text
        return actual == f"{initial_value}{expected_text}" if initial_value is not None else actual.endswith(expected_text)

    def _raise_resolution_error(self, error: Exception) -> None:
        cls_name = type(error).__name__
        code = getattr(error, "code", None)
        if cls_name in {"ElementValidationError", "InvalidRequestError"} or code in {
            "invalid_selector",
            "invalid_argument",
            "unsupported_operation",
        }:
            raise ConfigurationError("macOS element lookup failed: invalid_locator.", context={"resolution_reason": "invalid_locator"}) from None
        if cls_name == "InvalidSessionStateError" or code in {
            "session_closed",
            "session_disposed",
            "session_invalid",
            "session_lost",
            "connection_closed",
            "connection_closing",
        }:
            if not self._borrowed_session:
                self._cleanup_pending = True
            raise ToolExecutionError(
                "macOS session is unavailable.",
                context={"reason": "session_unavailable", **self._safe_diagnostics(error, stage="element_lookup")},
            ) from None
        raise ToolExecutionError(
            "macOS backend operation failed.",
            context={"stage": "element_lookup", "cause": error},
        ) from error

    def _run_tool(
        self,
        func: Callable[[], dict[str, object]],
        *,
        failure_default: str = "action_error",
        allow_cleanup_pending: bool = False,
    ) -> dict[str, object]:
        try:
            return self._run_on_worker(func, allow_cleanup_pending=allow_cleanup_pending)
        except _AmbiguousResolutionError as amb:
            return self._target_ambiguous_result(amb)
        except ValidationError:
            return self._failed("configuration_error", "Invalid macOS automation request parameters.")
        except ConfigurationError as exc:
            if exc.context.get("resolution_reason") == "invalid_locator":
                return self._failed("configuration_error", str(exc), metadata={"resolution_reason": "invalid_locator", **self._safe_diagnostics()})
            raise
        except ToolExecutionError as exc:
            cause = exc.context.get("cause")
            if isinstance(cause, Exception):
                self._propagate_if_cancelled(cause)
                return self._normalize_backend_exception(cause, stage=str(exc.context.get("stage") or "tool"), failure_default=failure_default)
            return self._failed(failure_default, str(exc), metadata=self._safe_diagnostics(exc))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, failure_default=failure_default)

    def _normalize_backend_exception(
        self,
        exc: Exception,
        *,
        stage: str | None = None,
        failure_default: str = "action_error",
    ) -> dict[str, object]:
        if isinstance(exc, ConfigurationError):
            raise exc
        if isinstance(exc, _AmbiguousResolutionError):
            return self._target_ambiguous_result(exc)
        code = getattr(exc, "code", None)
        cls_name = type(exc).__name__
        metadata = self._safe_diagnostics(exc, stage=stage)
        if cls_name in {"InvalidRequestError", "ElementValidationError"} or code in {"invalid_selector", "invalid_argument", "unsupported_operation"}:
            return self._failed("configuration_error", "Invalid macOS automation request parameters.", metadata={"resolution_reason": "invalid_locator", **metadata})
        if cls_name == "TargetError" and self._target_error_is_definitive(exc):
            if code == "target_ambiguous":
                return self._failed(
                    "target_resolution_error",
                    "macOS locator matches multiple elements; refine the locator.",
                    metadata={"resolution_reason": "ambiguous", **metadata},
                )
            if code == "target_not_found":
                return self._failed("target_resolution_error", "Target was not found.", metadata={"resolution_reason": "not_found", **metadata})
        if (
            cls_name == "InvalidSessionStateError"
            or code
            in {
                "session_closed",
                "session_disposed",
                "session_invalid",
                "session_lost",
                "connection_closed",
                "connection_closing",
            }
            or stage == "ensure_alive"
        ):
            if not self._borrowed_session:
                self._cleanup_pending = True
            metadata["reason"] = "session_unavailable"
        return self._failed(failure_default, "macOS backend operation failed.", metadata=metadata)

    def _target_ambiguous_result(self, amb: _AmbiguousResolutionError) -> dict[str, object]:
        diagnostics = self._safe_diagnostics(amb.cause, stage="element_lookup")
        return self._failed(
            "target_resolution_error",
            "macOS locator matches multiple elements; refine the locator.",
            metadata={
                "resolution_reason": "ambiguous",
                "match_count": max(len(amb.candidates), 2),
                "candidates": amb.candidates[:5],
                **diagnostics,
            },
        )

    def _target_error_is_definitive(self, exc: Exception) -> bool:
        if getattr(exc, "request_state", None) == "response_unknown":
            return False
        return self._safe_diagnostics(exc).get("session_state") == "active"

    def _safe_diagnostics(self, exc: Exception | None = None, *, stage: str | None = None) -> dict[str, object]:
        metadata: dict[str, object] = {}
        if stage is not None:
            metadata["stage"] = stage
        if self._closed:
            metadata["reason"] = "closed"
        elif self._cleanup_pending:
            metadata["reason"] = "cleanup_pending"
        if exc is not None:
            code = getattr(exc, "code", None)
            if isinstance(code, str) and code in MACOS_SAFE_BACKEND_CODES:
                metadata["backend_code"] = code
            request_state = getattr(exc, "request_state", None)
            if isinstance(request_state, str) and request_state in {"not_sent", "response_received", "response_unknown"}:
                metadata["request_state"] = request_state
            operation_id = getattr(exc, "operation_id", None)
            if isinstance(operation_id, str) and re.fullmatch(r"[0-9a-f]{32}", operation_id) is not None:
                metadata["operation_id"] = operation_id
            steps = getattr(exc, "completed_steps", None)
            if isinstance(steps, (tuple, list)):
                metadata["completed_steps"] = list(dict.fromkeys(step for step in steps[: len(MACOS_SAFE_COMPLETED_STEPS)] if isinstance(step, str) and step in MACOS_SAFE_COMPLETED_STEPS))
            if isinstance(exc, ToolExecutionError):
                reason = exc.context.get("reason")
                if reason in {"closed", "cleanup_pending", "session_unavailable"}:
                    metadata["reason"] = reason
        try:
            if self._session is not None:
                info_fn = getattr(self._session, "session_info", None)
                state = getattr(info_fn(), "state", "active") if callable(info_fn) else "active"
                if isinstance(state, str) and state in MACOS_SESSION_STATES:
                    metadata["session_state"] = state
        except BaseException:  # noqa: BLE001
            return metadata
        return metadata

    @staticmethod
    def _propagate_if_cancelled(exc: BaseException) -> None:
        if type(exc).__name__ == "OperationCancelledError" or getattr(exc, "code", None) == "operation_cancelled":
            import asyncio

            raise asyncio.CancelledError from exc

    def _wait_timeout(self) -> float:
        return max(0.5, float(self.action_timeout_seconds))

    @staticmethod
    def _ActionControl(driver: AppiumConnectMacOSDriver) -> Any:
        return driver._operation_control(max(5.0, float(driver.action_timeout_seconds) + 5.0), cancellation=driver._operation_cancellation)

    @staticmethod
    def _operation_control(timeout_seconds: float, *, cancellation: Event | None = None) -> Any:
        try:
            from appium_connect_agent.runtime import OperationControl
        except ImportError:
            return None
        return OperationControl(timeout_seconds=timeout_seconds, cancellation=cancellation)

    def _run_on_worker(
        self,
        func: Callable[[], _T],
        *,
        cancellation_check: Callable[[], None] | None = None,
        cancellation_event: Event | None = None,
        allow_cleanup_pending: bool = False,
    ) -> _T:
        if self._closed or (self._cleanup_pending and not allow_cleanup_pending):
            return func()

        def run_operation() -> _T:
            previous_cancellation = self._operation_cancellation
            self._operation_cancellation = cancellation_event
            try:
                return func()
            finally:
                self._operation_cancellation = previous_cancellation
                if self._session is not None:
                    self._last_operation_finished_at = time.monotonic()

        if self._borrowed_session and self._executor is None and cancellation_check is None:
            return run_operation()
        self._ensure_executor()
        try:
            return self._run_sync(run_operation, cancellation_check=cancellation_check, cancellation_event=cancellation_event)
        except BaseException:
            if not self._cleanup_pending and self._session is None and self._managed_scope is None and self._runtime is None:
                self._shutdown_executor()
            raise

    def _run_sync(
        self,
        func: Callable[[], _T],
        *,
        cancellation_check: Callable[[], None] | None = None,
        cancellation_event: Event | None = None,
    ) -> _T:
        if self._executor is None:
            return func()
        future = self._executor.submit(func)
        if cancellation_check is None:
            return future.result()
        while True:
            try:
                return future.result(timeout=0.025)
            except TimeoutError:
                if future.done():
                    return future.result()
                try:
                    cancellation_check()
                except BaseException:
                    if cancellation_event is not None:
                        cancellation_event.set()
                    with suppress(BaseException):
                        future.result()
                    raise

    def _ensure_executor(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-macos-driver")

    def _shutdown_executor(self) -> None:
        executor = self._executor
        if executor is not None:
            executor.shutdown(wait=True)
            self._executor = None

    def _target_missing(self) -> dict[str, object]:
        return self._failed(
            "target_resolution_error",
            "Target was not found.",
            metadata={"resolution_reason": "not_found", **self._safe_diagnostics()},
        )

    @staticmethod
    def _passed(output: object | None = None) -> dict[str, object]:
        return {"status": "passed", "output": output}

    @staticmethod
    def _failed(
        failure_category: str,
        error_message: str,
        *,
        output: object | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        return {
            "status": "failed",
            "failure_category": failure_category,
            "error_message": error_message,
            "output": output,
            "metadata": metadata or {},
        }
