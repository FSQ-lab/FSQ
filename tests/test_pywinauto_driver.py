# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import ctypes
import os
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageGrab

from fsq_agent.drivers.windows import _pywinauto
from fsq_agent.drivers.windows._pywinauto import PW_RENDERFULLCONTENT, PywinautoWindowsDriver
from fsq_agent.models import (
    ConfigurationError,
    SampleColorParams,
    WindowsClickOnParams,
    WindowsDoubleClickOnParams,
    WindowsDragToParams,
    WindowsHoverOnParams,
    WindowsLaunchAppParams,
    WindowsPressKeyParams,
    WindowsRightClickOnParams,
    WindowsScrollOnParams,
    WindowsTypeTextParams,
)


class _DynamicRect:
    def __init__(self, state: dict[str, bool]) -> None:
        self._state = state

    def mid_point(self) -> tuple[int, int]:
        if self._state["iconic"]:
            return (-32000, -32000)
        return (240, 180)


class _FakeControlWrapper:
    def __init__(self, events: list[str], state: dict[str, bool]) -> None:
        self._events = events
        self._state = state

    def click_input(self, **kwargs: object) -> None:
        button = kwargs.get("button", "left")
        self._events.append(f"click:{button}:iconic={self._state['iconic']}:focused={self._state['focused']}")

    def double_click_input(self, **kwargs: object) -> None:
        button = kwargs.get("button", "left")
        self._events.append(f"double_click:{button}:iconic={self._state['iconic']}:focused={self._state['focused']}")

    def set_focus(self) -> None:
        self._events.append(f"control_set_focus:iconic={self._state['iconic']}:focused={self._state['focused']}")

    def type_keys(self, text: str, **kwargs: object) -> None:
        assert kwargs["set_foreground"] is False
        self._events.append(f"type_keys:{text}:iconic={self._state['iconic']}:focused={self._state['focused']}")

    def rectangle(self) -> _DynamicRect:
        self._events.append(f"rect:iconic={self._state['iconic']}:focused={self._state['focused']}")
        return _DynamicRect(self._state)


class _FakeControlSpec:
    def __init__(self, wrapper: _FakeControlWrapper) -> None:
        self._wrapper = wrapper

    def exists(self) -> bool:
        return True

    def wrapper_object(self) -> _FakeControlWrapper:
        return self._wrapper


class _FakeInteractiveWindow:
    def __init__(self, handle: int, process_id: int, events: list[str], state: dict[str, bool]) -> None:
        self.handle = handle
        self.process_id = process_id
        self._events = events
        self._state = state
        self._wrapper = _FakeControlWrapper(events, state)

    def wait(self, wait_for: str, timeout: float | None = None) -> _FakeInteractiveWindow:
        return self

    def set_focus(self) -> None:
        self._state["focused"] = True
        self._events.append(f"set_focus:{self.handle}:iconic={self._state['iconic']}")

    def child_window(self, **kwargs: object) -> _FakeControlSpec:
        self._events.append(f"child_window:iconic={self._state['iconic']}:focused={self._state['focused']}")
        return _FakeControlSpec(self._wrapper)

    def type_keys(self, key: str, **kwargs: object) -> None:
        assert kwargs["set_foreground"] is False
        self._events.append(f"window_type_keys:{key}:iconic={self._state['iconic']}:focused={self._state['focused']}")


class _FakeMouse:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def move(self, *, coords: tuple[int, int]) -> None:
        self._events.append(f"mouse_move:{coords}")

    def scroll(self, *, coords: tuple[int, int], wheel_dist: int) -> None:
        self._events.append(f"mouse_scroll:{coords}:{wheel_dist}")

    def press(self, *, coords: tuple[int, int], button: str) -> None:
        self._events.append(f"mouse_press:{coords}:{button}")

    def release(self, *, coords: tuple[int, int], button: str) -> None:
        self._events.append(f"mouse_release:{coords}:{button}")

    def click(self, *, coords: tuple[int, int], button: str = "left") -> None:
        self._events.append(f"mouse_click:{coords}:{button}")

    def double_click(self, *, coords: tuple[int, int], button: str = "left") -> None:
        self._events.append(f"mouse_double_click:{coords}:{button}")


def test_launch_app_restores_iconic_hwnd_and_sets_focus_after_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    state = {"iconic": True, "focused": False}
    hwnd = 4201
    pid = 9101
    exe_path = r"C:\Apps\Demo\demo.exe"
    window = _FakeInteractiveWindow(hwnd, pid, events, state)

    class _FakeApp:
        def __init__(self, backend: str = "uia") -> None:
            self.process = pid

        def start(self, cmd: str) -> _FakeApp:
            events.append("app_start")
            return self

        def connect(self, **kwargs: object) -> _FakeApp:
            return self

        def window(self, **kwargs: object) -> _FakeInteractiveWindow:
            return window

        def kill(self) -> None:
            events.append("app_kill")

    driver = PywinautoWindowsDriver(app_path=exe_path)
    monkeypatch.setattr(driver, "_application_cls", lambda: _FakeApp)
    monkeypatch.setattr(driver, "_snapshot_pre_launch_state", lambda _norm: (set(), set()))
    monkeypatch.setattr(driver, "_list_top_windows", lambda **kwargs: [window])
    monkeypatch.setattr(driver, "_process_executable_path", lambda _pid: exe_path)
    monkeypatch.setattr(driver, "_is_win32_window", lambda candidate: candidate == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda candidate: candidate == hwnd and state["iconic"])
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: hwnd if state["focused"] else 0)

    def _restore(candidate: int, *, activate: bool = True) -> None:
        assert candidate == hwnd
        assert activate is True
        state["iconic"] = False
        events.append(f"restore:{candidate}")

    monkeypatch.setattr(driver, "_restore_window", _restore)

    try:
        ctx_before = driver.context()
        assert ctx_before["metadata"]["app_launched"] is False
        result = driver.launch_app(WindowsLaunchAppParams())
        assert result["status"] == "passed"
        ctx_after = driver.context()
        assert ctx_after["metadata"]["app_launched"] is True
        assert events == [
            "app_start",
            f"restore:{hwnd}",
            f"set_focus:{hwnd}:iconic=False",
        ]
    finally:
        driver.close()


@pytest.mark.parametrize(
    ("action_label", "invoke", "expected_tail"),
    [
        (
            "click_on",
            lambda d: d.click_on(WindowsClickOnParams(target="Click Save", locator={"title": "Save"})),
            ["child_window:iconic=False:focused=True", "click:left:iconic=False:focused=True"],
        ),
        (
            "double_click_on",
            lambda d: d.double_click_on(WindowsDoubleClickOnParams(target="Open item", locator={"title": "Item"})),
            ["child_window:iconic=False:focused=True", "double_click:left:iconic=False:focused=True"],
        ),
        (
            "right_click_on",
            lambda d: d.right_click_on(WindowsRightClickOnParams(target="Context menu", locator={"title": "Item"})),
            ["child_window:iconic=False:focused=True", "click:right:iconic=False:focused=True"],
        ),
        (
            "type_text",
            lambda d: d.type_text(WindowsTypeTextParams(target="Search box", locator={"automation_id": "Search"}, text="fsq", clear=True)),
            [
                "child_window:iconic=False:focused=True",
                "click:left:iconic=False:focused=True",
                "control_set_focus:iconic=False:focused=True",
                "type_keys:^a{BACKSPACE}:iconic=False:focused=True",
                "type_keys:fsq:iconic=False:focused=True",
            ],
        ),
        (
            "press_key",
            lambda d: d.press_key(WindowsPressKeyParams(key="{ENTER}")),
            ["window_type_keys:{ENTER}:iconic=False:focused=True"],
        ),
        (
            "hover_on",
            lambda d: d.hover_on(WindowsHoverOnParams(target="Hover menu", locator={"title": "Menu"})),
            [
                "child_window:iconic=False:focused=True",
                "rect:iconic=False:focused=True",
                "mouse_move:(240, 180)",
            ],
        ),
        (
            "scroll_on",
            lambda d: d.scroll_on(WindowsScrollOnParams(target="Scroll list", locator={"automation_id": "List"}, wheel_dist=-3)),
            [
                "child_window:iconic=False:focused=True",
                "rect:iconic=False:focused=True",
                "mouse_scroll:(240, 180):-3",
            ],
        ),
        (
            "drag_to",
            lambda d: d.drag_to(
                WindowsDragToParams(
                    target="Drag item",
                    source={"locator": {"automation_id": "Card"}},
                    destination={"offset": {"x": 15, "y": 0}},
                )
            ),
            [
                "child_window:iconic=False:focused=True",
                "rect:iconic=False:focused=True",
                "mouse_press:(240, 180):left",
                "mouse_move:(255, 180)",
                "mouse_release:(255, 180):left",
            ],
        ),
    ],
)
@pytest.mark.parametrize("focus_locked", [False, True])
def test_all_eight_interactions_restore_minimized_window_and_focus_before_resolving_or_acting(
    monkeypatch: pytest.MonkeyPatch,
    action_label: str,
    invoke: Any,
    expected_tail: list[str],
    focus_locked: bool,
) -> None:
    events: list[str] = []
    state = {"iconic": True, "focused": False}
    hwnd = 5500
    window = _FakeInteractiveWindow(hwnd, 7700, events, state)
    mouse = _FakeMouse(events)

    driver = PywinautoWindowsDriver(window_title_re=".*Demo")
    driver._bound_handle = hwnd
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)
    monkeypatch.setattr(driver, "_is_win32_window", lambda candidate: candidate == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda candidate: candidate == hwnd and state["iconic"])
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: hwnd if state["focused"] else 0)

    def _blocked_focus() -> None:
        events.append("focus_locked")
        raise RuntimeError("SetForegroundWindow blocked by foreground lock")

    if focus_locked:
        monkeypatch.setattr(window, "set_focus", _blocked_focus)

    def _restore(candidate: int, *, activate: bool = True) -> None:
        assert candidate == hwnd
        assert activate is True
        state["iconic"] = False
        events.append(f"restore:{candidate}")

    monkeypatch.setattr(driver, "_restore_window", _restore)

    try:
        if focus_locked:
            with pytest.raises(RuntimeError, match="foreground lock"):
                invoke(driver)
            assert events == [f"restore:{hwnd}", "focus_locked"]
            return
        result = invoke(driver)
        assert result["status"] == "passed", f"{action_label} failed"
        assert events == [
            f"restore:{hwnd}",
            f"set_focus:{hwnd}:iconic=False",
            *expected_tail,
        ]
    finally:
        driver.close()


@pytest.mark.parametrize("focus_locked", [False, True])
def test_ensure_window_foreground_preserves_non_native_wrapper_behavior_without_swallowing_focus_errors(monkeypatch: pytest.MonkeyPatch, focus_locked: bool) -> None:
    calls: list[str] = []

    class _WrapperWindow:
        handle = 999

        def is_minimized(self) -> bool:
            return True

        def restore(self) -> None:
            calls.append("wrapper_restore")

        def set_focus(self) -> None:
            calls.append("wrapper_set_focus")
            if focus_locked:
                raise RuntimeError("SetForegroundWindow blocked by foreground lock")

    driver = PywinautoWindowsDriver(window_title_re=".*Wrapper")
    monkeypatch.setattr(driver, "_is_win32_window", lambda _hwnd: False)

    window = _WrapperWindow()
    try:
        if focus_locked:
            with pytest.raises(RuntimeError, match="foreground lock"):
                driver._ensure_window_foreground(window)
        else:
            assert driver._ensure_window_foreground(window) == (window, None)
        assert calls == ["wrapper_restore", "wrapper_set_focus"]
    finally:
        driver.close()


@pytest.mark.parametrize(
    ("owners", "foreground_pid", "allowed"),
    [
        ({7003: 7002, 7002: 7001}, 8800, True),
        ({7003: 0}, 8800, False),  # A same-PID sibling is not a popup of the bound HWND.
        ({7003: 7001}, 9900, False),  # Do not admit an owned window of another process.
        ({7003: 7002, 7002: 7003}, 8800, False),  # Inconsistent ownership cannot authorize input.
    ],
)
def test_press_key_requires_foreground_to_be_the_target_or_its_owned_popup_chain(monkeypatch: pytest.MonkeyPatch, owners: dict[int, int], foreground_pid: int, allowed: bool) -> None:
    hwnd, popup_hwnd = 7001, 7003
    events: list[str] = []
    window = _FakeInteractiveWindow(hwnd, 8800, events, {"iconic": False, "focused": False})
    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: popup_hwnd)
    monkeypatch.setattr(driver, "_window_process_id", lambda handle: foreground_pid if handle == popup_hwnd else 8800)
    monkeypatch.setattr(driver, "_window_owner_handle", owners.__getitem__)

    try:
        if allowed:
            assert driver.press_key(WindowsPressKeyParams(key="{ENTER}"))["status"] == "passed"
            assert events == [f"set_focus:{hwnd}:iconic=False", "window_type_keys:{ENTER}:iconic=False:focused=True"]
        else:
            with pytest.raises(RuntimeError, match="foreground window"):
                driver.press_key(WindowsPressKeyParams(key="{ENTER}"))
            assert events == [f"set_focus:{hwnd}:iconic=False"]
    finally:
        driver.close()


@pytest.mark.parametrize("focus_delay", [0.010, None], ids=["delayed-target", "permanent-same-pid-sibling"])
def test_press_key_waits_only_for_an_authorized_foreground_after_focus(monkeypatch: pytest.MonkeyPatch, focus_delay: float | None) -> None:
    hwnd, sibling = 7001, 7003
    events: list[str] = []
    now = 0.0
    window = _FakeInteractiveWindow(hwnd, 8800, events, {"iconic": False, "focused": False})
    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    driver._bound_handle = hwnd
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: hwnd if focus_delay is not None and now >= focus_delay else sibling)
    monkeypatch.setattr(driver, "_window_process_id", lambda _handle: 8800)
    monkeypatch.setattr(driver, "_window_owner_handle", lambda _handle: 0)

    def _sleep(seconds: float) -> None:
        nonlocal now
        assert events == [f"set_focus:{hwnd}:iconic=False"]
        assert 0 < seconds <= 0.010
        now += seconds
        assert now <= 0.250

    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now, sleep=_sleep))
    try:
        if focus_delay is None:
            with pytest.raises(RuntimeError, match="foreground window"):
                driver.press_key(WindowsPressKeyParams(key="{ENTER}"))
            assert now == pytest.approx(0.250)
            assert events == [f"set_focus:{hwnd}:iconic=False"]
        else:
            assert driver.press_key(WindowsPressKeyParams(key="{ENTER}"))["status"] == "passed"
            assert now == pytest.approx(focus_delay)
            assert events == [f"set_focus:{hwnd}:iconic=False", "window_type_keys:{ENTER}:iconic=False:focused=True"]
    finally:
        driver.close()


@pytest.mark.parametrize("observation_end", [0.250, 0.251], ids=["at-deadline", "past-deadline"])
def test_press_key_rejects_successful_foreground_observation_completed_at_or_after_deadline(monkeypatch: pytest.MonkeyPatch, observation_end: float) -> None:
    hwnd = 7001
    events: list[str] = []
    now = 0.0
    window = _FakeInteractiveWindow(hwnd, 8800, events, {"iconic": False, "focused": False})
    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    driver._bound_handle = hwnd
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_window_process_id", lambda _handle: None)

    def _foreground() -> int:
        nonlocal now
        if now == 0.0:
            return 0
        now = observation_end
        return hwnd

    def _sleep(seconds: float) -> None:
        nonlocal now
        now += seconds
        assert 0 < now < 0.250

    monkeypatch.setattr(driver, "_foreground_window_handle", _foreground)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now, sleep=_sleep))
    try:
        with pytest.raises(RuntimeError, match="foreground window"):
            driver.press_key(WindowsPressKeyParams(key="{ENTER}"))
        assert now == observation_end
        assert events == [f"set_focus:{hwnd}:iconic=False"]
    finally:
        driver.close()


@pytest.mark.parametrize(
    ("owner_error", "at_dispatch"),
    [(1400, False), (0, False), (5, False), (1400, True), (0, True)],
    ids=["invalid-hwnd", "invalid-hwnd-without-error", "other-winerror", "dispatch-invalid-hwnd", "dispatch-invalid-hwnd-without-error"],
)
def test_press_key_retries_disappearing_popup_only_during_foreground_wait(monkeypatch: pytest.MonkeyPatch, owner_error: int, at_dispatch: bool) -> None:
    hwnd, popup = 7001, 7002
    events: list[str] = []
    sleeps: list[float] = []
    native_error = OSError("GetWindow failed")
    native_error.winerror = owner_error
    window = _FakeInteractiveWindow(hwnd, 8800, events, {"iconic": False, "focused": False})
    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    driver._bound_handle = hwnd
    observations = iter([hwnd, popup, hwnd] if at_dispatch else [popup, hwnd, hwnd])
    popup_pid_read = False
    popup_closed = False

    def _get_pid(handle: int) -> int:
        nonlocal popup_pid_read
        popup_pid_read |= handle == popup
        return 8800

    def _get_window(handle: int, command: int) -> int:
        nonlocal popup_closed
        assert popup_pid_read
        assert (handle, command) == (popup, 4)
        popup_closed = True
        return 0

    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle == hwnd or (handle == popup and not popup_closed))
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: next(observations))
    monkeypatch.setattr(driver, "_window_process_id", _get_pid)
    monkeypatch.setattr(_pywinauto, "_get_window_api", lambda: _get_window)
    monkeypatch.setattr(ctypes, "set_last_error", lambda _error: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: owner_error, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda _error: native_error, raising=False)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: sum(sleeps), sleep=sleeps.append))
    try:
        if at_dispatch or owner_error == 5:
            if owner_error:
                with pytest.raises(OSError, match="GetWindow failed") as exc_info:
                    driver.press_key(WindowsPressKeyParams(key="{ENTER}"))
                assert exc_info.value is native_error
            else:
                with pytest.raises(RuntimeError, match="Window disappeared during Win32 popup ownership check"):
                    driver.press_key(WindowsPressKeyParams(key="{ENTER}"))
            assert not sleeps
            assert events == [f"set_focus:{hwnd}:iconic=False"]
        else:
            assert driver.press_key(WindowsPressKeyParams(key="{ENTER}"))["status"] == "passed"
            assert sleeps == [0.010]
            assert events == [f"set_focus:{hwnd}:iconic=False", "window_type_keys:{ENTER}:iconic=False:focused=True"]
    finally:
        driver.close()


@pytest.mark.parametrize("popup_at_dispatch", [False, True], ids=["unowned-sibling", "unstable-owned-popup"])
def test_press_key_checks_foreground_again_at_dispatch_without_waiting(monkeypatch: pytest.MonkeyPatch, popup_at_dispatch: bool) -> None:
    hwnd, popup, sibling = 7001, 7002, 7003
    events: list[str] = []
    observations = iter([hwnd, popup, sibling] if popup_at_dispatch else [hwnd, sibling])
    window = _FakeInteractiveWindow(hwnd, 8800, events, {"iconic": False, "focused": False})
    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    driver._bound_handle = hwnd
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: next(observations))
    monkeypatch.setattr(driver, "_window_process_id", lambda _handle: 8800)
    monkeypatch.setattr(driver, "_window_owner_handle", {popup: hwnd, sibling: 0}.__getitem__)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _seconds: pytest.fail("dispatch must not wait")))

    try:
        with pytest.raises(RuntimeError, match="foreground window"):
            driver.press_key(WindowsPressKeyParams(key="{ENTER}"))
        assert events == [f"set_focus:{hwnd}:iconic=False"]
    finally:
        driver.close()


def test_scroll_does_not_dispatch_after_focus_changes_during_control_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    hwnd = 7001
    events: list[str] = []
    state = {"iconic": False, "focused": False}
    window = _FakeInteractiveWindow(hwnd, 8800, events, state)
    original_child_window = window.child_window

    def _child_window(**kwargs: object) -> _FakeControlSpec:
        control = original_child_window(**kwargs)
        state["focused"] = False
        return control

    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    monkeypatch.setattr(window, "child_window", _child_window)
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_mouse_module", lambda: _FakeMouse(events))
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: hwnd if state["focused"] else 7003)
    monkeypatch.setattr(driver, "_window_process_id", lambda _handle: 8800)
    monkeypatch.setattr(driver, "_window_owner_handle", lambda _handle: 0)

    try:
        with pytest.raises(RuntimeError, match="foreground window"):
            driver.scroll_on(WindowsScrollOnParams(target="List", locator={"title": "List"}, wheel_dist=1))
        assert events == [
            f"set_focus:{hwnd}:iconic=False",
            "child_window:iconic=False:focused=True",
            "rect:iconic=False:focused=False",
        ]
    finally:
        driver.close()


@pytest.mark.parametrize(
    ("fg_hwnd", "is_iconic"),
    [
        (7001, False),  # The target itself is foreground.
        (7002, False),  # An owned popup is foreground.
        (7003, False),  # An unrelated same-process sibling is foreground.
        (9100, False),  # An unrelated process is foreground.
        (9100, True),  # The target is minimized and must be restored without activation.
    ],
)
def test_screenshot_uses_only_bound_hwnd_even_if_foreground_changes_during_capture(monkeypatch: pytest.MonkeyPatch, fg_hwnd: int, is_iconic: bool) -> None:
    target_hwnd = 7001
    target_pid = 8800
    events: list[str] = []
    iconic_state = {"iconic": is_iconic}
    foreground = [fg_hwnd]

    class _BoundWindow:
        handle = target_hwnd
        process_id = target_pid

        def capture_as_image(self) -> Image.Image:
            pytest.fail("A bound Win32 window must never be captured with a desktop grab.")

    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: _BoundWindow())
    monkeypatch.setattr(driver, "_is_win32_window", lambda hwnd: hwnd == target_hwnd)
    monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: pytest.fail("The driver must not grab desktop pixels."))
    monkeypatch.setattr(driver, "_is_window_iconic", lambda hwnd: hwnd == target_hwnd and iconic_state["iconic"])

    def _restore_no_activate(hwnd: int, *, activate: bool = True) -> None:
        assert foreground[0] == fg_hwnd
        events.append(f"restore:{hwnd}:activate={activate}")
        iconic_state["iconic"] = False

    def _reminimize_no_activate(hwnd: int) -> None:
        assert foreground[0] == 9999
        events.append(f"minimize_no_activate:{hwnd}")
        iconic_state["iconic"] = True

    monkeypatch.setattr(driver, "_restore_window", _restore_no_activate)
    monkeypatch.setattr(driver, "_minimize_window_no_activate", _reminimize_no_activate)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: foreground[0])
    monkeypatch.setattr(driver, "_window_process_id", lambda hwnd: target_pid if hwnd in (7001, 7002, 7003) else 4444)

    def _fake_print_window(hwnd: int) -> Image.Image:
        assert hwnd == target_hwnd
        assert iconic_state["iconic"] is False
        foreground[0] = 9999
        events.append(f"print_window:{hwnd}")
        return Image.new("RGB", (80, 60), (220, 40, 30))

    monkeypatch.setattr(driver, "_capture_window_via_print_window", _fake_print_window)

    try:
        png_bytes = driver.screenshot()
        assert foreground == [9999]
        if is_iconic:
            assert events == [
                f"restore:{target_hwnd}:activate=False",
                f"print_window:{target_hwnd}",
                f"minimize_no_activate:{target_hwnd}",
            ]
            assert iconic_state["iconic"] is True
        else:
            assert events == [f"print_window:{target_hwnd}"]
        decoded = Image.open(BytesIO(png_bytes))
        assert decoded.format == "PNG"
        assert decoded.size == (80, 60)
        assert decoded.getpixel((5, 5)) == (220, 40, 30)
    finally:
        driver.close()


@pytest.mark.parametrize("is_iconic", [False, True])
def test_screenshot_raises_when_print_window_fails_without_a_desktop_fallback(
    monkeypatch: pytest.MonkeyPatch,
    is_iconic: bool,
) -> None:
    target_hwnd = 7001
    iconic = is_iconic

    class _BoundWindow:
        handle = target_hwnd
        process_id = 8800

        def capture_as_image(self) -> Image.Image:
            pytest.fail("The driver must not grab desktop pixels on PrintWindow failure.")

    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: _BoundWindow())
    monkeypatch.setattr(driver, "_is_win32_window", lambda hwnd: hwnd == target_hwnd)
    monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: pytest.fail("The driver must not grab desktop pixels on PrintWindow failure."))
    monkeypatch.setattr(driver, "_window_process_id", lambda _hwnd: 8800)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _hwnd: iconic)

    def _restore(hwnd: int, *, activate: bool) -> None:
        nonlocal iconic
        assert hwnd == target_hwnd
        assert not activate
        iconic = False

    def _minimize(hwnd: int) -> None:
        nonlocal iconic
        assert hwnd == target_hwnd
        iconic = True

    def _fail_capture(hwnd: int) -> Image.Image:
        assert hwnd == target_hwnd
        assert not iconic
        raise RuntimeError(f"Win32 PrintWindow(PW_RENDERFULLCONTENT) failed for the bound window (hwnd={hwnd}).")

    monkeypatch.setattr(driver, "_restore_window", _restore)
    monkeypatch.setattr(driver, "_minimize_window_no_activate", _minimize)
    monkeypatch.setattr(driver, "_capture_window_via_print_window", _fail_capture)

    try:
        with pytest.raises(RuntimeError, match=r"PrintWindow\(PW_RENDERFULLCONTENT\) failed"):
            driver.screenshot()
        assert iconic == is_iconic
    finally:
        driver.close()


def test_screenshot_rejects_render_if_bound_handle_changes_ownership_during_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BoundWindow:
        handle = 7001

    owner_pid = 8800

    def _capture(hwnd: int) -> Image.Image:
        nonlocal owner_pid
        assert hwnd == _BoundWindow.handle
        owner_pid = 8801
        return Image.new("RGB", (16, 16), (1, 2, 3))

    driver = PywinautoWindowsDriver(window_title_re=".*Target")
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: _BoundWindow())
    monkeypatch.setattr(driver, "_is_win32_window", lambda hwnd: hwnd == _BoundWindow.handle)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _hwnd: False)
    monkeypatch.setattr(driver, "_window_process_id", lambda _hwnd: owner_pid)
    monkeypatch.setattr(driver, "_capture_window_via_print_window", _capture)
    try:
        with pytest.raises(RuntimeError, match="target disappeared or changed ownership"):
            driver.screenshot()
    finally:
        driver.close()


def test_screenshot_non_win32_fake_handle_preserves_wrapper_capture_and_missing_image_error() -> None:
    class _FakeHandleWindow:
        handle = 101  # Not a real Win32 HWND

        def __init__(self, image: Image.Image | None) -> None:
            self._image = image

        def capture_as_image(self) -> Image.Image | None:
            return self._image

    driver = PywinautoWindowsDriver(window_title_re=".*Fake")
    driver._resolve_main_window = lambda **kwargs: _FakeHandleWindow(Image.new("RGB", (16, 16), (1, 2, 3)))  # type: ignore[method-assign]
    try:
        png = driver.screenshot()
        assert Image.open(BytesIO(png)).size == (16, 16)

        driver._resolve_main_window = lambda **kwargs: _FakeHandleWindow(None)  # type: ignore[method-assign]
        with pytest.raises(ConfigurationError, match="Windows screenshot capture returned no image"):
            driver.screenshot()
    finally:
        driver.close()


@pytest.mark.skipif(os.name != "nt", reason="Live Win32 GDI PrintWindow smoke test requires Windows")
def test_live_win32_print_window_captures_background_hwnd_without_gdi_handle_leaks(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes
    import threading
    from ctypes import wintypes

    assert PW_RENDERFULLCONTENT == 0x00000002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    create_window_ex = user32.CreateWindowExW
    create_window_ex.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    create_window_ex.restype = wintypes.HWND

    destroy_window = user32.DestroyWindow
    destroy_window.argtypes = [wintypes.HWND]
    destroy_window.restype = wintypes.BOOL

    show_window = user32.ShowWindow
    show_window.argtypes = [wintypes.HWND, ctypes.c_int]
    show_window.restype = wintypes.BOOL

    update_window = user32.UpdateWindow
    update_window.argtypes = [wintypes.HWND]
    update_window.restype = wintypes.BOOL

    peek_message = user32.PeekMessageW
    peek_message.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    peek_message.restype = wintypes.BOOL

    translate_message = user32.TranslateMessage
    translate_message.argtypes = [ctypes.POINTER(wintypes.MSG)]
    translate_message.restype = wintypes.BOOL

    dispatch_message = user32.DispatchMessageW
    dispatch_message.argtypes = [ctypes.POINTER(wintypes.MSG)]
    dispatch_message.restype = ctypes.c_ssize_t

    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE

    get_gui_resources = user32.GetGuiResources
    get_gui_resources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    get_gui_resources.restype = wintypes.DWORD

    get_module_handle = kernel32.GetModuleHandleW
    get_module_handle.argtypes = [wintypes.LPCWSTR]
    get_module_handle.restype = wintypes.HMODULE

    ws_popup = 0x80000000
    ws_ex_toolwindow = 0x00000080
    ws_ex_noactivate = 0x08000000
    sw_shownoactivate = 4
    pm_remove = 0x0001
    gr_gdiobjects = 0

    ready = threading.Event()
    stop = threading.Event()
    created_hwnd: dict[str, int] = {}

    def _ui_window_thread() -> None:
        hinstance = get_module_handle(None)
        hwnd_val = create_window_ex(
            ws_ex_toolwindow | ws_ex_noactivate,
            "STATIC",
            "FSQPrintWindowSmoke",
            ws_popup,
            50,
            50,
            140,
            90,
            None,
            None,
            hinstance,
            None,
        )
        if hwnd_val:
            show_window(hwnd_val, sw_shownoactivate)
            update_window(hwnd_val)
            created_hwnd["hwnd"] = int(hwnd_val)
        ready.set()
        if not hwnd_val:
            return
        msg = wintypes.MSG()
        while not stop.is_set():
            while peek_message(ctypes.byref(msg), None, 0, 0, pm_remove):
                translate_message(ctypes.byref(msg))
                dispatch_message(ctypes.byref(msg))
            stop.wait(0.005)
        destroy_window(hwnd_val)

    ui_thread = threading.Thread(target=_ui_window_thread, daemon=True)
    ui_thread.start()
    assert ready.wait(5.0), "Timed out creating Win32 smoke test window"
    hwnd = created_hwnd.get("hwnd", 0)
    assert hwnd > 0, "CreateWindowExW failed to create test STATIC window"

    driver = PywinautoWindowsDriver(window_title_re="FSQPrintWindowSmoke")
    proc_handle = get_current_process()
    try:
        assert driver._is_win32_window(hwnd) is True

        # Warm up PIL imports before measuring steady-state GDI handle count across repeated captures.
        first_img = driver._capture_window_via_print_window(hwnd)
        assert first_img.mode == "RGB"
        assert first_img.size == (140, 90)

        gdi_before = int(get_gui_resources(proc_handle, gr_gdiobjects))
        for _ in range(5):
            captured = driver._capture_window_via_print_window(hwnd)
            assert captured.size == (140, 90)
        gdi_after = int(get_gui_resources(proc_handle, gr_gdiobjects))
        assert gdi_after <= gdi_before, f"GDI handle leak detected: before={gdi_before}, after={gdi_after}"

        # Also exercise full driver.screenshot() routing through PrintWindow on the worker executor.
        class _LiveBoundWindow:
            handle = hwnd

            def capture_as_image(self) -> Image.Image:
                raise AssertionError("Expected occluded path to use Win32 PrintWindow")

        monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: _LiveBoundWindow())
        png_bytes = driver.screenshot()
        decoded = Image.open(BytesIO(png_bytes))
        assert decoded.format == "PNG"
        assert decoded.size == (140, 90)
    finally:
        driver.close()
        stop.set()
        ui_thread.join(timeout=5.0)


@pytest.mark.skipif(os.name != "nt", reason="Live UIA minimized window resolution test requires Windows")
def test_live_uia_minimized_window_resolves_for_lossless_screenshot_and_restores_before_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import sys
    import threading
    import uuid
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    def_window_proc = user32.DefWindowProcW
    def_window_proc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    def_window_proc.restype = ctypes.c_ssize_t

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", wndproc_type),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    register_class = user32.RegisterClassW
    register_class.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    register_class.restype = wintypes.ATOM

    unregister_class = user32.UnregisterClassW
    unregister_class.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    unregister_class.restype = wintypes.BOOL

    create_window_ex = user32.CreateWindowExW
    create_window_ex.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    create_window_ex.restype = wintypes.HWND

    destroy_window = user32.DestroyWindow
    destroy_window.argtypes = [wintypes.HWND]
    destroy_window.restype = wintypes.BOOL

    show_window = user32.ShowWindow
    show_window.argtypes = [wintypes.HWND, ctypes.c_int]
    show_window.restype = wintypes.BOOL

    update_window = user32.UpdateWindow
    update_window.argtypes = [wintypes.HWND]
    update_window.restype = wintypes.BOOL

    peek_message = user32.PeekMessageW
    peek_message.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    peek_message.restype = wintypes.BOOL

    translate_message = user32.TranslateMessage
    translate_message.argtypes = [ctypes.POINTER(wintypes.MSG)]
    translate_message.restype = wintypes.BOOL

    dispatch_message = user32.DispatchMessageW
    dispatch_message.argtypes = [ctypes.POINTER(wintypes.MSG)]
    dispatch_message.restype = ctypes.c_ssize_t

    create_solid_brush = gdi32.CreateSolidBrush
    create_solid_brush.argtypes = [wintypes.COLORREF]
    create_solid_brush.restype = wintypes.HBRUSH

    delete_object = gdi32.DeleteObject
    delete_object.argtypes = [wintypes.HGDIOBJ]
    delete_object.restype = wintypes.BOOL

    get_module_handle = kernel32.GetModuleHandleW
    get_module_handle.argtypes = [wintypes.LPCWSTR]
    get_module_handle.restype = wintypes.HMODULE

    @wndproc_type
    def _wnd_proc(hwnd_arg: int, msg_arg: int, wparam_arg: int, lparam_arg: int) -> int:
        return int(def_window_proc(hwnd_arg, msg_arg, wparam_arg, lparam_arg))

    unique = uuid.uuid4().hex[:8]
    class_name = f"FSQUiaMinClass_{unique}"
    window_title = f"FSQUiaMinWindow_{unique}"
    ws_overlappedwindow = 0x00CF0000
    sw_shownoactivate = 4
    sw_minimize = 6
    pm_remove = 0x0001

    ready = threading.Event()
    stop = threading.Event()
    created: dict[str, int] = {}

    def _ui_thread_main() -> None:
        hinst = get_module_handle(None)
        brush = create_solid_brush(0x0022AA44)  # BGR -> RGB(68, 170, 34)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = _wnd_proc
        wc.hInstance = hinst
        wc.hbrBackground = brush
        wc.lpszClassName = class_name
        register_class(ctypes.byref(wc))
        hwnd_val = create_window_ex(
            0,
            class_name,
            window_title,
            ws_overlappedwindow,
            120,
            120,
            260,
            180,
            None,
            None,
            hinst,
            None,
        )
        if hwnd_val:
            show_window(hwnd_val, sw_shownoactivate)
            update_window(hwnd_val)
            show_window(hwnd_val, sw_minimize)
            created["hwnd"] = int(hwnd_val)
        ready.set()
        if hwnd_val:
            msg = wintypes.MSG()
            while not stop.is_set():
                while peek_message(ctypes.byref(msg), None, 0, 0, pm_remove):
                    translate_message(ctypes.byref(msg))
                    dispatch_message(ctypes.byref(msg))
                stop.wait(0.005)
            destroy_window(hwnd_val)
        unregister_class(class_name, hinst)
        if brush:
            delete_object(brush)

    ui_thread = threading.Thread(target=_ui_thread_main, daemon=True)
    ui_thread.start()
    assert ready.wait(5.0), "Timed out creating minimized UIA test window"
    hwnd = created.get("hwnd", 0)
    assert hwnd > 0

    # Exercise BOTH real UIA resolution paths without monkeypatching _resolve_main_window:
    # 1. window_title_re-only driver: screenshot() resolves minimized UIA window, captures full client surface via PrintWindow,
    #    and leaves the window minimized (lossless observation).
    title_driver = PywinautoWindowsDriver(window_title_re=f"^{window_title}$", backend_kind="uia")
    # 2. app_path-launched driver: _select_target_window resolves minimized UIA window via PID/exe match and press_key restores it.
    app_driver = PywinautoWindowsDriver(app_path=sys.executable, window_title_re=f"^{window_title}$", backend_kind="uia")
    app_driver._launched = True
    app_driver._launched_pid = os.getpid()
    app_driver._owns_process = False
    app_driver._pre_existing_handles = set()
    app_driver._pre_existing_pids = {os.getpid()}

    try:
        assert title_driver._is_window_iconic(hwnd) is True

        png_bytes = title_driver.screenshot()
        decoded = Image.open(BytesIO(png_bytes))
        assert decoded.format == "PNG"
        assert decoded.width >= 200
        assert decoded.height >= 120
        # Window MUST remain minimized after read-only screenshot observation.
        assert title_driver._is_window_iconic(hwnd) is True

        # Keep live window resolution and restoration, but do not depend on whether Windows grants
        # this CI process the foreground or emit any host keyboard input.
        from pywinauto.base_wrapper import BaseWrapper
        from pywinauto.controls.uiawrapper import UIAWrapper

        foreground = 0

        def _set_focus(wrapper: UIAWrapper) -> None:
            nonlocal foreground
            assert wrapper.handle == hwnd
            assert not app_driver._is_window_iconic(hwnd)
            foreground = hwnd

        monkeypatch.setattr(UIAWrapper, "set_focus", _set_focus)
        monkeypatch.setattr(app_driver, "_foreground_window_handle", lambda: foreground)
        monkeypatch.setattr(BaseWrapper, "type_keys", lambda self, *a, **kw: self)
        press_res = app_driver.press_key(WindowsPressKeyParams(key="{VK_SHIFT}"))
        assert press_res["status"] == "passed"
        assert app_driver._bound_handle == hwnd
        # Interaction action MUST have restored the minimized window!
        assert app_driver._is_window_iconic(hwnd) is False
    finally:
        title_driver.close()
        app_driver.close()
        stop.set()
        ui_thread.join(timeout=5.0)


def test_pywinauto_driver_point_actions_enforce_bound_window_bounds_and_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    state = {"iconic": True, "focused": False}
    hwnd = 6100
    window = _FakeInteractiveWindow(hwnd, 8800, events, state)
    mouse = _FakeMouse(events)
    driver = PywinautoWindowsDriver(window_title_re=".*CanvasApp")
    driver._bound_handle = hwnd
    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)
    monkeypatch.setattr(driver, "_is_win32_window", lambda candidate: candidate == hwnd)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda candidate: candidate == hwnd and state["iconic"])
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: hwnd if state["focused"] else 0)
    monkeypatch.setattr(driver, "_win32_window_rect", lambda candidate: (100, 200, 500, 600) if candidate == hwnd else None)

    def _restore(candidate: int, *, activate: bool = True) -> None:
        assert candidate == hwnd
        assert activate is True
        state["iconic"] = False
        events.append(f"restore:{candidate}")

    monkeypatch.setattr(driver, "_restore_window", _restore)
    try:
        assert driver.click_on(WindowsClickOnParams(point={"x": 250, "y": 350}))["status"] == "passed"
        assert driver.double_click_on(WindowsDoubleClickOnParams(point={"x": 260, "y": 360}))["status"] == "passed"
        assert driver.right_click_on(WindowsRightClickOnParams(point={"x": 270, "y": 370}))["status"] == "passed"
        assert driver.hover_on(WindowsHoverOnParams(point={"x": 280, "y": 380}))["status"] == "passed"
        assert driver.scroll_on(WindowsScrollOnParams(point={"x": 290, "y": 390}, wheel_dist=-2))["status"] == "passed"
        assert events == [
            f"restore:{hwnd}",
            f"set_focus:{hwnd}:iconic=False",
            "mouse_click:(250, 350):left",
            f"set_focus:{hwnd}:iconic=False",
            "mouse_double_click:(260, 360):left",
            f"set_focus:{hwnd}:iconic=False",
            "mouse_click:(270, 370):right",
            f"set_focus:{hwnd}:iconic=False",
            "mouse_move:(280, 380)",
            f"set_focus:{hwnd}:iconic=False",
            "mouse_scroll:(290, 390):-2",
        ]

        # Out-of-bounds screen point raises LookupError without dispatching host mouse input.
        events.clear()
        with pytest.raises(LookupError, match="outside the bound window bounds"):
            driver.click_on(WindowsClickOnParams(point={"x": 500, "y": 350}))
        assert events == [f"set_focus:{hwnd}:iconic=False"]
    finally:
        driver.close()


def test_pywinauto_driver_sample_color_supports_image_and_verified_pointer_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    hwnd = 6200
    state = {"iconic": False, "focused": True}
    events: list[str] = []
    window = _FakeInteractiveWindow(hwnd, 8900, events, state)
    driver = PywinautoWindowsDriver(window_title_re=".*ColorApp")
    driver._bound_handle = hwnd

    img = Image.new("RGB", (60, 40), (18, 52, 86))
    for px in range(20, 30):
        for py in range(10, 20):
            img.putpixel((px, py), (220, 60, 40))

    monkeypatch.setattr(driver, "_resolve_main_window", lambda **kwargs: window)
    monkeypatch.setattr(driver, "_is_win32_window", lambda candidate: candidate == hwnd)
    monkeypatch.setattr(driver, "_window_process_id", lambda candidate: 8900 if candidate == hwnd else None)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda candidate: state["iconic"])
    monkeypatch.setattr(driver, "_win32_window_rect", lambda candidate: (100, 200, 160, 240))
    monkeypatch.setattr(driver, "_primary_screen_size", lambda _w=None: (1920, 1080))
    monkeypatch.setattr(driver, "_capture_window_via_print_window", lambda candidate: img.copy())

    try:
        ptr_res = driver.sample_color(SampleColorParams.model_validate({"region": {"left": 120, "top": 210, "right": 130, "bottom": 220}, "coordinate_space": "pointer"}))
        assert ptr_res["status"] == "passed"
        assert ptr_res["output"]["rgb"] == (220, 60, 40)
        assert ptr_res["output"]["hex"] == "#DC3C28"
        assert ptr_res["output"]["uniform"] is True
        assert ptr_res["output"]["image_bounds"] == {"x": 20, "y": 10, "width": 10, "height": 10}
        assert ptr_res["output"]["pointer_bounds"] == {"x": 120, "top": 210} or ptr_res["output"]["pointer_bounds"] == {
            "x": 120,
            "y": 210,
            "width": 10,
            "height": 10,
        }

        # Minimized window: image-space sampleColor succeeds, pointer-space sampleColor fails closed with pointer_mapping_unavailable.
        state["iconic"] = True
        monkeypatch.setattr(driver, "_restore_window", lambda candidate, *, activate=True: state.__setitem__("iconic", False))
        monkeypatch.setattr(driver, "_minimize_window_no_activate", lambda candidate: state.__setitem__("iconic", True))

        img_res = driver.sample_color(SampleColorParams.model_validate({"point": {"x": 25, "y": 15}, "coordinate_space": "image"}))
        assert img_res["status"] == "passed"
        assert img_res["output"]["hex"] == "#DC3C28"
        assert img_res["output"]["pointer_point"] is None
        assert state["iconic"] is True

        minimized_ptr_res = driver.sample_color(SampleColorParams.model_validate({"point": {"x": 125, "y": 215}, "coordinate_space": "pointer"}))
        assert minimized_ptr_res["status"] == "failed"
        assert minimized_ptr_res["failure_category"] == "observation_error"
        assert minimized_ptr_res["metadata"]["reason"] == "pointer_mapping_unavailable"
    finally:
        driver.close()
