# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import io
import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock
from xml.etree import ElementTree

import pytest
from PIL import Image

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.agent_engine._google_gemini_schema import google_schema
from fsq_agent.agent_engine._schema import ensure_strict_json_schema
from fsq_agent.case_dsl import FsqCaseLoader, FsqCaseSerializer
from fsq_agent.core import ArtifactStore, HarnessFactory, HarnessInterface
from fsq_agent.drivers.ios._appium_connect import AppiumConnectIOSDriver
from fsq_agent.harnesses._ios import IOSHarness
from fsq_agent.models import (
    IOS_ACTION_DEFINITIONS,
    AIAssertionRequest,
    AIAssertionResult,
    ConfigurationError,
    ExecutableStep,
    HarnessContext,
    HarnessSettings,
    IOSAssertNotVisibleParams,
    IOSAssertStateParams,
    IOSAssertVisibleParams,
    IOSClearTextParams,
    IOSDoubleTapOnParams,
    IOSElementState,
    IOSHandleAlertParams,
    IOSHarnessSettings,
    IOSHideKeyboardParams,
    IOSInputTextParams,
    IOSInspectElementParams,
    IOSLaunchAppParams,
    IOSLocator,
    IOSLongPressOnParams,
    IOSPoint,
    IOSPressKeyParams,
    IOSScreenSize,
    IOSScrollToParams,
    IOSStopAppParams,
    IOSSwipeParams,
    IOSTapAtParams,
    IOSTapOnParams,
    IOSTextAssertion,
    IOSUiSnapshotParams,
)

if TYPE_CHECKING:
    from pathlib import Path


def _obs(value: object, state: str = "value") -> Any:
    return SimpleNamespace(state=state, value=value if state == "value" else None)


def _attr_obs(name: str, value: object, state: str = "value") -> Any:
    return SimpleNamespace(name=name, state=state, value=value if state == "value" else None)


def _bounds(x: int, y: int, width: int = 40, height: int = 24) -> Any:
    return SimpleNamespace(x=x, y=y, width=width, height=height)


class TargetError(RuntimeError):
    def __init__(self, code: str, message: str = "target error") -> None:
        super().__init__(message)
        self.code = code


class OperationCancelledError(RuntimeError):
    def __init__(self, code: str = "operation_cancelled") -> None:
        super().__init__(code)
        self.code = code


class FakeElement:
    def __init__(
        self,
        *,
        by: str,
        value: str,
        predicate: str | None = None,
        parent: FakeElement | None = None,
        attrs: dict[str, tuple[str, object]] | None = None,
        inspect_error: Exception | None = None,
        send_keys_error: Exception | None = None,
    ) -> None:
        self.by = by
        self.value = value
        self.predicate = predicate
        self.parent = parent
        self.attrs: dict[str, tuple[str, object]] = {
            "type": ("value", "XCUIElementTypeButton"),
            "id": ("value", "btn_general"),
            "label": ("value", "General"),
            "value": ("missing", None),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "selected": ("value", False),
            "accessible": ("value", True),
            "focused": ("value", False),
            "bounds": ("value", _bounds(16, 120, 370, 52)),
        }
        if attrs:
            self.attrs.update(attrs)
        self.inspect_error = inspect_error
        self.send_keys_error = send_keys_error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def inspect(self, *, attributes: tuple[str, ...], control: object = None) -> Any:
        self.calls.append(("inspect", {"attributes": attributes, "control": control}))
        if self.inspect_error is not None:
            raise self.inspect_error
        entries = []
        for attr in attributes:
            state, val = self.attrs.get(attr, ("missing", None))
            entries.append(_attr_obs(attr, val, state=state))
        return SimpleNamespace(attributes=tuple(entries))

    def click(self, *, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("click", {"wait_timeout_seconds": wait_timeout_seconds}))

    def double_click(self, *, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("double_click", {"wait_timeout_seconds": wait_timeout_seconds}))

    def long_click(self, *, duration_ms: int, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("long_click", {"duration_ms": duration_ms, "wait_timeout_seconds": wait_timeout_seconds}))

    def clear(self, *, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("clear", {"wait_timeout_seconds": wait_timeout_seconds}))
        ph_state, ph_val = self.attrs.get("placeholder_value", ("missing", None))
        if ph_state == "value" and isinstance(ph_val, str):
            self.attrs["value"] = ("value", ph_val)
        else:
            self.attrs["value"] = ("value", "")

    def send_keys(self, text: str, *, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("send_keys", {"text": text}))
        if self.send_keys_error is not None:
            raise self.send_keys_error
        self.attrs["value"] = ("value", text)

    def send_macos_keys(self, keys: list[str], *, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("send_macos_keys", {"keys": keys}))
        self.attrs["value"] = ("value", "".join(keys))

    def macos_swipe(self, direction: str, *, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("macos_swipe", {"direction": direction}))

    def ios_scroll(self, direction: str, *, distance: float, wait_timeout_seconds: float, control: object = None) -> None:
        self.calls.append(("ios_scroll", {"direction": direction, "distance": distance}))


class FakeDevice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.screen_width = 402
        self.screen_height = 874
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color=(120, 130, 140)).save(buf, format="PNG")
        self.png_bytes = buf.getvalue()

    def display_size(self, *, control: object = None) -> Any:
        self.calls.append(("display_size", {}))
        return SimpleNamespace(width=self.screen_width, height=self.screen_height)

    def device_time(self, *, control: object = None) -> Any:
        self.calls.append(("device_time", {}))
        return SimpleNamespace(iso_local="2026-11-19T09:30:00+08:00")

    def take_screenshot(self, *, control: object = None) -> bytes:
        self.calls.append(("take_screenshot", {}))
        return self.png_bytes

    def activate_app(self, bundle_id: str, *, control: object = None) -> None:
        self.calls.append(("activate_app", {"bundle_id": bundle_id}))

    def launch_app(self, *, bundle_id: str, arguments: list[str] | None = None, control: object = None) -> None:
        self.calls.append(("launch_app", {"bundle_id": bundle_id, "arguments": arguments}))

    def terminate_app(self, bundle_id: str, *, control: object = None) -> bool:
        self.calls.append(("terminate_app", {"bundle_id": bundle_id}))
        return True

    def click_at(self, x: int, y: int, *, control: object = None) -> None:
        self.calls.append(("click_at", {"x": x, "y": y}))

    def press_button(self, name: str, *, control: object = None) -> None:
        self.calls.append(("press_button", {"name": name}))

    def send_keys(self, keys: list[str], *, control: object = None) -> None:
        self.calls.append(("send_keys", {"keys": keys}))

    def hide_keyboard(self, *, control: object = None) -> bool:
        self.calls.append(("hide_keyboard", {}))
        return True

    def accept_alert(self, *, button_label: str | None = None, control: object = None) -> None:
        self.calls.append(("accept_alert", {"button_label": button_label}))

    def dismiss_alert(self, *, button_label: str | None = None, control: object = None) -> None:
        self.calls.append(("dismiss_alert", {"button_label": button_label}))

    def swipe(self, direction: str, *, duration_ms: int, control: object = None) -> None:
        self.calls.append(("swipe", {"direction": direction, "duration_ms": duration_ms}))

    def swipe_between(self, start_x: int, start_y: int, end_x: int, end_y: int, *, duration_ms: int, control: object = None) -> None:
        self.calls.append(("swipe_between", {"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y, "duration_ms": duration_ms}))

    def inspect_with_ai(
        self,
        prompt: str,
        *,
        provider: Any,
        mode: str = "extract",
        region: Any = None,
        max_candidates: int = 5,
        control: object = None,
    ) -> Any:
        import hashlib

        digest = hashlib.sha256(self.png_bytes).hexdigest()
        request = SimpleNamespace(
            prompt=prompt,
            mode="locate" if mode == "locate" else "extract",
            png_bytes=self.png_bytes,
            image_width=4,
            image_height=4,
            remaining_timeout_seconds=None,
            region=region,
            max_candidates=max_candidates,
        )
        response = provider.inspect(request)
        candidates = [
            SimpleNamespace(
                label=cand.label,
                image_bounds=cand.image_bounds,
                pointer_bounds=SimpleNamespace(
                    x=cand.image_bounds.x,
                    y=cand.image_bounds.y,
                    width=cand.image_bounds.width,
                    height=cand.image_bounds.height,
                )
                if mode == "locate"
                else None,
                pointer_center=SimpleNamespace(
                    x=cand.image_bounds.x + cand.image_bounds.width // 2,
                    y=cand.image_bounds.y + cand.image_bounds.height // 2,
                )
                if mode == "locate"
                else None,
                confidence=cand.confidence,
            )
            for cand in response.candidates
        ]
        return SimpleNamespace(
            operation_id="0" * 32,
            observed_at="2026-11-19T01:30:00Z",
            platform="ios",
            mode="locate" if mode == "locate" else "extract",
            image_size=SimpleNamespace(width=4, height=4),
            pointer_size=SimpleNamespace(width=402, height=874) if mode == "locate" else None,
            png_sha256=digest,
            region=region,
            summary=response.summary,
            candidates=tuple(candidates),
        )


class FakeSession:
    def __init__(self) -> None:
        self.device = FakeDevice()
        self.state = "active"
        self.session_id_value = "xcuitest-session-01"
        self.elements: dict[tuple[str, str, str | None], FakeElement] = {}
        self.scoped_elements: dict[tuple[str, str, FakeElement], FakeElement] = {}
        self.default_element = FakeElement(by="default", value="default")
        self.tree_roots: tuple[Any, ...] = (
            SimpleNamespace(
                type=_obs("XCUIElementTypeApplication"),
                id=_obs("Settings"),
                label=_obs("Settings"),
                value=_obs(None, "missing"),
                placeholder_value=_obs(None, "missing"),
                title=_obs(None, "missing"),
                text=_obs("Settings"),
                enabled=_obs(True),
                selected=_obs(False),
                visibility="visible",
                bounds=_obs(_bounds(0, 0, 402, 874)),
                children=(
                    SimpleNamespace(
                        type=_obs("XCUIElementTypeSecureTextField"),
                        id=_obs("passcode_input"),
                        label=_obs("Passcode"),
                        value=_obs("TopSecretPin123"),
                        placeholder_value=_obs("Enter PIN"),
                        title=_obs(None, "missing"),
                        text=_obs("TopSecretPin123"),
                        enabled=_obs(True),
                        selected=_obs(False),
                        visibility="visible",
                        bounds=_obs(_bounds(20, 200, 360, 44)),
                        children=(),
                    ),
                    SimpleNamespace(
                        type=_obs("XCUIElementTypeButton"),
                        id=_obs("com.apple.settings.general"),
                        label=_obs("General"),
                        value=_obs(None, "missing"),
                        placeholder_value=_obs(None, "missing"),
                        title=_obs(None, "missing"),
                        text=_obs("General"),
                        enabled=_obs(True),
                        selected=_obs(False),
                        visibility="visible",
                        bounds=_obs(_bounds(16, 300, 370, 52)),
                        children=(),
                    ),
                ),
            ),
        )

    def session_info(self) -> Any:
        return SimpleNamespace(
            id="local-session-01",
            appium_session_id=self.session_id_value,
            state=self.state,
            platform="ios",
        )

    def element(self, by: str, value: str, *, predicate: str | None = None, parent: FakeElement | None = None) -> FakeElement:
        if parent is not None:
            scoped_key = (by, value, parent)
            if scoped_key in self.scoped_elements:
                return self.scoped_elements[scoped_key]
        key = (by, value, predicate)
        if key in self.elements:
            return self.elements[key]
        if by == "class name" and value == "XCUIElementTypeKeyboard":
            return FakeElement(by=by, value=value, inspect_error=TargetError("target_not_found"))
        return self.default_element

    def observe_tree(self, *, control: object = None) -> Any:
        return SimpleNamespace(
            roots=self.tree_roots,
            coverage="full",
            viewport=SimpleNamespace(width=_obs(402), height=_obs(874)),
        )


def test_ios_22_tool_schemas_pass_openai_strict_and_google_gemini_and_canonical_case_dsl(tmp_path: Path) -> None:
    registry = build_capability_registry(platform="ios")
    snapshot = registry.snapshot()
    names = [cap.name for cap in snapshot.capabilities]
    assert len(names) == 22
    assert set(names) == {"wait_ms", *{d.driver_method for d in IOS_ACTION_DEFINITIONS}}

    harness = HarnessFactory().create_harness(
        platform="ios",
        harness_settings=HarnessSettings(platform="ios"),
        artifact_store=ArtifactStore(run_dir=tmp_path / "run"),
        bundle_id="com.apple.Preferences",
        udid="00008110-0011223344556677",
    )
    try:
        assert isinstance(harness, IOSHarness)
        assert isinstance(harness, HarnessInterface)
        schemas = harness.action_space()
        assert len(schemas) == 22
        for schema in schemas:
            # reference_screen_size must never leak into model-facing online JSON schemas
            serialized = str(schema.params_json_schema)
            assert "reference_screen_size" not in serialized
            ensure_strict_json_schema(dict(schema.params_json_schema))
            google_schema(dict(schema.params_json_schema))
    finally:
        harness.close()

    # Canonical YAML roundtrip including inspectElement default list attributes
    raw_yaml = """schemaVersion: fsq.ai-test/v1
name: ios-roundtrip
description: Canonical iOS Case roundtrip
platform: ios
appId: com.apple.Preferences
---
- launchApp:
    bundle_id: com.apple.Preferences
- inspectElement:
    target: General
- tapAt:
    point:
      x: 120
      y: 340
    reference_screen_size:
      width: 402
      height: 874
- uiSnapshot:
    include_offscreen: true
- stopApp: {}
"""
    case = FsqCaseLoader().load_text(raw_yaml, path=tmp_path / "case.fsq.yaml")
    serializer = FsqCaseSerializer(snapshot)
    encoded_once = serializer.serialize(case)
    reloaded = FsqCaseLoader().load_text(encoded_once.decode("utf-8"), path=tmp_path / "case.fsq.yaml")
    assert serializer.serialize(reloaded) == encoded_once


def test_ios_snapshot_redacts_secure_text_field_and_includes_viewport_and_clock() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    snap = driver.ui_snapshot(IOSUiSnapshotParams(max_depth=6))
    assert "root" not in snap
    assert snap["viewport"] == {"width": 402, "height": 874}
    assert snap["device_local_time"] == {
        "status": "available",
        "local_datetime": "2026-11-19T09:30:00+08:00",
        "iso_weekday": 4,
        "source": "ios_device",
    }
    xml_text = str(snap["xml"])
    assert "TopSecretPin123" not in xml_text
    root = ElementTree.fromstring(xml_text)  # noqa: S314 - controlled test output
    secure_nodes = root.findall(".//XCUIElementTypeSecureTextField")
    assert len(secure_nodes) == 1
    assert "value" not in secure_nodes[0].attrib
    assert secure_nodes[0].attrib.get("placeholderValue") == "Enter PIN"


def test_ios_snapshot_prunes_offscreen_subtrees_without_losing_visible_descendants() -> None:
    def node(kind: str, name: str, visibility: str, *children: SimpleNamespace, label: str = "") -> SimpleNamespace:
        return SimpleNamespace(type=_obs(f"XCUIElementType{kind}"), id=_obs(name), visibility=visibility, label=_obs(label), children=children)

    transaction = "Transaction details " * 6
    long_title = "Long item title " * 12
    offscreen_rows = tuple(node("Cell", f"offscreen-{index}", "not_visible", label=transaction) for index in range(850))
    hidden_branch = node("Other", "hidden-branch", "not_visible", node("Cell", "hidden-child", "not_visible"))
    mixed_branch = node(
        "Other",
        "mixed-branch",
        "not_visible",
        node("Cell", "visible-transaction", "visible", label=transaction),
        node("Cell", "partially-visible", "partially_visible", label=long_title),
        node("Cell", "unknown-visibility", "unknown"),
        hidden_branch,
    )
    scroll_view = node("ScrollView", "transactions", "visible", mixed_branch, *offscreen_rows)
    session = FakeSession()
    session.tree_roots = (node("Application", "app", "not_visible", scroll_view),)
    driver = AppiumConnectIOSDriver(session=session)
    try:
        default = driver.ui_snapshot(IOSUiSnapshotParams())
        full = driver.ui_snapshot(IOSUiSnapshotParams(include_offscreen=True))
    finally:
        driver.close()

    root = ElementTree.fromstring(str(default["xml"]))  # noqa: S314 - controlled test output
    assert root.tag == "XCUIElementTypeApplication"
    assert root.get("visible") == "false"
    scroll = root.find("./XCUIElementTypeScrollView[@name='transactions']")
    assert scroll is not None
    mixed = scroll.find("./XCUIElementTypeOther[@name='mixed-branch']")
    assert mixed is not None
    assert mixed.get("visible") == "false"
    assert [(child.get("name"), child.get("label")) for child in mixed] == [
        ("visible-transaction", transaction.strip()),
        ("partially-visible", long_title.strip()[:160]),
        ("unknown-visibility", None),
    ]
    assert len(scroll) == 1
    assert "root" not in default
    assert "root" not in full
    compaction = default["compaction"]
    coverage = default["coverage"]
    assert isinstance(compaction, dict)
    assert isinstance(coverage, dict)
    assert compaction["offscreen_nodes_omitted"] == 852
    assert compaction["layout_nodes_removed"] == 0
    assert compaction["clipped_attributes"] == 1
    assert coverage["status"] == "partial"
    assert coverage["source_nodes"] == 858
    assert coverage["retained_nodes"] == 6
    full_compaction = full["compaction"]
    assert isinstance(full_compaction, dict)
    assert full_compaction["offscreen_nodes_omitted"] == 0
    full_root = ElementTree.fromstring(str(full["xml"]))  # noqa: S314 - controlled test output
    assert full_root.find(".//XCUIElementTypeCell[@name='offscreen-849'][@visible='false']") is not None
    assert full_root.find(".//XCUIElementTypeCell[@name='hidden-child'][@visible='false']") is not None
    assert len(json.dumps(default)) < 30_000 < len(json.dumps(full))


def test_ios_snapshot_reports_partial_when_offscreen_pruning_is_the_only_loss() -> None:
    hidden = SimpleNamespace(type=_obs("XCUIElementTypeCell"), visibility="not_visible")
    session = FakeSession()
    session.tree_roots = (SimpleNamespace(type=_obs("XCUIElementTypeApplication"), children=(hidden,)),)
    driver = AppiumConnectIOSDriver(session=session)
    try:
        snapshot = driver.ui_snapshot(IOSUiSnapshotParams())
    finally:
        driver.close()

    assert snapshot["xml"] == "<XCUIElementTypeApplication />"
    compaction = snapshot["compaction"]
    coverage = snapshot["coverage"]
    assert isinstance(compaction, dict)
    assert isinstance(coverage, dict)
    assert compaction["offscreen_nodes_omitted"] == 1
    assert coverage["status"] == "partial"


def test_ios_snapshot_empty_tree_has_no_duplicate_root_or_offscreen_omissions() -> None:
    session = FakeSession()
    session.tree_roots = ()
    driver = AppiumConnectIOSDriver(session=session)
    try:
        snapshot = driver.ui_snapshot(IOSUiSnapshotParams())
    finally:
        driver.close()

    assert "root" not in snapshot
    assert snapshot["xml"] == "<XCUIElementTypeApplication />"
    compaction = snapshot["compaction"]
    coverage = snapshot["coverage"]
    assert isinstance(compaction, dict)
    assert isinstance(coverage, dict)
    assert compaction["offscreen_nodes_omitted"] == 0
    assert coverage["status"] == "unknown"


def test_ios_inspect_element_withholds_secure_text_field_value_even_when_type_unrequested() -> None:
    session = FakeSession()
    secure_el = FakeElement(
        by="accessibility id",
        value="passcode_input",
        attrs={
            "type": ("value", "XCUIElementTypeSecureTextField"),
            "id": ("value", "passcode_input"),
            "label": ("value", "Passcode"),
            "value": ("value", "SuperSecret999"),
            "enabled": ("value", True),
        },
    )
    session.elements[("accessibility id", "passcode_input", None)] = secure_el
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    res = driver.inspect_element(
        IOSInspectElementParams(
            locator=IOSLocator(accessibility_id="passcode_input"),
            attributes=["label", "value"],
        )
    )
    attrs = res["attributes"]
    assert set(attrs.keys()) == {"label", "value"}
    assert attrs["label"] == {"state": "value", "value": "Passcode"}
    assert attrs["value"] == {"state": "not_applicable", "value": None}
    assert "SuperSecret999" not in str(res)


def test_ios_actions_touch_scroll_swipe_keyboard_alert_and_coordinate_scaling() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    # Launch activates existing session without recreating borrowed session
    launch_res = driver.launch_app(IOSLaunchAppParams(bundle_id="com.apple.Preferences"))
    assert launch_res["status"] == "passed"
    assert ("activate_app", {"bundle_id": "com.apple.Preferences"}) in session.device.calls

    # Tap, doubleTap, longPress
    assert driver.tap_on(IOSTapOnParams(locator=IOSLocator(accessibility_id="btn_general")))["status"] == "passed"
    assert driver.double_tap_on(IOSDoubleTapOnParams(locator=IOSLocator(accessibility_id="btn_general")))["status"] == "passed"
    assert driver.long_press_on(IOSLongPressOnParams(locator=IOSLocator(accessibility_id="btn_general"), duration_ms=1200))["status"] == "passed"

    # Proportional replay scaling on tapAt (reference 201x437 -> current 402x874 doubles coordinates)
    tap_at_res = driver.tap_at(
        IOSTapAtParams(
            point=IOSPoint(x=100, y=200),
            reference_screen_size=IOSScreenSize(width=201, height=437),
        )
    )
    assert tap_at_res["status"] == "passed"
    assert tap_at_res["output"] == {"x": 200, "y": 400}
    assert tap_at_res["metadata"]["safe_replay_params"]["reference_screen_size"] == {"width": 201, "height": 437}
    assert ("click_at", {"x": 200, "y": 400}) in session.device.calls

    # Orientation mismatch raises explicit ConfigurationError
    with pytest.raises(ConfigurationError, match="orientation"):
        driver.tap_at(
            IOSTapAtParams(
                point=IOSPoint(x=100, y=200),
                reference_screen_size=IOSScreenSize(width=874, height=402),
            )
        )

    # Full-screen swipe vs explicit-point swipe vs element swipe vs scrollTo
    assert driver.swipe(IOSSwipeParams(direction="up"))["status"] == "passed"
    assert driver.swipe(IOSSwipeParams(start=IOSPoint(x=50, y=600), end=IOSPoint(x=50, y=200), duration=450))["status"] == "passed"
    assert driver.swipe(IOSSwipeParams(locator=IOSLocator(accessibility_id="btn_general"), direction="left", percent=0.5))["status"] == "passed"
    assert driver.scroll_to(IOSScrollToParams(locator=IOSLocator(accessibility_id="btn_general"), direction="down", distance=0.6))["status"] == "passed"

    # Physical button vs active-responder key
    assert driver.press_key(IOSPressKeyParams(key="home")) == {"status": "passed", "output": {"button": "home"}}
    assert driver.press_key(IOSPressKeyParams(key="enter")) == {"status": "passed", "output": {"key": "XCUIKeyboardKeyEnter"}}

    # hideKeyboard skips WDA dismiss when no XCUIElementTypeKeyboard is present, and dismisses when present
    assert driver.hide_keyboard(IOSHideKeyboardParams()) == {"status": "passed", "output": {"hidden": False}}
    session.elements[("class name", "XCUIElementTypeKeyboard", None)] = FakeElement(
        by="class name",
        value="XCUIElementTypeKeyboard",
        attrs={"type": ("value", "XCUIElementTypeKeyboard"), "enabled": ("value", True)},
    )
    assert driver.hide_keyboard(IOSHideKeyboardParams()) == {"status": "passed", "output": {"hidden": True}}

    # handleAlert & stopApp
    assert driver.handle_alert(IOSHandleAlertParams(action="accept", button_label="Allow"))["status"] == "passed"
    assert driver.stop_app(IOSStopAppParams()) == {"status": "passed", "output": {"bundle_id": "com.apple.Preferences", "terminated": True}}


def test_ios_input_text_rejects_non_editable_and_falls_back_to_mobile_keys_without_echoing_secret() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    # Non-editable button rejected before typing
    bad_res = driver.input_text(IOSInputTextParams(locator=IOSLocator(accessibility_id="btn_general"), text="hello"))
    assert bad_res["status"] == "failed"
    assert bad_res["failure_category"] == "target_resolution_error"

    # Editable TextField with send_keys failure falls back to send_macos_keys (`mobile: keys`)
    field_el = FakeElement(
        by="accessibility id",
        value="search_input",
        attrs={
            "type": ("value", "XCUIElementTypeSearchField"),
            "id": ("value", "search_input"),
            "label": ("value", "Search"),
            "value": ("value", ""),
            "enabled": ("value", True),
            "displayed": ("value", True),
        },
        send_keys_error=type("CommandError", (RuntimeError,), {"code": "command_failed", "request_state": "response_received"})("WDA key typing glitch"),
    )
    session.elements[("accessibility id", "search_input", None)] = field_el
    ok_res = driver.input_text(IOSInputTextParams(locator=IOSLocator(accessibility_id="search_input"), text="Wi-Fi", clear=True))
    assert ok_res["status"] == "passed"
    method_names = [name for name, _ in field_el.calls]
    assert "clear" in method_names
    assert "send_macos_keys" in method_names

    assert driver.clear_text(IOSClearTextParams(locator=IOSLocator(accessibility_id="search_input")))["status"] == "passed"


def test_ios_deterministic_assertions_and_visual_ai_parity(tmp_path: Path) -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    assert driver.assert_visible(IOSAssertVisibleParams(locator=IOSLocator(accessibility_id="btn_general")))["status"] == "passed"
    session.elements[("accessibility id", "missing_el", None)] = FakeElement(
        by="accessibility id",
        value="missing_el",
        inspect_error=TargetError("target_not_found"),
    )
    assert driver.assert_not_visible(IOSAssertNotVisibleParams(locator=IOSLocator(accessibility_id="missing_el")))["status"] == "passed"

    assert (
        driver.assert_state(
            IOSAssertStateParams(
                element=IOSElementState(accessibility_id="btn_general", enabled=True, accessible=True),
                text=IOSTextAssertion(equals="General"),
            )
        )["status"]
        == "passed"
    )

    class StubEvaluator:
        def evaluate(self, request: AIAssertionRequest, *, cancellation_check: object = None) -> AIAssertionResult:
            if not request.upload_authorized:
                return AIAssertionResult(
                    status="inconclusive",
                    outcome="inconclusive",
                    error_code="privacy_blocked",
                    explanation="Provider upload is blocked by policy.",
                    capture=request.capture,
                    artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
                )
            return AIAssertionResult(
                status="passed",
                outcome="passed",
                passed=True,
                explanation="Icon matches expected tint.",
                confidence=0.95,
                capture=request.capture,
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
            )

    store = ArtifactStore(run_dir=tmp_path / "visual-run")
    harness = IOSHarness(
        driver=driver,
        artifact_store=store,
        ai_assertion_evaluator=StubEvaluator(),
        visual_assertion_provider_available=True,
    )
    step = ExecutableStep(
        step_id="vis-1",
        kind="assertion",
        action_name="assert_with_ai",
        params={
            "assertion": "The General gear badge uses neutral metallic tint.",
            "surface": "image_content",
            "full_screen_reason": "Inspect full Settings icon list.",
        },
    )
    # Without opt-in upload policy, visual assertion fails closed as incomplete/privacy_blocked
    closed_res = harness.invoke_action(step, HarnessContext(platform="ios"))
    assert closed_res.status == "incomplete"
    assert closed_res.metadata["ai_assertion"]["error_code"] == "privacy_blocked"
    # With allow_provider_upload=True, visual assertion succeeds and attaches host capture envelope
    from fsq_agent.models import AndroidVisualAssertionPolicy

    open_harness = IOSHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "visual-run-open"),
        ai_assertion_evaluator=StubEvaluator(),
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
        visual_assertion_provider_available=True,
    )
    open_res = open_harness.invoke_action(step, HarnessContext(platform="ios"))
    assert open_res.status == "passed"
    assert len(open_res.artifact_refs) == 1
    assert open_res.artifact_refs[0].metadata["capture"]["pointer_mapping_state"] == "unverified"


@pytest.mark.usefixtures("android_sdk")
def test_ios_inspect_with_ai_projects_ios_platform_and_candidates(tmp_path: Path) -> None:
    from fsq_agent.models import AIVisualCandidate, AIVisualInspectionResult, AndroidVisualAssertionPolicy, IOSInspectWithAIParams, VisualImageBounds

    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    class StubVisualEvaluator:
        def inspect(self, request: Any, *, cancellation_check: object = None) -> AIVisualInspectionResult:
            assert request.platform == "ios"
            return AIVisualInspectionResult(
                summary="Settings root shows General row.",
                candidates=(
                    AIVisualCandidate(
                        label="General",
                        image_bounds=VisualImageBounds(x=0, y=1, width=2, height=2),
                        confidence=0.92,
                    ),
                ),
            )

    harness = IOSHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "ios-inspect-ai"),
        ai_assertion_evaluator=StubVisualEvaluator(),
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
        visual_assertion_provider_available=True,
    )
    step = ExecutableStep(
        step_id="insp-1",
        kind="observation",
        action_name="inspect_with_ai",
        params=IOSInspectWithAIParams(prompt="Locate General row", mode="locate").model_dump(exclude_none=True),
    )
    result = harness.invoke_action(step, HarnessContext(platform="ios"))
    assert result.status == "passed"
    assert result.output["mode"] == "locate"
    assert result.output["candidates"][0]["label"] == "General"
    assert result.output["candidates"][0]["pointer_center"] == {"x": 1, "y": 2}


def test_ios_input_text_replaces_placeholder_equal_to_value_by_clearing() -> None:
    session = FakeSession()
    placeholder_field = FakeElement(
        by="accessibility id",
        value="email_field",
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "value": ("value", "Email"),
            "placeholder_value": ("value", "Email"),
        },
    )
    session.elements[("accessibility id", "email_field", None)] = placeholder_field
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)
    res = driver.input_text(
        IOSInputTextParams(
            locator=IOSLocator(accessibility_id="email_field"),
            text="alice@example.com",
        )
    )
    assert res["status"] == "passed"
    method_names = [name for name, _ in placeholder_field.calls]
    assert "clear" not in method_names
    assert "send_keys" in method_names

    existing_field = FakeElement(
        by="accessibility id",
        value="filled_field",
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "value": ("value", "old@example.com"),
            "placeholder_value": ("value", "Email"),
        },
    )
    session.elements[("accessibility id", "filled_field", None)] = existing_field
    res2 = driver.input_text(
        IOSInputTextParams(
            locator=IOSLocator(accessibility_id="filled_field"),
            text="new@example.com",
            clear=True,
        )
    )
    assert res2["status"] == "passed"
    method_names2 = [name for name, _ in existing_field.calls]
    assert "clear" in method_names2
    assert "send_keys" in method_names2


def test_ios_conjunctive_structural_and_predicate_locator_verifies_self_and_narrows_class_chain() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    # 1. class_chain + label appends [`label == "General"`] directly into the native class_chain query
    target_btn = FakeElement(by="-ios class chain", value='**/XCUIElementTypeButton[`label == "General"`]')
    session.elements[("-ios class chain", '**/XCUIElementTypeButton[`label == "General"`]', None)] = target_btn
    res_chain = driver.tap_on(IOSTapOnParams(locator=IOSLocator(class_chain="**/XCUIElementTypeButton", label="General")))
    assert res_chain["status"] == "passed"
    assert ("click", {"wait_timeout_seconds": 15.0}) in target_btn.calls

    # 2. xpath + predicate where only a child matches predicate must NOT click the child or false-pass
    cell_el = FakeElement(
        by="xpath",
        value="//XCUIElementTypeCell[1]",
        attrs={
            "type": ("value", "XCUIElementTypeCell"),
            "id": ("value", "cell_disabled"),
            "label": ("value", "Wi-Fi"),
            "enabled": ("value", False),
            "bounds": ("value", _bounds(0, 100, 402, 52)),
        },
    )
    child_label = FakeElement(
        by="-ios predicate string",
        value="enabled == true",
        parent=cell_el,
        attrs={
            "type": ("value", "XCUIElementTypeStaticText"),
            "id": ("value", "lbl_wifi"),
            "label": ("value", "Wi-Fi"),
            "enabled": ("value", True),
            "bounds": ("value", _bounds(16, 110, 100, 24)),
        },
    )
    session.elements[("xpath", "//XCUIElementTypeCell[1]", None)] = cell_el
    session.scoped_elements[("-ios predicate string", "enabled == true", cell_el)] = child_label
    res_mismatch = driver.tap_on(IOSTapOnParams(locator=IOSLocator(xpath="//XCUIElementTypeCell[1]", predicate="enabled == true")))
    assert res_mismatch["status"] == "failed"
    assert res_mismatch["failure_category"] == "target_resolution_error"
    assert all(name != "click" for name, _ in child_label.calls)
    assert all(name != "click" for name, _ in cell_el.calls)

    # 3. xpath + predicate where both container and child match (subtree ambiguous) narrows to container self
    enabled_cell = FakeElement(
        by="xpath",
        value="//XCUIElementTypeCell[2]",
        attrs={
            "type": ("value", "XCUIElementTypeCell"),
            "id": ("value", "cell_bluetooth"),
            "label": ("value", "Bluetooth"),
            "value": ("value", "On"),
            "enabled": ("value", True),
            "bounds": ("value", _bounds(0, 152, 402, 52)),
        },
    )
    ambiguous_subtree = FakeElement(
        by="-ios predicate string",
        value="enabled == true",
        parent=enabled_cell,
        inspect_error=TargetError("target_ambiguous"),
    )
    narrowed_query = (
        '(enabled == true) AND type == "XCUIElementTypeCell" AND name == "cell_bluetooth" '
        'AND label == "Bluetooth" AND value == "On" AND rect.x == 0 AND rect.y == 152 AND rect.width == 402 AND rect.height == 52'
    )
    session.elements[("xpath", "//XCUIElementTypeCell[2]", None)] = enabled_cell
    session.scoped_elements[("-ios predicate string", "enabled == true", enabled_cell)] = ambiguous_subtree
    session.scoped_elements[("-ios predicate string", narrowed_query, enabled_cell)] = enabled_cell
    res_ambig_self = driver.tap_on(IOSTapOnParams(locator=IOSLocator(xpath="//XCUIElementTypeCell[2]", predicate="enabled == true")))
    assert res_ambig_self["status"] == "passed"
    assert any(name == "click" for name, _ in enabled_cell.calls)


def test_ios_first_step_coordinate_safe_replay_params_and_ui_snapshot_compaction() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    tap_res = driver.tap_at(IOSTapAtParams(point=IOSPoint(x=100, y=200)))
    assert tap_res["status"] == "passed"
    assert tap_res["metadata"]["safe_replay_params"]["reference_screen_size"] == {"width": 402, "height": 874}

    swipe_res = driver.swipe(IOSSwipeParams(start=IOSPoint(x=100, y=500), end=IOSPoint(x=100, y=200)))
    assert swipe_res["status"] == "passed"
    assert swipe_res["metadata"]["safe_replay_params"]["reference_screen_size"] == {"width": 402, "height": 874}

    snap_res = driver.ui_snapshot(IOSUiSnapshotParams())
    compaction = snap_res["compaction"]
    assert compaction["applied"] is True
    assert compaction["text_attribute_limit"] == 160
    assert "layout_nodes_removed" in compaction
    assert "clipped_attributes" in compaction


def test_ios_close_preserves_borrowed_session_and_settles_owned_uncertain_session() -> None:
    borrowed = FakeSession()
    borrowed_driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=borrowed)
    borrowed_driver.close()
    assert borrowed_driver._session is borrowed
    assert borrowed_driver.tap_at(IOSTapAtParams(point=IOSPoint(x=10, y=20)))["status"] == "passed"

    try:
        from appium_connect_agent.automation import ResourceStateError as _SDKResourceStateError
    except (ImportError, ModuleNotFoundError):

        class QuitUnknownError(RuntimeError):
            code = "session_quit_unknown"
            request_state = "response_unknown"

        QuitUnknownError.__name__ = "ResourceStateError"
        quit_unknown_exc: BaseException = QuitUnknownError("remote quit uncertain")
    else:
        quit_unknown_exc = _SDKResourceStateError("session_quit_unknown", "a" * 32, "response_unknown")

    class OwnedSession:
        def __init__(self) -> None:
            self.state = "active"
            self.dispose_called = False

        def session_info(self) -> Any:
            return SimpleNamespace(state=self.state)

        def quit(self, *, control: object = None) -> None:
            self.state = "uncertain"
            raise quit_unknown_exc

        def dispose(self, *, control: object = None) -> None:
            self.dispose_called = True
            self.state = "disposed"

    class FakeScope:
        def __init__(self, runtime: FakeRuntime) -> None:
            self._runtime = runtime
            self.exited = False

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            self.exited = True
            return False

    class FakeRuntime:
        def __init__(self) -> None:
            self.managed_cleanup_pending = True
            self.cleanup_calls = 0

        def cleanup_managed(self, *, control: object = None) -> None:
            self.cleanup_calls += 1
            self.managed_cleanup_pending = False

    owned_driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences")
    owned_session = OwnedSession()
    fake_rt = FakeRuntime()
    fake_scope = FakeScope(fake_rt)
    owned_driver._session = owned_session
    owned_driver._runtime = fake_rt
    owned_driver._managed_scope = fake_scope

    owned_driver.close()
    assert owned_session.dispose_called is True
    assert fake_scope.exited is True
    assert fake_rt.cleanup_calls == 1
    assert owned_driver._session is None
    assert owned_driver._executor is None


def test_ios_input_and_clear_text_anchoring_response_unknown_and_readback_safety() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    # 1. Mutable bare-target / value-based locator without `id` rebinds via exact type+rect predicate before clear/send_keys
    rect_pred = 'type == "XCUIElementTypeTextField" AND rect.x == 24 AND rect.y == 220 AND rect.width == 320 AND rect.height == 44'
    mutable_el = FakeElement(
        by="predicate",
        value='name == "Draft" OR value == "Draft"',
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "id": ("missing", None),
            "label": ("missing", None),
            "value": ("value", "Draft"),
            "placeholder_value": ("value", "Title"),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "bounds": ("value", _bounds(24, 220, 320, 44)),
        },
    )
    session.elements[("name", "Draft", None)] = FakeElement(by="name", value="Draft", inspect_error=TargetError("target_not_found"))
    session.elements[("-ios predicate string", 'label == "Draft"', None)] = FakeElement(
        by="predicate",
        value='label == "Draft"',
        inspect_error=TargetError("target_not_found"),
    )
    session.elements[("-ios predicate string", 'name == "Draft" OR value == "Draft"', None)] = mutable_el
    session.elements[("-ios predicate string", rect_pred, None)] = mutable_el
    anchored_res = driver.input_text(IOSInputTextParams(target="Draft", text="Final Report", clear=True))
    assert anchored_res["status"] == "passed"
    assert ("clear", {"wait_timeout_seconds": 15.0}) in mutable_el.calls
    assert ("send_keys", {"text": "Final Report"}) in mutable_el.calls

    # 2. clearText rejects disabled/hidden/non-editable controls and verifies placeholder-aware clear readback
    disabled_field = FakeElement(
        by="accessibility id",
        value="disabled_input",
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "id": ("value", "disabled_input"),
            "enabled": ("value", False),
            "displayed": ("value", True),
        },
    )
    session.elements[("accessibility id", "disabled_input", None)] = disabled_field
    dis_clear = driver.clear_text(IOSClearTextParams(locator=IOSLocator(accessibility_id="disabled_input")))
    assert dis_clear["status"] == "failed"
    assert dis_clear["failure_category"] == "target_resolution_error"
    assert all(name != "clear" for name, _ in disabled_field.calls)

    clearable_field = FakeElement(
        by="accessibility id",
        value="notes_input",
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "id": ("value", "notes_input"),
            "value": ("value", "Temp Note"),
            "placeholder_value": ("value", "Enter note"),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "bounds": ("value", _bounds(16, 300, 350, 44)),
        },
    )
    session.elements[("accessibility id", "notes_input", None)] = clearable_field
    assert driver.clear_text(IOSClearTextParams(locator=IOSLocator(accessibility_id="notes_input")))["status"] == "passed"

    # 3. send_keys with request_state="response_unknown" MUST NOT fall back to send_macos_keys (no duplicate text)
    class UnknownSendError(RuntimeError):
        code = "execution_unknown"
        request_state = "response_unknown"
        operation_id = "a" * 32
        completed_steps = ("target_resolved", " secret_step_ignored ")

    unknown_field = FakeElement(
        by="accessibility id",
        value="amount_input",
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "id": ("value", "amount_input"),
            "value": ("value", ""),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "bounds": ("value", _bounds(16, 360, 200, 44)),
        },
        send_keys_error=UnknownSendError("socket dropped mid-POST"),
    )
    session.elements[("accessibility id", "amount_input", None)] = unknown_field
    unk_res = driver.input_text(IOSInputTextParams(locator=IOSLocator(accessibility_id="amount_input"), text="100"))
    assert unk_res["status"] == "failed"
    assert unk_res["failure_category"] == "action_error"
    assert unk_res["metadata"]["request_state"] == "response_unknown"
    assert unk_res["metadata"]["backend_code"] == "execution_unknown"
    assert unk_res["metadata"]["operation_id"] == "a" * 32
    assert unk_res["metadata"]["completed_steps"] == ["target_resolved"]
    assert all(name != "send_macos_keys" for name, _ in unknown_field.calls)

    # 4. Silent write failure (readback mismatch) fails explicitly on non-secure controls, while SecureTextField skips value readback
    class SilentNoOpElement(FakeElement):
        def send_keys(self, text: str, *, wait_timeout_seconds: float, control: object = None) -> None:
            self.calls.append(("send_keys", {"text": text}))
            # Intentionally leave value unchanged

    silent_el = SilentNoOpElement(
        by="accessibility id",
        value="broken_field",
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "id": ("value", "broken_field"),
            "value": ("value", ""),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "bounds": ("value", _bounds(16, 420, 200, 44)),
        },
    )
    session.elements[("accessibility id", "broken_field", None)] = silent_el
    mismatch_res = driver.input_text(IOSInputTextParams(locator=IOSLocator(accessibility_id="broken_field"), text="Expected"))
    assert mismatch_res["status"] == "failed"
    assert mismatch_res["failure_category"] == "action_error"

    secure_silent = SilentNoOpElement(
        by="accessibility id",
        value="pin_field",
        attrs={
            "type": ("value", "XCUIElementTypeSecureTextField"),
            "id": ("value", "pin_field"),
            "value": ("value", "••••"),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "bounds": ("value", _bounds(16, 480, 200, 44)),
        },
    )
    session.elements[("accessibility id", "pin_field", None)] = secure_silent
    sec_res = driver.input_text(IOSInputTextParams(locator=IOSLocator(accessibility_id="pin_field"), text="SecretPin!"))
    assert sec_res["status"] == "passed"
    post_inspect_attrs = secure_silent.calls[-1][1]["attributes"]
    assert "value" not in post_inspect_attrs


def test_ios_swipe_safe_interior_ranges_and_validated_element_insets() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    # Untargeted swipe uses 20%-80% vertical and 18%-82% horizontal via swipe_between (never raw Device.swipe)
    session.device.calls.clear()
    assert driver.swipe(IOSSwipeParams(direction="up"))["status"] == "passed"
    assert driver.swipe(IOSSwipeParams(direction="right"))["status"] == "passed"
    swipe_calls = [payload for name, payload in session.device.calls if name == "swipe_between"]
    assert len(swipe_calls) == 2
    assert all(name != "swipe" for name, _ in session.device.calls)
    # For 402x874: mid_x=201, top=round(874*0.20)=175, bottom=round(874*0.80)=699; left=round(402*0.18)=72, right=round(402*0.82)=330
    assert swipe_calls[0] == {"start_x": 201, "start_y": 699, "end_x": 201, "end_y": 175, "duration_ms": 300}
    assert swipe_calls[1] == {"start_x": 72, "start_y": 437, "end_x": 330, "end_y": 437, "duration_ms": 300}

    # Element swipe at percent=1.0 validates bounds and applies horizontal inset instead of unvalidated macos_swipe
    slider = FakeElement(
        by="accessibility id",
        value="volume_slider",
        attrs={
            "type": ("value", "XCUIElementTypeSlider"),
            "id": ("value", "volume_slider"),
            "enabled": ("value", True),
            "bounds": ("value", _bounds(20, 400, 300, 40)),
        },
    )
    session.elements[("accessibility id", "volume_slider", None)] = slider
    session.device.calls.clear()
    el_swipe = driver.swipe(IOSSwipeParams(locator=IOSLocator(accessibility_id="volume_slider"), direction="right", percent=1.0))
    assert el_swipe["status"] == "passed"
    assert all(name != "macos_swipe" for name, _ in slider.calls)
    # inset = min(40//2, 300//4) = 20 -> start_x = 20 + 20 = 40, end_x = min(401, 20 + 300) = 320, mid_y = 420
    assert ("swipe_between", {"start_x": 40, "start_y": 420, "end_x": 320, "end_y": 420, "duration_ms": 300}) in session.device.calls

    # Off-screen element bounds rejected safely
    offscreen = FakeElement(
        by="accessibility id",
        value="offscreen_slider",
        attrs={"enabled": ("value", True), "bounds": ("value", _bounds(350, 400, 120, 40))},
    )
    session.elements[("accessibility id", "offscreen_slider", None)] = offscreen
    off_res = driver.swipe(IOSSwipeParams(locator=IOSLocator(accessibility_id="offscreen_slider"), direction="left", percent=1.0))
    assert off_res["status"] == "failed"
    assert off_res["failure_category"] == "action_error"


def test_ios_safe_diagnostics_and_non_definitive_target_error_redaction() -> None:
    session = FakeSession()
    driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session)

    class UnlistedError(RuntimeError):
        code = "udid_00008110_secret_code"
        request_state = "raw_http_body_leak"
        operation_id = "NOT-HEX-SECRET"
        completed_steps = ("target_resolved", "leaked_password_value")

    diag = driver.safe_diagnostics(UnlistedError("raw message with secret"))
    assert "backend_code" not in diag
    assert "request_state" not in diag
    assert "operation_id" not in diag
    assert diag["completed_steps"] == ["target_resolved"]
    assert diag["session_state"] == "active"
    assert "secret" not in str(diag)

    # TargetError with response_unknown is NOT classified as target_not_found
    class UncertainTargetError(Exception):
        code = "target_not_found"
        request_state = "response_unknown"

    UncertainTargetError.__name__ = "TargetError"
    uncertain_btn = FakeElement(by="accessibility id", value="flaky_btn")
    session.elements[("accessibility id", "flaky_btn", None)] = uncertain_btn

    def raise_uncertain(*, wait_timeout_seconds: float, control: object = None) -> None:
        raise UncertainTargetError("transport reset")

    uncertain_btn.click = raise_uncertain  # type: ignore[method-assign]
    res = driver.tap_on(IOSTapOnParams(locator=IOSLocator(accessibility_id="flaky_btn")))
    assert res["status"] == "failed"
    assert res["failure_category"] == "action_error"
    assert res["metadata"].get("resolution_reason") != "not_found"

    # Non-definitive TargetError or timeout during element lookup produces action_error/timeout_error (never ConfigurationError)
    lookup_uncertain = FakeElement(
        by="accessibility id",
        value="lookup_uncertain_btn",
        inspect_error=UncertainTargetError("transport reset during lookup"),
    )
    session.elements[("accessibility id", "lookup_uncertain_btn", None)] = lookup_uncertain
    lookup_res = driver.tap_on(IOSTapOnParams(locator=IOSLocator(accessibility_id="lookup_uncertain_btn")))
    assert lookup_res["status"] == "failed"
    assert lookup_res["failure_category"] == "action_error"
    assert lookup_res["metadata"]["resolution_reason"] == "backend_error"
    assert lookup_res["metadata"]["request_state"] == "response_unknown"

    class LookupTimeoutError(Exception):
        code = "operation_timeout"
        request_state = "not_sent"

    LookupTimeoutError.__name__ = "OperationTimeoutError"
    lookup_timeout_el = FakeElement(
        by="accessibility id",
        value="lookup_timeout_btn",
        inspect_error=LookupTimeoutError("lookup deadline expired"),
    )
    session.elements[("accessibility id", "lookup_timeout_btn", None)] = lookup_timeout_el
    timeout_res = driver.tap_on(IOSTapOnParams(locator=IOSLocator(accessibility_id="lookup_timeout_btn")))
    assert timeout_res["status"] == "failed"
    assert timeout_res["failure_category"] == "timeout_error"
    assert timeout_res["metadata"]["backend_code"] == "operation_timeout"

    # Unsupported pressKey never echoes caller-supplied key text
    with pytest.raises(ConfigurationError) as bad_key_err:
        driver.press_key(IOSPressKeyParams(key="SUPER_SECRET_KEY_VALUE_999"))
    assert "SUPER_SECRET_KEY_VALUE_999" not in str(bad_key_err.value)

    # inputText(text="", clear=False) still verifies post-write empty readback, and no-id bounds shift fails identity check
    non_empty_no_id = FakeElement(
        by="predicate",
        value='placeholderValue == "Code"',
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "id": ("missing", None),
            "value": ("value", "ResidualText"),
            "placeholder_value": ("value", "Code"),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "bounds": ("value", _bounds(10, 500, 180, 40)),
        },
    )
    session.elements[("-ios predicate string", 'placeholderValue == "Code"', None)] = non_empty_no_id
    empty_check = driver.input_text(IOSInputTextParams(locator=IOSLocator(predicate='placeholderValue == "Code"'), text="", clear=False))
    assert empty_check["status"] == "failed"
    assert empty_check["failure_category"] == "action_error"

    class BoundsShiftingElement(FakeElement):
        def send_keys(self, text: str, *, wait_timeout_seconds: float, control: object = None) -> None:
            super().send_keys(text, wait_timeout_seconds=wait_timeout_seconds, control=control)
            self.attrs["bounds"] = ("value", _bounds(10, 620, 180, 40))

    shifting_el = BoundsShiftingElement(
        by="predicate",
        value='placeholderValue == "Shifting"',
        attrs={
            "type": ("value", "XCUIElementTypeTextField"),
            "id": ("missing", None),
            "value": ("value", ""),
            "placeholder_value": ("value", "Shifting"),
            "enabled": ("value", True),
            "displayed": ("value", True),
            "bounds": ("value", _bounds(10, 560, 180, 40)),
        },
    )
    session.elements[("-ios predicate string", 'placeholderValue == "Shifting"', None)] = shifting_el
    shift_res = driver.input_text(IOSInputTextParams(locator=IOSLocator(predicate='placeholderValue == "Shifting"'), text="abc", clear=False))
    assert shift_res["status"] == "failed"
    assert shift_res["failure_category"] == "action_error"
    assert "bounds changed" in str(shift_res["error_message"])


class _IntSubclass(int):
    pass


@pytest.mark.parametrize(
    "invalid_kwargs",
    [
        {"xcuitest_wait_for_idle_timeout_seconds": True},
        {"xcuitest_wait_for_idle_timeout_seconds": False},
        {"xcuitest_wait_for_idle_timeout_seconds": "2.0"},
        {"xcuitest_wait_for_idle_timeout_seconds": float("nan")},
        {"xcuitest_wait_for_idle_timeout_seconds": float("inf")},
        {"xcuitest_wait_for_idle_timeout_seconds": -0.001},
        {"xcuitest_wait_for_idle_timeout_seconds": 600.001},
        {"xcuitest_snapshot_max_depth": True},
        {"xcuitest_snapshot_max_depth": _IntSubclass(25)},
        {"xcuitest_snapshot_max_depth": 25.0},
        {"xcuitest_snapshot_max_depth": "25"},
        {"xcuitest_snapshot_max_depth": 0},
        {"xcuitest_snapshot_max_depth": -1},
        {"xcuitest_snapshot_max_depth": 501},
        {"xcuitest_accessibility_deadline_seconds": True},
        {"xcuitest_accessibility_deadline_seconds": "10.0"},
        {"xcuitest_accessibility_deadline_seconds": float("nan")},
        {"xcuitest_accessibility_deadline_seconds": float("-inf")},
        {"xcuitest_accessibility_deadline_seconds": -0.001},
        {"xcuitest_accessibility_deadline_seconds": 600.001},
        {"xcuitest_animation_cool_off_timeout_seconds": True},
        {"xcuitest_animation_cool_off_timeout_seconds": False},
        {"xcuitest_animation_cool_off_timeout_seconds": "2.0"},
        {"xcuitest_animation_cool_off_timeout_seconds": float("nan")},
        {"xcuitest_animation_cool_off_timeout_seconds": float("inf")},
        {"xcuitest_animation_cool_off_timeout_seconds": float("-inf")},
        {"xcuitest_animation_cool_off_timeout_seconds": -0.001},
        {"xcuitest_animation_cool_off_timeout_seconds": 600.001},
    ],
)
def test_ios_harness_settings_rejects_invalid_initial_wda_policies(invalid_kwargs: dict[str, Any]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IOSHarnessSettings(**invalid_kwargs)


def test_ios_harness_settings_and_driver_policy_forwarding_and_borrowed_rejection() -> None:
    from fsq_agent.core.interfaces import DriverFactory

    defaults = IOSHarnessSettings()
    assert defaults.xcuitest_wait_for_idle_timeout_seconds is None
    assert defaults.xcuitest_snapshot_max_depth is None
    assert defaults.xcuitest_accessibility_deadline_seconds is None
    assert defaults.xcuitest_animation_cool_off_timeout_seconds is None

    tuned = IOSHarnessSettings(
        action_timeout_seconds=15,
        xcuitest_wait_for_idle_timeout_seconds=0,
        xcuitest_snapshot_max_depth=25,
        xcuitest_accessibility_deadline_seconds=30,
        xcuitest_animation_cool_off_timeout_seconds=600,
    )
    assert tuned.xcuitest_wait_for_idle_timeout_seconds == 0.0
    assert type(tuned.xcuitest_wait_for_idle_timeout_seconds) is float
    assert tuned.xcuitest_snapshot_max_depth == 25
    assert type(tuned.xcuitest_snapshot_max_depth) is int
    assert tuned.xcuitest_accessibility_deadline_seconds == 30.0
    assert type(tuned.xcuitest_accessibility_deadline_seconds) is float
    assert tuned.xcuitest_animation_cool_off_timeout_seconds == 600.0
    assert type(tuned.xcuitest_animation_cool_off_timeout_seconds) is float

    driver = DriverFactory().create_ios_driver(tuned, bundle_id="com.apple.Preferences", udid="SIM-UDID")
    assert driver.xcuitest_wait_for_idle_timeout_seconds == 0.0
    assert driver.xcuitest_snapshot_max_depth == 25
    assert driver.xcuitest_accessibility_deadline_seconds == 30.0
    assert driver._snapshot_timeout_seconds() == 40.0
    assert driver._action_control_timeout_seconds() == 35.0

    session = FakeSession()
    for policy_kwarg in (
        {"xcuitest_wait_for_idle_timeout_seconds": 2.0},
        {"xcuitest_snapshot_max_depth": 25},
        {"xcuitest_accessibility_deadline_seconds": 10.0},
        {"xcuitest_animation_cool_off_timeout_seconds": 0.0},
    ):
        with pytest.raises(ConfigurationError, match="borrowed iOS Session"):
            AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=session, **policy_kwarg)


@pytest.mark.parametrize(
    ("cool_off_seconds", "excluded_attributes"),
    [(None, None), (0.0, ("visible", "accessible")), (0.25, ("nativeAccessibilityElement",)), (600.0, None)],
)
def test_ios_policies_are_forwarded_to_initial_and_replacement_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cool_off_seconds: float | None,
    excluded_attributes: tuple[str, ...] | None,
) -> None:
    from appium_connect_agent import automation, runtime

    from fsq_agent.config import load_platform_settings
    from fsq_agent.core.interfaces import DriverFactory
    from fsq_agent.drivers.ios import _appium_connect

    settings = load_platform_settings("ios", user_config_root=tmp_path).harness.ios
    assert "xcuitest_animation_cool_off_timeout_seconds" not in settings.model_fields_set
    assert settings.xcuitest_animation_cool_off_timeout_seconds is None
    if cool_off_seconds is not None:
        settings.xcuitest_animation_cool_off_timeout_seconds = cool_off_seconds
    assert "xcuitest_page_source_excluded_attributes" not in settings.model_fields_set
    assert settings.xcuitest_page_source_excluded_attributes is None
    settings.xcuitest_page_source_excluded_attributes = excluded_attributes

    connection = object()
    initial = SimpleNamespace(quit=Mock())
    replacement = SimpleNamespace(quit=Mock())
    create_session = Mock(side_effect=(initial, replacement))
    monkeypatch.setattr(automation, "create_xcuitest_session", create_session)
    monkeypatch.setattr(automation, "list_ios_devices", lambda **kwargs: ())
    monkeypatch.setattr(runtime, "Runtime", lambda settings: SimpleNamespace(start_managed=lambda **kwargs: nullcontext(connection)))
    monkeypatch.setattr(_appium_connect, "_resolve_managed_local_settings", lambda settings_cls: settings_cls())
    monkeypatch.setattr(_appium_connect, "_allocate_loopback_port", lambda: 4723)

    driver = DriverFactory().create_ios_driver(settings, bundle_id="com.apple.Preferences", udid="SIM-UDID")
    try:
        assert driver.ensure_session() is initial
        assert driver.launch_app(IOSLaunchAppParams(new_session=True))["status"] == "passed"
        assert driver.ensure_session() is replacement
        assert len(create_session.call_args_list) == 2
        for call in create_session.call_args_list:
            assert call.kwargs["xcuitest_animation_cool_off_timeout_seconds"] == cool_off_seconds
            if excluded_attributes is None:
                assert "xcuitest_page_source_excluded_attributes" not in call.kwargs
            else:
                assert call.kwargs["xcuitest_page_source_excluded_attributes"] == excluded_attributes
    finally:
        driver.close()


@pytest.mark.parametrize("value", ["visible", b"visible", [], (), {"visible"}, ["visible", "visible"], ["visible", 1], [b"visible"], ["Visible"], ["visible "], ["bogus"]])
def test_ios_page_source_exclusions_reject_invalid_values(value: object) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="xcuitest_page_source_excluded_attributes"):
        IOSHarnessSettings.model_validate({"xcuitest_page_source_excluded_attributes": value})


@pytest.mark.parametrize("value", [["accessible", "visible"], ("accessible", "visible")])
def test_ios_page_source_exclusions_normalize_to_ordered_tuple(value: list[str] | tuple[str, ...]) -> None:
    settings = IOSHarnessSettings.model_validate({"xcuitest_page_source_excluded_attributes": value})
    assert settings.xcuitest_page_source_excluded_attributes == ("accessible", "visible")


def test_ios_page_source_exclusions_reject_borrowed_session() -> None:
    with pytest.raises(ConfigurationError, match="borrowed iOS Session"):
        AppiumConnectIOSDriver(session=FakeSession(), xcuitest_page_source_excluded_attributes=["visible", "accessible"])
