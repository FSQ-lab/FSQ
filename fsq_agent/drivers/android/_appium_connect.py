# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
import re
import socket
import time
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from xml.etree import ElementTree

from pydantic import BaseModel

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _android_driver_tool
from fsq_agent.models import (
    AndroidAssertNotVisibleParams,
    AndroidAssertStateParams,
    AndroidAssertVisibleParams,
    AndroidAssertWithAIParams,
    AndroidInputTextParams,
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
DEFAULT_NEW_COMMAND_TIMEOUT_SECONDS = 300.0
DEFAULT_OPERATION_TIMEOUT_SECONDS = 25.0
DEFAULT_LONG_PRESS_DURATION_MS = 1000
MAX_COORDINATE_VALUE = 2_147_483_647
MAX_SWIPE_DURATION_MS = 60_000
MAX_PORT_BIND_ATTEMPTS = 3

ANDROID_STATE_ASSERTION_FIELDS = ("enabled", "checked", "selected", "clickable", "focused")
ANDROID_LOCATOR_FIELDS = ("resourceId", "accessibilityId", "text", "className", "xpath")
ANDROID_EDITABLE_CLASSES = frozenset(
    {
        "android.widget.EditText",
        "android.widget.AutoCompleteTextView",
        "android.widget.MultiAutoCompleteTextView",
    }
)
ANDROID_INPUT_IDENTITY_FIELDS = ("type", "id", "package")
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
ANDROID_INSPECT_ASSERT_ATTRIBUTES = (
    "text",
    "enabled",
    "checked",
    "selected",
    "clickable",
    "focused",
)
ANDROID_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
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
    "page_up": 92,
    "page_down": 93,
    "escape": 111,
    "esc": 111,
    "forward_del": 112,
    "move_home": 122,
    "move_end": 123,
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
    ) -> None:
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
        self._closed = False

    def close(self) -> None:
        if self._borrowed_session:
            return
        if self._executor is None and self._session is None and self._managed_scope is None and self._runtime is None:
            self._closed = True
            return
        try:
            self._run_sync(self._close_owned_resources)
        finally:
            # Pending disposal must retain its original SDK owner thread.
            if self._session is None and self._managed_scope is None and self._runtime is None:
                self._shutdown_executor()

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
                    except BaseException as exc:  # noqa: BLE001 - retain quit failure while explicitly disposing uncertain resources.
                        primary_error = self._cleanup_primary = exc
                        state = session.session_info().state
                        if state not in {"uncertain", "disposing", "disposed"}:
                            raise
                    else:
                        self._session_settled = True
                if not self._session_settled:
                    if state in {"uncertain", "disposing"}:
                        session.dispose(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                        managed_settled = True
                    elif state == "disposed":
                        managed_settled = True
                    elif state != "closed":
                        raise ToolExecutionError("Owned Android session is not available for terminal cleanup.", context={"state": state})
                    self._session_settled = True

            if not managed_settled:
                if self._scope_exit_attempted and self._runtime is not None:
                    self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                elif self._managed_scope is not None:
                    self._scope_exit_attempted = True
                    # No primary exception is passed: scope cleanup failure must raise,
                    # rather than being attached to and hidden behind a business error.
                    self._managed_scope.__exit__(None, None, None)
                elif self._runtime is not None:
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
        self._cleanup_primary = None
        self._cleanup_pending = False
        self._closed = True
        if primary_error is not None:
            raise primary_error

    def context(self) -> dict[str, object]:
        return self._run_on_worker(self._context_impl)

    def _context_impl(self) -> dict[str, object]:
        session = self._ensure_session()
        info = session.session_info()
        session_id = getattr(info, "appium_session_id", None) or getattr(info, "id", None) or f"appium_connect_agent:{self.serial or 'injected'}"
        observation = session.observe_tree(control=self._operation_control(DEFAULT_OPERATION_TIMEOUT_SECONDS))
        screen_size = self._viewport_size(observation)
        current_package = self._extract_current_package(observation)
        return {
            "session_id": str(session_id),
            "screen_size": screen_size,
            "metadata": {
                "backend": self.backend,
                "current_package": current_package,
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
        data = self._param_data(params)
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
        scaled = self._scale_points_if_needed(session, [point], data.get("reference_screen_size"))
        if scaled is None:
            return self._failed("action_error", "Current Android viewport size is unavailable for coordinate scaling.")
        x, y = scaled[0]
        if not (0 <= x <= MAX_COORDINATE_VALUE and 0 <= y <= MAX_COORDINATE_VALUE):
            return self._configuration_error("tapAt coordinates must be integers between 0 and 2147483647.")
        session.device.tap_at(x, y, control=self._operation_control(15.0))
        return self._passed({"point": {"x": x, "y": y}})

    @_android_driver_tool("longPressOn", description="Long press an Android UI target.")
    def long_press_on(self, params: AndroidLongPressOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._long_press_on_impl(params))

    def _long_press_on_impl(self, params: AndroidLongPressOnParams) -> dict[str, object]:
        data = self._param_data(params)
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
        if self._borrowed_session and self._executor is None:
            return self._input_text_impl(params)
        self._ensure_executor()
        return self._run_sync(lambda: self._input_text_impl(params))

    def _input_text_impl(self, params: AndroidInputTextParams) -> dict[str, object]:
        data = self._param_data(params)
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
            return self._failed("action_error", "Input target inspection failed.", metadata={"reason": "entry_backend_error"})

        before, resolution_failure = self._wait_and_inspect_input_target(element)
        if resolution_failure is not None or before is None:
            return resolution_failure or self._failed("action_error", "Input target inspection failed.", metadata={"reason": "entry_backend_error"})

        class_name = self._attr_value(before, "type")
        enabled = self._attr_value(before, "enabled")
        displayed = self._attr_value(before, "displayed")
        if not isinstance(class_name, str) or class_name not in ANDROID_EDITABLE_CLASSES or enabled is not True or displayed is not True:
            return self._failed("target_resolution_error", "Input target is not demonstrably editable.", metadata={"reason": "invalid_input_target"})

        before_package = self._attr_value(before, "package")
        before_bounds = self._attr_value(before, "bounds")
        if not self._input_text_is_readable(before) or not isinstance(before_package, str) or not before_package.strip() or before_bounds is None:
            return self._failed("action_error", "Input target text cannot be verified.", metadata={"reason": "verification_unavailable"})

        before_text = self._attr_value(before, "text")
        try:
            element.click(wait_timeout_seconds=2.0, control=self._operation_control(10.0))
            element.replace_element_value(text, wait_timeout_seconds=5.0, control=self._operation_control(15.0))
        # Contain backend messages locally so literal/secret text never leaks; BaseException cancellation propagates.
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._failed("action_error", "Text entry backend failed; acceptance is unknown.", metadata={"reason": "entry_backend_error"})

        if before_text != text and not self._input_locator_is_text_independent(data):
            return self._failed(
                "action_error",
                "Text entry acceptance cannot be verified on the original target.",
                metadata={"reason": "verification_unavailable"},
            )

        try:
            inspection = element.inspect(
                attributes=ANDROID_INSPECT_INPUT_ATTRIBUTES,
                control=self._operation_control(10.0),
            )
            after = self._attributes_dict(inspection)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._failed("action_error", "Text entry acceptance could not be read.", metadata={"reason": "verification_unavailable"})

        before_id = self._attr_raw_id(before)
        if (
            not self._input_text_is_readable(after)
            or self._attr_value(after, "enabled") is not True
            or any(before.get(field) != after.get(field) for field in ANDROID_INPUT_IDENTITY_FIELDS)
            or (self._attr_value(before, "bounds") != self._attr_value(after, "bounds") and not self._input_locator_binds_resource(data, before_id))
        ):
            return self._failed(
                "action_error",
                "Text entry acceptance cannot be verified on the original target.",
                metadata={"reason": "verification_unavailable"},
            )

        if self._attr_value(after, "text") != text:
            return self._failed("action_error", "Text entry did not match the requested replacement.", metadata={"reason": "entry_mismatch"})
        return self._passed()

    def _wait_and_inspect_input_target(self, element: Any) -> tuple[dict[str, tuple[str, object]] | None, dict[str, object] | None]:
        deadline = time.monotonic() + DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS
        while True:
            try:
                inspection = element.inspect(
                    attributes=ANDROID_INSPECT_INPUT_ATTRIBUTES,
                    control=self._operation_control(10.0),
                )
                return self._attributes_dict(inspection), None
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                code = getattr(exc, "code", None)
                if code == "target_ambiguous":
                    return None, self._failed(
                        "target_resolution_error",
                        "Target locator matched multiple Android elements; refine the locator.",
                        metadata={"reason": "target_ambiguous"},
                    )
                if code == "target_not_found":
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))
                        continue
                    return None, self._failed(
                        "target_resolution_error",
                        "Input target was not found.",
                        metadata={"reason": "target_missing"},
                    )
                return None, self._failed("action_error", "Input target inspection failed.", metadata={"reason": "entry_backend_error"})

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
        if entry is not None and entry[0] == "value" and isinstance(entry[1], str) and entry[1].strip():
            return entry[1]
        return None

    def _input_text_is_readable(self, attrs: dict[str, tuple[str, object]]) -> bool:
        password_entry = attrs.get("password")
        text_entry = attrs.get("text")
        if password_entry != ("value", False) or text_entry is None or text_entry[0] != "value":
            return False
        text = text_entry[1]
        return isinstance(text, str) and (not text or not set(text) <= {"*", "•", "●"})

    def _input_locator_is_text_independent(self, data: dict[str, object]) -> bool:
        locator = data.get("locator")
        if isinstance(locator, dict):
            xpath = locator.get("xpath")
            if isinstance(xpath, str) and xpath.strip():
                return ANDROID_TEXT_INDEPENDENT_XPATH.fullmatch(xpath) is not None
            populated = self._populated_locator_fields(locator)
            if populated:
                return "text" not in populated
        return False

    def _input_locator_binds_resource(self, data: dict[str, object], resource_name: str | None) -> bool:
        locator = data.get("locator")
        if not isinstance(resource_name, str) or not resource_name.strip() or not isinstance(locator, dict):
            return False
        xpath = locator.get("xpath")
        if isinstance(xpath, str) and xpath.strip():
            return ANDROID_TEXT_INDEPENDENT_XPATH.fullmatch(xpath) is not None and any(xpath.endswith(f"[@resource-id={quote}{resource_name}{quote}]") for quote in ("'", '"'))
        locator_id = locator.get("resourceId")
        if not isinstance(locator_id, str) or not locator_id.strip():
            return False
        if locator_id == resource_name:
            return True
        app_id = (self.app_id or "").strip()
        if ":" not in locator_id and ANDROID_LOCAL_RESOURCE_ID_PATTERN.fullmatch(locator_id) is not None and app_id:
            return resource_name == f"{app_id}:id/{locator_id}"
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
        session = self._ensure_session()
        if normalized == "back":
            session.device.back(control=self._operation_control(15.0))
            return self._passed({"key": key_text})
        keycode: int | None = ANDROID_KEYCODE_MAP.get(normalized)
        if keycode is None and normalized.isascii() and normalized.isdigit():
            parsed = int(normalized)
            if 0 <= parsed <= MAX_COORDINATE_VALUE:
                keycode = parsed
        if keycode is None:
            return self._configuration_error(f"Unsupported Android key: {key_text!r}.")
        session.device.press_keycode(keycode, control=self._operation_control(15.0))
        return self._passed({"key": key_text})

    @_android_driver_tool("swipe", description="Swipe by direction or explicit Android screen coordinates.")
    def swipe(self, params: AndroidSwipeParams) -> dict[str, object]:
        return self._run_tool(lambda: self._swipe_impl(params))

    def _swipe_impl(self, params: AndroidSwipeParams) -> dict[str, object]:
        data = self._param_data(params)
        raw_duration = data.get("duration")
        duration_ms = 200 if raw_duration is None else raw_duration
        if not _is_strict_int(duration_ms) or not (1 <= duration_ms <= MAX_SWIPE_DURATION_MS):
            return self._configuration_error("swipe duration must be an integer from 1 through 60000 milliseconds.")
        timeout_budget = max(15.0, duration_ms / 1000.0 + 10.0)
        session = self._ensure_session()

        if "start" in data or "end" in data:
            points = self._swipe_point_payload(data)
            if points is None:
                return self._configuration_error("swipe point payload requires integer start.x, start.y, end.x, and end.y parameters.")
            sx, sy, ex, ey = points
            scaled = self._scale_points_if_needed(session, [(sx, sy), (ex, ey)], data.get("reference_screen_size"))
            if scaled is None:
                return self._failed("action_error", "Current Android viewport size is unavailable for coordinate scaling.")
            (sx, sy), (ex, ey) = scaled
            if not all(0 <= coord <= MAX_COORDINATE_VALUE for coord in (sx, sy, ex, ey)):
                return self._configuration_error("swipe coordinates must be integers between 0 and 2147483647.")
            session.device.swipe_between(
                sx,
                sy,
                ex,
                ey,
                duration_ms=duration_ms,
                control=self._operation_control(timeout_budget),
            )
            return self._passed({"start": {"x": sx, "y": sy}, "end": {"x": ex, "y": ey}})

        direction = data.get("direction")
        if not isinstance(direction, str) or direction.strip().lower() not in {"up", "down", "left", "right"}:
            return self._configuration_error("swipe requires a supported direction parameter.")
        observation = session.observe_tree(control=self._operation_control(15.0))
        viewport = self._viewport_size(observation)
        if viewport is None:
            return self._failed("action_error", "Current Android viewport size is unavailable for directional swipe.")
        width, height = viewport
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
        data = self._param_data(params)
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
                if code == "target_ambiguous":
                    return self._target_ambiguous()
                if code != "target_not_found":
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._target_missing(data)
            time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

    @_android_driver_tool("assertNotVisible", description="Assert that an Android UI target is not visible.")
    def assert_not_visible(self, params: AndroidAssertNotVisibleParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_not_visible_impl(params))

    def _assert_not_visible_impl(self, params: AndroidAssertNotVisibleParams) -> dict[str, object]:
        data = self._param_data(params)
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
                if attrs.get("displayed") == ("value", False):
                    return self._passed()
            except Exception as exc:
                self._propagate_if_cancelled(exc)
                code = getattr(exc, "code", None)
                if code == "target_not_found":
                    return self._passed()
                if code != "target_ambiguous":
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._failed("assertion_error", "Target is visible.")
            time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

    @_android_driver_tool("assert", description="Assert Android element existence, text, or state.")
    def assert_state(self, params: AndroidAssertStateParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_state_impl(params))

    def _assert_state_impl(self, params: AndroidAssertStateParams) -> dict[str, object]:
        data = self._param_data(params)
        session = self._ensure_session()
        element = self._resolve_element(session, data, locator_key="element")
        attrs, resolution_error = self._wait_for_assert_attributes(element, data)
        if resolution_error is not None or attrs is None:
            return resolution_error or self._target_missing(data)

        expected_text = data.get("text")
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

        element_payload = data.get("element")
        if isinstance(element_payload, dict):
            expected_states = self._expected_element_states(element_payload)
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
            if self._has_locator(element_payload):
                return self._passed({"exists": True})
        return self._configuration_error("assert requires a text or supported element state assertion.")

    def _wait_for_assert_attributes(
        self,
        element: Any,
        data: dict[str, object],
    ) -> tuple[dict[str, tuple[str, object]] | None, dict[str, object] | None]:
        deadline = time.monotonic() + DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS
        while True:
            try:
                inspection = element.inspect(
                    attributes=ANDROID_INSPECT_ASSERT_ATTRIBUTES,
                    control=self._operation_control(10.0),
                )
                return self._attributes_dict(inspection), None
            except Exception as exc:
                self._propagate_if_cancelled(exc)
                code = getattr(exc, "code", None)
                if code == "target_ambiguous":
                    return None, self._target_ambiguous()
                if code != "target_not_found":
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, self._target_missing(data)
            time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

    @_android_driver_tool("assertWithAI", description="Evaluate an explicit Android visual assertion with AI.")
    def assert_with_ai(self, params: AndroidAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        del params
        return self._run_on_worker(self._screenshot_impl)

    def _screenshot_impl(self) -> bytes:
        session = self._ensure_session()
        return bytes(session.device.take_screenshot(control=self._operation_control(20.0)))

    @_android_driver_tool(
        "uiTree",
        description=(
            "Return a compact Android UI hierarchy XML snapshot synthesized from public TreeObservation. "
            "Layout-only wrapper nodes and empty/default attributes are removed, and long text/content-desc "
            "attributes are clipped to the first 50 characters."
        ),
    )
    def ui_snapshot(self, params: AndroidUiTreeParams) -> dict[str, object]:
        del params
        return self._run_on_worker(self._ui_snapshot_impl)

    def _ui_snapshot_impl(self) -> dict[str, object]:
        session = self._ensure_session()
        observation = session.observe_tree(control=self._operation_control(20.0))
        return self._serialize_tree_observation(observation)

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
            id_val = id_obs.value
            if ":" not in id_val and ANDROID_LOCAL_RESOURCE_ID_PATTERN.fullmatch(id_val) is not None and pkg_val is not None and ANDROID_PACKAGE_NAME_PATTERN.fullmatch(pkg_val) is not None:
                attributes["resource-id"] = f"{pkg_val}:id/{id_val}"
            else:
                attributes["resource-id"] = id_val
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
        if self._closed or self._cleanup_pending:
            raise ToolExecutionError("Android resources are closed or awaiting explicit terminal cleanup.")
        if self._session is not None:
            info_fn = getattr(self._session, "session_info", None)
            if callable(info_fn):
                state = getattr(info_fn(), "state", "active")
                if state != "active":
                    self._cleanup_pending = not self._borrowed_session
                    raise ToolExecutionError("Android session is unavailable; explicit terminal cleanup is required.", context={"state": state})
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
                    except BaseException as cleanup_error:
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
                new_command_timeout_seconds=DEFAULT_NEW_COMMAND_TIMEOUT_SECONDS,
                control=OperationControl(timeout_seconds=DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS),
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

    def _run_on_worker(self, func: Callable[[], _T]) -> _T:
        if self._borrowed_session and self._executor is None:
            return func()
        self._ensure_executor()
        try:
            return self._run_sync(func)
        except BaseException:
            if self._session is None and self._managed_scope is None and self._runtime is None:
                self._shutdown_executor()
            raise

    def _run_tool(self, func: Callable[[], dict[str, object]]) -> dict[str, object]:
        try:
            return self._run_on_worker(func)
        except _LocatorConfigurationError as exc:
            return self._configuration_error(str(exc))
        except ConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc)

    def _normalize_backend_exception(self, exc: Exception) -> dict[str, object]:
        code = getattr(exc, "code", None)
        request_state = getattr(exc, "request_state", None)
        cls_name = type(exc).__name__
        metadata: dict[str, Any] = {}
        if isinstance(code, str) and code:
            metadata["backend_code"] = code
        if isinstance(request_state, str) and request_state:
            metadata["request_state"] = request_state

        if cls_name in {"InvalidRequestError", "ElementValidationError"}:
            return self._failed("configuration_error", "Invalid Android automation request parameters.", metadata=metadata)
        if cls_name == "TargetError":
            if code == "target_ambiguous":
                return self._target_ambiguous()
            return self._failed("target_resolution_error", "Target was not found.", metadata=metadata)
        return self._failed("action_error", f"Android backend operation failed ({code or cls_name}).", metadata=metadata)

    @staticmethod
    def _propagate_if_cancelled(exc: BaseException) -> None:
        if type(exc).__name__ == "OperationCancelledError" or getattr(exc, "code", None) == "operation_cancelled":
            import asyncio

            raise asyncio.CancelledError from exc

    @staticmethod
    def _operation_control(timeout_seconds: float) -> Any:
        try:
            from appium_connect_agent.runtime import OperationControl
        except ImportError:
            return None
        return OperationControl(timeout_seconds=timeout_seconds)

    def _valid_bound_app_id(self, session: Any) -> str | None:
        info_fn = getattr(session, "session_info", None)
        if callable(info_fn):
            info = info_fn()
            target_app_id = getattr(info, "target_app_id", None)
            if isinstance(target_app_id, str) and ANDROID_PACKAGE_NAME_PATTERN.fullmatch(target_app_id) is not None:
                return target_app_id
        candidate = (self.app_id or "").strip()
        if candidate and ANDROID_PACKAGE_NAME_PATTERN.fullmatch(candidate) is not None:
            return candidate
        return None

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
                    if ":" not in resource_id and ANDROID_LOCAL_RESOURCE_ID_PATTERN.fullmatch(resource_id) is not None:
                        bound_app_id = self._valid_bound_app_id(session)
                        if bound_app_id is not None:
                            info_fn = getattr(session, "session_info", None)
                            session_target = getattr(info_fn(), "target_app_id", None) if callable(info_fn) else None
                            if isinstance(session_target, str) and ANDROID_PACKAGE_NAME_PATTERN.fullmatch(session_target) is not None:
                                return session.element("id", resource_id, raw_id=False)
                            return session.element("id", f"{bound_app_id}:id/{resource_id}", raw_id=False)
                    return session.element("id", resource_id, raw_id=True)
                if set(populated) == {"accessibilityId"}:
                    return session.element("accessibility id", populated["accessibilityId"])
                if populated:
                    predicates: list[str] = []
                    if "resourceId" in populated:
                        res_id = populated["resourceId"]
                        app_id = (self.app_id or "").strip()
                        if ":" not in res_id and ANDROID_LOCAL_RESOURCE_ID_PATTERN.fullmatch(res_id) is not None and ANDROID_PACKAGE_NAME_PATTERN.fullmatch(app_id) is not None:
                            full_lit = _xpath_literal(f"{app_id}:id/{res_id}")
                            raw_lit = _xpath_literal(res_id)
                            predicates.append(f"(@resource-id={full_lit} or @resource-id={raw_lit})")
                        else:
                            predicates.append(f"@resource-id={_xpath_literal(res_id)}")
                    if "accessibilityId" in populated:
                        predicates.append(f"@content-desc={_xpath_literal(populated['accessibilityId'])}")
                    if "text" in populated:
                        predicates.append(f"@text={_xpath_literal(populated['text'])}")
                    if "className" in populated:
                        predicates.append(f"@class={_xpath_literal(populated['className'])}")
                    return session.element("xpath", f"//*[{' and '.join(predicates)}]")
            fallback = params.get("target")
            if isinstance(fallback, str) and fallback.strip():
                return session.element("xpath", f"//*[@text={_xpath_literal(fallback.strip())}]")
        except ValueError as exc:
            raise _LocatorConfigurationError("Invalid Android locator syntax or length.") from exc

        raise _LocatorConfigurationError("Android action requires a non-empty target or locator.")

    @staticmethod
    def _populated_locator_fields(locator: dict[str, object]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in ("resourceId", "accessibilityId", "text", "className"):
            value = locator.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value
        return result

    @staticmethod
    def _viewport_size(observation: Any) -> tuple[int, int] | None:
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
    ) -> list[tuple[int, int]] | None:
        if not isinstance(reference_screen_size, dict):
            return points
        ref_w = reference_screen_size.get("width")
        ref_h = reference_screen_size.get("height")
        if not _is_strict_int(ref_w) or not _is_strict_int(ref_h) or ref_w < 1 or ref_h < 1:
            return points
        observation = session.observe_tree(control=self._operation_control(15.0))
        viewport = self._viewport_size(observation)
        if viewport is None:
            return None
        width, height = viewport
        scaled: list[tuple[int, int]] = []
        for px, py in points:
            sx = self._clamp_coordinate(round(px * width / ref_w), width)
            sy = self._clamp_coordinate(round(py * height / ref_h), height)
            scaled.append((sx, sy))
        return scaled

    @staticmethod
    def _clamp_coordinate(value: int, limit: int) -> int:
        return max(0, min(value, max(limit - 1, 0)))

    @staticmethod
    def _swipe_points(direction: str, width: int, height: int) -> tuple[int, int, int, int]:
        normalized = direction.strip().lower()
        mid_x = width // 2
        mid_y = height // 2
        if normalized == "up":
            return mid_x, int(height * 0.75), mid_x, int(height * 0.25)
        if normalized == "down":
            return mid_x, int(height * 0.25), mid_x, int(height * 0.75)
        if normalized == "left":
            return int(width * 0.75), mid_y, int(width * 0.25), mid_y
        if normalized == "right":
            return int(width * 0.25), mid_y, int(width * 0.75), mid_y
        return mid_x, int(height * 0.75), mid_x, int(height * 0.25)

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
    def _has_locator(element: dict[str, object]) -> bool:
        return any(isinstance(element.get(field), str) and str(element[field]).strip() for field in ANDROID_LOCATOR_FIELDS)

    @staticmethod
    def _param_data(params: BaseModel | dict[str, object]) -> dict[str, object]:
        if isinstance(params, BaseModel):
            return params.model_dump(mode="json", exclude_none=True)
        return dict(params)

    def _run_sync(self, func: Callable[[], _T]) -> _T:
        if self._executor is None:
            return func()
        return self._executor.submit(func).result()

    def _ensure_executor(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-android-driver")

    def _shutdown_executor(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True)

    def _target_missing(self, params: dict[str, object]) -> dict[str, object]:
        return self._failed("target_resolution_error", "Target was not found.", metadata={"params": params})

    def _target_ambiguous(self) -> dict[str, object]:
        return self._failed(
            "target_resolution_error",
            "Target locator matched multiple Android elements; refine the locator.",
            metadata={"reason": "target_ambiguous"},
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
