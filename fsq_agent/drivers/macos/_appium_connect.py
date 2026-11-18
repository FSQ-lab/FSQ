# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import logging
import os
import socket
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from xml.etree import ElementTree

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _macos_driver_tool
from fsq_agent.drivers.macos._elements import candidate
from fsq_agent.models import (
    ConfigurationError,
    MacOSAssertElementsOrderParams,
    MacOSAssertVisibleParams,
    MacOSAssertWithAIParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragEndpoint,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSLocator,
    MacOSPoint,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
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
DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS = 300
DEFAULT_MACOS_SNAPSHOT_SOURCE_PREVIEW_CHARS = 2000
DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS = 50
DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS = 15.0
DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS = 10.0
MAX_PORT_BIND_ATTEMPTS = 3

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
        new_command_timeout_seconds: int = DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS,
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

    def close(self) -> None:
        if self._borrowed_session:
            self._shutdown_executor()
            return
        if self._session is not None or self._managed_scope is not None or self._runtime is not None or self._connection is not None:
            self._run_sync(self._close_owned_resources)
        try:
            self._shutdown_executor()
        except BaseException as shutdown_error:
            primary_error = self._cleanup_primary
            if primary_error is not None:
                if isinstance(primary_error, (CancelledError, KeyboardInterrupt, SystemExit)):
                    raise primary_error from shutdown_error
                raise BaseExceptionGroup("macOS session and owner-thread shutdown failed.", [primary_error, shutdown_error]) from None
            raise
        primary_error = self._cleanup_primary
        self._cleanup_primary = None
        if primary_error is not None:
            raise primary_error

    def _close_owned_resources(self) -> None:
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
        if primary_error is not None:
            self._cleanup_primary = None
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
        if session is None:
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
            if state in {"closed", "disposed"}:
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
        return self._run_on_worker(lambda: self._launch_app_impl(params))

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
        return self._run_on_worker(lambda: self._kill_app_impl(params))

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
                self._close_owned_resources()
            return self._passed({"bundle_id": bundle_id, "close_session": True})
        if not bundle_id:
            raise ConfigurationError("macOS application termination requires bundle_id or a configured bundle id.")
        try:
            session.device.terminate_app(bundle_id, control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        return self._passed({"bundle_id": bundle_id, "close_session": False})

    @_macos_driver_tool("clickOn", description="Click a macOS element or point resolved through Appium Mac2.")
    def click_on(self, params: MacOSClickOnParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._click_on_impl(params))

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
                self._raise_resolution_error(exc)
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
        return self._run_on_worker(lambda: self._double_click_on_impl(params))

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
                self._raise_resolution_error(exc)
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
        return self._run_on_worker(lambda: self._right_click_on_impl(params))

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
                self._raise_resolution_error(exc)
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
        return self._run_on_worker(lambda: self._type_text_impl(params))

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
        initial_value = self._element_value(element)
        try:
            element.click(
                wait_timeout_seconds=self._wait_timeout(),
                control=self._ActionControl(self),
            )
            if params.clear:
                element.clear(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            element.send_keys(
                params.text,
                wait_timeout_seconds=self._wait_timeout(),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        if self._element_value_reflects_text(element, params.text, clear=bool(params.clear), initial_value=initial_value):
            return self._passed()

        try:
            if params.clear:
                element.clear(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            self._send_macos_text(params.text, element=element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        if self._element_value_reflects_text(element, params.text, clear=bool(params.clear), initial_value=initial_value):
            return self._passed()
        return self._failed("action_error", "macOS text entry did not update the target element value after fallback.")

    @_macos_driver_tool("pressKey", description="Send a keyboard shortcut or key to macOS.")
    def press_key(self, params: MacOSPressKeyParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._press_key_impl(params))

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
            self._raise_resolution_error(exc)
        return self._passed({"key": params.key, "modifiers": params.modifiers or []})

    @_macos_driver_tool("hoverOn", description="Move the pointer over a macOS element or point.")
    def hover_on(self, params: MacOSHoverOnParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._hover_on_impl(params))

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
                self._raise_resolution_error(exc)
            return self._passed({"point": center.model_dump(mode="json")} if center is not None else None)
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_hover(point)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool("dragTo", description="Drag from one macOS element or point to another.")
    def drag_to(self, params: MacOSDragToParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._drag_to_impl(params))

    def _drag_to_impl(self, params: MacOSDragToParams) -> dict[str, object]:
        source = self._point_from_endpoint(params.source)
        destination = self._point_from_endpoint(params.destination)
        if source is None or destination is None:
            return self._failed(
                "target_resolution_error",
                "Drag source or destination was not found.",
                metadata={"params": params.model_dump(mode="json", exclude_none=True)},
            )
        self._perform_pointer_drag(source, destination, duration_ms=params.duration_ms)
        return self._passed({"source": source.model_dump(mode="json"), "destination": destination.model_dump(mode="json")})

    @_macos_driver_tool("takeScreenshot", description="Capture a macOS screenshot through Appium Mac2.")
    def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes:
        return self.screenshot(params)

    @_macos_driver_tool(
        "uiSnapshot",
        description=("Read macOS accessibility evidence as a bounded compact control tree with explicit clipping markers. No-match is not proof of invisibility."),
    )
    def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._ui_snapshot_impl(params))

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

    @_macos_driver_tool("assertVisible", description="Assert that a macOS element is visible.")
    def assert_visible(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._assert_visible_impl(params))

    def _assert_visible_impl(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is None:
            return self._target_missing()
        if self._element_displayed(element):
            return self._passed()
        return self._failed("assertion_error", "Target is present but not visible.")

    @_macos_driver_tool("assertElementsOrder", description="Assert that macOS elements appear in the expected visual order.")
    def assert_elements_order(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._assert_elements_order_impl(params))

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

    @_macos_driver_tool("assertWithAI", description="Evaluate an explicit macOS visual assertion with AI.")
    def assert_with_ai(self, params: MacOSAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        del params
        return self._run_on_worker(self._screenshot_impl)

    def _screenshot_impl(self) -> bytes:
        session = self._require_session()
        try:
            data = session.device.take_screenshot(control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        if isinstance(data, bytes) and data:
            return data
        raise ConfigurationError("Appium Mac2 screenshot capture returned no image.")

    def _ensure_session(self, params: MacOSLaunchAppParams | None = None) -> Any:
        params = params or MacOSLaunchAppParams()
        if self._session is not None and not self._session_is_active(self._session):
            if not self._borrowed_session:
                self._close_owned_resources()
            else:
                self._session = None

        if self._session is None:
            return self._create_session(params)
        if params.new_session:
            self._require_creation_identity(params)
            if self._borrowed_session:
                quit_fn = getattr(self._session, "quit", None)
                if callable(quit_fn):
                    quit_fn()
                self._session = None
            else:
                self._close_owned_resources()
            return self._create_session(params)
        if params.app_path is not None or params.arguments is not None:
            raise ConfigurationError("app_path and arguments require new_session=true when a Mac2 session already exists.")
        bundle_id = self._effective_bundle_id(params.bundle_id)
        if bundle_id is None:
            raise ConfigurationError("macOS application activation requires bundle_id or a configured bundle id.")
        try:
            self._session.device.activate_app(bundle_id, control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
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
        scope: AbstractContextManager[Any] | None = None
        connection: Any | None = None
        last_port_error: BaseException | None = None
        for _ in range(MAX_PORT_BIND_ATTEMPTS):
            port = _allocate_loopback_port()
            candidate_scope = runtime.start_managed(
                host="127.0.0.1",
                port=port,
                base_path="",
                control=OperationControl(timeout_seconds=DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS),
            )
            try:
                connection = candidate_scope.__enter__()
                scope = candidate_scope
                break
            except ServiceLifecycleError as exc:
                if exc.code == "service_port_in_use":
                    last_port_error = exc
                    continue
                raise

        if scope is None or connection is None:
            if last_port_error is not None:
                raise last_port_error
            raise ConfigurationError("Failed to bind a managed Appium loopback port.")

        try:
            session = create_mac2_session(
                connection,
                bundle_id=bundle_id,
                app_path=resolved_app_path,
                arguments=params.arguments,
                no_reset=True,
                new_command_timeout_seconds=float(self.new_command_timeout_seconds),
                control=OperationControl(timeout_seconds=DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS),
            )
        except BaseException as exc:
            try:
                scope.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException as cleanup_exc:  # noqa: BLE001 - preserve session creation failure.
                logging.getLogger(__name__).warning(
                    "Managed Appium scope cleanup failed after Mac2 session creation error (%s)",
                    type(cleanup_exc).__name__,
                )
            if bool(getattr(runtime, "managed_cleanup_pending", False)):
                try:
                    runtime.cleanup_managed(control=OperationControl(timeout_seconds=DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                except BaseException as cleanup_exc:  # noqa: BLE001
                    logging.getLogger(__name__).debug("Secondary managed cleanup did not complete (%s)", type(cleanup_exc).__name__)
            raise

        self._borrowed_session = False
        self._runtime = runtime
        self._managed_scope = scope
        self._connection = connection
        self._session = session
        self._session_settled = False
        self._scope_exit_attempted = False
        self._cleanup_primary = None
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
        if self._session is None or not self._session_is_active(self._session):
            raise ConfigurationError(
                "Appium Mac2 session is not available. Call launchApp before macOS Appium actions.",
                context={"resolution_reason": "session_unavailable"},
            )
        return self._session

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
            if type(exc).__name__ == "TargetError":
                if code == "target_not_found":
                    return None
                if code == "target_ambiguous":
                    candidates = self._collect_ambiguous_candidates(session, locator)
                    raise ConfigurationError(
                        "macOS locator matches multiple elements; refine the locator.",
                        context={
                            "resolution_reason": "ambiguous",
                            "match_count": max(len(candidates), 2),
                            "candidates": candidates[:5],
                        },
                    ) from None
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
            if type(exc).__name__ == "TargetError":
                if code == "target_not_found":
                    return False
                if code == "target_ambiguous":
                    candidates = self._collect_ambiguous_candidates(session, locator)
                    raise ConfigurationError(
                        "macOS locator matches multiple elements; refine the locator.",
                        context={
                            "resolution_reason": "ambiguous",
                            "match_count": max(len(candidates), 2),
                            "candidates": candidates[:5],
                        },
                    ) from None
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
                control=self._operation_control(timeout_seconds),
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

    def _element_value(self, element: Any) -> str | None:
        try:
            inspection = element.inspect(attributes=("value",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        attrs = getattr(inspection, "attributes", ())
        if not attrs:
            return None
        first = attrs[0]
        if getattr(first, "state", None) == "value" and isinstance(first.value, str):
            return first.value
        if getattr(first, "state", None) == "missing":
            return ""
        return None

    def _element_value_reflects_text(self, element: Any, text: str, *, clear: bool, initial_value: str | None) -> bool:
        value = self._element_value(element)
        if value is None:
            return False
        if clear:
            return value == text
        return value == f"{initial_value}{text}" if initial_value is not None else value.endswith(text)

    def _element_displayed(self, element: Any) -> bool:
        try:
            inspection = element.inspect(attributes=("displayed",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        attrs = getattr(inspection, "attributes", ())
        if attrs and getattr(attrs[0], "state", None) == "value" and type(attrs[0].value) is bool:
            return bool(attrs[0].value)
        raise ConfigurationError("Mac2 element cannot report visibility.", context={"resolution_reason": "backend_error"})

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

    def _raise_resolution_error(self, error: Exception) -> None:
        cls_name = type(error).__name__
        code = getattr(error, "code", None)
        reason = "backend_error"
        if cls_name in {"ElementValidationError", "InvalidRequestError"} or code in {
            "invalid_selector",
            "invalid_argument",
            "unsupported_operation",
        }:
            reason = "invalid_locator"
        elif cls_name == "InvalidSessionStateError" or code in {
            "session_closed",
            "session_disposed",
            "session_invalid",
            "session_lost",
            "connection_closed",
            "connection_closing",
        }:
            reason = "session_unavailable"
        raise ConfigurationError(f"macOS element lookup failed: {reason}.", context={"resolution_reason": reason}) from None

    @staticmethod
    def _propagate_if_cancelled(exc: BaseException) -> None:
        if type(exc).__name__ == "OperationCancelledError" or getattr(exc, "code", None) == "operation_cancelled":
            import asyncio

            raise asyncio.CancelledError from exc

    def _wait_timeout(self) -> float:
        return max(0.5, float(self.action_timeout_seconds))

    @staticmethod
    def _ActionControl(driver: AppiumConnectMacOSDriver) -> Any:
        return driver._operation_control(max(5.0, float(driver.action_timeout_seconds) + 5.0))

    @staticmethod
    def _operation_control(timeout_seconds: float) -> Any:
        try:
            from appium_connect_agent.runtime import OperationControl
        except ImportError:
            return None
        return OperationControl(timeout_seconds=timeout_seconds)

    def _run_on_worker(self, func: Callable[[], _T]) -> _T:
        if self._borrowed_session and self._executor is None:
            return func()
        self._ensure_executor()
        try:
            return self._run_sync(func)
        except BaseException:
            if self._session is None and self._managed_scope is None:
                self._shutdown_executor()
            raise

    def _run_sync(self, func: Callable[[], _T]) -> _T:
        if self._executor is None:
            return func()
        return self._executor.submit(func).result()

    def _ensure_executor(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-macos-driver")

    def _shutdown_executor(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True)

    def _target_missing(self) -> dict[str, object]:
        return self._failed(
            "target_resolution_error",
            "Target was not found.",
            metadata={"resolution_reason": "not_found"},
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
