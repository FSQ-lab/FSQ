# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from types import SimpleNamespace

import pytest

from fsq_agent.drivers.macos import _native_fallback as native
from fsq_agent.drivers.macos._appium_connect import AppiumConnectMacOSDriver
from fsq_agent.drivers.macos._native_fallback import _read_ax_identity_and_value
from fsq_agent.models import MacOSAssertNotVisibleParams


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

    assert _read_ax_identity_and_value(read) == (role, subrole, "editable value" if reads_value else None)
    assert calls == ["AXRole", "AXSubrole", *(["AXValue"] if reads_value else [])]


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
    monkeypatch.setattr(native, "_frontmost_pid", lambda _: 42)
    monkeypatch.setattr(native, "time", SimpleNamespace(monotonic=lambda: clock.now))

    def run():
        control = SimpleNamespace(timeout_seconds=1.0, cancellation=None)
        return native._observe_macos_ax_tree(bundle_id="example.app", max_nodes=30, max_depth=10, control=control)

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
