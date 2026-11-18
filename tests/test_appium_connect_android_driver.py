# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import builtins
import json
import subprocess
import sys
import threading
from asyncio import CancelledError
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from fsq_agent.core import AndroidDriverInterface, CapabilityRegistry, StepRunner
from fsq_agent.core.evidence import ArtifactStore
from fsq_agent.core.harness._android import AndroidHarness
from fsq_agent.core.harness._appium_connect_android_driver import AppiumConnectAndroidDriver
from fsq_agent.core.harness._driver_tools import _discover_driver_capability_definitions
from fsq_agent.models import AndroidAssertWithAIParams, AndroidHarnessSettings, ConfigurationError, ExecutableStep, HarnessContext, ReplayPolicy, ToolExecutionError


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
        binding = attributes == ("type", "id", "package", "bounds")
        if binding and self.session.binding_code is not None:
            raise TargetError(self.session.binding_code)
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
        if binding:
            state_map.update(self.session.binding_attrs)
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

    def display_size(self, *, control: object | None = None) -> Any:
        del control
        self.session._record_thread()
        self.session.calls.append(("display_size",))
        if self.session.display_error is not None:
            raise self.session.display_error
        return SimpleNamespace(width=self.session.display[0], height=self.session.display[1])

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
        del flags, control
        self.session._record_thread()
        self.session.calls.append(("press_keycode", keycode) if metastate is None else ("press_keycode", keycode, metastate))

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
        self.binding_code: str | None = None
        self.binding_attrs: dict[str, tuple[str, object]] = {}
        self.display: tuple[object, object] = (1080, 2400)
        self.display_error: BaseException | None = None
        self.quit_error: BaseException | None = None
        self.state = "active"
        self.alive_result = False
        self.alive_error: BaseException | None = None
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
            "desc": ("value", "Input description"),
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
            state=self.state,
            target_app_id=self.target_app_id,
        )

    def ensure_alive(self, *, control: Any = None) -> bool:
        self._record_thread()
        self.calls.append(("ensure_alive", control.timeout_seconds))
        if self.alive_error is not None:
            raise self.alive_error
        return self.alive_result

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


@pytest.mark.parametrize("value", [0, 1000, 9_007_199_254_740_991])
def test_borrowed_session_rejects_initial_idle_override_without_operations(value: int) -> None:
    session = FakeSession()
    with pytest.raises(ConfigurationError, match="borrowed"):
        AppiumConnectAndroidDriver(app_id="com.example.app", session=session, uiautomator2_wait_for_idle_timeout_ms=value)
    assert session.calls == []
    assert session.thread_ids == set()


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
                "sdk_session_id": "local-session-1",
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
                "sdk_session_id": "local-session-1",
                "current_package": "com.example.app",
            },
        )
        assert driver.launch_app({}) == {"status": "passed", "output": {"app_id": "com.example.app"}}
        assert driver.kill_app({}) == {"status": "passed", "output": {"app_id": "com.example.app"}}
        assert driver.screenshot() == b"\x89PNG\r\n\x1a\nfake-png"

        snapshot = driver.ui_snapshot({})
        assert set(snapshot) == {"xml", "coverage", "compaction", "device_local_time"}
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
    session = FakeSession(observation=_tree_observation(height=2209))
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
            del _settings

        def start_managed(self, *, host: str, port: int, base_path: str, control: Any = None) -> FakeScope:
            del host, base_path, control
            return FakeScope(port)

        def cleanup_managed(self, *, control: Any = None) -> None:
            del control

    ports = iter([41001, 41002])
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect._allocate_loopback_port", lambda: next(ports))
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect._resolve_managed_local_settings", lambda _cls: SimpleNamespace())
    monkeypatch.setattr(aca_runtime, "Runtime", FakeRuntime)

    def fake_create_session(connection: Any, device_id: str, **_kwargs: Any) -> FakeSession:
        assert "uiautomator2_wait_for_idle_timeout_ms" not in _kwargs
        events.append(("create_session", connection.port, device_id, threading.get_ident()))
        return session

    monkeypatch.setattr(aca_automation, "create_session", fake_create_session)

    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="device-01")
    assert driver.launch_app({})["status"] == "passed"
    assert driver.context()["screen_size"] == (1080, 2400)
    assert driver.tap_at({"point": {"x": 1, "y": 2}})["status"] == "passed"
    assert driver.swipe({"direction": "up"})["status"] == "passed"
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
    assert session.thread_ids == worker_tids
    assert threading.get_ident() not in worker_tids


@pytest.fixture
def android_clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


@pytest.fixture
def owned_android_backend(monkeypatch: pytest.MonkeyPatch):
    import appium_connect_agent.automation as aca_automation
    import appium_connect_agent.runtime as aca_runtime

    session = FakeSession()
    events: list[tuple[Any, ...]] = []

    class FakeScope:
        def __enter__(self):
            events.append(("scope.enter", threading.get_ident()))
            return SimpleNamespace()

        def __exit__(self, *_args):
            events.append(("scope.exit", threading.get_ident()))
            return False

    class FakeRuntime:
        def __init__(self, _settings):
            pass

        def start_managed(self, **_kwargs):
            return FakeScope()

    def create_session(connection, device_id, **kwargs):
        events.append(("create_session", kwargs, threading.get_ident()))
        return session

    monkeypatch.setattr(aca_automation, "create_session", create_session)
    monkeypatch.setattr(aca_runtime, "Runtime", FakeRuntime)
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect._allocate_loopback_port", lambda: 41001)
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect._resolve_managed_local_settings", lambda _cls: SimpleNamespace())
    return session, events


@pytest.mark.parametrize("policy", [{}, {"new_command_timeout_seconds": None}, {"new_command_timeout_seconds": 0}, {"new_command_timeout_seconds": 0.125}, {"new_command_timeout_seconds": 1800.5}])
@pytest.mark.parametrize("idle", [None, 0, 1000])
def test_factory_forwards_command_and_ui_idle_policies_unchanged(owned_android_backend, policy, idle):
    from fsq_agent.core.interfaces import DriverFactory

    session, events = owned_android_backend
    settings = AndroidHarnessSettings(**policy, uiautomator2_wait_for_idle_timeout_ms=idle)
    driver = DriverFactory().create_android_driver(settings, app_id="com.example.app", serial="isolated-policy-device")
    try:
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        creates = [event for event in events if event[0] == "create_session"]
        assert len(creates) == 1
        kwargs = creates[0][1]
        assert kwargs["new_command_timeout_seconds"] == policy.get("new_command_timeout_seconds", 900.0)
        assert kwargs["no_reset"] is True
        assert kwargs["auto_launch"] is False
        assert kwargs["force_app_launch"] is False
        assert kwargs["full_reset"] is False
        assert kwargs["should_terminate_app"] is False
        if idle is None:
            assert "uiautomator2_wait_for_idle_timeout_ms" not in kwargs
        else:
            assert kwargs["uiautomator2_wait_for_idle_timeout_ms"] == idle
        assert session.calls == [("press_keycode", 3)]
    finally:
        driver.close()


@pytest.mark.parametrize("timeout", [None, 0, 25.5])
def test_borrowed_session_command_idle_policy_is_not_mutated(timeout):
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session, new_command_timeout_seconds=timeout)
    driver.close()
    assert session.calls == []
    assert session.thread_ids == set()


@pytest.mark.parametrize("recovered", [False, True])
def test_idle_liveness_is_bounded_owner_thread_preflight_not_a_business_write(owned_android_backend, android_clock, recovered):
    session, events = owned_android_backend
    session.alive_result = recovered
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="isolated-liveness-device")
    try:
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        android_clock.now = 44.0
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        android_clock.now = 89.0
        assert driver.press_key({"key": "Back"})["status"] == "passed"
        assert session.calls == [("press_keycode", 3), ("press_keycode", 3), ("ensure_alive", 25.0), ("back",)]
        assert len([event for event in events if event[0] == "create_session"]) == 1
        assert len(session.thread_ids) == 1
        assert threading.get_ident() not in session.thread_ids
        assert driver._session is session
        android_clock.now += 1.0
        assert driver.press_key({"key": "Back"})["status"] == "passed"
        assert sum(call[0] == "ensure_alive" for call in session.calls) == 1
    finally:
        driver.close()
    assert [event[0] for event in events] == ["scope.enter", "create_session", "scope.exit"]
    assert session.calls[-1] == ("quit",)


def test_idle_interval_starts_after_operation_completion(android_clock, monkeypatch):
    session = FakeSession()
    original_back = session.device.back

    def long_back(**kwargs):
        original_back(**kwargs)
        android_clock.now += 100.0

    monkeypatch.setattr(session.device, "back", long_back)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        assert driver.press_key({"key": "Back"})["status"] == "passed"
        android_clock.now += 44.999
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        android_clock.now += 45.0
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        assert session.calls == [("back",), ("press_keycode", 3), ("ensure_alive", 25.0), ("press_keycode", 3)]
    finally:
        driver.close()


@pytest.mark.parametrize("quarantined", [False, True])
def test_probe_failure_never_dispatches_or_retries_the_requested_write(android_clock, quarantined):
    session = FakeSession()
    failure = CommandError("operation_timeout", "probe-private-canary")
    failure.request_state = "response_unknown"

    def probe(*, control):
        session.calls.append(("ensure_alive", control.timeout_seconds))
        if quarantined:
            session.state = "uncertain"
        raise failure

    session.ensure_alive = probe
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        android_clock.now = 45.0
        result = driver.press_key({"key": "Back"})
        assert result["failure_category"] == "action_error"
        assert result["metadata"]["backend_code"] == "operation_timeout"
        assert result["metadata"]["request_state"] == "response_unknown"
        assert "probe-private-canary" not in str(result)
        assert session.calls == [("press_keycode", 3), ("ensure_alive", 25.0)]
        if quarantined:
            android_clock.now = 100.0
            assert driver.press_key({"key": "Back"})["metadata"]["reason"] == "session_unavailable"
            assert len(session.calls) == 2
    finally:
        driver.close()


def test_probe_cancellation_propagates_before_the_business_operation(android_clock):
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    interruption = CancelledError("probe-private-canary")
    try:
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        session.alive_error = interruption
        android_clock.now = 45.0
        with pytest.raises(CancelledError) as caught:
            driver.press_key({"key": "Back"})
        assert caught.value is interruption
        assert session.calls == [("press_keycode", 3), ("ensure_alive", 25.0)]
    finally:
        driver.close()


@pytest.mark.parametrize("state", ["creating", "uncertain", "disposing", "disposed", "closing", "closed"])
def test_idle_nonactive_session_is_not_probed_or_recovered(android_clock, state):
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        assert driver.press_key({"key": "Home"})["status"] == "passed"
        session.state = state
        android_clock.now = 100.0
        result = driver.press_key({"key": "Back"})
        assert result["metadata"]["reason"] == "session_unavailable"
        assert result["metadata"]["session_state"] == state
        assert session.calls == [("press_keycode", 3)]
    finally:
        driver.close()


@pytest.mark.parametrize("borrowed", [False, True])
def test_close_after_idle_never_probes_and_owned_close_fences_later_recovery(owned_android_backend, android_clock, borrowed):
    session, events = owned_android_backend
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="isolated-close-device", session=session if borrowed else None)
    assert driver.press_key({"key": "Home"})["status"] == "passed"
    android_clock.now = 100.0
    driver.close()
    calls = list(session.calls)
    event_snapshot = list(events)
    if not borrowed:
        assert driver.press_key({"key": "Back"})["metadata"]["reason"] == "closed"
        with pytest.raises(ToolExecutionError):
            driver.context()
    driver.close()
    assert session.calls == calls
    assert events == event_snapshot
    assert not any(call[0] == "ensure_alive" for call in calls)
    if borrowed:
        assert ("quit",) not in calls
    else:
        assert calls[-1] == ("quit",)


def test_pending_cleanup_does_not_probe_active_session_or_create_a_generation(owned_android_backend, android_clock):
    session, events = owned_android_backend
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="isolated-cleanup-device")
    assert driver.press_key({"key": "Home"})["status"] == "passed"
    session.quit_error = RuntimeError("quit-private-canary")
    try:
        with pytest.raises(RuntimeError):
            driver.close()
        android_clock.now = 100.0
        assert driver.press_key({"key": "Back"})["metadata"]["reason"] == "cleanup_pending"
        assert session.calls == [("press_keycode", 3), ("quit",)]
        assert [event[0] for event in events] == ["scope.enter", "create_session"]
    finally:
        session.quit_error = None
        with pytest.raises(RuntimeError):
            driver.close()


@pytest.mark.parametrize("request_state", ["response_received", "response_unknown"])
def test_business_failure_is_not_replayed_or_followed_by_an_fsq_reconnect(android_clock, monkeypatch, request_state):
    session = FakeSession()
    failure = CommandError("command_failed", "operation-private-canary")
    failure.request_state = request_state

    def fail_back(*, control):
        session.calls.append(("back",))
        raise failure

    monkeypatch.setattr(session.device, "back", fail_back)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.press_key({"key": "Back"})
        assert result["failure_category"] == "action_error"
        assert result["metadata"]["request_state"] == request_state
        assert session.calls == [("back",)]
    finally:
        driver.close()


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
        ("element", "xpath", "//*[(@resource-id=concat(@package, ':id/digit_7') or @resource-id='digit_7')]", False),
        ("element", "id", "nonstandard/id-1", True),
        ("element", "accessibility id", "Open menu", False),
        (
            "element",
            "xpath",
            """//*[(@resource-id=concat(@package, ':id/digit_7') or @resource-id='digit_7') and @text=concat('Say ', "'", 'hi', "'", ' and "bye"')]""",
            False,
        ),
        ("element", "xpath", """//*[@text="Sign 'In'" or (not(//*[@text="Sign 'In'"]) and @content-desc="Sign 'In'")]""", False),
        ("element", "xpath", "//android.widget.Button[@text='Hold']", False),
    ]


def test_observed_ids_in_ui_snapshot_session_fallback_and_input_text_bounds_binding() -> None:
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
    # Short ids resolve independently of the Session target or driver package.
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
        "digit_7",
        "android:id/button1",
        "custom/raw-id",
    ]
    assert input_res == {"status": "passed", "output": None}
    element_calls = [call for call in session.calls if call[0] == "element"]
    assert element_calls == [
        ("element", "xpath", "//*[(@resource-id=concat(@package, ':id/digit_7') or @resource-id='digit_7')]", False),
        (
            "element",
            "xpath",
            "//*[(@resource-id=concat(@package, ':id/digit_7') or @resource-id='digit_7') and @text='7']",
            False,
        ),
        ("element", "xpath", "//*[(@resource-id=concat(@package, ':id/formula') or @resource-id='formula')]", False),
        ("element", "id", "com.google.android.calculator:id/formula", False),
    ]

    # Without a bound app_id, a bare local id is matched exactly without Settings changes.
    unbound_session = FakeSession(target_app_id=None)
    unbound_driver = AppiumConnectAndroidDriver(app_id=None, session=unbound_session)
    try:
        assert unbound_driver.tap_on({"locator": {"resourceId": "digit_7"}})["status"] == "passed"
    finally:
        unbound_driver.close()
    assert [call for call in unbound_session.calls if call[0] == "element"] == [
        ("element", "xpath", "//*[(@resource-id=concat(@package, ':id/digit_7') or @resource-id='digit_7')]", False),
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
        assert res["metadata"] == {"reason": "target_ambiguous", "backend_code": "target_ambiguous", "session_state": "active"}


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
    session.display = (540, 1200)
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

    assert tap_plain["status"] == "passed"
    assert tap_plain["output"] == {"point": {"x": 320, "y": 640}}
    assert tap_scaled["status"] == "passed"
    assert tap_scaled["output"] == {"point": {"x": 50, "y": 100}}
    assert swipe_points["status"] == "passed"
    assert swipe_points["output"] == {"start": {"x": 400, "y": 950}, "end": {"x": 100, "y": 950}}
    assert swipe_dir == {"status": "passed", "output": {"direction": "up"}}
    assert bad_tap["failure_category"] == "configuration_error"
    assert bad_swipe["failure_category"] == "configuration_error"
    assert bad_duration["failure_category"] == "configuration_error"

    pointer_calls = [call for call in session.calls if call[0] in {"tap_at", "swipe_between"}]
    assert pointer_calls == [
        ("tap_at", 320, 640),
        ("tap_at", 50, 100),
        ("swipe_between", 400, 950, 100, 950, 1000),
        ("swipe_between", 270, 960, 270, 240, 250),
    ]


@pytest.mark.parametrize("display", [(1080, 2400), (541, 1201), (2, 2), (1, 1)])
@pytest.mark.parametrize("direction", ["up", "down", "left", "right"])
def test_untargeted_direction_swipe_uses_current_safe_physical_interior(display, direction):
    from math import ceil, floor

    session = FakeSession(observation=_tree_observation(width=99, height=101))
    session.display = display
    width, height = display
    x_mid, y_mid = width // 2, height // 2
    left, right = min(ceil(width * 0.18), width - 1), min(floor(width * 0.82), width - 1)
    top, bottom = min(ceil(height * 0.20), height - 1), min(floor(height * 0.80), height - 1)
    expected = {"up": (x_mid, bottom, x_mid, top), "down": (x_mid, top, x_mid, bottom), "left": (right, y_mid, left, y_mid), "right": (left, y_mid, right, y_mid)}[direction]
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        assert driver.swipe({"direction": direction})["status"] == "passed"
    finally:
        driver.close()
    assert session.calls == [("display_size",), ("swipe_between", *expected, 200)]


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
    assert sum(call[0] == "replace_element_value" for call in session.calls) == 1
    assert [call[1:4] for call in session.calls if call[0] in {"click", "replace_element_value"}] == [
        ("id", "com.example.app:id/input", False),
        ("id", "com.example.app:id/input", False),
    ]


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
    assert result["metadata"]["reason"] == "invalid_input_target"
    assert result["metadata"]["stage"] == "pre_inspection"
    assert not any(call[0] in {"click", "replace_element_value"} for call in session.calls)
    assert "submitted-private" not in str(result)


@pytest.mark.parametrize(
    "password_attr",
    [("value", True), ("missing", None), ("unknown", None), ("value", 0)],
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
    assert result["metadata"]["reason"] == "verification_unavailable"
    assert result["metadata"]["stage"] == "pre_inspection"
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
    assert result["metadata"]["reason"] == "verification_unavailable"
    assert result["metadata"]["stage"] == "post_verification"
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

    assert result == {"status": "passed", "output": None}
    assert session.text == "replacement"
    inspections = [call for call in session.calls if call[0] == "inspect"]
    assert inspections[-1][1:4] == ("id", "com.example.app:id/input", False)


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
    assert result.metadata["reason"] == reason
    assert result.metadata["stage"] == ("replace_value" if reason == "entry_backend_error" else "post_verification")
    assert result.output is None
    dumped = result.model_dump_json()
    assert all(secret not in dumped for secret in ("submitted-private", "observed-private", "private backend payload", "params"))
    assert sum(call[0] == "replace_element_value" for call in session.calls) == 1


@pytest.mark.parametrize("resource_id", [None, "", " \t ", "null", " null "])
@pytest.mark.parametrize("layout_changed", [False, True])
@pytest.mark.parametrize("locator", [{"xpath": "//android.widget.EditText[1]"}, {"xpath": "(//android.widget.EditText)[1]"}, {"className": "android.widget.EditText"}, {"accessibilityId": "Name"}])
def test_input_text_structural_xpath_without_resource_id_requires_stable_bounds(
    resource_id: str | None,
    layout_changed: bool,
    locator: dict[str, str],
) -> None:
    session = FakeSession()
    session.element_attrs["id"] = ("value", resource_id) if resource_id is not None else ("missing", None)
    if layout_changed:
        session.post_write_attrs["bounds"] = ("value", _bounds(186, 1209, 943, 1346))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"text": "Unicode 中文", "locator": locator})
    finally:
        driver.close()

    if layout_changed:
        assert result["status"] == "failed"
        assert result["metadata"]["reason"] == "verification_unavailable"
        assert result["metadata"]["stage"] == "post_verification"
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
        assert driver.assert_with_ai(AndroidAssertWithAIParams(assertion="Verify screen.", surface="appearance", full_screen_reason="Whole display appearance"))["status"] == "incomplete"

        runner = StepRunner(harness, capability_registry=registry)
        step = ExecutableStep(step_id="launch", action_name="launch_app", params={}, kind="setup")
        result = runner.run_step("run-1", step)
        assert result.status == "failed"
        assert result.error_message == "Android backend operation failed."
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


def test_context_and_all_pointer_modes_use_fresh_physical_display_not_viewport() -> None:
    session = FakeSession(observation=_tree_observation(height=2209))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.context()["screen_size"] == (1080, 2400)
    assert driver.swipe({"direction": "up"})["status"] == "passed"
    session.display = (540, 1200)
    assert driver.context()["screen_size"] == (540, 1200)
    assert driver.tap_at({"point": {"x": 1080, "y": 2400}, "reference_screen_size": {"width": 1080, "height": 2400}})["output"] == {"point": {"x": 539, "y": 1199}}
    assert driver.swipe({"start": {"x": 0, "y": 0}, "end": {"x": 539, "y": 1199}})["status"] == "passed"
    assert ("swipe_between", 540, 1920, 540, 480, 200) in session.calls
    assert sum(call[0] == "display_size" for call in session.calls) == 5
    assert sum(call[0] == "observe_tree" for call in session.calls) == 2


@pytest.mark.parametrize("display", [(True, 2400), (1080.0, 2400), (0, 2400), (1080, 2147483648), (1080, None)])
def test_invalid_physical_display_never_falls_back_or_sends_pointer(display: tuple[object, object]) -> None:
    session = FakeSession()
    session.display = display
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    with pytest.raises(ToolExecutionError):
        driver.context()
    for action, payload in ((driver.tap_at, {"point": {"x": 1, "y": 2}}), (driver.swipe, {"direction": "up"})):
        assert action(payload)["status"] == "failed"
    assert not any(call[0] in {"tap_at", "swipe_between", "take_screenshot"} for call in session.calls)


def test_missing_or_failed_display_api_is_explicit_and_never_uses_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    monkeypatch.setattr(session.device, "display_size", None)
    assert driver.swipe({"direction": "up"})["status"] == "failed"
    assert session.calls == []
    monkeypatch.undo()
    session.display_error = CommandError("operation_timeout", "private-display-canary")
    assert driver.tap_at({"point": {"x": 1, "y": 2}})["status"] == "failed"
    assert all(call[0] != "observe_tree" for call in session.calls)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("up", (60, 99, 60, 20)),
        ("down", (60, 20, 60, 99)),
        ("left", (84, 60, 10, 60)),
        ("right", (35, 60, 110, 60)),
    ],
)
def test_element_swipe_uses_unique_current_bounds_and_single_public_gesture(direction: str, expected: tuple[int, ...]) -> None:
    session = FakeSession()
    session.element_attrs["bounds"] = ("value", _bounds(10, 20, 110, 100))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.swipe({"target": "List", "direction": direction, "percent": None})["status"] == "passed"
    assert [call for call in session.calls if call[0] == "swipe_between"] == [("swipe_between", *expected, 200)]
    assert [call[-1] for call in session.calls if call[0] == "inspect"] == [("bounds",)]
    assert not any(call[0] == "observe_tree" for call in session.calls)


def test_element_swipe_fraction_is_center_symmetric() -> None:
    session = FakeSession()
    session.element_attrs["bounds"] = ("value", _bounds(10, 20, 111, 101))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.swipe({"locator": {"resourceId": "list"}, "direction": "left", "percent": 0.5})["status"] == "passed"
    assert ("swipe_between", 85, 60, 35, 60, 200) in session.calls


@pytest.mark.parametrize("bounds", [_bounds(0, 0, 0, 10), _bounds(0, 0, 1, 10), _bounds(-1, 0, 10, 10), _bounds(0, 0, 1081, 10), _bounds(0, 0, 10, 2401), None])
def test_element_swipe_rejects_degenerate_or_offscreen_bounds(bounds: object) -> None:
    session = FakeSession()
    session.element_attrs["bounds"] = ("value", bounds)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.swipe({"target": "List", "direction": "left"})["status"] == "failed"
    assert not any(call[0] == "swipe_between" for call in session.calls)


@pytest.mark.parametrize("code", ["target_ambiguous", "operation_timeout"])
def test_element_swipe_never_retries_failed_inspection(code: str) -> None:
    session = FakeSession()
    session.inspect_code = code
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.swipe({"target": "List", "direction": "up"})["status"] == "failed"
    assert sum(call[0] == "inspect" for call in session.calls) == 1
    assert not any(call[0] == "swipe_between" for call in session.calls)


def test_text_navigation_keys_and_ctrl_end_are_one_modified_event() -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    for key in ("MoveEnd", "move_end", "End", "MoveHome", "move_home", "Home", "Ctrl+End"):
        assert driver.press_key({"key": key})["status"] == "passed"
    before = list(session.calls)
    for key in ("Ctrl+Home", "Ctrl+Bogus", "Alt+End", "Ctrl+End+Home"):
        assert driver.press_key({"key": key})["failure_category"] == "configuration_error"
    assert session.calls == before
    assert [call for call in session.calls if call[0] == "press_keycode"] == [
        ("press_keycode", 123),
        ("press_keycode", 123),
        ("press_keycode", 123),
        ("press_keycode", 122),
        ("press_keycode", 122),
        ("press_keycode", 3),
        ("press_keycode", 123, 4096),
    ]


@pytest.mark.parametrize("raw_id", ["com.example.app:id/input", "custom/raw-id", "bare-id"])
def test_input_binds_exact_observed_id_before_mutation_and_verifies_after_layout_move(raw_id: str) -> None:
    session = FakeSession(text="original")
    session.element_attrs["id"] = ("value", raw_id)
    session.post_write_attrs["bounds"] = ("value", _bounds(0, 100, 100, 150))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.input_text({"target": "original", "text": "中文 café replacement"})["status"] == "passed"
    raw = raw_id != "com.example.app:id/input"
    assert ("element", "id", raw_id, raw) in session.calls
    writes = [call for call in session.calls if call[0] == "replace_element_value"]
    assert len(writes) == 1
    assert writes[0][1:4] == ("id", raw_id, raw)
    assert [call for call in session.calls if call[0] == "inspect"][-1][1:4] == ("id", raw_id, raw)


@pytest.mark.parametrize("field,value", [("type", "android.widget.AutoCompleteTextView"), ("package", "other.package"), ("id", "other:id/input"), ("bounds", _bounds(1, 0, 101, 50))])
def test_input_identity_binding_rejects_changed_target_before_any_write(field: str, value: object) -> None:
    session = FakeSession()
    session.binding_attrs[field] = ("value", value)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    result = driver.input_text({"locator": {"className": "android.widget.EditText"}, "text": "private-canary"})
    assert result["status"] == "failed"
    assert not any(call[0] in {"click", "replace_element_value"} for call in session.calls)
    assert "private-canary" not in str(result)


def test_duplicate_observed_id_and_ambiguous_no_id_target_fail_before_write() -> None:
    session = FakeSession()
    session.binding_code = "target_ambiguous"
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.input_text({"target": "Loaded", "text": "replacement"})["status"] == "failed"
    session.inspect_code = "target_ambiguous"
    assert driver.input_text({"locator": {"className": "android.widget.EditText"}, "text": "replacement"})["status"] == "failed"
    assert not any(call[0] in {"click", "replace_element_value"} for call in session.calls)


def test_input_safe_diagnostics_allow_only_enumerated_sdk_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    failure = CommandError("operation_timeout", "message-secret-canary")
    failure.request_state = "response_unknown"
    failure.completed_steps = ("target_resolved", "secret-canary", "action_completed")

    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(FakeElement, "replace_element_value", fail)
    result = driver.input_text({"target": "Loaded", "text": "input-secret-canary"})
    assert result["metadata"] == {
        "reason": "entry_backend_error",
        "stage": "replace_value",
        "backend_code": "operation_timeout",
        "request_state": "response_unknown",
        "completed_steps": ["target_resolved", "action_completed"],
        "session_state": "active",
    }
    assert "canary" not in str(result)
    failure.code = "code-secret-canary"
    failure.request_state = "request-secret-canary"
    assert "canary" not in str(driver.input_text({"target": "Loaded", "text": "input-secret-canary"}))


@pytest.mark.parametrize("error", [RuntimeError, CancelledError])
def test_diagnostic_session_getter_failure_does_not_replace_primary_input_failure(error: type[BaseException], monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)

    def fail(*args: object, **kwargs: object) -> None:
        monkeypatch.setattr(session, "session_info", lambda: (_ for _ in ()).throw(error("getter-canary")))
        raise CommandError("operation_timeout", "primary-canary")

    monkeypatch.setattr(FakeElement, "replace_element_value", fail)
    result = driver.input_text({"target": "Loaded", "text": "input-canary"})
    assert result["metadata"]["backend_code"] == "operation_timeout"
    assert "session_state" not in result["metadata"]
    assert "canary" not in str(result)


@pytest.mark.parametrize("password", [("value", True), ("unknown", None), ("missing", None), ("value", 0)])
def test_inspect_never_requests_protected_or_unknown_password_text(password: tuple[str, object]) -> None:
    from fsq_agent.models import AndroidInspectElementParams

    session = FakeSession(text="protected-text-canary")
    session.element_attrs["password"] = password
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    result = driver.inspect_element(AndroidInspectElementParams(target="Input"))
    assert result["attributes"]["text"] == {"state": "unknown", "value": None}
    assert result["attributes"]["desc"] == {"state": "value", "value": "Input description"}
    assert "protected-text-canary" not in str(result)
    assert all("text" not in call[-1] for call in session.calls if call[0] == "inspect")


def test_inspect_preserves_full_safe_values_states_and_serializable_bounds() -> None:
    from fsq_agent.models import AndroidInspectElementParams

    path = "/storage/emulated/0/" + "long-directory/" * 25 + "file.txt"
    session = FakeSession(text=path)
    session.element_attrs.update({"desc": ("value", path), "checked": ("not_applicable", None), "focused": ("unknown", None)})
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    harness = AndroidHarness(driver=driver, ai_assertion_evaluator=SimpleNamespace(close=lambda: None))
    actions = {schema.name: schema for schema in harness.action_space()}
    assert "assert_with_ai" in actions
    assert actions["inspect_element"].fsq_action_name == "inspectElement"
    assert "reference_screen_size" not in actions["tap_at"].params_json_schema["properties"]
    result = driver.inspect_element(AndroidInspectElementParams(target="Path", attributes=["text", "desc", "bounds", "checked", "focused"]))
    assert result == {
        "attributes": {
            "text": {"state": "value", "value": path},
            "desc": {"state": "value", "value": path},
            "bounds": {"state": "value", "value": {"x": 0, "y": 0, "width": 100, "height": 50}},
            "checked": {"state": "not_applicable", "value": None},
            "focused": {"state": "unknown", "value": None},
        }
    }
    assert next(call[-1] for call in session.calls if call[0] == "inspect") == ("password",)
    result = harness.invoke_action(
        ExecutableStep(step_id="inspect", kind="observation", action_name="inspectElement", params={"target": "Path", "attributes": ["text"]}), HarnessContext(platform="android")
    )
    assert result.status == "passed"
    assert result.output["attributes"]["text"]["value"] == path


def test_inspection_password_and_safe_attributes_share_one_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.models import AndroidInspectElementParams

    now = [100.0]
    budgets = []
    original = FakeElement.inspect

    def inspect(element: FakeElement, **kwargs: object) -> object:
        result = original(element, **kwargs)
        now[0] += 6.0
        return result

    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.time.monotonic", lambda: now[0])
    monkeypatch.setattr(FakeElement, "inspect", inspect)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=FakeSession())
    monkeypatch.setattr(driver, "_operation_control", budgets.append)
    driver.inspect_element(AndroidInspectElementParams(target="Input"))
    assert budgets == [10.0, 4.0]


def test_closed_driver_context_and_input_are_distinct_safe_local_failures() -> None:
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", serial="never-connect")
    driver.close()
    with pytest.raises(ToolExecutionError) as closed:
        driver.context()
    assert closed.value.context["reason"] == "closed"
    result = driver.input_text({"target": "Input", "text": "closed-input-canary"})
    assert result["metadata"]["reason"] == "closed"
    assert result["metadata"]["stage"] == "pre_inspection"
    assert "canary" not in str(result)
    assert driver._executor is None
    assert driver._session is None


@pytest.mark.parametrize("operation", ["display", "element_bounds", "inspect_password", "input_write"])
def test_new_public_operations_preserve_cancellation_without_retries(operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.models import AndroidInspectElementParams

    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)

    def cancelled(*args: object, **kwargs: object) -> None:
        raise CancelledError

    if operation == "display":
        session.display_error = CancelledError()
        action, payload = driver.swipe, {"direction": "up"}
    elif operation == "input_write":
        monkeypatch.setattr(FakeElement, "replace_element_value", cancelled)
        action, payload = driver.input_text, {"target": "Loaded", "text": "private"}
    else:
        monkeypatch.setattr(FakeElement, "inspect", cancelled)
        action, payload = (driver.swipe, {"target": "List", "direction": "up"}) if operation == "element_bounds" else (driver.inspect_element, AndroidInspectElementParams(target="Input"))
    with pytest.raises(CancelledError):
        action(payload)
    assert not any(call[0] in {"tap_at", "swipe_between", "replace_element_value"} for call in session.calls)


@pytest.mark.parametrize("bounds", [_bounds(0, 0, 100, 50), _bounds(0, 0, 101, 50)])
def test_element_swipe_tiny_fraction_fails_without_expanding_distance(bounds: object) -> None:
    session = FakeSession()
    session.element_attrs["bounds"] = ("value", bounds)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.swipe({"target": "List", "direction": "left", "percent": 0.0001})["status"] == "failed"
    assert not any(call[0] == "swipe_between" for call in session.calls)


def test_inspect_without_text_does_not_read_password_or_unselected_attributes() -> None:
    from fsq_agent.models import AndroidInspectElementParams

    session = FakeSession()
    session.element_attrs["enabled"] = ("unknown", None)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.inspect_element(AndroidInspectElementParams(target="Input", attributes=["enabled"])) == {"attributes": {"enabled": {"state": "unknown", "value": None}}}
    assert [call[-1] for call in session.calls if call[0] == "inspect"] == [("enabled",)]


def test_input_preinspection_failure_keeps_safe_backend_stage_without_write() -> None:
    session = FakeSession()
    session.inspect_code = "operation_timeout"
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    result = driver.input_text({"target": "Input", "text": "input-canary"})
    assert result["metadata"]["backend_code"] == "operation_timeout"
    assert result["metadata"]["stage"] == "pre_inspection"
    assert not any(call[0] in {"click", "replace_element_value"} for call in session.calls)
    assert "canary" not in str(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"direction": "up", "start": {"x": 1, "y": 2}, "end": {"x": 3, "y": 4}},
        {"target": "List", "start": {"x": 1, "y": 2}, "end": {"x": 3, "y": 4}},
        {"direction": "up", "percent": 0.5},
        {"target": "List", "direction": "up", "percent": float("nan")},
        {"target": "List", "direction": "up", "percent": True},
        {"target": "List", "direction": "up", "duration": 60001},
        {"start": {"x": 1, "y": 2}, "end": {"x": 3, "y": 4}, "percent": 0.5},
    ],
)
def test_direct_swipe_call_cannot_bypass_models_owned_modes(payload: dict[str, object]) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.swipe(payload)["failure_category"] == "configuration_error"
    assert session.calls == []


def test_online_gesture_schemas_exclude_replay_reference_without_removing_internal_aliases() -> None:
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=FakeSession())
    schemas = {schema.name: schema for schema in AndroidHarness(driver=driver).action_space()}
    for name in ("tap_at", "swipe"):
        assert "reference_screen_size" not in schemas[name].params_json_schema["properties"]
    definitions = {definition.name: definition for definition in _discover_driver_capability_definitions(driver, platform="android")}
    assert definitions["tap_at"].fsq_command_alias == "tapAt"
    assert definitions["swipe"].fsq_command_alias == "swipe"
    assert definitions["inspect_element"].step_kind == "observation"


def test_element_swipe_pixel_quantization_never_expands_requested_span() -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    assert driver.swipe({"target": "List", "direction": "left", "percent": 0.03})["status"] == "passed"
    assert [call for call in session.calls if call[0] == "swipe_between"] == [("swipe_between", 50, 25, 49, 25, 200)]


@pytest.mark.parametrize("strict_reference", [False, True])
@pytest.mark.parametrize("action_name", ["tap_at", "swipe"])
def test_coordinate_recording_uses_action_display_not_earlier_context(action_name: str, strict_reference: bool, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(observation=_tree_observation(height=2209))
    display_sizes = iter(((1080, 2400), (540, 1200), (270, 600)))
    original_display_size = session.device.display_size

    def display_size(*, control: object | None = None) -> Any:
        session.display = next(display_sizes)
        return original_display_size(control=control)

    monkeypatch.setattr(session.device, "display_size", display_size)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    harness = AndroidHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    registry = CapabilityRegistry()
    for definition in _discover_driver_capability_definitions(driver, platform="android"):
        registry.register(definition)
    params = {"point": {"x": 400, "y": 1000}} if action_name == "tap_at" else {"start": {"x": 50, "y": 100}, "end": {"x": 400, "y": 1000}}
    if strict_reference:
        params["reference_screen_size"] = {"width": 1080, "height": 2400}
    expected_replay = {**params, "reference_screen_size": {"width": 1080, "height": 2400} if strict_reference else {"width": 540, "height": 1200}}
    expected_pointer = ("tap_at", 200, 500) if strict_reference else ("tap_at", 400, 1000)
    if action_name == "swipe":
        expected_pointer = ("swipe_between", 25, 50, 200, 500, 200) if strict_reference else ("swipe_between", 50, 100, 400, 1000, 200)
    runner = StepRunner(harness, capability_registry=registry)
    try:
        result = runner.run_step("changing-display", ExecutableStep(step_id="coordinate", kind="action", action_name=action_name, params=params))
        assert result.status == "passed"
        assert [call for call in session.calls if call[0] in {"tap_at", "swipe_between"}] == [expected_pointer]
        assert sum(call[0] == "display_size" for call in session.calls) == 3
        execution = runner.last_capability_execution_result
        assert execution is not None
        assert execution.safe_replay_params == expected_replay
        invoke = next(report for report in result.phase_reports if report.phase == "invoke")
        assert invoke.metadata["safe_replay_params"] == expected_replay
        event = next(event for event in runner.events if event.event_type == "action_result")
        assert event.payload["metadata"]["safe_replay_params"] == expected_replay
        expected_output = {"point": {"x": expected_pointer[1], "y": expected_pointer[2]}}
        if action_name == "swipe":
            expected_output = {"start": {"x": expected_pointer[1], "y": expected_pointer[2]}, "end": {"x": expected_pointer[3], "y": expected_pointer[4]}}
        assert execution.output == expected_output
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("direction", "fraction", "bounds", "expected"),
    [
        ("right", 1.0, (42, 357, 1038, 483), (105, 420, 1038, 420)),
        ("left", 1.0, (42, 357, 1038, 483), (974, 420, 42, 420)),
        ("right", 0.5, (42, 357, 1038, 483), (291, 420, 788, 420)),
        ("left", 0.5, (42, 357, 1038, 483), (788, 420, 291, 420)),
        ("right", 1.0, (0, 357, 1080, 483), (63, 420, 1079, 420)),
        ("left", 1.0, (0, 357, 1080, 483), (1016, 420, 0, 420)),
        ("right", 1.0, (42, 357, 44, 483), (42, 420, 44, 420)),
        ("left", 1.0, (42, 357, 44, 483), (43, 420, 42, 420)),
        ("right", 0.5, (42, 357, 46, 483), (43, 420, 44, 420)),
        ("left", 0.5, (42, 357, 46, 483), (44, 420, 43, 420)),
    ],
)
def test_horizontal_element_swipe_uses_interior_start_and_exact_full_right_endpoint(direction, fraction, bounds, expected) -> None:
    session = FakeSession()
    session.element_attrs["bounds"] = ("value", _bounds(*bounds))
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.swipe({"locator": {"resourceId": "slider"}, "direction": direction, "percent": fraction})
    finally:
        driver.close()
    assert result["status"] == "passed"
    assert [call for call in session.calls if call[0] == "swipe_between"] == [("swipe_between", *expected, 200)]
    assert [call[-1] for call in session.calls if call[0] == "inspect"] == [("bounds",)]


@pytest.mark.parametrize("slow_stage", ["pre_inspection", "identity_binding", "post_verification"])
def test_input_text_slow_inspection_within_operation_budget_preserves_single_verified_write(monkeypatch: pytest.MonkeyPatch, slow_stage: str) -> None:
    session = FakeSession(text="original")
    original_inspect = FakeElement.inspect
    stages = []

    def inspect(self, *, attributes=(), wait_timeout_seconds=None, control=None):
        if attributes == ("type", "id", "package", "bounds"):
            stage = "identity_binding"
        elif any(call[0] == "replace_element_value" for call in session.calls):
            stage = "post_verification"
        else:
            stage = "pre_inspection"
        stages.append(stage)
        # Model a single read-only operation completing after nine idle-waiting
        # HTTP requests, without sleeping or touching a real SDK Session.
        if stage == slow_stage and control.timeout_seconds < 10.08:
            raise CommandError("operation_timeout", "read-only inspection exceeded its control budget")
        return original_inspect(self, attributes=attributes, wait_timeout_seconds=wait_timeout_seconds, control=control)

    monkeypatch.setattr(FakeElement, "inspect", inspect)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"locator": {"resourceId": "com.example.app:id/input"}, "text": "verified replacement"})
    finally:
        driver.close()
    assert result["status"] == "passed"
    assert session.text == "verified replacement"
    assert stages == ["pre_inspection", "identity_binding", "post_verification"]
    assert sum(call[0] == "click" for call in session.calls) == 1
    assert sum(call[0] == "replace_element_value" for call in session.calls) == 1


@pytest.mark.parametrize("locator_params", [{"target": "First name"}, {"locator": {"text": "First name"}}, {"locator": {"xpath": "//android.widget.EditText[@text='First name']"}}])
@pytest.mark.parametrize("resource_id", [None, "null", " \t", " null "])
def test_no_id_text_target_binds_exact_geometry_before_single_verified_replacement(locator_params: dict[str, object], resource_id: str | None) -> None:
    session = FakeSession(text="First name")
    session.element_attrs["id"] = ("missing", None) if resource_id is None else ("value", resource_id)
    session.element_attrs["bounds"] = ("value", _bounds(137, 858, 933, 1007))
    # SDK versions may report the same absent id differently after entry.
    session.post_write_attrs["id"] = ("missing", None)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({**locator_params, "text": "Hugo"})
    finally:
        driver.close()
    assert result == {"status": "passed", "output": None}
    assert session.text == "Hugo"
    anchor = "//*[@class='android.widget.EditText' and @package='com.example.app' and @bounds='[137,858][933,1007]']"
    writes = [call for call in session.calls if call[0] == "replace_element_value"]
    assert writes == [("replace_element_value", "xpath", anchor, False, "Hugo")]
    inspections = [call for call in session.calls if call[0] == "inspect"]
    assert inspections[-1][1:4] == ("xpath", anchor, False)
    assert not any(call[0] == "element" and call[1] == "id" for call in session.calls)


@pytest.mark.parametrize(
    "binding_code,binding_attrs",
    [
        ("target_not_found", {}),
        ("target_ambiguous", {}),
        (None, {"type": ("value", "android.widget.AutoCompleteTextView")}),
        (None, {"package": ("value", "other.package")}),
        (None, {"bounds": ("value", _bounds(1, 0, 101, 50))}),
        (None, {"id": ("value", "com.example.app:id/new")}),
    ],
)
def test_no_id_geometric_binding_failure_never_clicks_or_writes(binding_code: str | None, binding_attrs: dict[str, tuple[str, object]]) -> None:
    session = FakeSession(text="First name")
    session.element_attrs["id"] = ("value", "null")
    session.binding_code = binding_code
    session.binding_attrs = binding_attrs
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"target": "First name", "text": "private-canary"})
    finally:
        driver.close()
    assert result["metadata"]["reason"] == "verification_unavailable"
    assert result["metadata"]["stage"] == "identity_binding"
    assert not any(call[0] in {"click", "replace_element_value"} for call in session.calls)
    assert "private-canary" not in str(result)


@pytest.mark.parametrize(
    "post_code,post_attrs,reason",
    [
        ("target_not_found", {}, "verification_unavailable"),
        ("target_ambiguous", {}, "verification_unavailable"),
        (None, {"bounds": ("value", _bounds(0, 100, 100, 150))}, "verification_unavailable"),
        (None, {"type": ("value", "android.widget.AutoCompleteTextView")}, "verification_unavailable"),
        (None, {"package": ("value", "other.package")}, "verification_unavailable"),
        (None, {"id": ("value", "new-id")}, "verification_unavailable"),
        (None, {"enabled": ("value", False)}, "verification_unavailable"),
        (None, {"password": ("value", True)}, "verification_unavailable"),
        (None, {"text": ("missing", None)}, "verification_unavailable"),
        (None, {"text": ("value", "wrong-private")}, "entry_mismatch"),
    ],
)
def test_no_id_post_entry_failure_is_explicit_and_never_retries(post_code: str | None, post_attrs: dict[str, tuple[str, object]], reason: str) -> None:
    session = FakeSession(text="First name")
    session.element_attrs["id"] = ("value", "null")
    session.post_write_inspect_code = post_code
    session.post_write_attrs = post_attrs
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.input_text({"target": "First name", "text": "private-canary"})
    finally:
        driver.close()
    assert result["status"] == "failed"
    assert result["metadata"]["reason"] == reason
    assert result["metadata"]["stage"] == "post_verification"
    assert sum(call[0] == "replace_element_value" for call in session.calls) == 1
    if reason == "verification_unavailable":
        assert "Text entry was sent" in str(result)
        assert "before re-entering" in str(result)
    assert all(value not in str(result) for value in ("private-canary", "wrong-private", "First name"))


@pytest.mark.parametrize("compound", [False, True])
@pytest.mark.parametrize("resource_id", ["com.example.app:id/btn_record", "answer", "custom/raw-id"])
def test_resource_id_surrounding_whitespace_does_not_change_resolution(resource_id: str, compound: bool) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    suffix = {"className": "android.widget.EditText"} if compound else {}
    try:
        assert driver.tap_on({"locator": {"resourceId": resource_id, **suffix}})["status"] == "passed"
        assert driver.tap_on({"locator": {"resourceId": f" \t{resource_id} \n", **suffix}})["status"] == "passed"
    finally:
        driver.close()
    clicks = [call for call in session.calls if call[0] == "click"]
    assert clicks[0] == clicks[1]


@pytest.mark.parametrize("fallback", [False, True])
def test_snapshot_preserves_webview_and_nonstandard_bare_ids_without_fabricated_package(monkeypatch: pytest.MonkeyPatch, fallback: bool) -> None:
    session = FakeSession(
        observation=_tree_observation(
            roots=(
                _tree_node(resource_id="url_bar", package="com.android.chrome"),
                _tree_node(
                    tag="android.webkit.WebView",
                    package="com.android.chrome",
                    children=(
                        _tree_node(
                            resource_id="taskColors",
                            package="com.android.chrome",
                            children=(
                                _tree_node(tag="android.widget.EditText", resource_id="answer", package="com.android.chrome"),
                                _tree_node(tag="android.widget.Button", resource_id="button", package="com.android.chrome"),
                            ),
                        ),
                    ),
                ),
                _tree_node(resource_id="slider", package="com.android.systemui"),
                _tree_node(resource_id="customBare", package="com.example.other"),
            ),
        ),
    )
    driver = AppiumConnectAndroidDriver(app_id="com.android.chrome", session=session)
    if fallback:

        def fail_compaction(attributes: dict[str, str]) -> bool:
            raise RuntimeError("local compaction failure")

        monkeypatch.setattr(driver, "_has_ui_snapshot_signal", fail_compaction)
    try:
        snapshot = driver.ui_snapshot({})
    finally:
        driver.close()
    root = ElementTree.fromstring(str(snapshot["xml"]))  # noqa: S314
    assert [node.attrib["resource-id"] for node in root.iter() if "resource-id" in node.attrib] == ["url_bar", "taskColors", "answer", "button", "slider", "customBare"]
    assert snapshot["compaction"]["applied"] is not fallback


def test_compound_short_id_uses_node_package_instead_of_session_or_driver_package() -> None:
    session = FakeSession(target_app_id="com.android.chrome")
    driver = AppiumConnectAndroidDriver(app_id="com.example.other", session=session)
    try:
        assert driver.tap_on({"locator": {"resourceId": " answer ", "className": "android.widget.EditText"}})["status"] == "passed"
    finally:
        driver.close()
    assert ("element", "xpath", "//*[(@resource-id=concat(@package, ':id/answer') or @resource-id='answer') and @class='android.widget.EditText']", False) in session.calls


@pytest.mark.parametrize("app_id", [None, "com.dimowner.audiorecorder", "com.android.settings"])
@pytest.mark.parametrize("target_app_id", [None, "com.android.settings"])
@pytest.mark.parametrize("local_id", ["slider", "url_bar", "answer"])
def test_short_id_resolution_covers_other_package_windows_without_bound_app(app_id: str | None, target_app_id: str | None, local_id: str) -> None:
    session = FakeSession(target_app_id=target_app_id)
    driver = AppiumConnectAndroidDriver(app_id=app_id, session=session)
    try:
        assert driver.tap_on({"locator": {"resourceId": local_id}})["status"] == "passed"
    finally:
        driver.close()
    assert ("element", "xpath", f"//*[(@resource-id=concat(@package, ':id/{local_id}') or @resource-id='{local_id}')]", False) in session.calls


@pytest.mark.parametrize("stage", ["binding", "post"])
def test_no_id_geometric_inspection_preserves_cancellation(stage: str) -> None:
    session = FakeSession(text="First name")
    session.element_attrs["id"] = ("missing", None)
    if stage == "binding":
        session.binding_code = "operation_cancelled"
    else:
        session.post_write_inspect_code = "operation_cancelled"
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        with pytest.raises(CancelledError):
            driver.input_text({"target": "First name", "text": "private-canary"})
    finally:
        driver.close()
    assert sum(call[0] == "replace_element_value" for call in session.calls) == (0 if stage == "binding" else 1)


_NUMERIC_ATTRIBUTES = ("type", "id", "package", "enabled", "focused", "password", "text", "bounds")


def _numeric_command_error(code: str = "command_failed", state: str = "response_received") -> BaseException:
    from appium_connect_agent.automation import CommandError as SDKCommandError

    return SDKCommandError(code, "operation-private-canary", state)


class NumericElement(FakeElement):
    def inspect(self, *, attributes=(), wait_timeout_seconds=None, control=None):
        session = self.session
        observation = super().inspect(attributes=attributes, wait_timeout_seconds=wait_timeout_seconds, control=control)
        overrides = {}
        if attributes == _NUMERIC_ATTRIBUTES:
            session.recovery_reads += 1
            session.recovery_controls.append(control.timeout_seconds)
            overrides.update(session.recovery_overrides.get(session.recovery_reads, {}))
            if session.recovery_reads in session.inspect_errors:
                raise session.inspect_errors[session.recovery_reads]
        elif session.cleared or session.ordinary_success:
            overrides.update(session.final_overrides)
        attrs = {attr.name: attr for attr in observation.attributes}
        attrs.update({name: _attr_obs(name, value, state) for name, (state, value) in overrides.items()})
        return SimpleNamespace(attributes=tuple(attrs.values()))

    def click(self, *, wait_timeout_seconds=15.0, control=None):
        session = self.session
        super().click(wait_timeout_seconds=wait_timeout_seconds, control=control)
        session.actions.append(("click", self, wait_timeout_seconds, control.timeout_seconds))
        if session.click_error is not None:
            raise session.click_error
        if session.cleared:
            session.element_attrs["focused"] = ("value", session.refocus)
            if session.refocus_text is not None:
                session.text = session.refocus_text

    def replace_element_value(self, text, *, wait_timeout_seconds=15.0, control=None):
        session = self.session
        session.calls.append(("replace_element_value", self.by, self.value, self.raw_id, text))
        session.actions.append(("replace", self, wait_timeout_seconds, control.timeout_seconds))
        if session.replace_error is not None:
            raise session.replace_error
        session.text = text

    def clear(self, *, wait_timeout_seconds=5.0, control=None):
        session = self.session
        session.actions.append(("clear", self, wait_timeout_seconds, control.timeout_seconds))
        if session.clear_error is not None:
            raise session.clear_error
        session.cleared = True
        session.text = session.baseline
        session.element_attrs["focused"] = session.clear_focus


class NumericDevice(FakeDevice):
    def press_keycode(self, keycode, *, control=None):
        session = self.session
        session.calls.append(("press_keycode", keycode))
        session.actions.append(("key", keycode, control.timeout_seconds))
        if len([action for action in session.actions if action[0] == "key"]) == session.fail_key:
            raise session.key_error
        session.typed += {69: "-", 56: "."}.get(keycode, str(keycode - 7))
        session.text = session.typed + (", " + session.baseline if session.composed and session.baseline else "")


class NumericSession(FakeSession):
    def __init__(self):
        super().__init__(text="original-private-canary")
        self.device = NumericDevice(self)
        self.actions = []
        self.recovery_reads = 0
        self.recovery_controls = []
        self.recovery_overrides = {}
        self.inspect_errors = {}
        self.final_overrides = {}
        self.replace_error = _numeric_command_error()
        self.click_error = None
        self.clear_error = None
        self.key_error = _numeric_command_error(state="response_unknown")
        self.fail_key = None
        self.cleared = False
        self.ordinary_success = False
        self.clear_focus = ("value", True)
        self.refocus = True
        self.refocus_text = None
        self.baseline = "placeholder-private-canary"
        self.typed = ""
        self.composed = True

    def element(self, by, value, *, raw_id=False):
        super().element(by, value, raw_id=raw_id)
        return NumericElement(self, by, value, raw_id=raw_id)


def _numeric_input(session, text="20250", *, no_id=False):
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        locator = {"className": "android.widget.EditText"} if no_id else {"resourceId": "com.example.app:id/input"}
        return driver.input_text({"text": text, "locator": locator})
    finally:
        driver.close()


def _numeric_action_names(session):
    return [action[0] for action in session.actions]


def _assert_numeric_secret_safe(result, session):
    secrets = (session.baseline, "original-private-canary", "operation-private-canary", "observed-private-canary")
    assert all(secret not in str(result) for secret in secrets if secret)


@pytest.mark.parametrize("baseline", ["", "placeholder-private-canary"])
@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("text,keycodes", [("20250", [9, 7, 9, 12, 7]), ("-12.5", [69, 8, 9, 56, 12]), ("", [])])
def test_numeric_recovery_clears_once_and_verifies_exact_or_placeholder_composed_readback(composed, text, keycodes, baseline):
    session = NumericSession()
    session.composed = composed
    session.baseline = baseline
    result = _numeric_input(session, text)
    assert result == {"status": "passed", "output": None}
    assert _numeric_action_names(session) == ["click", "replace", "clear"] + ["key"] * len(keycodes)
    assert [action[1] for action in session.actions if action[0] == "key"] == keycodes
    assert all(action[2] == 10.0 for action in session.actions if action[0] == "key")
    assert session.actions[2][2:] == (5.0, 15.0)
    assert session.actions[0][1] is session.actions[1][1] is session.actions[2][1]
    assert session.actions[2][1].by == "id"
    assert session.actions[2][1].value == "com.example.app:id/input"
    assert session.recovery_reads == 2
    assert session.recovery_controls == [25.0] * session.recovery_reads
    expected_text = text + (", " + baseline if composed and baseline else "") if text else baseline
    assert session.text == expected_text
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("recovered", [False, True])
def test_idle_liveness_preserves_controlled_numeric_recovery_without_extra_writes(android_clock, recovered):
    session = NumericSession()
    session.alive_result = recovered
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        assert driver.assert_state({"element": {"resourceId": "com.example.app:id/input"}})["status"] == "passed"
        android_clock.now = 45.0
        result = driver.input_text({"text": "20250", "locator": {"resourceId": "com.example.app:id/input"}})
        assert result == {"status": "passed", "output": None}
        assert _numeric_action_names(session) == ["click", "replace", "clear"] + ["key"] * 5
        assert [action[1] for action in session.actions if action[0] == "key"] == [9, 7, 9, 12, 7]
        assert sum(call[0] == "ensure_alive" for call in session.calls) == 1
        _assert_numeric_secret_safe(result, session)
    finally:
        driver.close()


@pytest.mark.parametrize("text", ["hello", "\uff11\uff12", "\u0661\u0662", "12 3", "+12", "12\n"])
def test_numeric_recovery_never_handles_other_input_characters(text):
    session = NumericSession()
    result = _numeric_input(session, text)
    assert result["status"] == "failed"
    assert _numeric_action_names(session) == ["click", "replace"]
    assert session.recovery_reads == 0
    assert text not in str(result)
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("failure", ["response_unknown", "not_sent", "wrong_code", "wrong_type", "missing_state"])
def test_numeric_recovery_requires_received_public_sdk_command_rejection(failure):
    session = NumericSession()
    if failure in {"response_unknown", "not_sent"}:
        session.replace_error = _numeric_command_error(state=failure)
    elif failure == "wrong_code":
        session.replace_error = _numeric_command_error(code="operation_timeout")
    elif failure == "wrong_type":
        session.replace_error = RuntimeError("observed-private-canary")
        session.replace_error.code = "command_failed"
        session.replace_error.request_state = "response_received"
    else:
        from appium_connect_agent.automation import CommandError as SDKCommandError

        class MissingStateCommandError(SDKCommandError):
            def __getattribute__(self, name):
                if name == "request_state":
                    raise AttributeError(name)
                return super().__getattribute__(name)

        session.replace_error = MissingStateCommandError("command_failed", "operation-private-canary", "response_received")
    result = _numeric_input(session)
    assert result["status"] == "failed"
    assert _numeric_action_names(session) == ["click", "replace"]
    assert session.recovery_reads == 0
    _assert_numeric_secret_safe(result, session)


def test_numeric_recovery_does_not_handle_click_rejection_or_ordinary_composed_readback():
    session = NumericSession()
    session.click_error = _numeric_command_error()
    result = _numeric_input(session)
    assert result["status"] == "failed"
    assert _numeric_action_names(session) == ["click"]
    assert session.recovery_reads == 0

    session = NumericSession()
    session.replace_error = None
    session.ordinary_success = True
    session.final_overrides = {"text": ("value", "20250, " + session.baseline)}
    result = _numeric_input(session)
    assert result["metadata"]["reason"] == "entry_mismatch"
    assert _numeric_action_names(session) == ["click", "replace"]
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("read", [1, 2])
@pytest.mark.parametrize(
    "field,observation",
    [
        ("type", ("value", "android.widget.AutoCompleteTextView")),
        ("id", ("value", "other:id/input")),
        ("package", ("value", "other.package")),
        ("enabled", ("value", False)),
        ("enabled", ("unknown", None)),
        ("password", ("value", True)),
        ("password", ("missing", None)),
        ("text", ("unknown", None)),
    ],
)
def test_numeric_recovery_unprovable_target_stops_before_next_side_effect(read, field, observation):
    session = NumericSession()
    session.recovery_overrides[read] = {field: observation}
    result = _numeric_input(session)
    assert result["metadata"]["reason"] == "verification_unavailable"
    assert result["metadata"]["stage"] == ("numeric_clear" if read == 1 else "numeric_focus")
    assert _numeric_action_names(session) == ["click", "replace"] + ([] if read == 1 else ["clear"])
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("focus", [("value", False), ("unknown", None), ("value", 1)])
@pytest.mark.parametrize("outcome", ["success", "unfocused", "baseline_changed", "identity_changed"])
def test_numeric_recovery_refocuses_at_most_once_and_proves_baseline_and_target(focus, outcome, numeric_focus_clock):
    session = NumericSession()
    session.clear_focus = focus
    session.refocus = outcome != "unfocused"
    if outcome == "baseline_changed":
        session.refocus_text = "observed-private-canary"
    if outcome == "identity_changed":
        session.recovery_overrides[3] = {"id": ("value", "other:id/input")}
    result = _numeric_input(session, "2")
    expected = ["click", "replace", "clear", "click"] + (["key"] if outcome == "success" else [])
    assert _numeric_action_names(session) == expected
    assert session.actions[0][1] is session.actions[3][1]
    assert session.actions[0][2:] == session.actions[3][2:]
    if outcome == "success":
        assert result["status"] == "passed"
    else:
        assert result["metadata"]["reason"] == "verification_unavailable"
        assert result["metadata"]["stage"] == "numeric_focus"
    _assert_numeric_secret_safe(result, session)


def test_numeric_recovery_dispatches_contiguous_keys_without_per_character_inspection():
    session = NumericSession()
    result = _numeric_input(session)
    assert result["status"] == "passed"
    key_positions = [index for index, call in enumerate(session.calls) if call[0] == "press_keycode"]
    assert len(key_positions) == 5
    assert key_positions == list(range(key_positions[0], key_positions[0] + 5))
    assert session.recovery_reads == 2
    assert _numeric_action_names(session) == ["click", "replace", "clear"] + ["key"] * 5
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("read", [1, 2])
@pytest.mark.parametrize("no_id", [False, True])
def test_numeric_recovery_geometry_is_mandatory_only_without_real_resource_identity(read, no_id):
    session = NumericSession()
    if no_id:
        session.element_attrs["id"] = ("missing", None)
    session.recovery_overrides[read] = {"bounds": ("value", _bounds(10, 10, 110, 60))}
    result = _numeric_input(session, "2", no_id=no_id)
    if no_id:
        assert result["metadata"]["reason"] == "verification_unavailable"
        assert "key" not in _numeric_action_names(session)
    else:
        assert result["status"] == "passed"
        assert _numeric_action_names(session) == ["click", "replace", "clear", "key"]
    for action in session.actions:
        if action[0] in {"click", "replace", "clear"}:
            assert action[1] is session.actions[0][1]


@pytest.mark.parametrize("stage", ["preclear", "clear", "after_clear", "key"])
@pytest.mark.parametrize("cancel", [False, True])
def test_numeric_recovery_errors_and_cancellation_stop_without_repeats(stage, cancel):
    session = NumericSession()
    error = CancelledError("observed-private-canary") if cancel else _numeric_command_error(state="response_unknown")
    if stage == "preclear":
        session.inspect_errors[1] = error
    elif stage == "clear":
        session.clear_error = error
    elif stage == "after_clear":
        session.inspect_errors[2] = error
    else:
        session.fail_key = 2
        session.key_error = error
    if cancel:
        with pytest.raises(CancelledError):
            _numeric_input(session)
    else:
        result = _numeric_input(session)
        assert result["status"] == "failed"
        assert result["metadata"]["stage"] == ("numeric_keycode" if stage == "key" else "numeric_focus" if stage == "after_clear" else "numeric_clear")
        assert result["metadata"]["reason"] == ("verification_unavailable" if stage in {"preclear", "after_clear"} else "entry_backend_error")
        assert result["metadata"]["request_state"] == "response_unknown"
        assert "inspect" in str(result).lower()
        _assert_numeric_secret_safe(result, session)
    assert _numeric_action_names(session) == ["click", "replace"] + ([] if stage == "preclear" else ["clear"]) + (["key", "key"] if stage == "key" else [])
    if stage == "key":
        assert [action[1] for action in session.actions if action[0] == "key"] == [9, 7]


@pytest.mark.parametrize("observed", ["20250, wrong-private-canary", "20250 ", "2025", "020250", "20,250", "20250, ", "observed-private-canary"])
def test_numeric_recovery_rejects_all_other_readback_formats_without_secret_leaks(observed):
    session = NumericSession()
    session.final_overrides["text"] = ("value", observed)
    result = _numeric_input(session)
    assert result["metadata"]["reason"] == "entry_mismatch"
    assert _numeric_action_names(session) == ["click", "replace", "clear"] + ["key"] * 5
    assert observed not in str(result)
    assert "20250" not in str(result)
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("no_id", [False, True])
@pytest.mark.parametrize(
    "field,observation",
    [
        ("type", ("value", "android.widget.AutoCompleteTextView")),
        ("id", ("value", "other:id/input")),
        ("package", ("value", "other.package")),
        ("enabled", ("value", False)),
        ("password", ("unknown", None)),
        ("text", ("missing", None)),
        ("bounds", ("value", _bounds(10, 10, 110, 60))),
    ],
)
def test_numeric_recovery_final_verification_still_requires_safe_original_target(no_id, field, observation):
    session = NumericSession()
    if no_id:
        session.element_attrs["id"] = ("missing", None)
    session.final_overrides[field] = observation
    result = _numeric_input(session, no_id=no_id)
    assert _numeric_action_names(session) == ["click", "replace", "clear"] + ["key"] * 5
    if field == "bounds" and not no_id:
        assert result["status"] == "passed"
    else:
        assert result["metadata"]["reason"] == "verification_unavailable"
        assert result["metadata"]["stage"] == "post_verification"
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("cancel", [False, True])
def test_numeric_recovery_failed_refocus_click_never_enters_key_dispatch(cancel):
    session = NumericSession()
    session.clear_focus = ("value", False)
    error = CancelledError("observed-private-canary") if cancel else _numeric_command_error(state="response_unknown")

    class RefocusFailingElement(NumericElement):
        def click(self, **kwargs):
            if self.session.cleared:
                self.session.click_error = error
            super().click(**kwargs)

    def element(by, value, *, raw_id=False):
        return RefocusFailingElement(session, by, value, raw_id=raw_id)

    session.element = element
    if cancel:
        with pytest.raises(CancelledError):
            _numeric_input(session)
    else:
        result = _numeric_input(session)
        assert result["status"] == "failed"
        assert result["metadata"]["stage"] == "numeric_focus"
        assert result["metadata"]["reason"] == "entry_backend_error"
        assert result["metadata"]["request_state"] == "response_unknown"
        _assert_numeric_secret_safe(result, session)
    assert _numeric_action_names(session) == ["click", "replace", "clear", "click"]


@pytest.mark.parametrize("read", [1, 2, 3])
@pytest.mark.parametrize("code", ["target_not_found", "target_ambiguous"])
def test_numeric_recovery_thrown_identity_proof_is_verification_unavailable(read: int, code: str) -> None:
    from appium_connect_agent.automation import TargetError as SDKTargetError

    session = NumericSession()
    if read == 3:
        session.clear_focus = ("value", False)
    session.inspect_errors[read] = SDKTargetError(code, "operation-private-canary", "response_received")
    result = _numeric_input(session)
    assert result["status"] == "failed"
    assert result["metadata"]["reason"] == "verification_unavailable"
    assert result["metadata"]["stage"] == ("numeric_clear" if read == 1 else "numeric_focus")
    assert result["metadata"]["backend_code"] == code
    assert result["metadata"]["request_state"] == "response_received"
    assert _numeric_action_names(session) == ["click", "replace"] + (["clear"] if read >= 2 else []) + (["click"] if read == 3 else [])
    assert session.recovery_reads == read
    assert "Inspect UI state before re-entering" in str(result)
    _assert_numeric_secret_safe(result, session)


@pytest.fixture
def numeric_focus_clock(monkeypatch):
    import fsq_agent.drivers.android._appium_connect as implementation

    clock = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(implementation, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    return clock


@pytest.mark.parametrize("focus", [("value", False), ("unknown", None)])
def test_numeric_recovery_waits_for_async_focus_without_repeating_click(focus, numeric_focus_clock):
    session = NumericSession()
    session.clear_focus = focus
    session.recovery_overrides[3] = {"focused": focus}
    result = _numeric_input(session, "7")
    assert result["status"] == "passed"
    assert _numeric_action_names(session) == ["click", "replace", "clear", "click", "key"]
    assert session.typed == "7"
    assert numeric_focus_clock.sleeps == [0.25]
    assert session.recovery_controls == [25.0] * 4
    _assert_numeric_secret_safe(result, session)


def test_numeric_recovery_focus_window_expires_without_extra_reads_or_keys(numeric_focus_clock):
    session = NumericSession()
    session.clear_focus = ("value", False)
    session.refocus = False
    result = _numeric_input(session, "7")
    assert result["metadata"]["stage"] == "numeric_focus"
    assert result["metadata"]["reason"] == "verification_unavailable"
    assert _numeric_action_names(session) == ["click", "replace", "clear", "click"]
    assert numeric_focus_clock.now == 2.0
    assert numeric_focus_clock.sleeps == [0.25] * 8
    assert session.recovery_reads == 10
    assert session.recovery_controls == [25.0] * 10
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("change", [{"text": ("value", "observed-private-canary")}, {"id": ("value", "other:id/input")}, {"password": ("value", True)}, {"enabled": ("value", False)}])
def test_numeric_recovery_aborts_changed_target_during_focus_wait(change, numeric_focus_clock):
    session = NumericSession()
    session.clear_focus = ("value", False)
    session.recovery_overrides[3] = {"focused": ("value", False)}
    session.recovery_overrides[4] = change
    result = _numeric_input(session, "7")
    assert result["metadata"]["stage"] == "numeric_focus"
    assert result["metadata"]["reason"] == "verification_unavailable"
    assert _numeric_action_names(session) == ["click", "replace", "clear", "click"]
    assert numeric_focus_clock.sleeps == [0.25]
    _assert_numeric_secret_safe(result, session)


@pytest.mark.parametrize("focused", [False, True])
def test_numeric_recovery_allows_normal_budget_for_inflight_focus_read(focused, numeric_focus_clock):
    session = NumericSession()
    session.clear_focus = ("value", False)

    class SlowFocusElement(NumericElement):
        def inspect(self, **kwargs):
            if kwargs.get("attributes") == _NUMERIC_ATTRIBUTES and self.session.recovery_reads >= 2:
                assert kwargs["control"].timeout_seconds == 25.0
                numeric_focus_clock.now += 3.0
            return super().inspect(**kwargs)

    session.refocus = focused
    session.element = lambda by, value, raw_id=False: SlowFocusElement(session, by, value, raw_id=raw_id)
    result = _numeric_input(session, "7")
    assert _numeric_action_names(session) == ["click", "replace", "clear", "click"] + (["key"] if focused else [])
    assert result["status"] == ("passed" if focused else "failed")
    assert session.recovery_reads == 3
    assert numeric_focus_clock.sleeps == []
    _assert_numeric_secret_safe(result, session)


def test_numeric_recovery_cancelled_focus_poll_never_dispatches_keys(numeric_focus_clock):
    session = NumericSession()
    session.clear_focus = ("value", False)
    session.recovery_overrides[3] = {"focused": ("value", False)}
    interruption = CancelledError("observed-private-canary")
    session.inspect_errors[4] = interruption
    with pytest.raises(CancelledError) as caught:
        _numeric_input(session, "7")
    assert caught.value is interruption
    assert _numeric_action_names(session) == ["click", "replace", "clear", "click"]
    assert numeric_focus_clock.sleeps == [0.25]


@pytest.mark.parametrize(
    "key,keycode",
    [
        ("MediaPlayPause", 85),
        ("media_play_pause", 85),
        ("KEYCODE_MEDIA_PLAY_PAUSE", 85),
        ("mEdIaPlAy", 126),
        ("MEDIA_PLAY", 126),
        ("MediaPause", 127),
        ("media_pause", 127),
        ("MediaStop", 86),
        ("media_stop", 86),
        ("Space", 62),
    ],
)
def test_media_keys_send_one_public_key_event(key: str, keycode: int) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.press_key({"key": key})
    finally:
        driver.close()
    assert result["status"] == "passed"
    assert session.calls == [("press_keycode", keycode)]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": {"equals": "private-canary"}},
        {"element": None},
        {"element": {}},
        {"element": {"enabled": False}},
        {"element": {"resourceId": " \t", "checked": True}},
        {"element": {"xpath": " "}, "text": {"contains": "private-canary"}},
    ],
)
def test_direct_assertion_rejects_unlocated_requests_before_any_sdk_use(payload: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    monkeypatch.setattr(session, "session_info", lambda: pytest.fail("Malformed assertion reached the SDK"))
    try:
        result = driver.assert_state(payload)
    finally:
        driver.close()
    assert result["failure_category"] == "configuration_error"
    assert session.calls == []
    assert "private-canary" not in str(result)


@pytest.mark.parametrize(
    "payload,attributes,expected",
    [
        ({"element": {"resourceId": "input", "enabled": False}, "text": {"equals": "Loaded"}}, ("text",), {"text": "Loaded"}),
        ({"element": {"resourceId": "input", "focused": False, "checked": False, "enabled": False}}, ("enabled", "checked", "focused"), {"enabled": False, "checked": False, "focused": False}),
        ({"element": {"resourceId": "input"}}, ("enabled",), {"exists": True}),
    ],
)
def test_located_assertions_read_only_active_predicates(payload: dict[str, object], attributes: tuple[str, ...], expected: dict[str, object]) -> None:
    session = FakeSession()
    session.element_attrs["enabled"] = ("value", False)
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        result = driver.assert_state(payload)
    finally:
        driver.close()
    assert result == {"status": "passed", "output": expected}
    assert [call[-1] for call in session.calls if call[0] == "inspect"] == [attributes]


@pytest.mark.parametrize("method,stage,code", [("click", "pre_focus_click", "condition_timeout"), ("replace_element_value", "replace_value", "session_invalid")])
@pytest.mark.parametrize("operation_id", ["0123456789abcdef0123456789abcdef", "A" * 32, "a" * 31, "secret-private-canary", None])
def test_input_failure_preserves_only_safe_operation_id_and_actual_stage(method: str, stage: str, code: str, operation_id: object, monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    failure = CommandError(code, "message-private-canary")
    failure.operation_id = operation_id
    failure.request_state = "response_unknown"
    writes = []

    def fail(*args, **kwargs):
        writes.append(method)
        raise failure

    monkeypatch.setattr(FakeElement, method, fail)
    try:
        result = driver.input_text({"target": "Loaded", "text": "input-private-canary"})
    finally:
        driver.close()
    metadata = result["metadata"]
    assert metadata["stage"] == stage
    assert metadata["backend_code"] == code
    assert metadata["request_state"] == "response_unknown"
    if operation_id == "0123456789abcdef0123456789abcdef":
        assert metadata["operation_id"] == operation_id
    else:
        assert "operation_id" not in metadata
    assert writes == [method]
    assert not any(call[0] == "replace_element_value" for call in session.calls)
    assert "canary" not in str(result)


@pytest.mark.parametrize(
    "method,payload",
    [
        ("tap_on", {"target": "private-canary"}),
        ("assert_visible", {"target": "private-canary"}),
        ("assert_not_visible", {"target": "private-canary"}),
        ("assert_state", {"element": {"resourceId": "private-canary"}}),
        ("input_text", {"target": "private-canary", "text": "input-private-canary"}),
    ],
)
@pytest.mark.parametrize("code,state", [("target_not_found", "response_unknown"), ("target_ambiguous", "response_unknown"), ("condition_timeout", "response_unknown"), ("session_invalid", "not_sent")])
def test_unknown_or_invalid_target_operations_never_become_ordinary_absence(method: str, payload: dict[str, object], code: str, state: str, monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    failure = TargetError(code, "message-private-canary")
    failure.request_state = state
    failure.operation_id = "0123456789abcdef0123456789abcdef"
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise failure

    monkeypatch.setattr(FakeElement, "inspect", fail)
    monkeypatch.setattr(FakeElement, "click", fail)
    try:
        result = getattr(driver, method)(payload)
    finally:
        driver.close()
    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["backend_code"] == code
    assert result["metadata"]["request_state"] == state
    assert result["metadata"]["operation_id"] == failure.operation_id
    assert len(calls) == 1
    assert "canary" not in str(result)


@pytest.mark.parametrize("state", ["uncertain", "disposing", "disposed", "closed"])
def test_nonactive_session_is_not_target_absence_or_implicitly_rebuilt(state: str, monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    monkeypatch.setattr(session, "session_info", lambda: SimpleNamespace(state=state))
    try:
        result = driver.assert_not_visible({"target": "private-canary"})
    finally:
        driver.close()
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["reason"] == "session_unavailable"
    assert result["metadata"]["session_state"] == state
    assert session.calls == []


@contextmanager
def _sdk_target_peer():
    # The real SDK proves cardinality, selected-attribute requests, and write
    # boundaries. This controlled peer supplies candidates; it is not an XPath evaluator.
    from appium_connect_agent.automation import create_session
    from appium_connect_agent.runtime import LocalSettings, Runtime

    state = SimpleNamespace(candidate_ids=["first"], calls=[], mutations=[], disconnect=False, dead_probes=0, dead_operations=0, sessions=0)
    state.nodes = {
        element_id: {
            "class": "android.widget.EditText",
            "resource-id": f"com.example.app:id/{element_id}",
            "package": "com.example.app",
            "text": "Match",
            "enabled": True,
            "displayed": True,
            "password": False,
            "checked": False,
            "checkable": True,
            "focused": False,
            "selected": False,
            "clickable": True,
        }
        for element_id in ("first", "second")
    }

    class Handler(BaseHTTPRequestHandler):
        def respond(self, value, status=200):
            body = json.dumps({"value": value}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state.calls.append(("GET", self.path, None))
            if self.path == "/status":
                self.respond({"ready": True, "build": {"version": "3.7.0"}})
                return
            if self.path == "/session/target-session/appium/settings":
                if state.dead_probes:
                    state.dead_probes -= 1
                    self.respond({"error": "invalid session id", "message": "isolated remote Session expired", "stacktrace": ""}, 404)
                else:
                    self.respond({})
                return
            parts = self.path.split("/")
            if len(parts) >= 6 and parts[3] == "element" and parts[4] in state.nodes:
                node = state.nodes[parts[4]]
                if parts[5] == "attribute":
                    self.respond(node.get(parts[6]))
                elif parts[5] == "rect":
                    self.respond({"x": 0, "y": 0, "width": 100, "height": 50})
                else:
                    self.respond(node[parts[5]])
                return
            self.send_error(404)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            state.calls.append(("POST", self.path, payload))
            if self.path == "/session":
                state.sessions += 1
                self.respond({"sessionId": "target-session", "capabilities": {"platformName": "Android", "appium:automationName": "UiAutomator2"}})
            elif self.path == "/session/target-session/elements":
                if state.disconnect:
                    self.connection.shutdown(2)
                    self.connection.close()
                    return
                if state.dead_operations:
                    state.dead_operations -= 1
                    self.respond({"error": "invalid session id", "message": "isolated remote Session expired", "stacktrace": ""}, 404)
                    return
                candidates = state.candidate_ids if payload["using"] == "xpath" else [element_id for element_id, node in state.nodes.items() if node["resource-id"] == payload["value"]]
                self.respond([{"element-6066-11e4-a52e-4f735466cecf": element_id} for element_id in candidates])
            elif self.path == "/session/target-session/execute/sync":
                action = payload["script"]
                args = payload["args"][0]
                state.mutations.append((action, args["elementId"]))
                if action == "mobile: replaceElementValue":
                    state.nodes[args["elementId"]]["text"] = args["text"]
                self.respond(None)
            else:
                self.send_error(404)

        def do_DELETE(self):
            state.calls.append(("DELETE", self.path, None))
            self.respond(None)

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runtime = Runtime(LocalSettings(server_url=f"http://127.0.0.1:{server.server_port}"))
        with runtime.connect() as connection:
            session = create_session(connection, "target-test-device", app_id="com.example.app", auto_launch=False, no_reset=True)
            driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
            state.calls.clear()
            try:
                yield state, driver
            finally:
                driver.close()
                # External uncertainty retains its SDK reservation until process
                # exit; only the isolated child scenario may leave it uncertain.
                if session.session_info().state == "active":
                    session.quit()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def sdk_target_peer():
    with _sdk_target_peer() as peer:
        yield peer


@pytest.mark.parametrize("dead", [False, True])
def test_public_sdk_idle_probe_preserves_foreground_without_extra_business_dispatch(sdk_target_peer, android_clock, dead):
    state, driver = sdk_target_peer
    payload = {"element": {"resourceId": "com.example.app:id/first"}}
    assert driver.assert_state(payload)["status"] == "passed"
    state.calls.clear()
    state.dead_probes = int(dead)
    android_clock.now = 45.0
    assert driver.tap_on({"locator": payload["element"]})["status"] == "passed"
    assert state.sessions == (2 if dead else 1)
    assert state.calls[0][:2] == ("GET", "/session/target-session/appium/settings")
    assert sum(path.endswith("/appium/settings") for _, path, _ in state.calls) == 1
    assert sum(path.endswith("/elements") for _, path, _ in state.calls) == 1
    assert state.mutations == [("mobile: clickGesture", "first")]
    reconnects = [body["capabilities"]["alwaysMatch"] for method, path, body in state.calls if method == "POST" and path == "/session"]
    assert len(reconnects) == int(dead)
    if dead:
        caps = reconnects[0]
        assert caps["appium:noReset"] is True
        assert caps["appium:autoLaunch"] is False
        assert caps["appium:forceAppLaunch"] is False
        assert caps["appium:shouldTerminateApp"] is False
        assert caps["appium:newCommandTimeout"] == 900.0
        assert "appium:appPackage" not in caps
        assert "appium:appActivity" not in caps


@pytest.mark.parametrize("dead_responses", [1, 2])
def test_public_sdk_owns_one_shot_remote_dead_operation_recovery(sdk_target_peer, dead_responses):
    state, driver = sdk_target_peer
    state.dead_operations = dead_responses
    result = driver.tap_on({"locator": {"resourceId": "com.example.app:id/first"}})
    assert result["status"] == ("passed" if dead_responses == 1 else "failed")
    assert state.sessions == 2
    assert sum(path.endswith("/elements") for _, path, _ in state.calls) == 2
    assert not any(path.endswith("/appium/settings") for _, path, _ in state.calls)
    assert state.mutations == ([("mobile: clickGesture", "first")] if dead_responses == 1 else [])


@pytest.mark.parametrize("candidate_ids", [["first"], ["first", "second"]])
def test_public_sdk_requires_unique_candidate_before_input_side_effects(sdk_target_peer, candidate_ids):
    state, driver = sdk_target_peer
    state.candidate_ids = candidate_ids
    result = driver.input_text({"target": "Match", "text": "replacement-private-canary"})
    if len(candidate_ids) == 1:
        assert result["status"] == "passed"
        assert state.mutations == [("mobile: clickGesture", "first"), ("mobile: replaceElementValue", "first")]
        assert state.nodes["first"]["text"] == "replacement-private-canary"
        assert state.nodes["second"]["text"] == "Match"
    else:
        assert result["failure_category"] == "target_resolution_error"
        assert result["metadata"]["reason"] == "target_ambiguous"
        assert result["metadata"]["request_state"] == "response_received"
        assert len(result["metadata"]["operation_id"]) == 32
        assert state.mutations == []
        assert len(state.calls) == 1
    lookups = [payload for method, path, payload in state.calls if method == "POST" and path.endswith("/elements")]
    assert sum(payload["using"] == "xpath" for payload in lookups) == 1
    assert "canary" not in str(result)


@pytest.mark.parametrize("field", ["enabled", "displayed"])
def test_public_sdk_unusable_unique_input_candidate_never_falls_through_to_another(sdk_target_peer, field):
    state, driver = sdk_target_peer
    state.nodes["first"][field] = False
    result = driver.input_text({"target": "Match", "text": "replacement-private-canary"})
    assert result["metadata"]["reason"] == "invalid_input_target"
    assert state.mutations == []
    assert len([path for method, path, _ in state.calls if method == "POST" and path.endswith("/elements")]) == 1
    assert state.nodes["second"]["text"] == "Match"


@pytest.mark.parametrize(
    "payload,attribute_paths",
    [
        ({"element": {"resourceId": "com.example.app:id/first", "enabled": False}, "text": {"equals": "Match"}}, ["text"]),
        ({"element": {"resourceId": "com.example.app:id/first", "focused": False, "checked": False}}, ["attribute/checkable", "attribute/checked", "attribute/focused"]),
        ({"element": {"resourceId": "com.example.app:id/first"}}, ["attribute/enabled"]),
    ],
)
def test_public_sdk_located_assertion_fetches_only_required_attributes(sdk_target_peer, payload, attribute_paths):
    state, driver = sdk_target_peer
    result = driver.assert_state(payload)
    assert result["status"] == "passed"
    assert [path.split("/element/first/")[1] for method, path, _ in state.calls if method == "GET"] == attribute_paths
    assert len([path for method, path, _ in state.calls if method == "POST" and path.endswith("/elements")]) == 1
    assert state.mutations == []


def _exercise_unknown_target_response():
    with _sdk_target_peer() as (state, driver):
        state.disconnect = True
        result = driver.assert_not_visible({"target": "private-canary"})
        assert result["status"] == "failed"
        assert result["failure_category"] == "action_error"
        assert result["metadata"]["request_state"] == "response_unknown"
        assert result["metadata"]["session_state"] == "uncertain"
        assert len(result["metadata"]["operation_id"]) == 32
        before = len(state.calls)
        next_result = driver.assert_state({"element": {"resourceId": "com.example.app:id/first"}})
        assert next_result["failure_category"] == "action_error"
        assert next_result["metadata"]["reason"] == "session_unavailable"
        assert len(state.calls) == before
        assert state.mutations == []
        assert "canary" not in str(result)


def test_public_sdk_unknown_inspection_response_fences_session_without_action_or_reconnect():
    # An external connection cannot authorize terminal managed disposal. Keep
    # its intentionally quarantined Session and reservation inside one child.
    source = Path(__file__).resolve()
    completed = subprocess.run(  # noqa: S603 - Fixed interpreter and local helper; no shell or user input.
        [sys.executable, "-c", 'import runpy, sys; runpy.run_path(sys.argv[1])["_exercise_unknown_target_response"]()', str(source)],
        cwd=source.parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_public_sdk_explicit_locator_is_authoritative_over_ambiguous_bare_target(sdk_target_peer):
    state, driver = sdk_target_peer
    state.candidate_ids = ["first", "second"]
    result = driver.input_text({"target": "Match", "locator": {"resourceId": "com.example.app:id/second"}, "text": "replacement-private-canary"})
    assert result["status"] == "passed"
    assert state.nodes["first"]["text"] == "Match"
    assert state.nodes["second"]["text"] == "replacement-private-canary"
    assert state.mutations == [("mobile: clickGesture", "second"), ("mobile: replaceElementValue", "second")]


def test_public_sdk_zero_candidates_never_writes_or_falls_back_to_another(sdk_target_peer, monkeypatch):
    state, driver = sdk_target_peer
    state.candidate_ids = []
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS", 0.0)
    result = driver.input_text({"target": "Match", "text": "replacement-private-canary"})
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"]["reason"] == "target_missing"
    assert result["metadata"]["backend_code"] == "target_not_found"
    assert state.mutations == []
    assert len(state.calls) == 1
    assert "canary" not in str(result)


def test_public_sdk_condition_timeout_is_operational_not_absence(sdk_target_peer, monkeypatch):
    state, driver = sdk_target_peer
    state.nodes["first"]["enabled"] = False
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS", 0.01)
    result = driver.tap_on({"target": "Match"})
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["backend_code"] == "condition_timeout"
    assert result["metadata"]["request_state"] in {"not_sent", "response_received"}
    assert result["metadata"]["session_state"] == "active"
    assert state.mutations == []


@pytest.mark.parametrize(
    "method,payload",
    [
        ("tap_on", {}),
        ("tap_on", {"locator": {"resourceId": " "}}),
        ("long_press_on", {"target": " "}),
        ("assert_visible", {"locator": {"unknown": "private-canary"}}),
        ("assert_not_visible", {"target": 12}),
        ("input_text", {"target": "Match", "text": None}),
    ],
)
def test_direct_target_actions_reject_malformed_parameters_before_sdk(method, payload, monkeypatch):
    session = FakeSession()
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    monkeypatch.setattr(session, "session_info", lambda: pytest.fail("Malformed parameters reached the SDK"))
    try:
        result = getattr(driver, method)(payload)
    finally:
        driver.close()
    assert result["failure_category"] == "configuration_error"
    assert session.calls == []
    assert "canary" not in str(result)


def test_not_visible_duplicate_retains_safe_ambiguity_diagnostics(monkeypatch):
    session = FakeSession()
    session.inspect_code = "target_ambiguous"
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS", 0.0)
    try:
        result = driver.assert_not_visible({"target": "private-canary"})
    finally:
        driver.close()
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"] == {"reason": "target_ambiguous", "backend_code": "target_ambiguous", "session_state": "active"}
    assert "canary" not in str(result)


def test_public_sdk_invalid_attribute_response_keeps_safe_backend_code(sdk_target_peer):
    state, driver = sdk_target_peer
    state.nodes["first"]["enabled"] = "response-private-canary"
    result = driver.assert_state({"element": {"resourceId": "com.example.app:id/first", "enabled": True}})
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["backend_code"] == "source_invalid"
    assert result["metadata"]["request_state"] == "response_received"
    assert result["metadata"]["session_state"] == "active"
    assert state.mutations == []
    assert "canary" not in str(result)
