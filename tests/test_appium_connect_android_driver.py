# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import builtins
import threading
from asyncio import CancelledError
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from fsq_agent.core import AndroidDriverInterface, CapabilityRegistry, StepRunner
from fsq_agent.core.evidence import ArtifactStore
from fsq_agent.core.harness._android import AndroidHarness
from fsq_agent.core.harness._appium_connect_android_driver import AppiumConnectAndroidDriver
from fsq_agent.core.harness._driver_tools import _discover_driver_capability_definitions
from fsq_agent.models import AndroidAssertWithAIParams, ConfigurationError, ExecutableStep, HarnessContext, ReplayPolicy


class TargetError(RuntimeError):
    def __init__(self, code: str, message: str = "target failure") -> None:
        super().__init__(message)
        self.code = code


class CommandError(RuntimeError):
    def __init__(self, code: str = "command_failed", message: str = "backend failure") -> None:
        super().__init__(message)
        self.code = code


def _obs(value: object, state: str = "value") -> Any:
    return SimpleNamespace(state=state, value=value if state == "value" else None)


def _attr_obs(name: str, value: object, state: str = "value") -> Any:
    return SimpleNamespace(name=name, state=state, value=value if state == "value" else None)


def _bounds(left: int, top: int, right: int, bottom: int) -> Any:
    return SimpleNamespace(x=left, y=top, width=right - left, height=bottom - top)


def _tree_node(
    *,
    tag: str = "android.widget.FrameLayout",
    resource_id: str | None = None,
    text: str | None = None,
    desc: str | None = None,
    package: str | None = "com.example.app",
    enabled: bool = True,
    displayed: bool = True,
    focused: bool = False,
    checked: bool = False,
    selected: bool = False,
    bounds: tuple[int, int, int, int] | None = (0, 0, 1080, 2400),
    window_id: int | None = None,
    children: tuple[Any, ...] = (),
) -> Any:
    bounds_obj = _bounds(*bounds) if bounds is not None else None
    return SimpleNamespace(
        type=_obs(tag),
        id=_obs(resource_id, "value" if resource_id is not None else "missing"),
        text=_obs(text, "value" if text is not None else "missing"),
        desc=_obs(desc, "value" if desc is not None else "missing"),
        package=_obs(package, "value" if package is not None else "missing"),
        enabled=_obs(enabled),
        focused=_obs(focused),
        checked=_obs(checked),
        selected=_obs(selected),
        bounds=_obs(bounds_obj, "value" if bounds_obj is not None else "missing"),
        visibility="visible" if displayed else "not_visible",
        viewport_relation="inside" if displayed else "outside",
        window_id=_obs(window_id, "value" if window_id is not None else "missing"),
        children=children,
    )


def _tree_observation(
    roots: tuple[Any, ...] = (),
    *,
    width: int | None = 1080,
    height: int | None = 2400,
    rotation: int | None = 0,
    coverage: str = "requested_scope",
    complete_for_requested_scope: bool = True,
) -> Any:
    return SimpleNamespace(
        coverage=coverage,
        complete_for_requested_scope=complete_for_requested_scope,
        upstream_completeness="unknown",
        viewport=SimpleNamespace(
            width=_obs(width, "value" if width is not None else "unknown"),
            height=_obs(height, "value" if height is not None else "unknown"),
            rotation=_obs(rotation, "value" if rotation is not None else "unknown"),
        ),
        roots=roots,
    )


class FakeElement:
    def __init__(self, session: FakeSession, by: str, value: str, *, raw_id: bool = False) -> None:
        self.session = session
        self.by = by
        self.value = value
        self.raw_id = raw_id

    def inspect(
        self,
        *,
        attributes: tuple[str, ...] = (),
        wait_timeout_seconds: float | None = None,
        control: object | None = None,
    ) -> Any:
        del wait_timeout_seconds, control
        self.session._record_thread()
        self.session.calls.append(("inspect", self.by, self.value, self.raw_id, attributes))
        written = any(call[0] == "replace_element_value" for call in self.session.calls)
        error_code = self.session.post_write_inspect_code if written else self.session.inspect_code
        if error_code is not None:
            if error_code == "exception":
                raise RuntimeError("submitted-private observed-private private backend payload")
            if error_code in {"target_not_found", "target_ambiguous"}:
                raise TargetError(error_code, "submitted-private private backend payload")
            raise CommandError(error_code, "submitted-private private backend payload")
        if not self.session.exists:
            raise TargetError("target_not_found")
        state_map = dict(self.session.element_attrs)
        state_map["text"] = ("value", self.session.text)
        if written:
            state_map.update(self.session.post_write_attrs)
        attr_list = tuple(_attr_obs(name, val, state) for name, (state, val) in state_map.items() if not attributes or name in attributes)
        return SimpleNamespace(attributes=attr_list)

    def click(
        self,
        *,
        wait_timeout_seconds: float = 15.0,
        control: object | None = None,
    ) -> None:
        del wait_timeout_seconds, control
        self.session._record_thread()
        self.session.calls.append(("click", self.by, self.value, self.raw_id))
        if self.session.click_code is not None:
            if self.session.click_code in {"target_not_found", "target_ambiguous"}:
                raise TargetError(self.session.click_code)
            raise CommandError(self.session.click_code)
        if not self.session.exists:
            raise TargetError("target_not_found")

    def long_click(
        self,
        *,
        duration_ms: int = 500,
        wait_timeout_seconds: float = 15.0,
        control: object | None = None,
    ) -> None:
        del wait_timeout_seconds, control
        self.session._record_thread()
        self.session.calls.append(("long_click", self.by, self.value, self.raw_id, duration_ms))
        if not self.session.exists:
            raise TargetError("target_not_found")

    def replace_element_value(
        self,
        text: str,
        *,
        wait_timeout_seconds: float = 15.0,
        control: object | None = None,
    ) -> None:
        del wait_timeout_seconds, control
        self.session._record_thread()
        self.session.calls.append(("replace_element_value", self.by, self.value, self.raw_id, text))
        if self.session.write_mode == "error":
            raise RuntimeError("submitted-private observed-private private backend payload")
        if self.session.write_mode == "code_error":
            raise CommandError("command_failed", "submitted-private private backend payload")
        if self.session.write_mode == "accept":
            self.session.text = text


class FakeDevice:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def activate_app(self, app_id: str, *, control: object | None = None) -> None:
        del control
        self.session._record_thread()
        self.session.calls.append(("activate_app", app_id))
        if self.session.activate_error is not None:
            raise self.session.activate_error

    def terminate_app(self, app_id: str, *, control: object | None = None) -> bool:
        del control
        self.session._record_thread()
        self.session.calls.append(("terminate_app", app_id))
        return True

    def back(self, *, control: object | None = None) -> None:
        del control
        self.session._record_thread()
        self.session.calls.append(("back",))

    def press_keycode(self, keycode: int, *, metastate: int | None = None, flags: int | None = None, control: object | None = None) -> None:
        del metastate, flags, control
        self.session._record_thread()
        self.session.calls.append(("press_keycode", keycode))

    def tap_at(self, x: int, y: int, *, control: object | None = None) -> None:
        del control
        self.session._record_thread()
        self.session.calls.append(("tap_at", x, y))

    def swipe_between(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        duration_ms: int = 200,
        control: object | None = None,
    ) -> None:
        del control
        self.session._record_thread()
        self.session.calls.append(("swipe_between", start_x, start_y, end_x, end_y, duration_ms))

    def take_screenshot(self, *, control: object | None = None) -> bytes:
        del control
        self.session._record_thread()
        self.session.calls.append(("take_screenshot",))
        if self.session.screenshot_error is not None:
            raise self.session.screenshot_error
        return self.session.screenshot_bytes


class FakeSession:
    def __init__(
        self,
        *,
        exists: bool = True,
        text: str = "Loaded",
        observation: Any | None = None,
        activate_error: BaseException | None = None,
        target_app_id: str | None = "com.example.app",
    ) -> None:
        self.exists = exists
        self.text = text
        self.target_app_id = target_app_id
        self.observation = observation or _tree_observation(
            roots=(_tree_node(package="com.example.app"),),
        )
        self.screenshot_bytes = b"\x89PNG\r\n\x1a\nfake-png"
        self.screenshot_error: BaseException | None = None
        self.activate_error: BaseException | None = activate_error
        self.write_mode = "accept"
        self.inspect_code: str | None = None
        self.post_write_inspect_code: str | None = None
        self.click_code: str | None = None
        self.quit_error: BaseException | None = None
        self.calls: list[tuple[Any, ...]] = []
        self.thread_ids: set[int] = set()
        self.device = FakeDevice(self)
        self.element_attrs: dict[str, tuple[str, object]] = {
            "type": ("value", "android.widget.EditText"),
            "id": ("value", "com.example.app:id/input"),
            "package": ("value", "com.example.app"),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "password": ("value", False),
            "bounds": ("value", _bounds(0, 0, 100, 50)),
            "checked": ("value", False),
            "selected": ("value", False),
            "clickable": ("value", True),
            "focused": ("value", False),
        }
        self.post_write_attrs: dict[str, tuple[str, object]] = {}

    def _record_thread(self) -> None:
        self.thread_ids.add(threading.get_ident())

    def session_info(self) -> Any:
        self._record_thread()
        return SimpleNamespace(
            appium_session_id="appium-session-123",
            id="local-session-1",
            state="active",
            target_app_id=self.target_app_id,
        )

    def observe_tree(self, *, max_depth: int | None = None, max_nodes: int | None = None, control: object | None = None) -> Any:
        del max_depth, max_nodes, control
        self._record_thread()
        self.calls.append(("observe_tree",))
        return self.observation

    def element(self, by: str, value: str, *, raw_id: bool = False) -> FakeElement:
        self._record_thread()
        if not value or not value.strip():
            raise ValueError("empty locator")
        self.calls.append(("element", by, value, raw_id))
        return FakeElement(self, by, value, raw_id=raw_id)

    def quit(self, *, control: object | None = None) -> None:
        del control
        self._record_thread()
        self.calls.append(("quit",))
        if self.quit_error is not None:
            raise self.quit_error


def test_driver_lazy_construction_does_not_connect_or_start_threads() -> None:
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="emulator-5554")

    assert isinstance(driver, AndroidDriverInterface)
    assert driver.backend == "appium_connect_agent"
    assert driver._session is None
    assert driver._executor is None
    driver.close()


def test_driver_context_launch_kill_screenshot_and_ui_snapshot() -> None:
    root = _tree_node(
        tag="android.widget.FrameLayout",
        package="com.android.systemui",
        children=(
            _tree_node(
                tag="android.widget.TextView",
                resource_id="com.example.app:id/title",
                text="Welcome",
                package="com.example.app",
                focused=True,
                bounds=(10, 20, 300, 80),
            ),
        ),
    )
    session = FakeSession(observation=_tree_observation(roots=(root,), width=1080, height=2400))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        context = driver.context()

        assert context == {
            "session_id": "appium-session-123",
            "screen_size": (1080, 2400),
            "metadata": {
                "backend": "appium_connect_agent",
                "current_package": "com.example.app",
            },
        }
        assert AndroidHarness(driver=driver).get_context() == HarnessContext(
            platform="android",
            session_id="appium-session-123",
            current_activity=None,
            screen_size=(1080, 2400),
            metadata={
                "backend": "appium_connect_agent",
                "current_package": "com.example.app",
            },
        )
        assert driver.launch_app({}) == {"status": "passed", "output": {"app_id": "com.example.app"}}
        assert driver.kill_app({}) == {"status": "passed", "output": {"app_id": "com.example.app"}}
        assert driver.screenshot() == b"\x89PNG\r\n\x1a\nfake-png"

        snapshot = driver.ui_snapshot({})
        assert set(snapshot) == {"xml", "coverage", "compaction"}
        assert snapshot["coverage"]["status"] == "partial"
        assert snapshot["coverage"]["reason"] == "semantic_compaction"
        assert snapshot["coverage"]["source_nodes"] == 2
        assert snapshot["coverage"]["retained_nodes"] == 1
        assert snapshot["compaction"] == {
            "applied": True,
            "text_attribute_limit": 50,
            "layout_nodes_removed": 1,
            "clipped_attributes": 0,
        }
        assert 'resource-id="com.example.app:id/title"' in str(snapshot["xml"])
        assert 'text="Welcome"' in str(snapshot["xml"])
        assert len(session.thread_ids) == 1
    finally:
        driver.close()
    assert ("quit",) not in session.calls


def test_owned_managed_lifecycle_closes_session_before_exiting_managed_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    import appium_connect_agent.automation as aca_automation
    import appium_connect_agent.runtime as aca_runtime

    events: list[tuple[Any, ...]] = []
    session = FakeSession()
    orig_quit = session.quit

    def tracked_quit(*, control: object | None = None) -> None:
        events.append(("session.quit", threading.get_ident()))
        orig_quit(control=control)

    session.quit = tracked_quit  # type: ignore[method-assign]

    attempts = 0

    class FakeScope:
        def __init__(self, port: int) -> None:
            self.port = port

        def __enter__(self) -> SimpleNamespace:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise aca_runtime.ServiceLifecycleError("service_port_in_use", "op-1", SimpleNamespace())  # type: ignore[arg-type]
            events.append(("scope.enter", self.port, threading.get_ident()))
            return SimpleNamespace(port=self.port)

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            events.append(("scope.exit", exc_type, threading.get_ident()))
            return False

    class FakeRuntime:
        def __init__(self, _settings: Any) -> None:
            self._managed_cleanup = None

        def start_managed(self, *, host: str, port: int, base_path: str, control: Any = None) -> FakeScope:
            del host, base_path, control
            return FakeScope(port)

    ports = iter([41001, 41002])
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect._allocate_loopback_port", lambda: next(ports))
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect._resolve_managed_local_settings", lambda _cls: SimpleNamespace())
    monkeypatch.setattr(aca_runtime, "Runtime", FakeRuntime)

    def fake_create_session(connection: Any, device_id: str, **_kwargs: Any) -> FakeSession:
        events.append(("create_session", connection.port, device_id, threading.get_ident()))
        return session

    monkeypatch.setattr(aca_automation, "create_session", fake_create_session)

    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="device-01")
    assert driver.launch_app({})["status"] == "passed"
    driver.close()
    # Second close is idempotent.
    driver.close()

    event_names = [item[0] for item in events]
    assert event_names == ["scope.enter", "create_session", "session.quit", "scope.exit"]
    assert events[0][1] == 41002
    assert events[1][1] == 41002
    assert events[1][2] == "device-01"
    worker_tids = {item[-1] for item in events}
    assert len(worker_tids) == 1


def test_locator_translation_handles_standard_id_short_id_raw_id_accessibility_compound_and_injection_safe_xpath() -> None:
    session = FakeSession(text="hello")
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        assert driver.tap_on({"locator": {"resourceId": "com.example.app:id/login"}})["status"] == "passed"
        assert driver.tap_on({"locator": {"resourceId": "digit_7"}})["status"] == "passed"
        assert driver.tap_on({"locator": {"resourceId": "nonstandard/id-1"}})["status"] == "passed"
        assert driver.tap_on({"locator": {"accessibilityId": "Open menu"}})["status"] == "passed"
        assert driver.tap_on({"locator": {"resourceId": "digit_7", "text": "Say 'hi' and \"bye\""}})["status"] == "passed"
        assert driver.tap_on({"target": "Sign 'In'"})["status"] == "passed"
        assert driver.long_press_on({"locator": {"xpath": "//android.widget.Button[@text='Hold']"}})["status"] == "passed"
    finally:
        driver.close()

    element_calls = [call for call in session.calls if call[0] == "element"]
    assert element_calls == [
        ("element", "id", "com.example.app:id/login", False),
        ("element", "id", "digit_7", False),
        ("element", "id", "nonstandard/id-1", True),
        ("element", "accessibility id", "Open menu", False),
        (
            "element",
            "xpath",
            """//*[(@resource-id='com.example.app:id/digit_7' or @resource-id='digit_7') and @text=concat('Say ', "'", 'hi', "'", ' and "bye"')]""",
            False,
        ),
        ("element", "xpath", """//*[@text="Sign 'In'"]""", False),
        ("element", "xpath", "//android.widget.Button[@text='Hold']", False),
    ]


def test_short_id_restoration_in_ui_snapshot_session_fallback_and_input_text_bounds_binding() -> None:
    tree = _tree_observation(
        roots=(
            _tree_node(
                tag="android.widget.FrameLayout",
                package="com.google.android.calculator",
                children=(
                    _tree_node(
                        tag="android.widget.Button",
                        resource_id="digit_7",
                        text="7",
                        package="com.google.android.calculator",
                        bounds=(10, 20, 110, 120),
                    ),
                    _tree_node(
                        tag="android.widget.Button",
                        resource_id="android:id/button1",
                        text="OK",
                        package="com.google.android.calculator",
                        bounds=(120, 20, 220, 120),
                    ),
                    _tree_node(
                        tag="android.widget.Button",
                        resource_id="custom/raw-id",
                        text="Raw",
                        package="com.google.android.calculator",
                        bounds=(230, 20, 330, 120),
                    ),
                ),
            ),
        ),
    )
    # Session without target_app_id falls back to driver.app_id when normalizing short resourceId.
    session = FakeSession(observation=tree, target_app_id=None)
    session.element_attrs["id"] = ("value", "com.google.android.calculator:id/formula")
    session.element_attrs["package"] = ("value", "com.google.android.calculator")
    session.post_write_attrs["bounds"] = ("value", _bounds(0, 0, 240, 80))
    driver = AppiumConnectAndroidDriver(app_id="com.google.android.calculator", session=session)
    try:
        snap = driver.ui_snapshot({})
        assert driver.tap_on({"locator": {"resourceId": "digit_7"}})["status"] == "passed"
        assert driver.tap_on({"locator": {"resourceId": "digit_7", "text": "7"}})["status"] == "passed"
        input_res = driver.input_text({"text": "42", "locator": {"resourceId": "formula"}})
    finally:
        driver.close()

    root = ElementTree.fromstring(str(snap["xml"]))  # noqa: S314
    assert [child.attrib.get("resource-id") for child in root] == [
        "com.google.android.calculator:id/digit_7",
        "android:id/button1",
        "custom/raw-id",
    ]
    assert input_res == {"status": "passed", "output": None}
    element_calls = [call for call in session.calls if call[0] == "element"]
    assert element_calls == [
        ("element", "id", "com.google.android.calculator:id/digit_7", False),
        (
            "element",
            "xpath",
            "//*[(@resource-id='com.google.android.calculator:id/digit_7' or @resource-id='digit_7') and @text='7']",
            False,
        ),
        ("element", "id", "com.google.android.calculator:id/formula", False),
    ]

    # Without a bound app_id, bare local id falls back to raw_id=True.
    unbound_session = FakeSession(target_app_id=None)
    unbound_driver = AppiumConnectAndroidDriver(app_id=None, session=unbound_session)
    try:
        assert unbound_driver.tap_on({"locator": {"resourceId": "digit_7"}})["status"] == "passed"
    finally:
        unbound_driver.close()
    assert [call for call in unbound_session.calls if call[0] == "element"] == [
        ("element", "id", "digit_7", True),
    ]


def test_tap_on_and_assert_surface_ambiguous_targets_distinctly() -> None:
    session = FakeSession()
    session.inspect_code = "target_ambiguous"
    session.click_code = "target_ambiguous"
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        tap_result = driver.tap_on({"locator": {"text": "Duplicate"}})
        vis_result = driver.assert_visible({"locator": {"text": "Duplicate"}})
        state_result = driver.assert_state({"element": {"text": "Duplicate"}, "text": {"equals": "Duplicate"}})
    finally:
        driver.close()

    for res in (tap_result, vis_result, state_result):
        assert res["status"] == "failed"
        assert res["failure_category"] == "target_resolution_error"
        assert res["metadata"] == {"reason": "target_ambiguous"}


def test_press_key_maps_back_named_keys_keycodes_and_rejects_unknown_keys() -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        assert driver.press_key({"key": "Back"}) == {"status": "passed", "output": {"key": "Back"}}
        assert driver.press_key({"key": "Enter"}) == {"status": "passed", "output": {"key": "Enter"}}
        assert driver.press_key({"key": "KEYCODE_HOME"}) == {"status": "passed", "output": {"key": "KEYCODE_HOME"}}
        assert driver.press_key({"key": "66"}) == {"status": "passed", "output": {"key": "66"}}
        bad = driver.press_key({"key": "not_a_real_key"})
    finally:
        driver.close()

    assert bad["status"] == "failed"
    assert bad["failure_category"] == "configuration_error"
    device_calls = [call for call in session.calls if call[0] in {"back", "press_keycode"}]
    assert device_calls == [
        ("back",),
        ("press_keycode", 66),
        ("press_keycode", 3),
        ("press_keycode", 66),
    ]


def test_point_tap_and_swipe_support_scaling_directional_swipe_and_reject_malformed_inputs() -> None:
    session = FakeSession(observation=_tree_observation(width=540, height=1200))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        tap_plain = driver.tap_at({"point": {"x": 320, "y": 640}})
        tap_scaled = driver.tap_at({"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}})
        swipe_points = driver.swipe(
            {
                "start": {"x": 800, "y": 1900},
                "end": {"x": 200, "y": 1900},
                "reference_screen_size": {"width": 1080, "height": 2400},
                "duration": 1000,
            }
        )
        swipe_dir = driver.swipe({"direction": "up", "duration": 250})
        bad_tap = driver.tap_at({"point": {"x": 320}})
        bad_swipe = driver.swipe({"start": {"x": 800}, "end": {"x": 200, "y": 1900}})
        bad_duration = driver.swipe({"direction": "up", "duration": 0})
    finally:
        driver.close()

    assert tap_plain == {"status": "passed", "output": {"point": {"x": 320, "y": 640}}}
    assert tap_scaled == {"status": "passed", "output": {"point": {"x": 50, "y": 100}}}
    assert swipe_points == {"status": "passed", "output": {"start": {"x": 400, "y": 950}, "end": {"x": 100, "y": 950}}}
    assert swipe_dir == {"status": "passed", "output": {"direction": "up"}}
    assert bad_tap["failure_category"] == "configuration_error"
    assert bad_swipe["failure_category"] == "configuration_error"
    assert bad_duration["failure_category"] == "configuration_error"

    pointer_calls = [call for call in session.calls if call[0] in {"tap_at", "swipe_between"}]
    assert pointer_calls == [
        ("tap_at", 320, 640),
        ("tap_at", 50, 100),
        ("swipe_between", 400, 950, 100, 950, 1000),
        ("swipe_between", 270, 900, 270, 300, 250),
    ]


def test_ui_snapshot_compacts_wrapper_nodes_truncates_text_and_reports_coverage() -> None:
    long_text = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    tree = _tree_observation(
        roots=(
            _tree_node(
                tag="android.widget.FrameLayout",
                package="com.example",
                children=(
                    _tree_node(
                        tag="android.widget.LinearLayout",
                        package="com.example",
                        children=(
                            _tree_node(
                                tag="android.widget.TextView",
                                resource_id="com.example:id/title",
                                text=long_text,
                                package="com.example",
                                bounds=(0, 0, 100, 50),
                            ),
                            _tree_node(
                                tag="android.view.View",
                                text="",
                                package="com.example",
                                bounds=(0, 50, 100, 100),
                            ),
                            _tree_node(
                                tag="android.widget.ImageButton",
                                text="",
                                desc="Open menu",
                                package="com.example",
                                bounds=(100, 0, 160, 60),
                            ),
                            _tree_node(
                                tag="android.widget.Button",
                                text="",
                                resource_id="com.example:id/disabled",
                                enabled=False,
                                displayed=False,
                                package="com.example",
                                bounds=(0, 100, 100, 160),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        coverage="depth_truncated",
        complete_for_requested_scope=False,
    )
    session = FakeSession(observation=tree)
    driver = AppiumConnectAndroidDriver(app_id="com.example", session=session)
    try:
        result = driver.ui_snapshot({})
    finally:
        driver.close()

    assert result["coverage"]["status"] == "partial"
    assert result["coverage"]["reason"] == "upstream_depth_truncated"
    assert result["coverage"]["source_nodes"] == 6
    assert result["coverage"]["retained_nodes"] == 3
    assert result["compaction"] == {
        "applied": True,
        "text_attribute_limit": 50,
        "layout_nodes_removed": 3,
        "clipped_attributes": 1,
    }
    # Trusted static test fixture output.
    root = ElementTree.fromstring(str(result["xml"]))  # noqa: S314
    nodes = list(root)
    assert [node.attrib for node in nodes] == [
        {
            "class": "android.widget.TextView",
            "package": "com.example",
            "resource-id": "com.example:id/title",
            "text": long_text[:50],
            "bounds": "[0,0][100,50]",
        },
        {
            "class": "android.widget.ImageButton",
            "package": "com.example",
            "content-desc": "Open menu",
            "bounds": "[100,0][160,60]",
        },
        {
            "class": "android.widget.Button",
            "package": "com.example",
            "resource-id": "com.example:id/disabled",
            "enabled": "false",
            "displayed": "false",
            "bounds": "[0,100][100,160]",
        },
    ]


@pytest.mark.parametrize("text", ["", "hello", "中文 café 👩🏽‍💻\n第二行", " existing whitespace "])
@pytest.mark.parametrize(
    "class_name",
    [
        "android.widget.EditText",
        "android.widget.AutoCompleteTextView",
        "android.widget.MultiAutoCompleteTextView",
    ],
)
def test_input_text_inspects_editability_focuses_replaces_and_verifies_exact_readback(text: str, class_name: str) -> None:
    session = FakeSession(text="previous text")
    session.element_attrs["type"] = ("value", class_name)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"text": text, "locator": {"resourceId": "com.example.app:id/input"}})
    finally:
        driver.close()

    assert result == {"status": "passed", "output": None}
    assert session.text == text
    op_names = [call[0] for call in session.calls]
    assert op_names == ["element", "inspect", "click", "replace_element_value", "inspect"]


@pytest.mark.parametrize(
    "attr_overrides",
    [
        {"type": ("missing", None)},
        {"type": ("value", "android.widget.FrameLayout"), "clickable": ("value", True), "focused": ("value", True)},
        {"type": ("value", "android.inputmethodservice.KeyboardView")},
        {"type": ("value", "android.widget.EditText"), "enabled": ("value", False)},
        {"type": ("value", "android.widget.EditText"), "displayed": ("value", False)},
    ],
)
def test_input_text_rejects_non_editable_or_disabled_targets_without_side_effects(
    attr_overrides: dict[str, tuple[str, object]],
) -> None:
    session = FakeSession()
    session.element_attrs.update(attr_overrides)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"text": "submitted-private", "locator": {"resourceId": "input"}})
    finally:
        driver.close()

    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"] == {"reason": "invalid_input_target"}
    assert not any(call[0] in {"click", "replace_element_value"} for call in session.calls)
    assert "submitted-private" not in str(result)


@pytest.mark.parametrize(
    "password_attr",
    [("value", True), ("missing", None), ("unknown", None)],
)
def test_input_text_requires_explicit_unmasked_password_false(password_attr: tuple[str, object]) -> None:
    session = FakeSession()
    session.element_attrs["password"] = password_attr
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"text": "••••", "locator": {"resourceId": "input"}})
    finally:
        driver.close()

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert not any(call[0] in {"click", "replace_element_value"} for call in session.calls)


@pytest.mark.parametrize("mask", ["••••", "****", "●●●●"])
def test_input_text_cannot_pass_by_matching_mask_characters(mask: str) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"text": mask, "locator": {"resourceId": "com.example.app:id/input"}})
    finally:
        driver.close()

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert mask not in str(result)


@pytest.mark.parametrize(
    "locator_params",
    [
        {"target": "original"},
        {"locator": {"text": "original"}},
        {"locator": {"text": "original", "resourceId": "com.example.app:id/input"}},
        {"locator": {"xpath": "//android.widget.EditText[@text='original']"}},
        {"locator": {"xpath": "//*[@*[.='original']]"}},
    ],
)
def test_input_text_does_not_reresolve_mutated_text_locators(locator_params: dict[str, object]) -> None:
    session = FakeSession(text="original")
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"text": "replacement", **locator_params})
    finally:
        driver.close()

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert session.text == "replacement"
    assert sum(call[0] == "inspect" for call in session.calls) == 1


@pytest.mark.parametrize(
    ("write_mode", "post_attrs", "post_code", "reason"),
    [
        ("noop", {}, None, "entry_mismatch"),
        ("accept", {"text": ("value", "observed-private")}, None, "entry_mismatch"),
        ("accept", {"text": ("missing", None)}, None, "verification_unavailable"),
        ("accept", {"id": ("value", "com.example.app:id/different")}, None, "verification_unavailable"),
        ("accept", {"package": ("value", "other.pkg")}, None, "verification_unavailable"),
        ("accept", {"enabled": ("value", False)}, None, "verification_unavailable"),
        ("accept", {"type": ("value", "android.widget.AutoCompleteTextView")}, None, "verification_unavailable"),
        ("accept", {"password": ("value", True)}, None, "verification_unavailable"),
        ("accept", {}, "target_ambiguous", "verification_unavailable"),
        ("accept", {}, "exception", "verification_unavailable"),
        ("error", {}, None, "entry_backend_error"),
        ("code_error", {}, None, "entry_backend_error"),
    ],
)
def test_input_text_postcondition_and_backend_errors_are_secret_safe_and_never_retried(
    write_mode: str,
    post_attrs: dict[str, tuple[str, object]],
    post_code: str | None,
    reason: str,
) -> None:
    session = FakeSession()
    session.write_mode = write_mode
    session.post_write_attrs = post_attrs
    session.post_write_inspect_code = post_code
    harness = AndroidHarness(driver=AppiumConnectAndroidDriver(app_id="com.example.app", session=session))
    step = ExecutableStep(
        step_id="input",
        kind="action",
        action_name="inputText",
        params={"text": "submitted-private", "locator": {"resourceId": "com.example.app:id/input"}},
    )
    try:
        result = harness.invoke_action(step, harness.get_context())
    finally:
        harness.close()

    assert result.status == "failed"
    assert result.failure_category == "action_error"
    assert result.metadata == {"reason": reason}
    assert result.output is None
    dumped = result.model_dump_json()
    assert all(secret not in dumped for secret in ("submitted-private", "observed-private", "private backend payload", "params"))
    assert sum(call[0] == "replace_element_value" for call in session.calls) == 1


@pytest.mark.parametrize("resource_id", [None, ""])
@pytest.mark.parametrize("layout_changed", [False, True])
def test_input_text_structural_xpath_without_resource_id_requires_stable_bounds(
    resource_id: str | None,
    layout_changed: bool,
) -> None:
    session = FakeSession()
    session.element_attrs["id"] = ("value", resource_id) if resource_id is not None else ("missing", None)
    if layout_changed:
        session.post_write_attrs["bounds"] = ("value", _bounds(186, 1209, 943, 1346))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"text": "Unicode 中文", "locator": {"xpath": "//android.widget.EditText[1]"}})
    finally:
        driver.close()

    if layout_changed:
        assert result["status"] == "failed"
        assert result["metadata"] == {"reason": "verification_unavailable"}
    else:
        assert result == {"status": "passed", "output": None}


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, CancelledError])
def test_driver_preserves_process_interrupts(error: type[BaseException], monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(FakeElement, "replace_element_value", interrupt)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=FakeSession())
    try:
        with pytest.raises(error):
            driver.input_text({"text": "private", "locator": {"resourceId": "com.example.app:id/input"}})
    finally:
        driver.close()


def test_assert_visible_not_visible_and_assert_state_behaviors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.DEFAULT_POLL_INTERVAL_SECONDS", 0.01)

    present = FakeSession(exists=True, text="chinatravel.com")
    present.element_attrs.update(
        {
            "enabled": ("value", False),
            "checked": ("value", True),
            "selected": ("value", True),
            "clickable": ("value", False),
            "focused": ("value", True),
        }
    )
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=present)
    try:
        assert driver.assert_visible({"locator": {"text": "Present"}})["status"] == "passed"
        state_ok = driver.assert_state(
            {
                "element": {
                    "xpath": "//android.widget.FrameLayout[1]",
                    "enabled": False,
                    "checked": True,
                    "selected": True,
                    "clickable": False,
                    "focused": True,
                }
            }
        )
        assert state_ok == {
            "status": "passed",
            "output": {
                "enabled": False,
                "checked": True,
                "selected": True,
                "clickable": False,
                "focused": True,
            },
        }
        state_mismatch = driver.assert_state({"element": {"resourceId": "item", "enabled": True}})
        assert state_mismatch["status"] == "failed"
        assert state_mismatch["failure_category"] == "assertion_error"
        assert state_mismatch["output"] == {"field": "enabled", "expected": True, "actual": False}
        text_ok = driver.assert_state({"element": {"resourceId": "url"}, "text": {"contains": "china"}})
        assert text_ok == {"status": "passed", "output": {"text": "chinatravel.com"}}
        exists_only = driver.assert_state({"element": {"xpath": "//android.widget.EditText[1]"}})
        assert exists_only == {"status": "passed", "output": {"exists": True}}
        invalid_shape = driver.assert_state({"element": {}})
        assert invalid_shape["failure_category"] == "configuration_error"
    finally:
        driver.close()

    absent = FakeSession(exists=False)
    absent_driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=absent)
    try:
        missing = absent_driver.assert_visible({"locator": {"text": "Missing"}})
        gone = absent_driver.assert_not_visible({"locator": {"text": "Missing"}})
    finally:
        absent_driver.close()
    assert missing["status"] == "failed"
    assert missing["failure_category"] == "target_resolution_error"
    assert gone["status"] == "passed"


def test_runner_step_execution_and_ai_assertion_metadata(tmp_path: Any) -> None:
    session = FakeSession(activate_error=RuntimeError("app start rejected"))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    harness = AndroidHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    registry = CapabilityRegistry()
    for definition in _discover_driver_capability_definitions(
        driver,
        platform="android",
        metadata={"driver_class": "AppiumConnectAndroidDriver", "backend": "appium_connect_agent"},
    ):
        registry.register(definition)
    try:
        assert registry.get("assert_with_ai").replay == ReplayPolicy(kind="fsq_command", alias="assertWithAI")
        assert driver.perform_actions({"actions": []})["failure_category"] == "configuration_error"
        assert driver.assert_with_ai(AndroidAssertWithAIParams(prompt="Verify screen."))["failure_category"] == "configuration_error"

        runner = StepRunner(harness, capability_registry=registry)
        step = ExecutableStep(step_id="launch", action_name="launch_app", params={}, kind="setup")
        result = runner.run_step("run-1", step)
        assert result.status == "failed"
        assert result.error_message == "Android backend operation failed (RuntimeError)."
        assert "app start rejected" not in str(result.error_message)
    finally:
        harness.close()


def test_driver_raises_configuration_error_when_appium_connect_agent_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("appium_connect_agent"):
            raise ImportError("missing appium_connect_agent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="emulator-5554")
    try:
        with pytest.raises(ConfigurationError) as exc_info:
            driver.context()
        assert "Reinstall or repair fsq-agent" in str(exc_info.value.context.get("action", ""))
    finally:
        driver.close()
