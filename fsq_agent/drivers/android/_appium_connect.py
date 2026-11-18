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
from datetime import datetime
from math import ceil, floor
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, TypeVar
from xml.etree import ElementTree

from pydantic import BaseModel, ValidationError

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _android_driver_tool
from fsq_agent.drivers.android._visual_inspection import _run_visual_inspection
from fsq_agent.models import (
    AndroidAssertNotVisibleParams,
    AndroidAssertStateParams,
    AndroidAssertVisibleParams,
    AndroidAssertWithAIParams,
    AndroidHarnessSettings,
    AndroidInputTextParams,
    AndroidInspectElementParams,
    AndroidInspectWithAIParams,
    AndroidKillAppParams,
    AndroidLaunchAppParams,
    AndroidLongPressOnParams,
    AndroidPerformActionsParams,
    AndroidPressKeyParams,
    AndroidSwipeParams,
    AndroidTapAtParams,
    AndroidTapOnParams,
    AndroidUiTreeParams,
    ConfigurationError,
    ToolExecutionError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

_T = TypeVar("_T")

DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS = 10.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS = 15.0
DEFAULT_NEW_COMMAND_TIMEOUT_SECONDS = 900.0
SESSION_IDLE_LIVENESS_SECONDS = 45.0
DEFAULT_OPERATION_TIMEOUT_SECONDS = 25.0
DEFAULT_LONG_PRESS_DURATION_MS = 1000
MAX_COORDINATE_VALUE = 2_147_483_647
MAX_PORT_BIND_ATTEMPTS = 3

ANDROID_STATE_ASSERTION_FIELDS = ("enabled", "checked", "selected", "clickable", "focused")
ANDROID_EDITABLE_CLASSES = frozenset(
    {
        "android.widget.EditText",
        "android.widget.AutoCompleteTextView",
        "android.widget.MultiAutoCompleteTextView",
    }
)
ANDROID_INPUT_IDENTITY_FIELDS = ("type", "id", "package")
ANDROID_INPUT_BINDING_ATTRIBUTES = (*ANDROID_INPUT_IDENTITY_FIELDS, "bounds")
ANDROID_SAFE_BACKEND_CODES = frozenset(
    {
        "invalid_argument",
        "invalid_request",
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
        "cleanup_failed",
        "source_settings_invalid",
        "source_invalid",
        "unsupported_operation",
        "unsupported_platform",
        "invalid_response",
    }
)
ANDROID_SAFE_COMPLETED_STEPS = frozenset(
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
ANDROID_SESSION_STATES = frozenset({"creating", "active", "uncertain", "disposing", "disposed", "closing", "closed"})
ANDROID_INSPECT_INPUT_ATTRIBUTES = (
    "type",
    "id",
    "package",
    "enabled",
    "displayed",
    "password",
    "text",
    "bounds",
)
ANDROID_INSPECT_INPUT_POST_ATTRIBUTES = (
    "type",
    "id",
    "package",
    "enabled",
    "password",
    "text",
    "bounds",
)
ANDROID_INSPECT_INPUT_NUMERIC_ATTRIBUTES = ("type", "id", "package", "enabled", "focused", "password", "text", "bounds")
ANDROID_LOCAL_RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ANDROID_STANDARD_RESOURCE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:id/[A-Za-z_][A-Za-z0-9_]*$")
# Only these structural XPath forms are demonstrably independent of the text being replaced.
ANDROID_TEXT_INDEPENDENT_XPATH = re.compile(r"""(?:/+(?:hierarchy|[A-Za-z_][\w.$]*|\*)(?:\[\d+\]|\[@(?:resource-id|content-desc|class)=(?:'[^']*'|"[^"]*")\])?)+""")
ANDROID_XML_TAG_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$]*$")
ANDROID_UI_SNAPSHOT_TEXT_LIMIT_CHARS = 50
ANDROID_UI_SNAPSHOT_STRUCTURAL_ATTRIBUTES = frozenset({"class", "package", "bounds", "window-id"})
ANDROID_SYSTEM_UI_PACKAGES = frozenset({"com.android.systemui"})

ANDROID_KEYCODE_MAP: dict[str, int] = {
    "home": 3,
    "back": 4,
    "call": 5,
    "endcall": 6,
    "up": 19,
    "dpad_up": 19,
    "down": 20,
    "dpad_down": 20,
    "left": 21,
    "dpad_left": 21,
    "right": 22,
    "dpad_right": 22,
    "center": 23,
    "dpad_center": 23,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "camera": 27,
    "clear": 28,
    "tab": 61,
    "space": 62,
    "enter": 66,
    "delete": 67,
    "del": 67,
    "backspace": 67,
    "menu": 82,
    "search": 84,
    "mediaplaypause": 85,
    "media_play_pause": 85,
    "mediastop": 86,
    "media_stop": 86,
    "mediaplay": 126,
    "media_play": 126,
    "mediapause": 127,
    "media_pause": 127,
    "page_up": 92,
    "page_down": 93,
    "escape": 111,
    "esc": 111,
    "forward_del": 112,
    "move_home": 122,
    "movehome": 122,
    "move_end": 123,
    "moveend": 123,
    "end": 123,
    "numpad_enter": 160,
    "volume_mute": 164,
    "mute": 164,
    "recent": 187,
    "recent_apps": 187,
    "app_switch": 187,
}


def _is_strict_int(value: object) -> bool:
    return type(value) is int


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    encoded = ', "\'", '.join(f"'{part}'" for part in parts)
    return f"concat({encoded})"


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
        candidate = candidate_dir / name
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return resolved
        except OSError:
            continue
    return None


def _resolve_android_sdk_root() -> Path | None:
    for env_key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = os.environ.get(env_key, "").strip()
        if raw:
            candidate = Path(raw)
            if candidate.is_absolute() and (candidate / "platform-tools" / "adb").is_file():
                return candidate
    adb_executable = _resolve_host_executable("adb")
    if adb_executable is not None and adb_executable.parent.name == "platform-tools":
        sdk_candidate = adb_executable.parent.parent
        if sdk_candidate.is_absolute() and (sdk_candidate / "platform-tools" / "source.properties").is_file():
            return sdk_candidate
    default_mac_sdk = Path.home() / "Library" / "Android" / "sdk"
    if default_mac_sdk.is_absolute() and (default_mac_sdk / "platform-tools" / "adb").is_file():
        return default_mac_sdk
    return None


def _resolve_java_home() -> Path | None:
    raw = os.environ.get("JAVA_HOME", "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute() and (candidate / "bin" / "java").is_file():
            return candidate
    jvm_dir = Path("/Library/Java/JavaVirtualMachines")
    try:
        entries = sorted(jvm_dir.iterdir(), reverse=True) if jvm_dir.is_dir() else []
    except OSError:
        entries = []
    for entry in entries:
        candidate = entry / "Contents" / "Home"
        if candidate.is_absolute() and (candidate / "bin" / "java").is_file() and (candidate / "release").is_file():
            return candidate
    brew_candidates = (
        Path("/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
        Path("/usr/local/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
    )
    for candidate in brew_candidates:
        if candidate.is_absolute() and (candidate / "bin" / "java").is_file():
            return candidate
    return None


def _resolve_managed_local_settings(local_settings_cls: type[Any]) -> Any:
    return local_settings_cls(
        node_executable=_resolve_host_executable("node"),
        npm_executable=_resolve_host_executable("npm"),
        java_home=_resolve_java_home(),
        android_sdk_root=_resolve_android_sdk_root(),
    )


class _LocatorConfigurationError(ValueError):
    """Raised internally when FSQ locator arguments cannot build a valid Automation Element."""


class AppiumConnectAndroidDriver(AIAssertionBackendToolMixin):
    backend = "appium_connect_agent"

    def __init__(
        self,
        *,
        app_id: str,
        serial: str | None = None,
        session: object | None = None,
        uiautomator2_wait_for_idle_timeout_ms: int | None = None,
        new_command_timeout_seconds: float | None = DEFAULT_NEW_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if session is not None and uiautomator2_wait_for_idle_timeout_ms is not None:
            raise ConfigurationError("An initial idle override cannot be applied to a borrowed Android Session.")
        settings = AndroidHarnessSettings(
            uiautomator2_wait_for_idle_timeout_ms=uiautomator2_wait_for_idle_timeout_ms,
            new_command_timeout_seconds=new_command_timeout_seconds,
        )
        self._uiautomator2_wait_for_idle_timeout_ms = settings.uiautomator2_wait_for_idle_timeout_ms
        self._new_command_timeout_seconds = settings.new_command_timeout_seconds
        self.app_id = app_id
        self.serial = serial
        self._borrowed_session = session is not None
        self._session: Any | None = session
        self._runtime: Any | None = None
        self._managed_scope: AbstractContextManager[Any] | None = None
        self._connection: Any | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._cleanup_pending = False
        self._session_settled = False
        self._scope_exit_attempted = False
        self._cleanup_primary: BaseException | None = None
        self._terminal_disposal_diagnostic: dict[str, object] | None = None
        self._closed = False
        self._last_operation_finished_at: float | None = None
        self._operation_cancellation: Event | None = None

    def close(self) -> None:
        if self._borrowed_session:
            self._shutdown_executor()
            return
        if self._session is not None or self._managed_scope is not None or self._runtime is not None or self._connection is not None:
            self._run_sync(self._close_owned_resources)
        # Only the executor can remain after resource settlement. Do not submit
        # work to it again: a failed shutdown may already have stopped submission.
        try:
            self._shutdown_executor()
        except BaseException as shutdown_error:
            self._cleanup_pending = True
            primary_error = self._cleanup_primary
            if primary_error is not None:
                if isinstance(primary_error, (CancelledError, KeyboardInterrupt, SystemExit)):
                    raise primary_error from shutdown_error
                raise BaseExceptionGroup("Android session and owner-thread shutdown failed.", [primary_error, shutdown_error]) from None
            raise

        primary_error = self._cleanup_primary
        self._cleanup_primary = None
        self._cleanup_pending = False
        self._closed = True
        if self._terminal_disposal_diagnostic is not None:
            if not self._terminal_disposal_diagnostic["terminal_disposal_completed"]:
                self._terminal_disposal_diagnostic["terminal_disposal_completed"] = True
                logging.getLogger(__name__).info("Android terminal disposal settled an unconfirmed remote quit.", extra=self._terminal_disposal_diagnostic)
        elif primary_error is not None:
            raise primary_error

    def _close_owned_resources(self) -> None:
        self._cleanup_pending = True
        primary_error = self._cleanup_primary
        managed_settled = False
        try:
            session = self._session
            if session is not None and not self._session_settled:
                state = session.session_info().state
                if state == "active":
                    try:
                        session.quit(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                    except BaseException as exc:  # Retain quit failure while explicitly disposing uncertain resources.
                        primary_error = self._cleanup_primary = exc
                        state = session.session_info().state
                        if state in {"uncertain", "disposing"} and getattr(exc, "code", None) == "session_quit_unknown":
                            from appium_connect_agent.automation import ResourceStateError

                            if isinstance(exc, ResourceStateError):
                                self._terminal_disposal_diagnostic = {
                                    "backend_code": "session_quit_unknown",
                                    "request_state": exc.request_state if exc.request_state in {"not_sent", "response_unknown", "response_received"} else "unknown",
                                    "remote_quit_confirmed": False,
                                    "terminal_disposal_completed": False,
                                }
                        if state not in {"uncertain", "disposing", "disposed"}:
                            raise
                    else:
                        self._session_settled = True
                if not self._session_settled:
                    if state in {"uncertain", "disposing"}:
                        session.dispose(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                        state = session.session_info().state
                        if state != "disposed":
                            raise ToolExecutionError("Owned Android terminal disposal did not complete.", context={"state": state})  # noqa: TRY301
                        managed_settled = True
                    elif state == "disposed":
                        managed_settled = True
                    elif state != "closed":
                        # Rejection must traverse the common ownership-retention path below.
                        raise ToolExecutionError("Owned Android session is not available for terminal cleanup.", context={"state": state})  # noqa: TRY301
                    self._session_settled = True

            if self._scope_exit_attempted and self._runtime is not None and not managed_settled:
                self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
            elif self._managed_scope is not None and not self._scope_exit_attempted:
                self._scope_exit_attempted = True
                # No primary exception is passed: scope cleanup failure must raise,
                # rather than being attached to and hidden behind a business error.
                self._managed_scope.__exit__(None, None, None)
            elif self._runtime is not None and not managed_settled:
                self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
        except BaseException as cleanup_error:
            if primary_error is not None and cleanup_error is not primary_error:
                if isinstance(primary_error, (CancelledError, KeyboardInterrupt, SystemExit)):
                    raise primary_error from cleanup_error
                raise BaseExceptionGroup("Android session and owned-resource cleanup failed.", [primary_error, cleanup_error]) from None
            raise

        # Public quit + scope completion, or public dispose, is the settlement proof.
        self._session = None
        self._managed_scope = None
        self._connection = None
        self._runtime = None
        self._session_settled = False
        self._scope_exit_attempted = False

    def context(self) -> dict[str, object]:
        return self._run_on_worker(self._context_impl)

    def _context_impl(self) -> dict[str, object]:
        session = self._ensure_session()
        info = session.session_info()
        session_id = getattr(info, "appium_session_id", None) or getattr(info, "id", None) or f"appium_connect_agent:{self.serial or 'injected'}"
        observation = session.observe_tree(control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS))
        screen_size = self._display_size(session)
        current_package = self._extract_current_package(observation)
        return {
            "session_id": str(session_id),
            "screen_size": screen_size,
            "metadata": {
                "backend": self.backend,
                "current_package": current_package,
                "sdk_session_id": getattr(info, "id", None),
            },
        }

    @_android_driver_tool("launchApp", description="Launch the configured Android app.")
    def launch_app(self, params: AndroidLaunchAppParams) -> dict[str, object]:
        return self._run_tool(lambda: self._launch_app_impl(params))

    def _launch_app_impl(self, params: AndroidLaunchAppParams) -> dict[str, object]:
        data = self._param_data(params)
        app_id = str(data.get("app_id") or self.app_id).strip()
        if not app_id:
            return self._configuration_error("launchApp requires a non-empty Android app_id.")
        session = self._ensure_session()
        session.device.activate_app(app_id, control=self._operation_control(30.0))
        return self._passed({"app_id": app_id})

    @_android_driver_tool("killApp", description="Stop the configured Android app.")
    def kill_app(self, params: AndroidKillAppParams) -> dict[str, object]:
        return self._run_tool(lambda: self._kill_app_impl(params))

    def _kill_app_impl(self, params: AndroidKillAppParams) -> dict[str, object]:
        data = self._param_data(params)
        app_id = str(data.get("app_id") or self.app_id).strip()
        if not app_id:
            return self._configuration_error("killApp requires a non-empty Android app_id.")
        session = self._ensure_session()
        session.device.terminate_app(app_id, control=self._operation_control(15.0))
        return self._passed({"app_id": app_id})

    @_android_driver_tool("tapOn", description="Tap an Android UI target.")
    def tap_on(self, params: AndroidTapOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._tap_on_impl(params))

    def _tap_on_impl(self, params: AndroidTapOnParams) -> dict[str, object]:
        data = AndroidTapOnParams.model_validate(params).model_dump(mode="json", exclude_none=True)
        session = self._ensure_session()
        element = self._resolve_element(session, data)
        element.click(
            wait_timeout_seconds=DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS,
            control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS),
        )
        return self._passed()

    @_android_driver_tool("tapAt", description="Tap Android screen coordinates.")
    def tap_at(self, params: AndroidTapAtParams) -> dict[str, object]:
        return self._run_tool(lambda: self._tap_at_impl(params))

    def _tap_at_impl(self, params: AndroidTapAtParams) -> dict[str, object]:
        data = self._param_data(params)
        point = self._point_payload(data)
        if point is None:
            return self._configuration_error("tapAt requires integer point.x and point.y parameters.")
        session = self._ensure_session()
        scaled, screen_size = self._scale_points_if_needed(session, [point], data.get("reference_screen_size"))
        x, y = scaled[0]
        session.device.tap_at(x, y, control=self._operation_control(15.0))
        if data.get("reference_screen_size") is None:
            data["reference_screen_size"] = {"width": screen_size[0], "height": screen_size[1]}
        return {**self._passed({"point": {"x": x, "y": y}}), "metadata": {"safe_replay_params": data}}

    @_android_driver_tool("longPressOn", description="Long press an Android UI target.")
    def long_press_on(self, params: AndroidLongPressOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._long_press_on_impl(params))

    def _long_press_on_impl(self, params: AndroidLongPressOnParams) -> dict[str, object]:
        data = AndroidLongPressOnParams.model_validate(params).model_dump(mode="json", exclude_none=True)
        session = self._ensure_session()
        element = self._resolve_element(session, data)
        element.long_click(
            duration_ms=DEFAULT_LONG_PRESS_DURATION_MS,
            wait_timeout_seconds=DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS,
            control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS),
        )
        return self._passed()

    @_android_driver_tool("inputText", description="Replace text in an editable Android UI target and verify the accepted value.")
    def input_text(self, params: AndroidInputTextParams) -> dict[str, object]:
        return self._run_tool(lambda: self._input_text_impl(params))

    def _input_text_impl(self, params: AndroidInputTextParams) -> dict[str, object]:
        data = AndroidInputTextParams.model_validate(params).model_dump(mode="json", exclude_none=True)
        text = data.get("text")
        if not isinstance(text, str):
            return self._configuration_error("inputText requires a string text parameter.")
        try:
            session = self._ensure_session()
            element = self._resolve_element(session, data)
        except _LocatorConfigurationError as exc:
            return self._configuration_error(str(exc))
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            if isinstance(exc, ConfigurationError):
                raise
            return self._input_failure(exc, "entry_backend_error", "pre_inspection", "Input target inspection failed.")

        before, resolution_failure = self._wait_and_inspect_input_target(element)
        if resolution_failure is not None or before is None:
            return resolution_failure or self._input_failure(None, "entry_backend_error", "pre_inspection", "Input target inspection failed.")

        class_name = self._attr_value(before, "type")
        enabled = self._attr_value(before, "enabled")
        displayed = self._attr_value(before, "displayed")
        if not isinstance(class_name, str) or class_name not in ANDROID_EDITABLE_CLASSES or enabled is not True or displayed is not True:
            return self._input_failure(None, "invalid_input_target", "pre_inspection", "Input target is not demonstrably editable.", failure_category="target_resolution_error")

        before_package = self._attr_value(before, "package")
        before_bounds = self._attr_value(before, "bounds")
        if not self._input_text_is_readable(before) or not isinstance(before_package, str) or not before_package.strip() or before_bounds is None:
            return self._input_failure(None, "verification_unavailable", "pre_inspection", "Input target text cannot be verified.")

        before_id = self._attr_raw_id(before)
        if before_id is not None:
            try:
                # Bind the observed raw identity before mutation; never re-use mutable text or a positional selector.
                element = session.element("id", before_id, raw_id=ANDROID_STANDARD_RESOURCE_ID.fullmatch(before_id) is None)
                bound = self._attributes_dict(element.inspect(attributes=ANDROID_INPUT_BINDING_ATTRIBUTES, control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS)))
            except Exception as exc:  # noqa: BLE001 - Sanitize arbitrary SDK/client failures; cancellation is re-raised below.
                self._propagate_if_cancelled(exc)
                return self._input_failure(exc, "verification_unavailable", "identity_binding", "Input target identity could not be established.")
            if any(before.get(field) != bound.get(field) for field in ANDROID_INPUT_BINDING_ATTRIBUTES):
                return self._input_failure(None, "verification_unavailable", "identity_binding", "Input target identity changed before entry.")
        elif not self._input_locator_is_text_independent(data):
            rectangle = tuple(getattr(before_bounds, field, None) for field in ("x", "y", "width", "height"))
            if not all(_is_strict_int(value) for value in rectangle) or rectangle[2] <= 0 or rectangle[3] <= 0:
                return self._input_failure(None, "verification_unavailable", "identity_binding", "Input target has no usable geometric identity.")
            x, y, width, height = rectangle
            bounds_xml = f"[{x},{y}][{x + width},{y + height}]"
            anchor = f"//*[@class={_xpath_literal(class_name)} and @package={_xpath_literal(before_package)} and @bounds={_xpath_literal(bounds_xml)}]"
            try:
                element = session.element("xpath", anchor)
                bound = self._attributes_dict(element.inspect(attributes=ANDROID_INPUT_BINDING_ATTRIBUTES, control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS)))
            except Exception as exc:  # noqa: BLE001 - Binding failure must precede all input side effects.
                self._propagate_if_cancelled(exc)
                return self._input_failure(exc, "verification_unavailable", "identity_binding", "Input target geometric identity could not be established.")
            if self._attr_raw_id(bound) is not None or any(before.get(field) != bound.get(field) for field in ("type", "package", "bounds")):
                return self._input_failure(None, "verification_unavailable", "identity_binding", "Input target geometric identity changed before entry.")

        no_id_verification_message = (
            "Text entry was sent, but post-entry verification could not re-anchor the target because it has no resource-id; its bounds may have shifted. Inspect UI state before re-entering."
        )
        try:
            element.click(wait_timeout_seconds=2.0, control=self._operation_control(10.0))
        except Exception as exc:  # noqa: BLE001 - Click rejection must never initiate replacement recovery.
            self._propagate_if_cancelled(exc)
            return self._input_failure(exc, "entry_backend_error", "pre_focus_click", "Input focus click failed; inspect UI state before re-entering.")

        numeric_baseline: str | None = None
        try:
            element.replace_element_value(text, wait_timeout_seconds=5.0, control=self._operation_control(15.0))
        except Exception as exc:  # noqa: BLE001 - Never expose submitted text from SDK/client failures; preserve cancellation below.
            self._propagate_if_cancelled(exc)
            if not self._can_recover_numeric_input(exc, text):
                return self._input_failure(exc, "entry_backend_error", "replace_value", "Text entry backend failed; acceptance is unknown.")
            numeric_baseline, recovery_failure = self._recover_numeric_input(session, element, before, text)
            if recovery_failure is not None:
                return recovery_failure

        recovery_warning = " Inspect UI state before re-entering." if numeric_baseline is not None else ""

        try:
            inspection = element.inspect(
                attributes=ANDROID_INSPECT_INPUT_POST_ATTRIBUTES,
                control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS),
            )
            after = self._attributes_dict(inspection)
        except Exception as exc:  # noqa: BLE001 - Sanitize observed text from SDK/client failures; preserve cancellation below.
            self._propagate_if_cancelled(exc)
            message = no_id_verification_message if before_id is None else "Text entry acceptance could not be read."
            return self._input_failure(exc, "verification_unavailable", "post_verification", message + (recovery_warning if before_id is not None else ""))

        if not self._input_target_is_safe(before, after):
            message = no_id_verification_message if before_id is None else "Text entry acceptance cannot be verified on the original target."
            return self._input_failure(None, "verification_unavailable", "post_verification", message + (recovery_warning if before_id is not None else ""))

        observed = self._attr_value(after, "text")
        matches = observed == text
        if numeric_baseline is not None:
            if not text:
                matches = matches or observed == numeric_baseline
            elif numeric_baseline:
                matches = matches or observed == text + ", " + numeric_baseline
        if not matches:
            return self._input_failure(None, "entry_mismatch", "post_verification", "Text entry did not match the requested replacement." + recovery_warning)
        return self._passed()

    @staticmethod
    def _can_recover_numeric_input(exc: Exception, text: str) -> bool:
        if getattr(exc, "code", None) != "command_failed" or getattr(exc, "request_state", None) != "response_received" or any(character not in "0123456789.-" for character in text):
            return False
        try:
            from appium_connect_agent.automation import CommandError
        except ImportError:
            return False
        return isinstance(exc, CommandError)

    def _input_target_is_safe(self, before: dict[str, tuple[str, object]], observed: dict[str, tuple[str, object]]) -> bool:
        before_id = self._attr_raw_id(before)
        return (
            self._input_text_is_readable(observed)
            and self._attr_value(observed, "enabled") is True
            and all(before.get(field) == observed.get(field) for field in ("type", "package"))
            and self._attr_raw_id(observed) == before_id
            and (before_id is not None or before.get("bounds") == observed.get("bounds"))
        )

    def _recover_numeric_input(self, session: Any, element: Any, before: dict[str, tuple[str, object]], text: str) -> tuple[str | None, dict[str, object] | None]:
        message = "Numeric text entry acceptance is unknown. Inspect UI state before re-entering."
        stage = "numeric_clear"
        reason = "verification_unavailable"
        try:
            observed = self._attributes_dict(element.inspect(attributes=ANDROID_INSPECT_INPUT_NUMERIC_ATTRIBUTES, control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS)))
            if not self._input_target_is_safe(before, observed):
                return None, self._input_failure(None, "verification_unavailable", stage, message)
            reason = "entry_backend_error"
            element.clear(wait_timeout_seconds=5.0, control=self._operation_control(15.0))

            stage = "numeric_focus"
            reason = "verification_unavailable"
            observed = self._attributes_dict(element.inspect(attributes=ANDROID_INSPECT_INPUT_NUMERIC_ATTRIBUTES, control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS)))
            if not self._input_target_is_safe(before, observed):
                return None, self._input_failure(None, "verification_unavailable", stage, message)
            baseline = self._attr_value(observed, "text")
            if not isinstance(baseline, str):
                return None, self._input_failure(None, "verification_unavailable", stage, message)
            if self._attr_value(observed, "focused") is not True:
                reason = "entry_backend_error"
                element.click(wait_timeout_seconds=2.0, control=self._operation_control(10.0))
                reason = "verification_unavailable"
                focus_deadline = time.monotonic() + 2.0
                while time.monotonic() < focus_deadline:
                    observed = self._attributes_dict(element.inspect(attributes=ANDROID_INSPECT_INPUT_NUMERIC_ATTRIBUTES, control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS)))
                    if not self._input_target_is_safe(before, observed) or self._attr_value(observed, "text") != baseline:
                        return None, self._input_failure(None, "verification_unavailable", stage, message)
                    if self._attr_value(observed, "focused") is True:
                        break
                    remaining = focus_deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))
                else:
                    return None, self._input_failure(None, "verification_unavailable", stage, message)

            # Focus was verified above, not locked atomically across subsequent device commands.
            stage = "numeric_keycode"
            reason = "entry_backend_error"
            for character in text:
                keycode = 69 if character == "-" else 56 if character == "." else ord(character) - ord("0") + 7
                session.device.press_keycode(keycode, control=self._operation_control(10.0))
        except Exception as exc:  # noqa: BLE001 - Never replay a possibly accepted clear, click, or key; sanitize all SDK failures.
            self._propagate_if_cancelled(exc)
            return None, self._input_failure(exc, reason, stage, message)
        return baseline, None

    def _input_failure(self, exc: Exception | None, reason: str, stage: str, message: str, *, failure_category: str = "action_error") -> dict[str, object]:
        metadata = self._safe_diagnostics(exc, stage=stage)
        metadata.setdefault("reason", reason)
        return self._failed(failure_category, message, metadata=metadata)

    def _wait_and_inspect_input_target(self, element: Any) -> tuple[dict[str, tuple[str, object]] | None, dict[str, object] | None]:
        deadline = time.monotonic() + DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS
        while True:
            try:
                inspection = element.inspect(
                    attributes=ANDROID_INSPECT_INPUT_ATTRIBUTES,
                    control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS),
                )
                return self._attributes_dict(inspection), None
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                code = getattr(exc, "code", None)
                if not self._target_error_is_definitive(exc):
                    return None, self._input_failure(exc, "entry_backend_error", "pre_inspection", "Input target inspection failed.")
                if code == "target_ambiguous":
                    return None, self._input_failure(
                        exc, "target_ambiguous", "pre_inspection", "Target locator matched multiple Android elements; refine the locator.", failure_category="target_resolution_error"
                    )
                if code == "target_not_found":
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))
                        continue
                    return None, self._input_failure(exc, "target_missing", "pre_inspection", "Input target was not found.", failure_category="target_resolution_error")
                return None, self._input_failure(exc, "entry_backend_error", "pre_inspection", "Input target inspection failed.")

    @staticmethod
    def _attributes_dict(inspection: Any) -> dict[str, tuple[str, object]]:
        return {attr.name: (attr.state, attr.value) for attr in getattr(inspection, "attributes", ())}

    @staticmethod
    def _attr_value(attrs: dict[str, tuple[str, object]], name: str) -> object:
        entry = attrs.get(name)
        if entry is None or entry[0] != "value":
            return None
        return entry[1]

    @staticmethod
    def _attr_raw_id(attrs: dict[str, tuple[str, object]]) -> str | None:
        entry = attrs.get("id")
        if entry is not None and entry[0] == "value" and isinstance(entry[1], str) and entry[1].strip() not in {"", "null"}:
            return entry[1]
        return None

    def _input_text_is_readable(self, attrs: dict[str, tuple[str, object]]) -> bool:
        text_entry = attrs.get("text")
        if self._attr_value(attrs, "password") is not False or text_entry is None or text_entry[0] != "value":
            return False
        text = text_entry[1]
        return isinstance(text, str) and (not text or not set(text) <= {"*", "•", "●"})

    def _input_locator_is_text_independent(self, data: dict[str, object]) -> bool:
        locator = data.get("locator")
        if isinstance(locator, dict):
            xpath = locator.get("xpath")
            if isinstance(xpath, str) and xpath.strip():
                positional = re.fullmatch(r"\((.+)\)\[\d+\]", xpath)
                structural = positional.group(1) if positional is not None else xpath
                return ANDROID_TEXT_INDEPENDENT_XPATH.fullmatch(structural) is not None
            populated = self._populated_locator_fields(locator)
            if populated:
                return "text" not in populated
        return False

    @_android_driver_tool("pressKey", description="Press an Android key.")
    def press_key(self, params: AndroidPressKeyParams) -> dict[str, object]:
        return self._run_tool(lambda: self._press_key_impl(params))

    def _press_key_impl(self, params: AndroidPressKeyParams) -> dict[str, object]:
        data = self._param_data(params)
        raw_key = data.get("key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            return self._configuration_error("pressKey requires a key parameter.")
        key_text = raw_key.strip()
        normalized = key_text.lower().replace("-", "_").replace(" ", "_")
        if normalized.startswith("keycode_"):
            normalized = normalized.removeprefix("keycode_")
        keycode: int | None = 123 if normalized == "ctrl+end" else ANDROID_KEYCODE_MAP.get(normalized)
        if keycode is None and normalized.isascii() and normalized.isdigit():
            parsed = int(normalized)
            if 0 <= parsed <= MAX_COORDINATE_VALUE:
                keycode = parsed
        if keycode is None:
            return self._configuration_error(f"Unsupported Android key: {key_text!r}.")
        session = self._ensure_session()
        if normalized == "back":
            session.device.back(control=self._operation_control(15.0))
            return self._passed({"key": key_text})
        if normalized == "ctrl+end":
            session.device.press_keycode(keycode, metastate=4096, control=self._operation_control(15.0))
        else:
            session.device.press_keycode(keycode, control=self._operation_control(15.0))
        return self._passed({"key": key_text})

    @_android_driver_tool("swipe", description="Swipe in exactly one mode: full-screen direction, explicit points, or target/locator plus direction and optional finite percent of element bounds.")
    def swipe(self, params: AndroidSwipeParams) -> dict[str, object]:
        return self._run_tool(lambda: self._swipe_impl(params))

    def _swipe_impl(self, params: AndroidSwipeParams) -> dict[str, object]:
        try:
            data = AndroidSwipeParams.model_validate(self._param_data(params)).model_dump(mode="json", exclude_none=True)
        except ValidationError:
            return self._configuration_error("Invalid Android swipe mode, coordinates, fraction, or duration.")
        duration_ms = data.get("duration", 200)
        timeout_budget = max(15.0, duration_ms / 1000.0 + 10.0)
        session = self._ensure_session()

        if "start" in data or "end" in data:
            points = self._swipe_point_payload(data)
            if points is None:
                return self._configuration_error("swipe point payload requires integer start.x, start.y, end.x, and end.y parameters.")
            sx, sy, ex, ey = points
            scaled, screen_size = self._scale_points_if_needed(session, [(sx, sy), (ex, ey)], data.get("reference_screen_size"))
            (sx, sy), (ex, ey) = scaled
            session.device.swipe_between(
                sx,
                sy,
                ex,
                ey,
                duration_ms=duration_ms,
                control=self._operation_control(timeout_budget),
            )
            if "reference_screen_size" not in data:
                data["reference_screen_size"] = {"width": screen_size[0], "height": screen_size[1]}
            return {**self._passed({"start": {"x": sx, "y": sy}, "end": {"x": ex, "y": ey}}), "metadata": {"safe_replay_params": data}}

        direction = str(data["direction"])
        width, height = self._display_size(session)
        if "target" in data or "locator" in data:
            element = self._resolve_element(session, data)
            attrs = self._attributes_dict(element.inspect(attributes=("bounds",), control=self._operation_control(10.0)))
            bounds = self._attr_value(attrs, "bounds")
            if bounds is None or not all(_is_strict_int(getattr(bounds, name, None)) for name in ("x", "y", "width", "height")):
                return self._failed("action_error", "Element bounds are unavailable for swipe.")
            x, y, element_width, element_height = bounds.x, bounds.y, bounds.width, bounds.height
            if x < 0 or y < 0 or element_width <= 0 or element_height <= 0 or x + element_width > width or y + element_height > height:
                return self._failed("action_error", "Element swipe requires positive fully on-screen bounds.")
            horizontal = direction in {"left", "right"}
            first = x if horizontal else y
            last = first + (element_width if horizontal else element_height) - 1
            fraction = data.get("percent", 1.0)
            midpoint = (first + last) / 2
            half_span = (last - first) * fraction / 2
            low, high = ceil(midpoint - half_span), floor(midpoint + half_span)
            if low >= high:
                return self._failed("action_error", "Element swipe collapses to fewer than two physical pixels.")
            start_axis, end_axis = (high, low) if direction in {"up", "left"} else (low, high)
            if horizontal:
                inset = min(element_height // 2, element_width // 4)
                if direction == "right":
                    if fraction == 1.0:
                        end_axis = min(width - 1, x + element_width)
                    inset_start = max(start_axis, x + inset)
                    if inset_start < end_axis:
                        start_axis = inset_start
                else:
                    inset_start = min(start_axis, x + element_width - 1 - inset)
                    if inset_start > end_axis:
                        start_axis = inset_start
            sx, sy, ex, ey = (start_axis, y + element_height // 2, end_axis, y + element_height // 2) if horizontal else (x + element_width // 2, start_axis, x + element_width // 2, end_axis)
        else:
            sx, sy, ex, ey = self._swipe_points(direction, width, height)
        session.device.swipe_between(
            sx,
            sy,
            ex,
            ey,
            duration_ms=duration_ms,
            control=self._operation_control(timeout_budget),
        )
        return self._passed({"direction": direction})

    def perform_actions(self, params: AndroidPerformActionsParams) -> dict[str, object]:
        del params
        return self._configuration_error("performActions is not implemented for the appium_connect_agent backend.")

    @_android_driver_tool("assertVisible", description="Assert that an Android UI target is visible.")
    def assert_visible(self, params: AndroidAssertVisibleParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_visible_impl(params))

    def _assert_visible_impl(self, params: AndroidAssertVisibleParams) -> dict[str, object]:
        data = AndroidAssertVisibleParams.model_validate(params).model_dump(mode="json", exclude_none=True)
        session = self._ensure_session()
        element = self._resolve_element(session, data)
        deadline = time.monotonic() + DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS
        while True:
            try:
                inspection = element.inspect(
                    attributes=("displayed",),
                    control=self._operation_control(10.0),
                )
                attrs = self._attributes_dict(inspection)
                if attrs.get("displayed") == ("value", True):
                    return self._passed()
            except Exception as exc:
                self._propagate_if_cancelled(exc)
                code = getattr(exc, "code", None)
                if not self._target_error_is_definitive(exc):
                    raise
                if code == "target_ambiguous":
                    return self._target_ambiguous(exc)
                if code != "target_not_found":
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._target_missing()
            time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

    @_android_driver_tool("assertNotVisible", description="Assert that an Android UI target is not visible.")
    def assert_not_visible(self, params: AndroidAssertNotVisibleParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_not_visible_impl(params))

    def _assert_not_visible_impl(self, params: AndroidAssertNotVisibleParams) -> dict[str, object]:
        data = AndroidAssertNotVisibleParams.model_validate(params).model_dump(mode="json", exclude_none=True)
        session = self._ensure_session()
        element = self._resolve_element(session, data)
        deadline = time.monotonic() + DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS
        while True:
            ambiguity_error: Exception | None = None
            try:
                inspection = element.inspect(
                    attributes=("displayed",),
                    control=self._operation_control(10.0),
                )
                attrs = self._attributes_dict(inspection)
                if attrs.get("displayed") == ("value", False):
                    return self._passed()
            except Exception as exc:
                self._propagate_if_cancelled(exc)
                code = getattr(exc, "code", None)
                if not self._target_error_is_definitive(exc):
                    raise
                if code == "target_not_found":
                    return self._passed()
                if code != "target_ambiguous":
                    raise
                ambiguity_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if ambiguity_error is not None:
                    return self._target_ambiguous(ambiguity_error)
                return self._failed("assertion_error", "Target is visible.")
            time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

    @_android_driver_tool("assert", description="Assert Android element existence, text, or state.")
    def assert_state(self, params: AndroidAssertStateParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_state_impl(params))

    def _assert_state_impl(self, params: AndroidAssertStateParams) -> dict[str, object]:
        data = AndroidAssertStateParams.model_validate(params).model_dump(mode="json", exclude_none=True)
        session = self._ensure_session()
        expected_text = data.get("text")
        element_payload = data["element"]
        expected_states = self._expected_element_states(element_payload)
        attributes = ("text",) if isinstance(expected_text, dict) else tuple(expected_states) or ("enabled",)
        element = self._resolve_element(session, data, locator_key="element")
        attrs, resolution_error = self._wait_for_assert_attributes(element, attributes)
        if resolution_error is not None or attrs is None:
            return resolution_error or self._target_missing()

        if isinstance(expected_text, dict):
            actual = self._attr_value(attrs, "text")
            if not isinstance(actual, str):
                return self._failed("assertion_error", "Text assertion failed.", output={"text": None})
            contains = expected_text.get("contains")
            if isinstance(contains, str) and contains in actual:
                return self._passed({"text": actual})
            equals = expected_text.get("equals")
            if isinstance(equals, str) and equals == actual:
                return self._passed({"text": actual})
            return self._failed("assertion_error", "Text assertion failed.", output={"text": actual})

        if expected_states:
            passed: dict[str, bool] = {}
            for field, expected in expected_states.items():
                actual = self._attr_value(attrs, field)
                if actual is not expected:
                    return self._failed(
                        "assertion_error",
                        "Element state assertion failed.",
                        output={"field": field, "expected": expected, "actual": actual},
                    )
                passed[field] = expected
            return self._passed(passed)
        return self._passed({"exists": True})

    def _wait_for_assert_attributes(
        self,
        element: Any,
        attributes: tuple[str, ...],
    ) -> tuple[dict[str, tuple[str, object]] | None, dict[str, object] | None]:
        deadline = time.monotonic() + DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS
        while True:
            try:
                inspection = element.inspect(
                    attributes=attributes,
                    control=self._operation_control(10.0),
                )
                return self._attributes_dict(inspection), None
            except Exception as exc:
                self._propagate_if_cancelled(exc)
                code = getattr(exc, "code", None)
                if not self._target_error_is_definitive(exc):
                    raise
                if code == "target_ambiguous":
                    return None, self._target_ambiguous(exc)
                if code != "target_not_found":
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, self._target_missing()
            time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

    @_android_driver_tool(
        "assertWithAI",
        description="Evaluate a pure visual appearance/image/Canvas assertion. Not for grounding, visual driving, replacing available exact accessibility text/SeekBar values/checked/selected assertions, or overriding contradictory structural evidence.",
    )
    def assert_with_ai(self, params: AndroidAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    @_android_driver_tool(
        "inspectWithAI",
        description="Read current pixel content with extract, or locate an inaccessible target after accessibility refinement. Read-only; only SDK pointer_center may feed an explicit tap_at/swipe. Not an assertion.",
    )
    def inspect_with_ai(self, params: AndroidInspectWithAIParams) -> dict[str, object]:
        if not isinstance(params, AndroidInspectWithAIParams):
            params = AndroidInspectWithAIParams.model_validate(params)
        return _run_visual_inspection(self, params)

    def screenshot(self, params: object | None = None) -> bytes:
        options = params if isinstance(params, dict) else {}
        check = options.get("cancellation_check")
        timeout = options.get("timeout_seconds", 20.0)
        cancellation = Event() if check is not None else None
        failures: list[BaseException] = []
        backend_error: BaseException | None = None

        def capture():
            nonlocal backend_error
            try:
                return self._screenshot_impl(timeout, cancellation)
            except BaseException as error:
                # The caller settles this same operation before reading its safe facts.
                backend_error = error
                raise

        def check_on_caller():
            try:
                check()
            except BaseException as error:
                failures.append(error)
                raise

        if check is not None:
            check()
        try:
            data = self._run_on_worker(capture, cancellation_check=check_on_caller if check is not None else None, cancellation_event=cancellation)
            if check is not None:
                check_on_caller()
        except BaseException as error:
            if check is not None and not failures:
                with suppress(BaseException):
                    check_on_caller()
            diagnostics = {
                key: value
                for key, value in self._safe_diagnostics(backend_error if backend_error is not None else error, stage="screenshot").items()
                if key in {"backend_code", "request_state", "session_state", "stage"}
            }
            if failures:
                interrupted = failures[0]
                interrupted.fsq_capture_diagnostics = diagnostics
                raise interrupted from None
            if isinstance(error, CancelledError) or type(error).__name__ == "OperationCancelledError" or getattr(error, "code", None) == "operation_cancelled":
                cancelled = CancelledError()
                cancelled.fsq_capture_diagnostics = diagnostics
                raise cancelled from None
            if not isinstance(error, Exception):
                raise
            raise ToolExecutionError("Android screenshot is unavailable.", context={"capture_diagnostics": diagnostics}) from None
        return data

    def _screenshot_impl(self, timeout_seconds=20.0, cancellation=None) -> bytes:
        session = self._ensure_session()
        control = self._operation_control(timeout_seconds)
        if cancellation is not None:
            from appium_connect_agent.runtime import OperationControl

            control = OperationControl(timeout_seconds=timeout_seconds, cancellation=cancellation)
        return bytes(session.device.take_screenshot(control=control))

    @_android_driver_tool(
        "uiTree",
        description=(
            "Return a compact Android UI hierarchy XML snapshot synthesized from public TreeObservation. "
            "Layout-only wrapper nodes and empty/default attributes are removed, and long text/content-desc "
            "attributes are clipped to the first 50 characters. Includes fresh device_local_time with UTC offset/weekday or explicit unavailability; tree and clock reads are not atomic."
        ),
    )
    def ui_snapshot(self, params: AndroidUiTreeParams) -> dict[str, object]:
        del params
        return self._run_on_worker(self._ui_snapshot_impl)

    def _ui_snapshot_impl(self) -> dict[str, object]:
        session = self._ensure_session()
        observation = session.observe_tree(control=self._operation_control(20.0))
        snapshot = self._serialize_tree_observation(observation)
        snapshot["device_local_time"] = self._device_local_time(session)
        return snapshot

    def _device_local_time(self, session: Any) -> dict[str, object]:
        try:
            clock = session.device.device_time(control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS))
            local = datetime.fromisoformat(clock.iso_local)
            if local.utcoffset() is None:
                raise ValueError("Device clock offset is unavailable.")  # noqa: TRY301 - Invalid responses share the optional-clock failure boundary.
            return {"status": "available", "local_datetime": clock.iso_local, "iso_weekday": local.isoweekday(), "source": "android_device"}
        except Exception as exc:  # noqa: BLE001 - Optional clock failure must preserve the observed tree without leaking backend text.
            self._propagate_if_cancelled(exc)
            return {"status": "unavailable", "reason": "device_time_unavailable", "source": "android_device", "diagnostics": self._safe_diagnostics(exc, stage="device_time")}

    @_android_driver_tool(
        "inspectElement",
        description="Inspect one uniquely resolved Android element and return complete selected safe attributes with explicit observation states. Text is read only after password is explicitly false.",
    )
    def inspect_element(self, params: AndroidInspectElementParams) -> dict[str, object]:
        return self._run_on_worker(lambda: self._inspect_element_impl(params))

    def _inspect_element_impl(self, params: AndroidInspectElementParams) -> dict[str, object]:
        if not isinstance(params, AndroidInspectElementParams):
            params = AndroidInspectElementParams.model_validate(params)
        data = self._param_data(params)
        attributes = params.attributes
        session = self._ensure_session()
        element = self._resolve_element(session, data)
        deadline = time.monotonic() + DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS
        readable_text = False
        if "text" in attributes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolExecutionError("Android element inspection timed out.")
            password = self._attributes_dict(element.inspect(attributes=("password",), control=self._operation_control(remaining)))
            readable_text = self._attr_value(password, "password") is False
        selected = tuple(name for name in attributes if name != "text" or readable_text)
        attrs = {}
        if selected:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolExecutionError("Android element inspection timed out.")
            attrs = self._attributes_dict(element.inspect(attributes=selected, control=self._operation_control(remaining)))
        output: dict[str, object] = {}
        for name in attributes:
            state, value = attrs.get(name, ("unknown", None))
            if state != "value":
                value = None
            elif name == "bounds":
                value = {field: getattr(value, field) for field in ("x", "y", "width", "height")}
            output[name] = {"state": state, "value": value}
        return {"attributes": output}

    def _serialize_tree_observation(self, observation: Any) -> dict[str, object]:
        try:
            compact_root, source_nodes, retained_nodes, clipped_attributes = self._build_hierarchy_xml(
                observation,
                compact=True,
            )
            compact_xml = ElementTree.tostring(compact_root, encoding="unicode", short_empty_elements=True)
            layout_nodes_removed = max(0, source_nodes - retained_nodes)
            complete_scope = bool(getattr(observation, "complete_for_requested_scope", True))
            upstream_coverage = str(getattr(observation, "coverage", "requested_scope"))
            is_partial = (not complete_scope) or layout_nodes_removed > 0 or clipped_attributes > 0
            return {
                "xml": compact_xml,
                "coverage": {
                    "status": "partial" if is_partial else "unknown",
                    "reason": "semantic_compaction" if complete_scope else f"upstream_{upstream_coverage}",
                    "source_nodes": source_nodes,
                    "retained_nodes": retained_nodes,
                    "retained_characters": len(compact_xml),
                },
                "compaction": {
                    "applied": True,
                    "text_attribute_limit": ANDROID_UI_SNAPSHOT_TEXT_LIMIT_CHARS,
                    "layout_nodes_removed": layout_nodes_removed,
                    "clipped_attributes": clipped_attributes,
                },
            }
        except Exception as exc:  # noqa: BLE001 - safe unfolded fallback on local compaction failure.
            self._propagate_if_cancelled(exc)
            fallback_root, source_nodes, retained_nodes, _ = self._build_hierarchy_xml(observation, compact=False)
            fallback_xml = ElementTree.tostring(fallback_root, encoding="unicode", short_empty_elements=True)
            return {
                "xml": fallback_xml,
                "coverage": {
                    "status": "unknown",
                    "reason": "uncompacted_tree_fallback",
                    "source_nodes": source_nodes,
                    "retained_nodes": retained_nodes,
                    "retained_characters": len(fallback_xml),
                },
                "compaction": {
                    "applied": False,
                    "text_attribute_limit": None,
                    "layout_nodes_removed": 0,
                    "clipped_attributes": 0,
                },
            }

    def _build_hierarchy_xml(self, observation: Any, *, compact: bool) -> tuple[ElementTree.Element, int, int, int]:
        root_attribs: dict[str, str] = {}
        viewport = getattr(observation, "viewport", None)
        rotation = getattr(viewport, "rotation", None)
        if getattr(rotation, "state", None) == "value" and _is_strict_int(rotation.value):
            root_attribs["rotation"] = str(rotation.value)
        hierarchy = ElementTree.Element("hierarchy", root_attribs)

        counters = [0, 0, 0]  # [source_nodes, retained_nodes, clipped_attributes]
        for tree_root in getattr(observation, "roots", ()):
            hierarchy.extend(self._project_tree_node(tree_root, compact=compact, counters=counters))
        return hierarchy, counters[0], counters[1], counters[2]

    def _project_tree_node(
        self,
        node: Any,
        *,
        compact: bool,
        counters: list[int],
    ) -> list[ElementTree.Element]:
        counters[0] += 1
        tag, attributes, clipped = self._tree_node_tag_and_attributes(node, compact=compact)
        counters[2] += clipped

        children: list[ElementTree.Element] = []
        for child in getattr(node, "children", ()):
            children.extend(self._project_tree_node(child, compact=compact, counters=counters))

        if compact and not self._has_ui_snapshot_signal(attributes):
            return children

        counters[1] += 1
        element = ElementTree.Element(tag, attributes)
        element.extend(children)
        return [element]

    def _tree_node_tag_and_attributes(self, node: Any, *, compact: bool) -> tuple[str, dict[str, str], int]:
        attributes: dict[str, str] = {}
        clipped = 0

        type_obs = getattr(node, "type", None)
        class_name = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) else None
        tag = class_name if class_name and ANDROID_XML_TAG_PATTERN.fullmatch(class_name) else "node"
        if class_name:
            attributes["class"] = class_name

        pkg_obs = getattr(node, "package", None)
        pkg_val = pkg_obs.value if getattr(pkg_obs, "state", None) == "value" and isinstance(pkg_obs.value, str) and pkg_obs.value else None
        if pkg_val is not None:
            attributes["package"] = pkg_val

        id_obs = getattr(node, "id", None)
        if getattr(id_obs, "state", None) == "value" and isinstance(id_obs.value, str) and id_obs.value:
            attributes["resource-id"] = id_obs.value
        for field_name, xml_attr in (("text", "text"), ("desc", "content-desc")):
            obs = getattr(node, field_name, None)
            if getattr(obs, "state", None) == "value" and isinstance(obs.value, str) and obs.value:
                raw_text = obs.value
                if compact and len(raw_text) > ANDROID_UI_SNAPSHOT_TEXT_LIMIT_CHARS:
                    clipped += 1
                    raw_text = raw_text[:ANDROID_UI_SNAPSHOT_TEXT_LIMIT_CHARS]
                if raw_text:
                    attributes[xml_attr] = raw_text

        enabled_obs = getattr(node, "enabled", None)
        if getattr(enabled_obs, "state", None) == "value" and enabled_obs.value is False:
            attributes["enabled"] = "false"

        if getattr(node, "visibility", None) == "not_visible":
            attributes["displayed"] = "false"

        for bool_field in ("focused", "checked", "selected"):
            obs = getattr(node, bool_field, None)
            if getattr(obs, "state", None) == "value" and obs.value is True:
                attributes[bool_field] = "true"

        window_obs = getattr(node, "window_id", None)
        if getattr(window_obs, "state", None) == "value" and _is_strict_int(window_obs.value):
            attributes["window-id"] = str(window_obs.value)

        bounds_obs = getattr(node, "bounds", None)
        bounds_val = getattr(bounds_obs, "value", None) if getattr(bounds_obs, "state", None) == "value" else None
        if bounds_val is not None and all(hasattr(bounds_val, attr) for attr in ("x", "y", "width", "height")):
            left = int(bounds_val.x)
            top = int(bounds_val.y)
            right = left + int(bounds_val.width)
            bottom = top + int(bounds_val.height)
            attributes["bounds"] = f"[{left},{top}][{right},{bottom}]"

        return tag, attributes, clipped

    @staticmethod
    def _has_ui_snapshot_signal(attributes: dict[str, str]) -> bool:
        return any(key not in ANDROID_UI_SNAPSHOT_STRUCTURAL_ATTRIBUTES for key in attributes)

    def _ensure_session(self) -> Any:
        if self._closed:
            raise ToolExecutionError("Android resources are closed.", context=self._safe_diagnostics())
        if self._cleanup_pending:
            raise ToolExecutionError("Android resources await explicit terminal cleanup.", context=self._safe_diagnostics())
        if self._session is not None:
            info_fn = getattr(self._session, "session_info", None)
            if callable(info_fn):
                state = getattr(info_fn(), "state", "active")
                if state != "active":
                    self._cleanup_pending = not self._borrowed_session
                    raise ToolExecutionError("Android session is unavailable; explicit terminal cleanup is required.", context={"reason": "session_unavailable", **self._safe_diagnostics()})
            if self._last_operation_finished_at is not None and time.monotonic() - self._last_operation_finished_at >= SESSION_IDLE_LIVENESS_SECONDS:
                self._session.ensure_alive(control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS, cancellation=self._operation_cancellation))
            return self._session

        serial = (self.serial or "").strip()
        if not serial:
            raise ConfigurationError(
                "Android device serial is required to open a managed Appium session.",
                context={"action": "Select an online authorized Android device or pass --android-serial."},
            )

        try:
            from appium_connect_agent.automation import create_session
            from appium_connect_agent.contracts import LocalSettings
            from appium_connect_agent.runtime import OperationControl, Runtime, ServiceLifecycleError
        except ImportError as exc:
            raise ConfigurationError(
                "appium-connect-agent is required for AppiumConnectAndroidDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc

        local_settings = _resolve_managed_local_settings(LocalSettings)
        runtime = Runtime(local_settings)
        self._runtime = runtime
        self._cleanup_pending = True
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
            self._managed_scope = candidate_scope
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
                    except BaseException as cleanup_error:  # noqa: BLE001 - preserve startup and cleanup failures, including cancellation.
                        raise BaseExceptionGroup("Managed startup and cleanup failed.", [exc, cleanup_error]) from None
                    self._managed_scope = None
                    self._scope_exit_attempted = False
                    last_port_error = exc
                    continue
                raise

        if scope is None or connection is None:
            if last_port_error is not None:
                raise last_port_error
            raise ConfigurationError("Failed to bind a managed Appium loopback port.")

        initial_settings = {}
        if self._uiautomator2_wait_for_idle_timeout_ms is not None:
            initial_settings["uiautomator2_wait_for_idle_timeout_ms"] = self._uiautomator2_wait_for_idle_timeout_ms

        try:
            session = create_session(
                connection,
                serial,
                app_id=(self.app_id or "").strip() or None,
                auto_launch=False,
                force_app_launch=False,
                no_reset=True,
                full_reset=False,
                should_terminate_app=False,
                new_command_timeout_seconds=self._new_command_timeout_seconds,
                control=OperationControl(timeout_seconds=DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS),
                **initial_settings,
            )
        except BaseException as exc:
            # SDK create errors can carry the exact uncertain Session. Keep the
            # complete owner chain for explicit close; never rebuild it implicitly.
            self._session = getattr(exc, "session", None)
            self._cleanup_pending = True
            raise

        self._session = session
        self._cleanup_pending = False
        return session

    def _run_on_worker(self, func: Callable[[], _T], *, cancellation_check: Callable[[], None] | None = None, cancellation_event: Event | None = None) -> _T:
        if self._closed or self._cleanup_pending:
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

        # Borrowed Sessions retain their caller's ownership; controlled capture
        # still uses the existing worker and cross-thread cancellation Event.
        if self._borrowed_session and cancellation_check is None:
            return run_operation()
        self._ensure_executor()
        try:
            return self._run_sync(run_operation, cancellation_check=cancellation_check, cancellation_event=cancellation_event)
        except BaseException:
            if not self._cleanup_pending and self._session is None and self._managed_scope is None and self._runtime is None:
                self._shutdown_executor()
            raise

    def _run_tool(self, func: Callable[[], dict[str, object]]) -> dict[str, object]:
        try:
            return self._run_on_worker(func)
        except _LocatorConfigurationError as exc:
            return self._configuration_error(str(exc))
        except ValidationError:
            return self._configuration_error("Invalid Android automation request parameters.")
        except ConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc)

    def _normalize_backend_exception(self, exc: Exception) -> dict[str, object]:
        code = getattr(exc, "code", None)
        cls_name = type(exc).__name__
        metadata = self._safe_diagnostics(exc)

        if cls_name in {"InvalidRequestError", "ElementValidationError"}:
            return self._failed("configuration_error", "Invalid Android automation request parameters.", metadata=metadata)
        if cls_name == "TargetError" and self._target_error_is_definitive(exc):
            if code == "target_ambiguous":
                return self._target_ambiguous(exc)
            if code == "target_not_found":
                return self._failed("target_resolution_error", "Target was not found.", metadata=metadata)
        return self._failed("action_error", "Android backend operation failed.", metadata=metadata)

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
            if isinstance(code, str) and code in ANDROID_SAFE_BACKEND_CODES:
                metadata["backend_code"] = code
            request_state = getattr(exc, "request_state", None)
            if isinstance(request_state, str) and request_state in {"not_sent", "response_received", "response_unknown"}:
                metadata["request_state"] = request_state
            operation_id = getattr(exc, "operation_id", None)
            if isinstance(operation_id, str) and re.fullmatch(r"[0-9a-f]{32}", operation_id) is not None:
                metadata["operation_id"] = operation_id
            steps = getattr(exc, "completed_steps", None)
            if isinstance(steps, (tuple, list)):
                metadata["completed_steps"] = list(dict.fromkeys(step for step in steps[: len(ANDROID_SAFE_COMPLETED_STEPS)] if isinstance(step, str) and step in ANDROID_SAFE_COMPLETED_STEPS))
            if isinstance(exc, ToolExecutionError):
                reason = exc.context.get("reason")
                if reason in {"closed", "cleanup_pending", "session_unavailable", "display_size_unavailable"}:
                    metadata["reason"] = reason
        # SessionInfo is a public local snapshot: no device request, reconnection, or cleanup.
        # A broken diagnostic getter must never replace a primary failure or cancellation.
        try:
            if self._session is not None:
                state = self._session.session_info().state
                if isinstance(state, str) and state in ANDROID_SESSION_STATES:
                    metadata["session_state"] = state
        except BaseException:  # noqa: BLE001 - Optional local diagnostics must not replace the primary failure or its cancellation.
            return metadata
        return metadata

    @staticmethod
    def _propagate_if_cancelled(exc: BaseException) -> None:
        if type(exc).__name__ == "OperationCancelledError" or getattr(exc, "code", None) == "operation_cancelled":
            import asyncio

            raise asyncio.CancelledError from exc

    @staticmethod
    def _operation_control(timeout_seconds: float, *, cancellation: Event | None = None) -> Any:
        try:
            from appium_connect_agent.runtime import OperationControl
        except ImportError:
            return None
        return OperationControl(timeout_seconds=timeout_seconds, cancellation=cancellation)

    @staticmethod
    def _resource_id_predicate(resource_id: str) -> str:
        raw = _xpath_literal(resource_id)
        if ANDROID_LOCAL_RESOURCE_ID_PATTERN.fullmatch(resource_id) is not None:
            # SDK TreeNode.id localizes native ids using the node's own package, not the Session target.
            suffix = _xpath_literal(f":id/{resource_id}")
            return f"(@resource-id=concat(@package, {suffix}) or @resource-id={raw})"
        return f"@resource-id={raw}"

    def _resolve_element(self, session: Any, params: dict[str, object], *, locator_key: str = "locator") -> Any:
        locator = params.get(locator_key)
        if not isinstance(locator, dict):
            locator = params.get("locator")
        try:
            if isinstance(locator, dict):
                xpath = locator.get("xpath")
                if isinstance(xpath, str) and xpath.strip():
                    return session.element("xpath", xpath)
                populated = self._populated_locator_fields(locator)
                if set(populated) == {"resourceId"}:
                    resource_id = populated["resourceId"]
                    if ANDROID_STANDARD_RESOURCE_ID.fullmatch(resource_id) is not None:
                        return session.element("id", resource_id, raw_id=False)
                    if ANDROID_LOCAL_RESOURCE_ID_PATTERN.fullmatch(resource_id) is not None:
                        return session.element("xpath", f"//*[{self._resource_id_predicate(resource_id)}]")
                    return session.element("id", resource_id, raw_id=True)
                if set(populated) == {"accessibilityId"}:
                    return session.element("accessibility id", populated["accessibilityId"])
                if populated:
                    predicates: list[str] = []
                    if "resourceId" in populated:
                        predicates.append(self._resource_id_predicate(populated["resourceId"]))
                    if "accessibilityId" in populated:
                        predicates.append(f"@content-desc={_xpath_literal(populated['accessibilityId'])}")
                    if "text" in populated:
                        predicates.append(f"@text={_xpath_literal(populated['text'])}")
                    if "className" in populated:
                        predicates.append(f"@class={_xpath_literal(populated['className'])}")
                    return session.element("xpath", f"//*[{' and '.join(predicates)}]")
            fallback = params.get("target")
            if isinstance(fallback, str) and fallback.strip():
                literal = _xpath_literal(fallback.strip())
                return session.element("xpath", f"//*[@text={literal} or (not(//*[@text={literal}]) and @content-desc={literal})]")
        except ValueError as exc:
            raise _LocatorConfigurationError("Invalid Android locator syntax or length.") from exc

        raise _LocatorConfigurationError("Android action requires a non-empty target or locator.")

    @staticmethod
    def _populated_locator_fields(locator: dict[str, object]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in ("resourceId", "accessibilityId", "text", "className"):
            value = locator.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip() if key == "resourceId" else value
        return result

    def _display_size(self, session: Any) -> tuple[int, int]:
        query = getattr(session.device, "display_size", None)
        if not callable(query):
            raise ToolExecutionError("Public Android physical display size is unavailable.", context={"reason": "display_size_unavailable"})
        display = query(control=self._operation_control(15.0))
        width, height = getattr(display, "width", None), getattr(display, "height", None)
        if not (_is_strict_int(width) and _is_strict_int(height) and 1 <= width <= MAX_COORDINATE_VALUE and 1 <= height <= MAX_COORDINATE_VALUE):
            raise ToolExecutionError("Public Android physical display size is invalid.", context={"reason": "display_size_unavailable"})
        return width, height

    def _extract_current_package(self, observation: Any) -> str | None:
        focused_candidate: str | None = None
        first_app_candidate: str | None = None
        fallback_candidate: str | None = None
        stack = list(reversed(getattr(observation, "roots", ())))
        while stack:
            node = stack.pop()
            pkg_obs = getattr(node, "package", None)
            pkg = pkg_obs.value if getattr(pkg_obs, "state", None) == "value" and isinstance(pkg_obs.value, str) and pkg_obs.value else None
            if pkg:
                if fallback_candidate is None:
                    fallback_candidate = pkg
                if pkg not in ANDROID_SYSTEM_UI_PACKAGES:
                    if first_app_candidate is None:
                        first_app_candidate = pkg
                    focused_obs = getattr(node, "focused", None)
                    if getattr(focused_obs, "state", None) == "value" and focused_obs.value is True:
                        focused_candidate = pkg
                        break
            children = getattr(node, "children", ())
            if children:
                stack.extend(reversed(children))
        return focused_candidate or first_app_candidate or fallback_candidate

    def _scale_points_if_needed(
        self,
        session: Any,
        points: list[tuple[int, int]],
        reference_screen_size: object,
    ) -> tuple[list[tuple[int, int]], tuple[int, int]]:
        screen_size = self._display_size(session)
        width, height = screen_size
        if reference_screen_size is None:
            if not all(0 <= px < width and 0 <= py < height for px, py in points):
                raise _LocatorConfigurationError("Android coordinates must be inside the current physical display.")
            return points, screen_size
        if not isinstance(reference_screen_size, dict):
            raise _LocatorConfigurationError("Invalid Android replay reference screen size.")
        ref_w = reference_screen_size.get("width")
        ref_h = reference_screen_size.get("height")
        if not _is_strict_int(ref_w) or not _is_strict_int(ref_h) or ref_w < 1 or ref_h < 1:
            raise _LocatorConfigurationError("Invalid Android replay reference screen size.")
        scaled: list[tuple[int, int]] = []
        for px, py in points:
            sx = self._clamp_coordinate(round(px * width / ref_w), width)
            sy = self._clamp_coordinate(round(py * height / ref_h), height)
            scaled.append((sx, sy))
        return scaled, screen_size

    @staticmethod
    def _clamp_coordinate(value: int, limit: int) -> int:
        return max(0, min(value, max(limit - 1, 0)))

    @staticmethod
    def _swipe_points(direction: str, width: int, height: int) -> tuple[int, int, int, int]:
        normalized = direction.strip().lower()
        mid_x = width // 2
        mid_y = height // 2
        top = min(ceil(height * 0.20), height - 1)
        bottom = min(floor(height * 0.80), height - 1)
        left = min(ceil(width * 0.18), width - 1)
        right = min(floor(width * 0.82), width - 1)
        if normalized == "up":
            return mid_x, bottom, mid_x, top
        if normalized == "down":
            return mid_x, top, mid_x, bottom
        if normalized == "left":
            return right, mid_y, left, mid_y
        return left, mid_y, right, mid_y

    @staticmethod
    def _swipe_point_payload(params: dict[str, object]) -> tuple[int, int, int, int] | None:
        start = params.get("start")
        end = params.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            return None
        sx = start.get("x")
        sy = start.get("y")
        ex = end.get("x")
        ey = end.get("y")
        if not all(_is_strict_int(value) for value in (sx, sy, ex, ey)):
            return None
        return int(sx), int(sy), int(ex), int(ey)  # type: ignore[arg-type]

    @staticmethod
    def _point_payload(params: dict[str, object]) -> tuple[int, int] | None:
        point = params.get("point")
        if not isinstance(point, dict):
            return None
        x = point.get("x")
        y = point.get("y")
        if not _is_strict_int(x) or not _is_strict_int(y):
            return None
        return int(x), int(y)  # type: ignore[arg-type]

    @staticmethod
    def _expected_element_states(element: dict[str, object]) -> dict[str, bool]:
        return {field: value for field in ANDROID_STATE_ASSERTION_FIELDS if type(value := element.get(field)) is bool}

    @staticmethod
    def _param_data(params: BaseModel | dict[str, object]) -> dict[str, object]:
        if isinstance(params, BaseModel):
            return params.model_dump(mode="json", exclude_none=True)
        return dict(params)

    def _run_sync(self, func: Callable[[], _T], *, cancellation_check: Callable[[], None] | None = None, cancellation_event: Event | None = None) -> _T:
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
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-android-driver")

    def _shutdown_executor(self) -> None:
        executor = self._executor
        if executor is not None:
            executor.shutdown(wait=True)
            self._executor = None

    def _target_missing(self) -> dict[str, object]:
        return self._failed("target_resolution_error", "Target was not found.", metadata={"reason": "target_missing", **self._safe_diagnostics()})

    def _target_ambiguous(self, exc: Exception | None = None) -> dict[str, object]:
        return self._failed(
            "target_resolution_error",
            "Target locator matched multiple Android elements; refine the locator.",
            metadata={"reason": "target_ambiguous", **self._safe_diagnostics(exc)},
        )

    def _configuration_error(self, message: str) -> dict[str, object]:
        return self._failed("configuration_error", message)

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
