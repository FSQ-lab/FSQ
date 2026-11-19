# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
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
        send_keys_error=RuntimeError("WDA key typing glitch"),
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
    assert compaction["text_attribute_limit"] == 50
    assert "layout_nodes_removed" in compaction
    assert "clipped_attributes" in compaction


def test_ios_close_preserves_borrowed_session_and_settles_owned_uncertain_session() -> None:
    borrowed = FakeSession()
    borrowed_driver = AppiumConnectIOSDriver(bundle_id="com.apple.Preferences", session=borrowed)
    borrowed_driver.close()
    assert borrowed_driver._session is borrowed
    assert borrowed_driver.tap_at(IOSTapAtParams(point=IOSPoint(x=10, y=20)))["status"] == "passed"

    class QuitUnknownError(RuntimeError):
        code = "session_quit_unknown"

    class OwnedSession:
        def __init__(self) -> None:
            self.state = "active"
            self.dispose_called = False

        def session_info(self) -> Any:
            return SimpleNamespace(state=self.state)

        def quit(self, *, control: object = None) -> None:
            self.state = "uncertain"
            raise QuitUnknownError("remote quit uncertain")

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
