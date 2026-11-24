# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ctypes
from types import SimpleNamespace

import pytest

from fsq_agent.drivers.macos import _appium_connect as appium
from fsq_agent.drivers.macos import _native_fallback as native
from fsq_agent.drivers.macos._appium_connect import AppiumConnectMacOSDriver
from fsq_agent.drivers.macos._native_fallback import _read_ax_identity_and_value
from fsq_agent.models import (
    ConfigurationError,
    MacOSAssertNotVisibleParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
    MacOSScrollOnParams,
    MacOSTypeTextParams,
)


@pytest.mark.parametrize(
    ("role", "subrole", "reads_value"),
    [("AXTextField", "AXSecureTextField", False), ("AXUnknown", "", False), ("AXTextField", None, False), ("AXTextField", "", True)],
)
def test_native_ax_value_read_requires_known_safe_type(role: str, subrole: str | None, reads_value: bool) -> None:
    calls: list[str] = []
    values = {"AXRole": role, "AXSubrole": subrole, "AXValue": "editable value"}

    def read(attribute: str) -> str | None:
        calls.append(attribute)
        return values[attribute]

    assert _read_ax_identity_and_value(read) == (role, None if role == "AXUnknown" else subrole, "editable value" if reads_value else None)
    assert calls == (["AXRole"] if role == "AXUnknown" else ["AXRole", "AXSubrole", *(["AXValue"] if reads_value else [])])


@pytest.fixture
def capture(monkeypatch):
    pytest.importorskip("appium_connect_agent.automation")
    calls = []
    attributes = {(1, "AXRole"): "AXApplication", (1, "AXWindows"): [2, 3], (2, "AXRole"): "AXWindow", (3, "AXRole"): "AXWindow"}
    clock = SimpleNamespace(now=0.0, advance_on=None, advance_to=1.0)

    def attribute(ref, name):
        calls.append((ref, name))
        value = attributes.get((ref, name), -25205)
        if (ref, name) == clock.advance_on:
            clock.now = clock.advance_to
        return (value, None) if type(value) is int and value < 0 else (0, value)

    ax = SimpleNamespace(
        application=lambda _: 1,
        timeout=lambda ref, seconds: calls.append((ref, "timeout", seconds)) or 0,
        attribute=attribute,
        decode_text=lambda value: value if isinstance(value, str) else None,
        decode_bounds=lambda read: read("AXPosition")[1],
        array_type=list,
        type=type,
        count=len,
        item=lambda array, index: array[index],
        equal=lambda a, b: a == b,
        true=True,
        retain=lambda ref: ref,
        release=lambda _: None,
    )
    monkeypatch.setattr(native, "_AX", lambda: ax)
    monkeypatch.setattr(native, "_macos_ax_process_trusted", lambda: True)
    monkeypatch.setattr(native, "_frontmost_pid", lambda _, **_kw: 42)
    monkeypatch.setattr(native, "time", SimpleNamespace(monotonic=lambda: clock.now))

    def run(bundle_id="example.app"):
        control = SimpleNamespace(timeout_seconds=1.0, cancellation=None)
        return native._observe_macos_ax_tree(bundle_id=bundle_id, max_nodes=30, max_depth=10, control=control)

    return run, attributes, calls, clock


def test_native_capture_sets_timeout_before_each_reference_read(capture):
    run, attributes, calls, _ = capture
    attributes.update({(2, "AXChildren"): [4], (4, "AXRole"): "AXButton"})
    assert run().complete_for_requested_scope
    for ref in (1, 2, 3, 4):
        assert next(call for call in calls if call[0] == ref) == (ref, "timeout", 0.25)


@pytest.mark.parametrize("failure", [-25204, -25202, -25211])
@pytest.mark.parametrize("attribute", ["AXRole", "AXTitle"])
def test_native_capture_stops_failed_node_but_keeps_healthy_sibling(capture, failure, attribute):
    run, attributes, calls, _ = capture
    attributes[2, attribute] = failure
    observation = run()
    assert not observation.complete_for_requested_scope
    assert [node.handle.element for node in observation.roots[0].children] == [3]
    assert [call for call in calls if call[0] == 2][-1] == (2, attribute)


@pytest.mark.parametrize("open_attribute", ["AXSelected", "AXExpanded"])
@pytest.mark.parametrize("windows", [[], [2, 3]])
def test_native_capture_prunes_unopened_and_nested_menus(capture, windows, open_attribute):
    run, attributes, calls, _ = capture
    attributes.update(
        {
            (1, "AXWindows"): windows,
            (1, "AXChildren"): [2, 4],
            (1, "AXMenuBar"): 4,
            (4, "AXRole"): "AXMenuBar",
            (4, "AXChildren"): [5, 6],
            (5, "AXRole"): "AXMenuBarItem",
            (5, "AXTitle"): "Closed",
            (5, "AXChildren"): [7],
            (6, "AXRole"): "AXMenuBarItem",
            (6, open_attribute): True,
            (6, "AXChildren"): [8],
            (8, "AXRole"): "AXMenu",
            (8, "AXChildren"): [9],
            (9, "AXRole"): "AXMenuItem",
            (9, "AXTitle"): "Open entry",
            (9, "AXChildren"): [10],
        }
    )
    observation = run()
    assert observation.complete_for_requested_scope
    assert ((1, "AXChildren") in calls) == (not windows)
    assert b"Closed" in observation.raw_xml
    assert b"Open entry" in observation.raw_xml
    assert (5, "AXChildren") not in calls
    assert (9, "AXChildren") not in calls
    assert not {7, 10}.intersection(call[0] for call in calls)


def test_native_capture_budget_stops_child_and_ancestor_ipc(capture):
    run, attributes, calls, clock = capture
    attributes[2, "AXChildren"] = [4]
    attributes[4, "AXRole"] = "AXButton"
    clock.advance_on = (4, "AXRole")
    observation = run()
    assert not observation.complete_for_requested_scope
    assert calls[-1] == (4, "AXRole")
    assert calls.index((2, "AXTitle")) < calls.index((2, "AXChildren"))


def test_native_capture_caps_timeout_below_ten_milliseconds(capture):
    run, _, calls, clock = capture
    clock.advance_on = (1, "AXWindows")
    clock.advance_to = 0.995
    assert run().complete_for_requested_scope
    child_timeout = next(call[2] for call in calls if call[:2] == (2, "timeout"))
    assert 0 < child_timeout <= 1.0 - clock.now


def test_native_capture_retains_windows_after_menu_read_failure(capture):
    run, attributes, calls, _ = capture
    attributes[1, "AXMenuBar"] = -25204
    observation = run()
    assert not observation.complete_for_requested_scope
    assert [node.handle.element for node in observation.roots[0].children] == [2, 3]
    assert calls[-1] == (1, "AXMenuBar")


def test_native_capture_does_not_fall_back_after_window_read_error(capture):
    run, attributes, calls, _ = capture
    attributes[1, "AXWindows"] = -25200
    assert not run().complete_for_requested_scope
    assert (1, "AXChildren") not in calls


def test_native_capture_marks_unknown_menu_child_incomplete(capture):
    run, attributes, _, _ = capture
    attributes.update({(1, "AXMenuBar"): 4, (4, "AXRole"): "AXMenuBar", (4, "AXChildren"): [5]})
    assert not run().complete_for_requested_scope


def test_native_capture_preserves_application_attributes(capture):
    run, attributes, _, _ = capture
    attributes.update({(1, "AXPosition"): (0, 0, 640, 480), (1, "AXEnabled"): True, (1, "AXPlaceholderValue"): "Root placeholder"})
    root = run().roots[0]
    assert root.bounds.value.width == 640
    assert root.enabled.value is True
    assert root.placeholder_value.value == "Root placeholder"


def test_native_absence_distinguishes_pruned_target_from_incomplete_capture(capture, monkeypatch):
    run, attributes, _, _ = capture
    attributes.update({(1, "AXChildren"): [2, 3, 4], (4, "AXRole"): "AXButton", (4, "AXIdentifier"): "PrunedButton"})
    driver = AppiumConnectMacOSDriver(session=SimpleNamespace(), action_timeout_seconds=1)
    monkeypatch.setattr(driver, "_native_ax_mode", True)
    monkeypatch.setattr(driver, "_native_ax_candidate_bundles", lambda: ("example.app",))
    monkeypatch.setattr(driver, "_observe_native_ax_tree_for_bundle", lambda _: run())
    params = MacOSAssertNotVisibleParams(target="PrunedButton")

    result = driver.assert_not_visible(params)
    assert result["status"] == "passed", result
    assert result["output"] == {"visible": False}

    attributes[2, "AXRole"] = -25204
    result = driver.assert_not_visible(params)
    assert result["status"] == "failed", result
    assert "inconclusive" in result["error_message"]


@pytest.mark.parametrize(
    "canvas_locator",
    [
        {"role": "AXScrollArea"},
        {"role": "AXScrollArea", "label": "Canvas"},
        {"controlType": "ScrollView"},
        {"controlType": "ScrollView", "label": "Canvas"},
        {"xpath": "//XCUIElementTypeScrollView"},
        {"xpath": "//XCUIElementTypeScrollView[1]"},
        {"xpath": "//XCUIElementTypeScrollView", "label": "Canvas"},
        {"predicate": "role == 'AXScrollArea'", "label": "Canvas"},
        {"predicate": "role == 'AXScrollArea' AND label == 'Canvas'"},
    ],
)
def test_keynote_toolbar_metadata_failure_does_not_hide_canvas(capture, monkeypatch, canvas_locator):
    run, attributes, calls, _ = capture
    attributes.update(
        {
            (2, "AXChildren"): [4, 5, 6, 7],
            (4, "AXRole"): "AXMenuButton",
            (4, "AXSubrole"): -25200,
            (5, "AXRole"): "AXCheckBox",
            (5, "AXSubrole"): -25200,
            (5, "AXDescription"): -25200,
            (5, "AXEnabled"): -25200,
            (6, "AXRole"): "AXTextField",
            (6, "AXSubrole"): -25200,
            (7, "AXRole"): "AXScrollArea",
            (7, "AXDescription"): "Canvas",
            (7, "AXPosition"): (0, 0, 640, 480),
        }
    )
    observation = run()
    assert observation.complete_for_requested_scope
    assert observation.roots[0].children[0].children[1].label.state == "unknown"
    assert not {(4, "AXSubrole"), (5, "AXSubrole")}.intersection(calls)
    assert (6, "AXSubrole") in calls
    assert all(name != "AXValue" for _, name, *_ in calls)
    clicks = []
    automation = pytest.importorskip("appium_connect_agent.automation")
    monkeypatch.setattr(automation, "NativeMacOSDesktop", lambda **_: SimpleNamespace(click_at=lambda *args, **kwargs: clicks.append((args, kwargs))), raising=False)
    session = SimpleNamespace(device=SimpleNamespace(click_at=lambda *a, **kw: pytest.fail("WDA click used")))
    driver = AppiumConnectMacOSDriver(session=session, action_timeout_seconds=1)
    monkeypatch.setattr(driver, "_native_ax_mode", True)
    monkeypatch.setattr(driver, "_active_bundle_id", "example.app")
    monkeypatch.setattr(driver, "_native_ax_candidate_bundles", lambda **_: ("example.app",))
    monkeypatch.setattr(driver, "_observe_native_ax_tree_for_bundle", lambda _: run())
    monkeypatch.setattr(appium, "_verify_ax_point_hit", lambda handle, x, y, **_: (handle.element, x, y) == (7, 320, 240))
    monkeypatch.setattr(appium, "_frontmost_pid", lambda _, **_kw: 42)
    result = driver.click_on(MacOSClickOnParams(locator=canvas_locator))
    assert result["status"] == "passed", result
    assert len(clicks) == 1
    for locator in (
        {"label": "Unknown label"},
        {"xpath": "//*[not(@label)]"},
        {"xpath": "//XCUIElementTypeScrollView[@label='Canvas']"},
        {"xpath": "//XCUIElementTypeAny/XCUIElementTypeScrollView"},
        {"xpath": "//XCUIElementTypeTextField[1]"},
        {"role": "AXCheckBox", "predicate": "enabled == true"},
    ):
        result = driver.assert_not_visible(MacOSAssertNotVisibleParams(locator=locator))
        assert "inconclusive" in result["error_message"]
    assert len(clicks) == 1
    result = driver.assert_not_visible(MacOSAssertNotVisibleParams(locator={"role": "AXCheckBox", "predicate": "enabled ?? true"}))
    assert result["failure_category"] == "configuration_error"
    attributes[7, "AXIdentifier"] = "Canvas"
    attributes[7, "AXDescription"] = -25200
    for predicate in ("name == 'Canvas'", "displayed == true", "visible == true", "amRect.x == 0"):
        result = driver.click_on(MacOSClickOnParams(locator={"role": "AXScrollArea", "predicate": predicate}))
        assert result["status"] == "passed", result
    attributes[7, "AXDescription"] = "Canvas"
    attributes.update({(1, "AXWindows"): [2, 3, 8], (8, "AXRole"): "AXWindow", (8, "AXDescription"): -25200})
    attributes[2, "AXDescription"] = attributes[3, "AXDescription"] = "Duplicate"
    for locator in ({"label": "Duplicate"}, {"xpath": "//XCUIElementTypeWindow", "label": "Duplicate"}, {"predicate": "role == 'AXWindow' AND label == 'Duplicate'"}):
        result = driver.click_on(MacOSClickOnParams(locator=locator))
        assert result["failure_category"] == "target_resolution_error"
        assert result["metadata"]["resolution_reason"] == "ambiguous"

    # Full XPath remains available when the capture has no failed metadata reads.
    for key in [key for key, value in attributes.items() if value == -25200]:
        del attributes[key]
    result = driver.click_on(MacOSClickOnParams(locator={"xpath": "//XCUIElementTypeScrollView[@label='Canvas'][@role='AXScrollArea']"}))
    assert result["status"] == "passed", result


@pytest.mark.parametrize("attribute", ["AXRole", "AXChildren"])
def test_native_structural_failure_is_incomplete(capture, attribute):
    run, attributes, _, _ = capture
    attributes[2, attribute] = -25200
    assert not run().complete_for_requested_scope


def test_unknown_menu_selection_makes_menu_topology_incomplete(capture):
    run, attributes, _, _ = capture
    attributes.update({(1, "AXMenuBar"): 4, (4, "AXRole"): "AXMenuBar", (4, "AXChildren"): [5], (5, "AXRole"): "AXMenuBarItem", (5, "AXSelected"): -25200})
    assert not run().complete_for_requested_scope
    attributes[5, "AXExpanded"] = True
    assert run().complete_for_requested_scope


@pytest.mark.parametrize("rooted_at_application", [False, True])
def test_finder_capture_includes_live_search_menu_but_not_nested_menus(capture, rooted_at_application):
    run, attributes, calls, _ = capture
    attributes.update(
        {
            (2, "AXChildren"): [4],
            (4, "AXRole"): "AXTextField",
            (4, "AXSubrole"): "AXSearchField",
            (4, "AXChildren"): [] if rooted_at_application else [5],
            (5, "AXRole"): "SGTMenu",
            (5, "AXChildren"): [6],
            (6, "AXRole"): "AXMenu",
            (6, "AXPosition"): (830, 291, 244, 115),
            (6, "AXChildren"): [7],
            (7, "AXRole"): "AXMenuItem",
            (7, "AXTitle"): "Suggestion",
            (7, "AXChildren"): [8],
            (8, "AXRole"): "AXMenu",
            (8, "AXChildren"): [9],
            (9, "AXRole"): "AXMenuItem",
            (1, "AXChildren"): [2, 3, 5],
        }
    )
    observation = run("com.apple.finder")
    assert observation.complete_for_requested_scope
    assert observation.raw_xml.count(b'title="Suggestion"') == 1
    assert b'role="SGTMenu"' in observation.raw_xml
    assert not {(7, "AXChildren"), (8, "AXRole"), (9, "AXRole")} & set(calls)


def test_finder_search_menu_with_unknown_geometry_does_not_prove_absence(capture):
    run, attributes, _, _ = capture
    attributes.update({(1, "AXChildren"): [4], (4, "AXRole"): "AXMenu", (4, "AXPosition"): -25200})
    assert not run("com.apple.finder").complete_for_requested_scope


@pytest.fixture
def desktop(monkeypatch):
    automation = pytest.importorskip("appium_connect_agent.automation")
    calls = []
    hit_checks = []
    foreground_checks = []
    state = SimpleNamespace(pid=42, pre_dispatch_pid=None, post_dispatch_pid=None, hit=True, focus=True)
    bundle = "com.apple.iWork.Keynote"

    class Desktop:
        def __init__(self, *, foreground_check):
            self.foreground_check = foreground_check

        def __getattr__(self, operation):
            def dispatch(*args, **kwargs):
                if state.pre_dispatch_pid is not None:
                    state.pid = state.pre_dispatch_pid
                if self.foreground_check() is not True:
                    raise native._NativeForegroundError("target_not_foreground", "Untrusted details", request_state="not_sent")
                calls.append((operation, args, kwargs))
                if state.post_dispatch_pid is not None:
                    state.pid = state.post_dispatch_pid
                if self.foreground_check() is not True:
                    raise native._NativeForegroundError("target_not_foreground", "Untrusted details", request_state="response_unknown")

            return dispatch

    def foreground(bundle_id, *, app_path, **kwargs):
        foreground_checks.append((bundle_id, app_path))
        assert bundle_id == bundle
        return state.pid

    def hit(handle, x, y, *, pid, **kwargs):
        hit_checks.append((handle, pid, x, y))
        return state.hit and (x, y) != (999, 999)

    def focus(pid, **kwargs):
        return SimpleNamespace(pid=pid, element=1) if state.focus else None

    def unexpected(*args, **kwargs):
        pytest.fail("Logical target input must not use the WDA host")

    monkeypatch.setattr(automation, "NativeMacOSDesktop", Desktop, raising=False)
    monkeypatch.setattr(appium, "_frontmost_pid", foreground)
    monkeypatch.setattr(appium, "_verify_ax_point_hit", hit)
    monkeypatch.setattr(appium, "_focused_ax_handle", focus)
    monkeypatch.setattr(appium, "_verify_ax_focus", lambda *args, **kwargs: state.focus)
    driver = AppiumConnectMacOSDriver(bundle_id=bundle, app_path="/Applications/Keynote.app", session=SimpleNamespace(device=object(), observe_tree=unexpected, element=unexpected))
    driver._wda_host_bundle_id = "com.apple.finder"
    return driver, calls, hit_checks, foreground_checks, state


@pytest.mark.parametrize(
    ("method", "params", "operation", "args", "options"),
    [
        ("click_on", MacOSClickOnParams(point={"x": -5, "y": 7}), "click_at", (-5, 7), {"button": "left", "click_count": 1, "modifier_flags": 0}),
        ("double_click_on", MacOSDoubleClickOnParams(point={"x": -5, "y": 7}), "click_at", (-5, 7), {"button": "left", "click_count": 2, "modifier_flags": 0}),
        ("right_click_on", MacOSRightClickOnParams(point={"x": -5, "y": 7}), "click_at", (-5, 7), {"button": "right", "click_count": 1, "modifier_flags": 0}),
        ("hover_on", MacOSHoverOnParams(point={"x": -5, "y": 7}), "hover_at", (-5, 7), {}),
        ("scroll_on", MacOSScrollOnParams(point={"x": 5, "y": 7}, delta_x=8, delta_y=-120, modifiers=["SHIFT"]), "scroll_at", (5, 7, 8, -120), {"unit": "pixel", "modifier_flags": 2}),
        ("drag_to", MacOSDragToParams(source={"point": {"x": 5, "y": 7}}, destination={"point": {"x": 8, "y": 9}}, duration_ms=100), "drag_between", (5, 7, 8, 9), {"duration_ms": 100}),
        ("drag_to", MacOSDragToParams(source={"point": {"x": 5, "y": 7}}, destination={"point": {"x": 8, "y": 9}}, duration_ms=10), "drag_between", (5, 7, 8, 9), {"duration_ms": 50}),
        ("drag_to", MacOSDragToParams(source={"point": {"x": 5, "y": 7}}, destination={"point": {"x": 8, "y": 9}}, duration_ms=6000), "drag_between", (5, 7, 8, 9), {"duration_ms": 5000}),
    ],
)
def test_native_pointer_actions_guarded_and_dispatch_only(desktop, method, params, operation, args, options):
    driver, calls, hit_checks, foreground_checks, _ = desktop
    result = getattr(driver, method)(params)
    assert result["status"] == "passed", result
    assert result["output"]["effect_verified"] is False
    assert len(calls) == 1
    name, actual_args, kwargs = calls[0]
    assert (name, actual_args) == (operation, args)
    assert kwargs.pop("control").timeout_seconds > 0
    assert kwargs == options
    expected_hits = {(42, *args[:2])}
    if operation == "drag_between":
        expected_hits.add((42, *args[2:]))
    assert {(pid, x, y) for _, pid, x, y in hit_checks} == expected_hits
    assert len(foreground_checks) >= 3
    assert {str(path) for _, path in foreground_checks} == {"/Applications/Keynote.app"}


@pytest.mark.parametrize(("key", "modifiers"), [("CapsLock", []), ("Insert", []), ("/", []), ("s", ["FUNCTION"])])
def test_native_unsupported_key_or_modifier_is_not_a_locator_error(monkeypatch, desktop, key, modifiers):
    automation = pytest.importorskip("appium_connect_agent.automation")

    class InvalidKeyDesktop:
        def __init__(self, *, foreground_check):
            assert foreground_check() is True

        def send_keys(self, keys, *, control):
            raise automation.InvalidRequestError("invalid_argument", "0" * 32, "not_sent")

    monkeypatch.setattr(automation, "NativeMacOSDesktop", InvalidKeyDesktop)
    driver, _, _, _, _ = desktop
    with pytest.raises(ConfigurationError, match="key or modifier"):
        driver.press_key(MacOSPressKeyParams(key=key, modifiers=modifiers))


def test_native_foreign_drag_destination_rejects_entire_gesture(desktop):
    driver, calls, _, _, _ = desktop
    result = driver.drag_to(MacOSDragToParams(source={"point": {"x": 5, "y": 7}}, destination={"point": {"x": 999, "y": 999}}))
    assert result["status"] == "failed"
    assert not calls


@pytest.mark.parametrize(("switch", "request_state"), [("pre_dispatch_pid", "not_sent"), ("post_dispatch_pid", "response_unknown")])
def test_native_sdk_pid_guard_preserves_delivery_and_recovery_guidance(desktop, switch, request_state):
    driver, calls, _, _, state = desktop
    setattr(state, switch, 43)
    result = driver.press_key(MacOSPressKeyParams(key="s", modifiers=["COMMAND"]))
    assert result["status"] == "failed"
    assert result["metadata"]["request_state"] == request_state
    assert "Untrusted details" not in result["error_message"]
    assert driver._active_bundle_id in result["error_message"]
    assert "new_session=false" in result["error_message"]
    assert len(calls) == int(request_state == "response_unknown")


@pytest.mark.parametrize(("point", "clear", "expected"), [(None, False, ["type_text"]), ({"x": 5, "y": 7}, False, ["click_at", "type_text"]), ({"x": 5, "y": 7}, True, [])])
def test_native_untargeted_text_preserves_current_editing_focus(desktop, point, clear, expected):
    driver, calls, _, _, _ = desktop
    text = "A\r\n文😀"
    result = driver.type_text(MacOSTypeTextParams(text=text, point=point, clear=clear))
    assert result["status"] == ("passed" if expected else "failed")
    assert [operation for operation, _, _ in calls] == expected
    if expected:
        assert calls[-1][1] == (text,)
        assert result["output"]["effect_verified"] is False
    assert text not in str(result)


def test_native_text_rejects_unattributed_focus(desktop):
    driver, calls, _, _, state = desktop
    state.focus = False
    result = driver.type_text(MacOSTypeTextParams(text="private"))
    assert result["status"] == "failed"
    assert not calls


@pytest.mark.parametrize(
    ("semantic_ref", "hit_pid", "parent_pid", "expected"),
    [(11, 42, 42, True), (12, 42, 42, False), (11, 99, 42, False), (11, 42, 99, False), (None, 42, 42, True), (None, 99, 42, False)],
)
def test_live_system_hit_requires_target_pid_and_semantic_identity(monkeypatch, semantic_ref, hit_pid, parent_pid, expected):
    def set_hit(system, x, y, out):
        assert (system, x, y) == (1, -20, 40)
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = 10
        return 0

    def pid(ref, out):
        ctypes.cast(out, ctypes.POINTER(ctypes.c_int))[0] = hit_pid if ref == 10 else parent_pid
        return 0

    ax = SimpleNamespace(
        system=lambda: 1,
        timeout=lambda *args: 0,
        hit=set_hit,
        pid=pid,
        attribute=lambda ref, name: (0, 11) if (ref, name) == (10, "AXParent") else (-25205, None),
        equal=lambda a, b: a == b,
        release=lambda _: None,
    )
    monkeypatch.setattr(native, "_macos_ax_process_trusted", lambda: True)
    monkeypatch.setattr(native, "_AX", lambda: ax)
    handle = SimpleNamespace(pid=42, element=semantic_ref, backend=ax) if semantic_ref is not None else None
    assert native._verify_ax_point_hit(handle, -20, 40, pid=42, deadline=native.time.monotonic() + 1, cancellation=None) is expected


def test_native_semantic_miss_never_uses_supplied_point(desktop, monkeypatch):
    driver, calls, hit_checks, _, _ = desktop
    observation = SimpleNamespace(roots=(SimpleNamespace(children=()),), complete_for_requested_scope=True)
    monkeypatch.setattr(driver, "_observe_native_ax_tree_for_bundle", lambda bundle: observation)
    monkeypatch.setattr(driver, "_match_native_ax_nodes_in_observation", lambda obs, locator: ([], False, None))
    result = driver.click_on(MacOSClickOnParams(target="Missing", point={"x": 5, "y": 7}))
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"]["resolution_reason"] == "not_found"
    assert not calls
    assert not hit_checks
