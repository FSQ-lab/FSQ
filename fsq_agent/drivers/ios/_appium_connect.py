# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import logging
import os
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

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _ios_driver_tool
from fsq_agent.drivers._visual_inspection import _run_visual_inspection
from fsq_agent.models import (
    ConfigurationError,
    IOSAssertNotVisibleParams,
    IOSAssertStateParams,
    IOSAssertVisibleParams,
    IOSAssertWithAIParams,
    IOSClearTextParams,
    IOSDoubleTapOnParams,
    IOSHandleAlertParams,
    IOSHarnessSettings,
    IOSHideKeyboardParams,
    IOSInputTextParams,
    IOSInspectElementParams,
    IOSInspectWithAIParams,
    IOSLaunchAppParams,
    IOSLocator,
    IOSLongPressOnParams,
    IOSPressKeyParams,
    IOSScrollToParams,
    IOSStopAppParams,
    IOSSwipeParams,
    IOSTakeScreenshotParams,
    IOSTapAtParams,
    IOSTapOnParams,
    IOSUiSnapshotParams,
    ToolExecutionError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from pydantic import BaseModel

_T = TypeVar("_T")

DEFAULT_IOS_PAGE_SOURCE_MAX_DEPTH = 12
DEFAULT_IOS_ACTION_TIMEOUT_SECONDS = 15
DEFAULT_IOS_NEW_COMMAND_TIMEOUT_SECONDS = 900.0
DEFAULT_IOS_SNAPSHOT_SOURCE_PREVIEW_CHARS = 2000
DEFAULT_IOS_SNAPSHOT_TEXT_LIMIT_CHARS = 50
DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS = 120.0
DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS = 15.0
DEFAULT_SESSION_IDLE_LIVENESS_SECONDS = 45.0
DEFAULT_SWIPE_DURATION_MS = 300
DEFAULT_POLL_INTERVAL_SECONDS = 0.25
MAX_PORT_BIND_ATTEMPTS = 3

IOS_PHYSICAL_BUTTON_MAP = {
    "home": "home",
    "volumeup": "volumeUp",
    "volume_up": "volumeUp",
    "volumedown": "volumeDown",
    "volume_down": "volumeDown",
}

IOS_KEY_NAMES = {
    "return": "XCUIKeyboardKeyReturn",
    "enter": "XCUIKeyboardKeyEnter",
    "tab": "XCUIKeyboardKeyTab",
    "space": "XCUIKeyboardKeySpace",
    "escape": "XCUIKeyboardKeyEscape",
    "esc": "XCUIKeyboardKeyEscape",
    "delete": "XCUIKeyboardKeyDelete",
    "backspace": "XCUIKeyboardKeyDelete",
    "forwarddelete": "XCUIKeyboardKeyForwardDelete",
    "forward_delete": "XCUIKeyboardKeyForwardDelete",
    "up": "XCUIKeyboardKeyUpArrow",
    "uparrow": "XCUIKeyboardKeyUpArrow",
    "up_arrow": "XCUIKeyboardKeyUpArrow",
    "down": "XCUIKeyboardKeyDownArrow",
    "downarrow": "XCUIKeyboardKeyDownArrow",
    "down_arrow": "XCUIKeyboardKeyDownArrow",
    "left": "XCUIKeyboardKeyLeftArrow",
    "leftarrow": "XCUIKeyboardKeyLeftArrow",
    "left_arrow": "XCUIKeyboardKeyLeftArrow",
    "right": "XCUIKeyboardKeyRightArrow",
    "rightarrow": "XCUIKeyboardKeyRightArrow",
    "right_arrow": "XCUIKeyboardKeyRightArrow",
    "home_key": "XCUIKeyboardKeyHome",
    "end": "XCUIKeyboardKeyEnd",
    "pageup": "XCUIKeyboardKeyPageUp",
    "page_up": "XCUIKeyboardKeyPageUp",
    "pagedown": "XCUIKeyboardKeyPageDown",
    "page_down": "XCUIKeyboardKeyPageDown",
    "clear": "XCUIKeyboardKeyClear",
    "help": "XCUIKeyboardKeyHelp",
    **{f"f{index}": f"XCUIKeyboardKeyF{index}" for index in range(1, 20)},
}

DEFAULT_IOS_SNAPSHOT_ATTRIBUTE_KEYS = frozenset(
    {
        "name",
        "label",
        "value",
        "placeholderValue",
        "enabled",
        "selected",
        "visible",
        "accessible",
        "x",
        "y",
        "width",
        "height",
    }
)
IOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS = frozenset({"name", "label", "value", "placeholderValue"})
IOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS = frozenset({"name", "label", "value", "placeholderValue"})
IOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS = frozenset({"accessible"})
IOS_SNAPSHOT_STATE_DEFAULTS = {
    "enabled": "true",
    "selected": "false",
    "visible": "true",
}
IOS_INTERACTIVE_SHORT_TYPES = frozenset(
    {
        "Button",
        "CheckBox",
        "CollectionView",
        "ComboBox",
        "DatePicker",
        "DisclosureTriangle",
        "Link",
        "MenuButton",
        "MenuItem",
        "PageIndicator",
        "Picker",
        "PickerWheel",
        "PopUpButton",
        "RadioButton",
        "ScrollView",
        "SearchField",
        "SecureTextField",
        "SegmentedControl",
        "Slider",
        "Stepper",
        "Switch",
        "Tab",
        "TabBar",
        "Table",
        "TextField",
        "TextView",
        "Toggle",
        "WebView",
    }
)

IOS_EDITABLE_SHORT_TYPES = frozenset(
    {
        "TextField",
        "SecureTextField",
        "SearchField",
        "TextView",
        "ComboBox",
    }
)

IOS_STATE_ASSERTION_FIELDS = ("enabled", "selected", "focused", "accessible")

IOS_ELEMENT_INSPECT_MAP = {
    "type": "type",
    "name": "id",
    "label": "label",
    "value": "value",
    "enabled": "enabled",
    "displayed": "displayed",
    "selected": "selected",
    "accessible": "accessible",
    "focused": "focused",
    "bounds": "bounds",
}


def _is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def _predicate_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
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


def _resolve_ios_app_artifact_path(app_path: str | Path) -> Path:
    path = Path(app_path).expanduser()
    for parent in path.parents:
        if parent.name.lower().endswith(".app"):
            return parent
    return path


class AppiumConnectIOSDriver(AIAssertionBackendToolMixin):
    backend = "appium_connect_agent"

    def __init__(
        self,
        *,
        udid: str | None = None,
        bundle_id: str | None = None,
        app_path: str | Path | None = None,
        page_source_max_depth: int = DEFAULT_IOS_PAGE_SOURCE_MAX_DEPTH,
        action_timeout_seconds: int = DEFAULT_IOS_ACTION_TIMEOUT_SECONDS,
        new_command_timeout_seconds: float | None = DEFAULT_IOS_NEW_COMMAND_TIMEOUT_SECONDS,
        wda_launch_timeout_ms: int | None = None,
        xcode_org_id: str | None = None,
        xcode_signing_id: str | None = None,
        updated_wda_bundle_id: str | None = None,
        session: object | None = None,
    ) -> None:
        settings = IOSHarnessSettings(
            page_source_max_depth=page_source_max_depth,
            action_timeout_seconds=action_timeout_seconds,
            new_command_timeout_seconds=new_command_timeout_seconds,
            wda_launch_timeout_ms=wda_launch_timeout_ms,
        )
        settings.udid = udid
        settings.bundle_id = bundle_id
        settings.app_path = app_path
        settings.xcode_org_id = xcode_org_id
        settings.xcode_signing_id = xcode_signing_id
        settings.updated_wda_bundle_id = updated_wda_bundle_id

        self.udid = settings.udid
        self.bundle_id = settings.bundle_id
        self.app_path = str(settings.app_path) if settings.app_path is not None else None
        self.page_source_max_depth = settings.page_source_max_depth
        self.action_timeout_seconds = settings.action_timeout_seconds
        self.new_command_timeout_seconds = DEFAULT_IOS_NEW_COMMAND_TIMEOUT_SECONDS if settings.new_command_timeout_seconds is None else float(settings.new_command_timeout_seconds)
        self.wda_launch_timeout_ms = settings.wda_launch_timeout_ms
        self.xcode_org_id = settings.xcode_org_id
        self.xcode_signing_id = settings.xcode_signing_id
        self.updated_wda_bundle_id = settings.updated_wda_bundle_id

        self._executor: ThreadPoolExecutor | None = None
        self._runtime: Any | None = None
        self._managed_scope: AbstractContextManager[Any] | None = None
        self._connection: Any | None = None
        self._session: Any | None = session
        self._borrowed_session: bool = session is not None
        self._session_settled: bool = False
        self._scope_exit_attempted: bool = False
        self._cleanup_primary: BaseException | None = None
        self._last_session_activity_monotonic: float | None = time.monotonic() if session is not None else None
        self._resolved_device_target: str | None = None

    @_ios_driver_tool("launchApp", description="Start or activate an iOS application in the XCUITest session.")
    def launch_app(self, params: IOSLaunchAppParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            session = self._ensure_session_impl(params=params, explicit_launch=True)
            info = session.session_info() if hasattr(session, "session_info") else None
            effective_bundle = self._effective_bundle_id(params.bundle_id)
            effective_app = self._effective_app_path(params.app_path)
            raw_id = getattr(info, "appium_session_id", None) or getattr(info, "session_id", None) or getattr(info, "id", None)
            return self._passed(
                {
                    "bundle_id": effective_bundle,
                    "app_path": effective_app,
                    "session_id": str(raw_id) if raw_id else None,
                    "platform": getattr(info, "platform", "ios"),
                }
            )

        return self._run_tool(_op)

    @_ios_driver_tool("stopApp", description="Terminate an iOS application by bundle id.")
    def stop_app(self, params: IOSStopAppParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            bundle_id = self._effective_bundle_id(params.bundle_id)
            if bundle_id is None:
                return self._failed("configuration_error", "stopApp requires bundle_id or a configured bundle id.")
            session = self._ensure_session_impl()
            terminated = bool(session.device.terminate_app(bundle_id, control=self._ActionControl(self)))
            return self._passed({"bundle_id": bundle_id, "terminated": terminated})

        return self._run_tool(_op)

    @_ios_driver_tool("tapOn", description="Tap an iOS UI element resolved by semantic target or structured XCUITest locator.")
    def tap_on(self, params: IOSTapOnParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            element.click(wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))
            return self._passed()

        return self._run_tool(_op)

    @_ios_driver_tool("tapAt", description="Tap explicit iOS logical screen coordinates.")
    def tap_at(self, params: IOSTapAtParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            data = params.model_dump(mode="json", exclude_none=True)
            session = self._ensure_session_impl()
            cur_w, cur_h = self._display_size(session)
            resolved_x, resolved_y = self._resolve_point_coordinates_with_size(cur_w, cur_h, params.point, params.reference_screen_size)
            session.device.click_at(resolved_x, resolved_y, control=self._ActionControl(self))
            if data.get("reference_screen_size") is None:
                data["reference_screen_size"] = {"width": cur_w, "height": cur_h}
            return {**self._passed({"x": resolved_x, "y": resolved_y}), "metadata": {"safe_replay_params": data}}

        return self._run_tool(_op)

    @_ios_driver_tool("doubleTapOn", description="Double-tap an iOS UI element.")
    def double_tap_on(self, params: IOSDoubleTapOnParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            element.double_click(wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))
            return self._passed()

        return self._run_tool(_op)

    @_ios_driver_tool("longPressOn", description="Touch and hold an iOS UI element.")
    def long_press_on(self, params: IOSLongPressOnParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            duration_ms = int(params.duration_ms)
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            element.long_click(
                duration_ms=duration_ms,
                wait_timeout_seconds=self._wait_timeout(),
                control=self._operation_control(max(5.0, float(self.action_timeout_seconds) + (duration_ms / 1000.0) + 5.0)),
            )
            return self._passed({"duration_ms": duration_ms})

        return self._run_tool(_op)

    @_ios_driver_tool(
        "inputText",
        description="Enter text into an editable iOS element (TextField, SecureTextField, SearchField, TextView).",
        metadata={"credential_input": True},
    )
    def input_text(self, params: IOSInputTextParams) -> dict[str, object]:
        return self._run_tool(lambda: self._input_text_impl(params))

    def _input_text_impl(self, params: IOSInputTextParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is None:
            return self._target_missing()

        inspection = element.inspect(attributes=("type", "enabled", "displayed"), control=self._ActionControl(self))
        attrs = self._inspection_dict(inspection)
        type_entry = attrs.get("type")
        type_name = str(type_entry[1]).removeprefix("XCUIElementType") if type_entry and type_entry[0] == "value" and type_entry[1] else ""
        if type_name and type_name not in IOS_EDITABLE_SHORT_TYPES:
            return self._failed(
                "target_resolution_error",
                f"iOS inputText requires an editable text element, got {type_name}.",
                metadata={"element_type": type_name},
            )
        enabled_entry = attrs.get("enabled")
        if enabled_entry and enabled_entry[0] == "value" and enabled_entry[1] is False:
            return self._failed("target_resolution_error", "iOS inputText target is disabled.")

        if params.clear:
            element.clear(wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))
        if params.text == "":
            return self._passed()

        try:
            element.send_keys(params.text, wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            # Element.send_macos_keys is the public SDK per-keystroke input API for both Mac2 and XCUITest.
            element.send_macos_keys(list(params.text), wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))

        if type_name != "SecureTextField":
            try:
                after_inspection = element.inspect(attributes=("value", "text"), control=self._ActionControl(self))
                after_attrs = self._inspection_dict(after_inspection)
                observed_values = [str(entry[1]) for key in ("value", "text") if (entry := after_attrs.get(key)) is not None and entry[0] == "value" and isinstance(entry[1], str)]
                if observed_values and params.clear and not any(params.text in val or val == params.text for val in observed_values):
                    element.clear(wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))
                    element.send_macos_keys(list(params.text), wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
        return self._passed()

    @_ios_driver_tool("clearText", description="Clear text from an editable iOS element.")
    def clear_text(self, params: IOSClearTextParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            element.clear(wait_timeout_seconds=self._wait_timeout(), control=self._ActionControl(self))
            return self._passed()

        return self._run_tool(_op)

    @_ios_driver_tool("pressKey", description="Press a physical iOS button (`home`, `volume_up`, `volume_down`) or send a keyboard key to the focused responder.")
    def press_key(self, params: IOSPressKeyParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            session = self._ensure_session_impl()
            raw_key = params.key.strip()
            normalized = raw_key.lower().replace("-", "_").replace(" ", "_")
            if normalized in IOS_PHYSICAL_BUTTON_MAP:
                button_name = IOS_PHYSICAL_BUTTON_MAP[normalized]
                session.device.press_button(button_name, control=self._ActionControl(self))
                return self._passed({"button": button_name})

            mapped_key = IOS_KEY_NAMES.get(normalized)
            if mapped_key is None:
                if raw_key.startswith("XCUIKeyboardKey"):
                    mapped_key = raw_key
                elif len(params.key) == 1:
                    mapped_key = params.key
                else:
                    raise ConfigurationError(f"Unsupported iOS key: {params.key!r}.")
            session.device.send_keys([mapped_key], control=self._ActionControl(self))
            return self._passed({"key": mapped_key})

        return self._run_tool(_op)

    @_ios_driver_tool("hideKeyboard", description="Dismiss the on-screen iOS software keyboard if visible.")
    def hide_keyboard(self, params: IOSHideKeyboardParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            _ = params
            session = self._ensure_session_impl()
            keyboard = self._element_from_locator(IOSLocator(element_type="XCUIElementTypeKeyboard"))
            if keyboard is None:
                return self._passed({"hidden": False})
            hidden = bool(session.device.hide_keyboard(control=self._ActionControl(self)))
            return self._passed({"hidden": hidden})

        return self._run_tool(_op)

    @_ios_driver_tool("handleAlert", description="Accept or dismiss an active native iOS alert.")
    def handle_alert(self, params: IOSHandleAlertParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            session = self._ensure_session_impl()
            label = params.button_label.strip() if isinstance(params.button_label, str) and params.button_label.strip() else None
            if params.action == "accept":
                session.device.accept_alert(button_label=label, control=self._ActionControl(self))
            else:
                session.device.dismiss_alert(button_label=label, control=self._ActionControl(self))
            return self._passed({"action": params.action, "button_label": label})

        return self._run_tool(_op)

    @_ios_driver_tool("swipe", description="Perform an iOS full-screen directional swipe, explicit-point swipe, or element-scoped swipe.")
    def swipe(self, params: IOSSwipeParams) -> dict[str, object]:
        return self._run_tool(lambda: self._swipe_impl(params))

    def _swipe_impl(self, params: IOSSwipeParams) -> dict[str, object]:
        session = self._ensure_session_impl()
        duration_ms = int(params.duration) if params.duration is not None else DEFAULT_SWIPE_DURATION_MS
        control = self._operation_control(max(5.0, float(self.action_timeout_seconds) + (duration_ms / 1000.0) + 5.0))

        if params.start is not None and params.end is not None:
            data = params.model_dump(mode="json", exclude_none=True)
            cur_w, cur_h = self._display_size(session)
            sx, sy = self._resolve_point_coordinates_with_size(cur_w, cur_h, params.start, params.reference_screen_size)
            ex, ey = self._resolve_point_coordinates_with_size(cur_w, cur_h, params.end, params.reference_screen_size)
            session.device.swipe_between(sx, sy, ex, ey, duration_ms=duration_ms, control=control)
            if data.get("reference_screen_size") is None:
                data["reference_screen_size"] = {"width": cur_w, "height": cur_h}
            return {
                **self._passed({"start": {"x": sx, "y": sy}, "end": {"x": ex, "y": ey}, "duration": duration_ms}),
                "metadata": {"safe_replay_params": data},
            }
        if params.direction is None:
            raise ConfigurationError("iOS swipe requires direction or explicit start/end points.")

        if self._has_semantic_target(params):
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            if params.percent is not None and float(params.percent) < 1.0:
                inspection = element.inspect(attributes=("bounds",), control=self._ActionControl(self))
                attrs = self._inspection_dict(inspection)
                bounds_entry = attrs.get("bounds")
                bounds = bounds_entry[1] if bounds_entry and bounds_entry[0] == "value" else None
                if bounds is not None and all(hasattr(bounds, k) for k in ("x", "y", "width", "height")):
                    sx, sy, ex, ey = self._compute_element_swipe_points(
                        int(bounds.x),
                        int(bounds.y),
                        int(bounds.width),
                        int(bounds.height),
                        params.direction,
                        float(params.percent),
                    )
                    session.device.swipe_between(sx, sy, ex, ey, duration_ms=duration_ms, control=control)
                    return self._passed({"direction": params.direction, "percent": float(params.percent)})
            # Element.macos_swipe is the public SDK element-scoped directional swipe API for both Mac2 and XCUITest.
            element.macos_swipe(
                direction=params.direction,
                wait_timeout_seconds=self._wait_timeout(),
                control=control,
            )
            return self._passed({"direction": params.direction, "percent": float(params.percent or 1.0)})

        session.device.swipe(params.direction, duration_ms=duration_ms, control=control)
        return self._passed({"direction": params.direction, "duration": duration_ms})

    @staticmethod
    def _compute_element_swipe_points(
        x: int,
        y: int,
        width: int,
        height: int,
        direction: str,
        fraction: float,
    ) -> tuple[int, int, int, int]:
        horizontal = direction in {"left", "right"}
        first = x if horizontal else y
        span = max(2, width if horizontal else height)
        last = first + span - 1
        midpoint = (first + last) / 2.0
        half_span = max(1.0, (last - first) * fraction / 2.0)
        low, high = ceil(midpoint - half_span), floor(midpoint + half_span)
        if low >= high:
            low, high = first, last
        start_axis, end_axis = (high, low) if direction in {"up", "left"} else (low, high)
        if horizontal:
            mid_y = y + max(0, height // 2)
            return start_axis, mid_y, end_axis, mid_y
        mid_x = x + max(0, width // 2)
        return mid_x, start_axis, mid_x, end_axis

    @_ios_driver_tool("scrollTo", description="Scroll one uniquely resolved iOS scrollable container or element in direction by a bounded distance fraction.")
    def scroll_to(self, params: IOSScrollToParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            element.ios_scroll(
                params.direction,
                distance=float(params.distance),
                wait_timeout_seconds=self._wait_timeout(),
                control=self._ActionControl(self),
            )
            return self._passed({"direction": params.direction, "distance": float(params.distance)})

        return self._run_tool(_op)

    @_ios_driver_tool("takeScreenshot", description="Capture an iOS PNG screenshot.")
    def take_screenshot(self, params: IOSTakeScreenshotParams) -> bytes:
        _ = params
        return self.screenshot()

    @_ios_driver_tool("uiSnapshot", description="Read the current compact iOS XCUITest UI hierarchy and device clock context.")
    def ui_snapshot(self, params: IOSUiSnapshotParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            try:
                return self._raw_ui_snapshot_impl(params)
            except ConfigurationError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                raise ToolExecutionError(
                    "iOS UI snapshot is unavailable.",
                    context={"capture_diagnostics": self.safe_diagnostics(exc, stage="ui_snapshot")},
                ) from None

        return self._run_on_worker(_op)

    def _device_local_time(self, session: Any) -> dict[str, object]:
        try:
            clock = session.device.device_time(control=self._ActionControl(self))
            iso_local = getattr(clock, "iso_local", None) if not isinstance(clock, str) else clock
            if not isinstance(iso_local, str) or not iso_local:
                raise ValueError("iOS device clock iso_local is unavailable.")  # noqa: TRY301
            local = datetime.fromisoformat(iso_local)
            if local.utcoffset() is None:
                raise ValueError("iOS device clock UTC offset is unavailable.")  # noqa: TRY301
            return {
                "status": "available",
                "local_datetime": iso_local,
                "iso_weekday": local.isoweekday(),
                "source": "ios_device",
            }
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return {
                "status": "unavailable",
                "reason": "device_time_unavailable",
                "source": "ios_device",
                "diagnostics": self.safe_diagnostics(exc, stage="device_time"),
            }

    @_ios_driver_tool(
        "inspectElement",
        description="Inspect one uniquely resolved iOS target or non-empty locator. Returns complete unclipped safe attributes; value is withheld on XCUIElementTypeSecureTextField.",
    )
    def inspect_element(self, params: IOSInspectElementParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            needs_type_guard = "value" in params.attributes and "type" not in params.attributes
            sdk_attrs_list = [IOS_ELEMENT_INSPECT_MAP[attr] for attr in params.attributes]
            if needs_type_guard:
                sdk_attrs_list.append("type")
            sdk_attrs = tuple(dict.fromkeys(sdk_attrs_list))
            inspection = element.inspect(attributes=sdk_attrs, control=self._ActionControl(self))
            raw_map = {attr.name: attr for attr in getattr(inspection, "attributes", ())}
            type_obs = raw_map.get("type")
            is_secure = type_obs is not None and getattr(type_obs, "state", None) == "value" and str(getattr(type_obs, "value", "")).removeprefix("XCUIElementType") == "SecureTextField"
            projected: dict[str, dict[str, object]] = {}
            for requested in params.attributes:
                if requested == "value" and is_secure:
                    projected[requested] = {"state": "not_applicable", "value": None}
                    continue
                sdk_name = IOS_ELEMENT_INSPECT_MAP[requested]
                obs = raw_map.get(sdk_name)
                if obs is None or getattr(obs, "state", None) != "value":
                    state = str(getattr(obs, "state", "unknown")) if obs is not None else "unknown"
                    projected[requested] = {"state": state, "value": None}
                    continue
                val = obs.value
                if requested == "bounds" and val is not None and all(hasattr(val, k) for k in ("x", "y", "width", "height")):
                    val = {"x": int(val.x), "y": int(val.y), "width": int(val.width), "height": int(val.height)}
                projected[requested] = {"state": "value", "value": val}
            return {"attributes": projected}

        return self._run_tool(_op)

    @_ios_driver_tool("inspectWithAI", description="Read current pixel content with extract, or locate an inaccessible iOS target. Read-only; not an assertion.")
    def inspect_with_ai(self, params: IOSInspectWithAIParams) -> dict[str, object]:
        if not isinstance(params, IOSInspectWithAIParams):
            params = IOSInspectWithAIParams.model_validate(params)
        return _run_visual_inspection(self, params)

    @_ios_driver_tool("assertVisible", description="Assert that an iOS UI target is present and visible.")
    def assert_visible(self, params: IOSAssertVisibleParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            deadline = time.monotonic() + self._wait_timeout()
            while True:
                element = self._resolve_element_or_none(params)
                if element is not None:
                    inspection = element.inspect(attributes=("displayed",), control=self._ActionControl(self))
                    attrs = self._inspection_dict(inspection)
                    if attrs.get("displayed") == ("value", True):
                        return self._passed({"visible": True})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if params.optional:
                        return self._passed({"visible": False, "optional": True})
                    return self._failed("assertion_error", "Expected iOS element to be visible, but it was not visible.")
                time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

        return self._run_tool(_op)

    @_ios_driver_tool("assertNotVisible", description="Assert that an iOS UI target is absent or not visible.")
    def assert_not_visible(self, params: IOSAssertNotVisibleParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            deadline = time.monotonic() + self._wait_timeout()
            while True:
                ambiguous_exc: ConfigurationError | None = None
                try:
                    element = self._resolve_element_or_none(params)
                except ConfigurationError as exc:
                    if exc.context.get("resolution_reason") == "ambiguous":
                        ambiguous_exc = exc
                        element = None
                    else:
                        raise
                if ambiguous_exc is None:
                    if element is None:
                        return self._passed({"visible": False})
                    inspection = element.inspect(attributes=("displayed",), control=self._ActionControl(self))
                    attrs = self._inspection_dict(inspection)
                    if attrs.get("displayed") == ("value", False):
                        return self._passed({"visible": False})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if ambiguous_exc is not None:
                        raise ambiguous_exc
                    if params.optional:
                        return self._passed({"visible": True, "optional": True})
                    return self._failed("assertion_error", "Expected iOS element not to be visible, but it remains visible.")
                time.sleep(min(DEFAULT_POLL_INTERVAL_SECONDS, remaining))

        return self._run_tool(_op)

    @_ios_driver_tool("assertState", description="Assert existence, text, or state on one uniquely located iOS element.")
    def assert_state(self, params: IOSAssertStateParams) -> dict[str, object]:
        def _op() -> dict[str, object]:
            locator = IOSLocator(
                accessibility_id=params.element.accessibility_id,
                id=params.element.id,
                name=params.element.name,
                label=params.element.label,
                value=params.element.value,
                element_type=params.element.element_type,
                predicate=params.element.predicate,
                class_chain=params.element.class_chain,
                xpath=params.element.xpath,
            )
            element = self._element_from_locator(locator)
            if element is None:
                if params.optional:
                    return self._passed({"exists": False, "optional": True})
                return self._target_missing()

            expected_states = {field: val for field in IOS_STATE_ASSERTION_FIELDS if type(val := getattr(params.element, field, None)) is bool}
            if params.text is not None:
                inspection = element.inspect(attributes=("type", "label", "value", "id", "text"), control=self._ActionControl(self))
                attrs = self._inspection_dict(inspection)
                type_entry = attrs.get("type")
                is_secure = type_entry is not None and type_entry[0] == "value" and str(type_entry[1]).removeprefix("XCUIElementType") == "SecureTextField"
                candidates: list[str] = []
                for key in ("label", "value", "id", "text"):
                    if key == "value" and is_secure:
                        continue
                    entry = attrs.get(key)
                    if entry and entry[0] == "value" and isinstance(entry[1], str):
                        candidates.append(entry[1])
                if params.text.equals is not None:
                    if any(candidate == params.text.equals for candidate in candidates):
                        return self._passed({"text": params.text.equals})
                elif params.text.contains is not None and any(params.text.contains in candidate for candidate in candidates):
                    return self._passed({"contains": params.text.contains})
                if params.optional:
                    return self._passed({"matched": False, "optional": True})
                return self._failed(
                    "assertion_error",
                    "iOS text assertion failed.",
                    output={"observed": candidates[0] if candidates else None},
                )

            if expected_states:
                inspection = element.inspect(attributes=tuple(expected_states.keys()), control=self._ActionControl(self))
                attrs = self._inspection_dict(inspection)
                verified: dict[str, bool] = {}
                for field, expected in expected_states.items():
                    entry = attrs.get(field)
                    actual = entry[1] if entry and entry[0] == "value" else None
                    if actual is not expected:
                        if params.optional:
                            return self._passed({"matched": False, "optional": True, "field": field})
                        return self._failed(
                            "assertion_error",
                            "iOS element state assertion failed.",
                            output={"field": field, "expected": expected, "actual": actual},
                        )
                    verified[field] = expected
                return self._passed(verified)

            return self._passed({"exists": True})

        return self._run_tool(_op)

    @_ios_driver_tool("assertWithAI", description="Evaluate only an image-space iOS visual assertion.")
    def assert_with_ai(self, params: IOSAssertWithAIParams) -> dict[str, object]:
        if not isinstance(params, IOSAssertWithAIParams):
            params = IOSAssertWithAIParams.model_validate(params)
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        options = params if isinstance(params, dict) else {}
        check = options.get("cancellation_check")
        timeout = options.get("timeout_seconds", 20.0)
        cancellation = Event() if check is not None else None

        def check_on_caller() -> None:
            if check is None or cancellation is None:
                return
            try:
                if bool(check()):
                    cancellation.set()
            except BaseException:
                cancellation.set()
                raise

        def capture() -> bytes:
            session = self._ensure_session_impl()
            control = self._operation_control_with_cancellation(float(timeout), cancellation)
            return bytes(session.device.take_screenshot(control=control))

        check_on_caller()
        try:
            data = self._run_on_worker(capture, cancellation_check=check_on_caller, cancellation_event=cancellation)
        except BaseException as error:
            self._propagate_if_cancelled(error)
            if not isinstance(error, Exception):
                raise
            raise ToolExecutionError(
                "iOS screenshot is unavailable.",
                context={"capture_diagnostics": self.safe_diagnostics(error, stage="screenshot")},
            ) from None
        check_on_caller()
        return data

    def safe_diagnostics(self, error: BaseException | None = None, *, stage: str = "driver") -> dict[str, object]:
        diag: dict[str, object] = {"platform": "ios", "backend": self.backend, "stage": stage}
        if error is not None:
            diag["error_type"] = type(error).__name__
            code = getattr(error, "code", None)
            if isinstance(code, str) and code:
                diag["backend_code"] = code
            req_state = getattr(error, "request_state", None)
            if isinstance(req_state, str) and req_state:
                diag["request_state"] = req_state
        return diag

    def _safe_diagnostics(self, error: BaseException | None = None, *, stage: str = "driver") -> dict[str, object]:
        return self.safe_diagnostics(error, stage=stage)

    def _ensure_session(self) -> Any:
        return self._ensure_session_impl()

    def ensure_session(self) -> Any:
        return self._run_on_worker(self._ensure_session_impl)

    def context(self) -> dict[str, object]:
        if self._session is None:
            return {
                "session_id": None,
                "screen_size": None,
                "capabilities": {},
                "metadata": {
                    "backend": self.backend,
                    "udid_configured": self.udid is not None,
                    "bundle_id_configured": self.bundle_id is not None,
                    "app_path_configured": self.app_path is not None,
                },
            }
        return self._run_on_worker(self._context_impl)

    def _raw_ui_snapshot_impl(self, options: IOSUiSnapshotParams) -> dict[str, object]:
        session = self._ensure_session_impl()
        snapshot_timeout = max(20.0, float(self.action_timeout_seconds) + 10.0)
        observation = session.observe_tree(control=self._operation_control(snapshot_timeout))
        max_depth = int(options.max_depth or self.page_source_max_depth)
        compacted = self._simplify_tree_observation(observation, max_depth=max_depth)
        viewport = getattr(observation, "viewport", None)
        width_obs = getattr(viewport, "width", None)
        height_obs = getattr(viewport, "height", None)
        if getattr(width_obs, "state", None) == "value" and getattr(height_obs, "state", None) == "value" and _is_strict_int(width_obs.value) and _is_strict_int(height_obs.value):
            compacted["viewport"] = {"width": int(width_obs.value), "height": int(height_obs.value)}
        compacted["device_local_time"] = self._device_local_time(session)
        return compacted

    def _context_impl(self) -> dict[str, object]:
        session = self._session
        if session is None:
            return {
                "session_id": None,
                "screen_size": None,
                "capabilities": {},
                "metadata": {
                    "backend": self.backend,
                    "udid_configured": self.udid is not None,
                    "bundle_id_configured": self.bundle_id is not None,
                    "app_path_configured": self.app_path is not None,
                },
            }
        info_fn = getattr(session, "session_info", None)
        session_id: str | None = None
        if callable(info_fn):
            info = info_fn()
            state = getattr(info, "state", "active")
            if state not in {"closed", "disposed"}:
                raw_id = getattr(info, "appium_session_id", None) or getattr(info, "session_id", None) or getattr(info, "id", None)
                session_id = str(raw_id) if raw_id else None
        else:
            raw_id = getattr(session, "session_id", None)
            session_id = raw_id if isinstance(raw_id, str) else None

        screen_size: tuple[int, int] | None = None
        if session_id is not None:
            try:
                screen_size = self._display_size(session)
            except Exception:  # noqa: BLE001
                screen_size = None
        return {
            "session_id": session_id,
            "screen_size": screen_size,
            "capabilities": {},
            "metadata": {
                "backend": self.backend,
                "target_kind": self._resolved_device_target,
                "udid_configured": self.udid is not None,
                "bundle_id_configured": self.bundle_id is not None,
                "app_path_configured": self.app_path is not None,
            },
        }

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
                raise BaseExceptionGroup("iOS session and owner-thread shutdown failed.", [primary_error, shutdown_error]) from None
            raise
        primary_error = self._cleanup_primary
        self._cleanup_primary = None
        if primary_error is not None:
            raise primary_error

    def _close_owned_resources(self) -> None:
        primary_error: BaseException | None = self._cleanup_primary
        managed_settled = False
        try:
            session = self._session
            if session is not None and not self._session_settled:
                info_fn = getattr(session, "session_info", None)
                state = getattr(info_fn(), "state", "active") if callable(info_fn) else "active"
                if state == "active":
                    try:
                        session.quit(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                    except BaseException as exc:
                        if getattr(exc, "code", None) in {"session_closed", "session_disposed"}:
                            self._session_settled = True
                        else:
                            primary_error = self._cleanup_primary = exc
                            state = getattr(info_fn(), "state", "uncertain") if callable(info_fn) else "uncertain"
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
                            raise ToolExecutionError("Owned iOS terminal disposal did not complete.", context={"state": state})  # noqa: TRY301
                        managed_settled = True
                        if getattr(primary_error, "code", None) == "session_quit_unknown":
                            primary_error = None
                            self._cleanup_primary = None
                    elif state == "disposed":
                        managed_settled = True
                    elif state != "closed":
                        raise ToolExecutionError("Owned iOS session is not available for terminal cleanup.", context={"state": state})  # noqa: TRY301
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
                raise BaseExceptionGroup("iOS session and owned-resource cleanup failed.", [primary_error, cleanup_error]) from None
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

    def _ensure_session_impl(self, params: IOSLaunchAppParams | None = None, *, explicit_launch: bool = False) -> Any:
        launch_params = params or IOSLaunchAppParams()
        if self._session is not None and self._session_is_active(self._session):
            self._ensure_idle_session_alive(self._session)
            if not explicit_launch:
                self._last_session_activity_monotonic = time.monotonic()
                return self._session
            if launch_params.new_session:
                if self._borrowed_session:
                    raise ConfigurationError("Cannot recreate a borrowed iOS XCUITest session with new_session=true.")
                self._close_owned_resources()
                return self._create_session(launch_params)
            if launch_params.app_path is not None:
                raise ConfigurationError("app_path requires new_session=true when an iOS XCUITest session already exists.")
            bundle_id = self._effective_bundle_id(launch_params.bundle_id)
            if bundle_id is None:
                return self._session
            if launch_params.arguments is not None:
                self._session.device.launch_app(
                    bundle_id=bundle_id,
                    arguments=launch_params.arguments,
                    control=self._ActionControl(self),
                )
            else:
                self._session.device.activate_app(bundle_id, control=self._ActionControl(self))
            self._last_session_activity_monotonic = time.monotonic()
            return self._session

        if self._borrowed_session:
            raise ConfigurationError("Borrowed iOS XCUITest session is no longer active.")
        if self._managed_scope is not None or self._runtime is not None:
            self._close_owned_resources()
        return self._create_session(launch_params)

    def _ensure_idle_session_alive(self, session: Any) -> None:
        if self._borrowed_session:
            return
        now = time.monotonic()
        last = self._last_session_activity_monotonic
        if last is not None and (now - last) < DEFAULT_SESSION_IDLE_LIVENESS_SECONDS:
            return
        ensure_fn = getattr(session, "ensure_alive", None)
        if callable(ensure_fn):
            ensure_fn(control=self._ActionControl(self))
        self._last_session_activity_monotonic = time.monotonic()

    @staticmethod
    def _session_is_active(session: Any) -> bool:
        info_fn = getattr(session, "session_info", None)
        if not callable(info_fn):
            return True
        state = getattr(info_fn(), "state", "active")
        return state == "active"

    def _select_target_device(self, list_ios_devices_fn: Callable[..., Sequence[Any]], operation_control_cls: type[Any]) -> tuple[str, str]:
        devices = list(list_ios_devices_fn(control=operation_control_cls(timeout_seconds=15.0)))
        if self.udid:
            for item in devices:
                item_udid = getattr(item, "id", None) or getattr(item, "udid", None)
                if item_udid == self.udid:
                    state = getattr(item, "state", "")
                    target = str(getattr(item, "kind", None) or getattr(item, "target", "simulator"))
                    if state not in {"booted", "connected"}:
                        raise ConfigurationError(f"Configured iOS target device is not ready (state={state}).")
                    return self.udid, target
            return self.udid, "unknown"

        ready = [item for item in devices if getattr(item, "state", None) in {"booted", "connected"}]
        if not ready:
            raise ConfigurationError(
                "No booted iOS Simulator or connected real iOS device was discovered.",
                context={"action": "Boot an iOS Simulator or connect an unlocked iOS device, or specify ios.udid explicitly."},
            )
        if len(ready) > 1:
            raise ConfigurationError(
                f"Multiple ready iOS devices discovered ({len(ready)}); specify ios.udid explicitly.",
                context={"ready_device_count": len(ready)},
            )
        chosen = ready[0]
        chosen_udid = getattr(chosen, "id", None) or getattr(chosen, "udid", None)
        chosen_kind = getattr(chosen, "kind", None) or getattr(chosen, "target", "simulator")
        return str(chosen_udid), str(chosen_kind)

    def _create_session(self, params: IOSLaunchAppParams) -> Any:
        try:
            from appium_connect_agent.automation import create_xcuitest_session, list_ios_devices
            from appium_connect_agent.contracts import LocalSettings
            from appium_connect_agent.runtime import OperationControl, Runtime, ServiceLifecycleError
        except ImportError as exc:
            raise ConfigurationError(
                "appium-connect-agent is required for AppiumConnectIOSDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc

        selected_udid, target_kind = self._select_target_device(list_ios_devices, OperationControl)
        bundle_id = self._effective_bundle_id(params.bundle_id)
        raw_app_path = self._effective_app_path(params.app_path)
        resolved_app_path = _resolve_ios_app_artifact_path(raw_app_path) if raw_app_path else None
        if resolved_app_path is not None:
            suffix = resolved_app_path.name.lower()
            if target_kind == "simulator" and suffix.endswith(".ipa"):
                raise ConfigurationError("iOS Simulator requires a .app bundle directory, not a .ipa archive.")
            if target_kind == "device" and suffix.endswith(".app"):
                raise ConfigurationError("Real iOS devices require a signed .ipa package or installed bundle_id, not a Simulator .app directory.")

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

        has_target_app = bundle_id is not None or resolved_app_path is not None
        create_timeout_seconds = max(
            DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS,
            ((self.wda_launch_timeout_ms / 1000.0) + 30.0) if self.wda_launch_timeout_ms else DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS,
        )
        try:
            session = create_xcuitest_session(
                connection,
                selected_udid,
                bundle_id=bundle_id,
                app_path=resolved_app_path,
                arguments=params.arguments,
                auto_launch=has_target_app,
                no_reset=True,
                xcode_org_id=self.xcode_org_id,
                xcode_signing_id=self.xcode_signing_id,
                updated_wda_bundle_id=self.updated_wda_bundle_id,
                wda_launch_timeout_ms=self.wda_launch_timeout_ms,
                new_command_timeout_seconds=float(self.new_command_timeout_seconds),
                control=OperationControl(timeout_seconds=create_timeout_seconds),
            )
        except BaseException as exc:
            try:
                scope.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException as cleanup_exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "Managed Appium scope cleanup failed after XCUITest session creation error (%s)",
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
        self._resolved_device_target = target_kind
        self._last_session_activity_monotonic = time.monotonic()
        return session

    def _effective_bundle_id(self, bundle_id: str | None) -> str | None:
        if isinstance(bundle_id, str) and bundle_id.strip():
            return bundle_id.strip()
        return self.bundle_id

    def _effective_app_path(self, app_path: str | None) -> str | None:
        if isinstance(app_path, str) and app_path.strip():
            return app_path.strip()
        return self.app_path

    def _display_size(self, session: Any) -> tuple[int, int]:
        raw = session.device.display_size(control=self._ActionControl(self))
        if isinstance(raw, tuple) and len(raw) == 2:
            width, height = raw
        else:
            width = getattr(raw, "width", None)
            height = getattr(raw, "height", None)
        if not (_is_strict_int(width) and _is_strict_int(height) and width > 0 and height > 0):
            raise ConfigurationError("iOS display_size returned invalid dimensions.")
        return int(width), int(height)

    def _resolve_point_coordinates(
        self,
        session: Any,
        point: Any,
        reference_screen_size: Any = None,
    ) -> tuple[int, int]:
        cur_w, cur_h = self._display_size(session)
        return self._resolve_point_coordinates_with_size(cur_w, cur_h, point, reference_screen_size)

    @staticmethod
    def _resolve_point_coordinates_with_size(
        cur_w: int,
        cur_h: int,
        point: Any,
        reference_screen_size: Any = None,
    ) -> tuple[int, int]:
        if reference_screen_size is None:
            x_val, y_val = int(point.x), int(point.y)
            if not (0 <= x_val < cur_w and 0 <= y_val < cur_h):
                raise ConfigurationError("iOS coordinates must be inside the current logical viewport.")
            return x_val, y_val
        ref_w, ref_h = int(reference_screen_size.width), int(reference_screen_size.height)
        if (ref_w > ref_h) != (cur_w > cur_h) and ref_w != ref_h and cur_w != cur_h:
            raise ConfigurationError(f"iOS reference_screen_size orientation ({ref_w}x{ref_h}) mismatches current viewport ({cur_w}x{cur_h}).")
        scaled_x = round((int(point.x) * cur_w) / ref_w)
        scaled_y = round((int(point.y) * cur_h) / ref_h)
        clamped_x = max(0, min(cur_w - 1, scaled_x))
        clamped_y = max(0, min(cur_h - 1, scaled_y))
        return clamped_x, clamped_y

    @staticmethod
    def _has_semantic_target(params: BaseModel) -> bool:
        target = getattr(params, "target", None)
        if isinstance(target, str) and target.strip():
            return True
        locator = getattr(params, "locator", None)
        return isinstance(locator, IOSLocator) and locator.has_value()

    def _resolve_element_or_none(self, params: BaseModel) -> Any | None:
        locator = getattr(params, "locator", None)
        if isinstance(locator, IOSLocator) and locator.has_value():
            return self._element_from_locator(locator)
        target = getattr(params, "target", None)
        if isinstance(target, str) and target.strip():
            return self._resolve_bare_target(target.strip())
        return None

    def _resolve_bare_target(self, text: str) -> Any | None:
        found = self._element_from_locator(IOSLocator(label=text))
        if found is not None:
            return found
        lit = _predicate_literal(text)
        fallback_pred = f"name == {lit} OR value == {lit}"
        return self._element_from_locator(IOSLocator(predicate=fallback_pred))

    def _element_from_locator(self, locator: IOSLocator) -> Any | None:
        session = self._ensure_session_impl()
        if not locator.has_value():
            return None

        by, query_value, extra_predicate = self._build_session_locator_query(locator)
        try:
            element = session.element(by, query_value, predicate=extra_predicate)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        if locator.xpath and locator.predicate:
            return self._intersect_structural_and_predicate(session, element, locator)
        if locator.class_chain and locator.xpath is None and locator.predicate and "`" in locator.predicate:
            return self._intersect_structural_and_predicate(session, element, locator)

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
                        "iOS locator matches multiple elements; refine the locator.",
                        context={
                            "resolution_reason": "ambiguous",
                            "match_count": max(len(candidates), 2),
                            "candidates": candidates[:5],
                        },
                    ) from None
            self._raise_resolution_error(exc)
        return element

    def _intersect_structural_and_predicate(self, session: Any, base_element: Any, locator: IOSLocator) -> Any | None:
        if locator.predicate is None:
            return base_element
        if not self._probe_single_or_ambiguous(session, base_element, locator):
            return None
        try:
            scoped_element = session.element("-ios predicate string", locator.predicate, parent=base_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        try:
            scoped_inspection = scoped_element.inspect(
                attributes=("type", "id", "label", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "TargetError":
                code = getattr(exc, "code", None)
                if code == "target_not_found":
                    return None
                if code == "target_ambiguous":
                    return self._confirm_self_matches_predicate_among_subtree(session, base_element, locator.predicate)
            self._raise_resolution_error(exc)
        if self._element_signature(base_element) != self._signature_from_inspection(scoped_inspection):
            return None
        return base_element

    def _confirm_self_matches_predicate_among_subtree(self, session: Any, base_element: Any, predicate: str) -> Any | None:
        base_sig = self._element_signature(base_element)
        clauses = [f"({predicate})"]
        elem_type, elem_id, elem_label, elem_value, bounds_tuple = base_sig
        if isinstance(elem_type, str) and elem_type:
            clauses.append(f"type == {_predicate_literal(elem_type)}")
        if isinstance(elem_id, str) and elem_id:
            clauses.append(f"name == {_predicate_literal(elem_id)}")
        if isinstance(elem_label, str) and elem_label:
            clauses.append(f"label == {_predicate_literal(elem_label)}")
        if isinstance(elem_value, str) and elem_value:
            clauses.append(f"value == {_predicate_literal(elem_value)}")
        if isinstance(bounds_tuple, tuple) and len(bounds_tuple) == 4 and all(type(coord) is int for coord in bounds_tuple):
            bx, by, bw, bh = bounds_tuple
            clauses.extend((f"rect.x == {bx}", f"rect.y == {by}", f"rect.width == {bw}", f"rect.height == {bh}"))
        try:
            narrowed = session.element("-ios predicate string", " AND ".join(clauses), parent=base_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        try:
            narrowed_inspection = narrowed.inspect(
                attributes=("type", "id", "label", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "TargetError" and getattr(exc, "code", None) in {"target_not_found", "target_ambiguous"}:
                return None
            self._raise_resolution_error(exc)
        return base_element if base_sig == self._signature_from_inspection(narrowed_inspection) else None

    def _probe_single_or_ambiguous(self, session: Any, element: Any, locator: IOSLocator) -> bool:
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
                        "iOS locator matches multiple elements; refine the locator.",
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
                attributes=("type", "id", "label", "value", "bounds"),
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
        return (attrs.get("type"), attrs.get("id"), attrs.get("label"), attrs.get("value"), bounds_tuple)

    def _build_session_locator_query(self, locator: IOSLocator) -> tuple[str, str, str | None]:
        if locator.xpath:
            constraints = self._xpath_constraints(locator)
            selector = f"({locator.xpath})" if constraints else locator.xpath
            if constraints:
                selector += "[" + " and ".join(constraints) + "]"
            return "xpath", selector, None
        if locator.class_chain:
            clauses = self._locator_predicate_clauses(locator)
            if not clauses:
                return "-ios class chain", locator.class_chain, None
            predicate = " AND ".join(f"({clause})" for clause in clauses) if len(clauses) > 1 else clauses[0]
            if "`" in predicate:
                non_pred_clauses = self._locator_predicate_clauses(locator, skip_predicate=True)
                chain = locator.class_chain.rstrip()
                if non_pred_clauses:
                    non_pred = " AND ".join(f"({c})" for c in non_pred_clauses) if len(non_pred_clauses) > 1 else non_pred_clauses[0]
                    if "`" not in non_pred:
                        chain = f"{chain}[`{non_pred}`]"
                return "-ios class chain", chain, None
            return "-ios class chain", f"{locator.class_chain.rstrip()}[`{predicate}`]", None

        populated = [
            bool(val)
            for val in (
                locator.accessibility_id,
                locator.id,
                locator.name,
                locator.label,
                locator.value,
                locator.element_type,
                locator.predicate,
            )
        ]
        if sum(populated) == 1:
            if locator.accessibility_id:
                return "accessibility id", locator.accessibility_id, None
            if locator.id:
                return "id", locator.id, None
            if locator.name:
                return "name", locator.name, None
            if locator.element_type:
                normalized_cls = locator.element_type if locator.element_type.startswith("XCUIElementType") else f"XCUIElementType{locator.element_type}"
                return "class name", normalized_cls, None
            if locator.predicate:
                return "-ios predicate string", locator.predicate, None

        if locator.accessibility_id:
            extra = self._locator_predicate_clauses(locator, skip_accessibility_id=True)
            extra_pred = " AND ".join(f"({c})" for c in extra) if len(extra) > 1 else (extra[0] if extra else None)
            return "accessibility id", locator.accessibility_id, extra_pred

        clauses = self._locator_predicate_clauses(locator)
        predicate = " AND ".join(f"({clause})" for clause in clauses) if len(clauses) > 1 else clauses[0]
        return "-ios predicate string", predicate, None

    @staticmethod
    def _xpath_constraints(locator: IOSLocator) -> list[str]:
        constraints: list[str] = [f"@name={_xpath_literal(raw)}" for raw in (locator.accessibility_id, locator.id, locator.name) if raw]
        if locator.label:
            constraints.append(f"@label={_xpath_literal(locator.label)}")
        if locator.value:
            constraints.append(f"@value={_xpath_literal(locator.value)}")
        if locator.element_type:
            expected = locator.element_type if locator.element_type.startswith("XCUIElementType") else f"XCUIElementType{locator.element_type}"
            constraints.append(f"name()={_xpath_literal(expected)}")
        return constraints

    @staticmethod
    def _locator_predicate_clauses(
        locator: IOSLocator,
        *,
        skip_accessibility_id: bool = False,
        skip_predicate: bool = False,
    ) -> list[str]:
        clauses: list[str] = []
        if locator.accessibility_id and not skip_accessibility_id:
            clauses.append(f"name == {_predicate_literal(locator.accessibility_id)}")
        if locator.id:
            clauses.append(f"name == {_predicate_literal(locator.id)}")
        if locator.name:
            clauses.append(f"name == {_predicate_literal(locator.name)}")
        if locator.label:
            clauses.append(f"label == {_predicate_literal(locator.label)}")
        if locator.value:
            clauses.append(f"value == {_predicate_literal(locator.value)}")
        if locator.element_type:
            cls = locator.element_type
            prefixed = cls if cls.startswith("XCUIElementType") else f"XCUIElementType{cls}"
            clauses.append(f"type == {_predicate_literal(prefixed)}")
        if locator.predicate and not skip_predicate:
            clauses.append(locator.predicate)
        return clauses

    def _collect_ambiguous_candidates(self, session: Any, locator: IOSLocator) -> list[dict[str, object]]:
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
                matches.append(
                    {
                        "type": xml_node.tag,
                        "name": xml_node.attrib.get("name"),
                        "label": xml_node.attrib.get("label"),
                        "value": xml_node.attrib.get("value"),
                    }
                )
            children = getattr(node, "children", ())
            if children:
                stack.extend(reversed(children))
        return matches

    @staticmethod
    def _xml_node_matches_locator(node: ElementTree.Element, locator: IOSLocator) -> bool:
        attrs = node.attrib
        for id_like in (locator.accessibility_id, locator.id, locator.name):
            if id_like and attrs.get("name") != id_like:
                return False
        if locator.label and attrs.get("label") != locator.label:
            return False
        if locator.value and attrs.get("value") != locator.value:
            return False
        if locator.element_type:
            prefixed = locator.element_type if locator.element_type.startswith("XCUIElementType") else f"XCUIElementType{locator.element_type}"
            if node.tag not in {locator.element_type, prefixed}:
                return False
        return True

    @staticmethod
    def _inspection_dict(inspection: Any) -> dict[str, tuple[str, object]]:
        return {attr.name: (attr.state, attr.value) for attr in getattr(inspection, "attributes", ())}

    def _simplify_tree_observation(self, observation: Any, *, max_depth: int) -> dict[str, object]:
        roots = getattr(observation, "roots", ())
        if not roots:
            return {
                "xml": "<XCUIElementTypeApplication />",
                "format": "xml",
                "root": None,
                "source_length": 0,
                "node_count": 0,
                "coverage": {
                    "status": "unknown",
                    "reason": "empty_tree",
                    "source_nodes": 0,
                    "retained_nodes": 0,
                    "retained_characters": 0,
                },
                "compaction": {
                    "applied": True,
                    "text_attribute_limit": DEFAULT_IOS_SNAPSHOT_TEXT_LIMIT_CHARS,
                    "max_depth": max_depth,
                    "layout_nodes_removed": 0,
                    "clipped_attributes": 0,
                },
            }
        if len(roots) == 1:
            xml_root = self._tree_node_to_xml_element(roots[0])
        else:
            xml_root = ElementTree.Element("XCUIElementTypeApplication")
            for child_root in roots:
                xml_root.append(self._tree_node_to_xml_element(child_root))
        serialized = ElementTree.tostring(xml_root, encoding="unicode")
        source_nodes = self._element_count(xml_root)
        try:
            compacted_root = self._element_snapshot(xml_root, depth=1, max_depth=max_depth)
            compact_xml_el = self._snapshot_dict_to_xml(compacted_root)
            compact_xml = ElementTree.tostring(compact_xml_el, encoding="unicode", short_empty_elements=True)
            retained_nodes = self._element_count(compact_xml_el)
            layout_nodes_removed = max(0, source_nodes - retained_nodes)
            clipped_attributes = self._count_clipped_in_snapshot(compacted_root)
            complete_scope = bool(getattr(observation, "complete_for_requested_scope", True))
            upstream_coverage = str(getattr(observation, "coverage", "requested_scope"))
            is_partial = (not complete_scope) or layout_nodes_removed > 0 or clipped_attributes > 0
            return {
                "xml": compact_xml,
                "format": "xml",
                "source_length": len(serialized),
                "node_count": source_nodes,
                "root": compacted_root,
                "coverage": {
                    "status": "partial" if is_partial else "unknown",
                    "reason": "semantic_compaction" if complete_scope else f"upstream_{upstream_coverage}",
                    "source_nodes": source_nodes,
                    "retained_nodes": retained_nodes,
                    "retained_characters": len(compact_xml),
                },
                "compaction": {
                    "applied": True,
                    "text_attribute_limit": DEFAULT_IOS_SNAPSHOT_TEXT_LIMIT_CHARS,
                    "max_depth": max_depth,
                    "layout_nodes_removed": layout_nodes_removed,
                    "clipped_attributes": clipped_attributes,
                },
            }
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            preview = serialized[:DEFAULT_IOS_SNAPSHOT_SOURCE_PREVIEW_CHARS]
            return {
                "xml": preview,
                "format": "unparsed_xml",
                "source_preview": preview,
                "source_length": len(serialized),
                "node_count": source_nodes,
                "truncated": len(serialized) > len(preview),
                "parse_error": type(exc).__name__,
                "coverage": {
                    "status": "unknown",
                    "reason": "uncompacted_tree_fallback",
                    "source_nodes": source_nodes,
                    "retained_nodes": source_nodes,
                    "retained_characters": len(preview),
                },
                "compaction": {
                    "applied": False,
                    "text_attribute_limit": None,
                    "max_depth": max_depth,
                    "layout_nodes_removed": 0,
                    "clipped_attributes": 0,
                },
            }

    def _count_clipped_in_snapshot(self, snapshot: dict[str, object]) -> int:
        truncated = snapshot.get("truncated_attributes")
        count = len(truncated) if isinstance(truncated, list) else 0
        if snapshot.get("text_truncated") is True:
            count += 1
        children = snapshot.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    count += self._count_clipped_in_snapshot(child)
        return count

    def _snapshot_dict_to_xml(self, snapshot: dict[str, object]) -> ElementTree.Element:
        tag = str(snapshot.get("type") or "XCUIElementTypeAny")
        raw_attrs = snapshot.get("attributes")
        attribs = {str(k): str(v) for k, v in raw_attrs.items()} if isinstance(raw_attrs, dict) else {}
        el = ElementTree.Element(tag, attribs)
        text = snapshot.get("text")
        if isinstance(text, str) and text:
            el.text = text
        children = snapshot.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    el.append(self._snapshot_dict_to_xml(child))
        return el

    def _tree_node_to_xml_element(self, node: Any) -> ElementTree.Element:
        type_obs = getattr(node, "type", None)
        tag = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) and type_obs.value else "XCUIElementTypeAny"
        is_secure = tag.removeprefix("XCUIElementType") == "SecureTextField"
        attribs: dict[str, str] = {}
        for attr_field, xml_name in (
            ("id", "name"),
            ("label", "label"),
            ("value", "value"),
            ("placeholder_value", "placeholderValue"),
        ):
            if attr_field == "value" and is_secure:
                continue
            obs = getattr(node, attr_field, None)
            if getattr(obs, "state", None) == "value" and isinstance(obs.value, str) and obs.value:
                attribs[xml_name] = obs.value

        for bool_field in ("enabled", "selected", "accessible"):
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

        xml_el = ElementTree.Element(tag, attribs)
        raw_text = getattr(node, "raw_text", None)
        if isinstance(raw_text, str) and raw_text and not is_secure:
            xml_el.text = raw_text
        for child in getattr(node, "children", ()):
            xml_el.append(self._tree_node_to_xml_element(child))
        return xml_el

    def _element_snapshot(self, element: ElementTree.Element, *, depth: int, max_depth: int) -> dict[str, object]:
        snapshot = self._compact_element_snapshots(element, preserve_node=True)[0]
        self._bound_snapshot_depth(snapshot, depth=depth, max_depth=max_depth)
        return snapshot

    def _compact_element_snapshots(
        self,
        element: ElementTree.Element,
        *,
        preserve_node: bool = False,
    ) -> list[dict[str, object]]:
        children = [child_snapshot for child in element for child_snapshot in self._compact_element_snapshots(child)]
        attributes = self._snapshot_attributes(element.attrib)
        text = (element.text or "").strip()[:DEFAULT_IOS_SNAPSHOT_TEXT_LIMIT_CHARS]
        interactive = self._xml_name(element.tag).removeprefix("XCUIElementType") in IOS_INTERACTIVE_SHORT_TYPES
        if not preserve_node and not interactive and not self._has_snapshot_signal(attributes, text=text):
            return children

        snapshot: dict[str, object] = {"type": self._xml_name(element.tag)}
        if attributes:
            snapshot["attributes"] = attributes
        clipped = [key for key, value in element.attrib.items() if self._xml_name(key) in IOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS and len(value.strip()) > DEFAULT_IOS_SNAPSHOT_TEXT_LIMIT_CHARS]
        if clipped:
            snapshot["truncated_attributes"] = clipped
        if len((element.text or "").strip()) > DEFAULT_IOS_SNAPSHOT_TEXT_LIMIT_CHARS:
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

    def _snapshot_attributes(self, attributes: dict[str, str]) -> dict[str, object]:
        selected: dict[str, object] = {}
        for key, raw_value in attributes.items():
            normalized_key = self._xml_name(key)
            value = str(raw_value).strip()
            if not value or normalized_key not in DEFAULT_IOS_SNAPSHOT_ATTRIBUTE_KEYS:
                continue
            default_state = IOS_SNAPSHOT_STATE_DEFAULTS.get(normalized_key)
            if default_state is not None and value.lower() == default_state:
                continue
            if normalized_key in IOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS:
                value = value[:DEFAULT_IOS_SNAPSHOT_TEXT_LIMIT_CHARS]
            selected[normalized_key] = value
        return selected

    def _has_snapshot_signal(self, attributes: dict[str, object], *, text: str) -> bool:
        non_default_state = any(key in IOS_SNAPSHOT_STATE_DEFAULTS for key in attributes)
        identity_or_text = bool(text) or any(key in IOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS for key in attributes)
        if self._is_zero_size(attributes) or str(attributes.get("visible", "")).lower() == "false":
            return identity_or_text or non_default_state
        return identity_or_text or non_default_state or any(key in IOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS for key in attributes)

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
        raise ConfigurationError(f"iOS element lookup failed: {reason}.", context={"resolution_reason": reason}) from None

    @staticmethod
    def _propagate_if_cancelled(exc: BaseException) -> None:
        if type(exc).__name__ == "OperationCancelledError" or getattr(exc, "code", None) == "operation_cancelled":
            import asyncio

            raise asyncio.CancelledError from exc

    def _wait_timeout(self) -> float:
        return max(0.5, float(self.action_timeout_seconds))

    @staticmethod
    def _ActionControl(driver: AppiumConnectIOSDriver) -> Any:
        return driver._operation_control(max(5.0, float(driver.action_timeout_seconds) + 5.0))

    @staticmethod
    def _operation_control(timeout_seconds: float) -> Any:
        try:
            from appium_connect_agent.runtime import OperationControl
        except ImportError:
            return None
        return OperationControl(timeout_seconds=timeout_seconds)

    @staticmethod
    def _operation_control_with_cancellation(timeout_seconds: float, cancellation: Event | None) -> Any:
        try:
            from appium_connect_agent.runtime import OperationControl
        except ImportError:
            return None
        return OperationControl(timeout_seconds=timeout_seconds, cancellation=cancellation)

    def _run_tool(self, func: Callable[[], dict[str, object]]) -> dict[str, object]:
        try:
            return self._run_on_worker(func)
        except ConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            code = getattr(exc, "code", None)
            cls_name = type(exc).__name__
            if cls_name == "TargetError":
                if code == "target_not_found":
                    return self._target_missing()
                if code == "target_ambiguous":
                    return self._failed(
                        "target_resolution_error",
                        "iOS target locator matched multiple elements; refine the locator.",
                        metadata={"resolution_reason": "ambiguous", **self.safe_diagnostics(exc)},
                    )
                return self._failed(
                    "target_resolution_error",
                    "iOS target is not ready for interaction.",
                    metadata={"resolution_reason": str(code or "not_ready"), **self.safe_diagnostics(exc)},
                )
            if cls_name == "OperationTimeoutError" or code == "operation_timeout":
                return self._failed("timeout_error", "iOS operation timed out.", metadata=self.safe_diagnostics(exc))
            if cls_name in {"InvalidRequestError", "ElementValidationError"}:
                return self._failed("configuration_error", "Invalid iOS backend request.", metadata=self.safe_diagnostics(exc))
            return self._failed("action_error", "iOS backend operation failed.", metadata=self.safe_diagnostics(exc))

    def _run_on_worker(
        self,
        func: Callable[[], _T],
        *,
        cancellation_check: Callable[[], None] | None = None,
        cancellation_event: Event | None = None,
    ) -> _T:
        if self._borrowed_session and cancellation_check is None:
            return func()
        self._ensure_executor()
        try:
            return self._run_sync(func, cancellation_check=cancellation_check, cancellation_event=cancellation_event)
        except BaseException:
            if self._session is None and self._managed_scope is None:
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
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-ios-driver")

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
