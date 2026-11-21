# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

if TYPE_CHECKING:
    from pathlib import Path


import pytest
from PIL import Image
from pydantic import ValidationError

from fsq_agent.core import ArtifactStore, HarnessInterface
from fsq_agent.core.harness._ai_assertion_tool import AIAssertionBackendToolMixin
from fsq_agent.core.harness._appium_connect_macos_driver import AppiumConnectMacOSDriver
from fsq_agent.core.harness._driver_tools import _macos_driver_tool
from fsq_agent.core.harness._macos import MacOSHarness
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    AIVisualInspectionRequest,
    AIVisualInspectionResult,
    AndroidVisualAssertionPolicy,
    ConfigurationError,
    ExecutableStep,
    HarnessContext,
    MacOSAssertElementsOrderParams,
    MacOSAssertNotVisibleParams,
    MacOSAssertStateParams,
    MacOSAssertVisibleParams,
    MacOSAssertWithAIParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSInspectElementParams,
    MacOSInspectWithAIParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSLocator,
    MacOSPoint,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
    MacOSScrollOnParams,
    MacOSTakeScreenshotParams,
    MacOSTypeTextParams,
    MacOSUiSnapshotParams,
)


def _valid_png_bytes(width: int = 80, height: int = 60) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (30, 120, 200, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _obs(value: object, state: str = "value") -> Any:
    return SimpleNamespace(state=state, value=value if state == "value" else None)


def _attr_obs(name: str, value: object, state: str = "value") -> Any:
    return SimpleNamespace(name=name, state=state, value=value if state == "value" else None)


def _bounds(x: int, y: int, width: int = 10, height: int = 10) -> Any:
    return SimpleNamespace(x=x, y=y, width=width, height=height)


class TargetError(RuntimeError):
    def __init__(self, code: str, message: str = "target error") -> None:
        super().__init__(message)
        self.code = code


class FakeMacOSDriver(AIAssertionBackendToolMixin):
    backend = "fake-appium-connect-macos"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def context(self) -> dict[str, object]:
        self.calls.append(("context", None))
        return {
            "session_id": "mac2:session",
            "current_url": None,
            "screen_size": (1440, 900),
            "capabilities": {"platformName": "Mac"},
            "metadata": {"backend": self.backend, "bundle_id_configured": True},
        }

    def _record(self, method_name: str, params: object) -> dict[str, object]:
        recorded = params.model_dump(mode="json", exclude_none=True) if hasattr(params, "model_dump") else params
        self.calls.append((method_name, recorded))
        return {"status": "passed", "output": {method_name: True}}

    @_macos_driver_tool(
        "launchApp",
        description="Launch a configured macOS application.",
    )
    def launch_app(self, params: MacOSLaunchAppParams) -> dict[str, object]:
        return self._record("launch_app", params)

    @_macos_driver_tool("killApp", description="Stop the active macOS application.")
    def kill_app(self, params: MacOSKillAppParams) -> dict[str, object]:
        return self._record("kill_app", params)

    @_macos_driver_tool("clickOn", description="Click a macOS element or point.")
    def click_on(self, params: MacOSClickOnParams) -> dict[str, object]:
        return self._record("click_on", params)

    @_macos_driver_tool("doubleClickOn", description="Double-click a macOS element or point.")
    def double_click_on(self, params: MacOSDoubleClickOnParams) -> dict[str, object]:
        return self._record("double_click_on", params)

    @_macos_driver_tool("rightClickOn", description="Right-click a macOS element or point.")
    def right_click_on(self, params: MacOSRightClickOnParams) -> dict[str, object]:
        return self._record("right_click_on", params)

    @_macos_driver_tool("typeText", description="Type text into macOS.")
    def type_text(self, params: MacOSTypeTextParams) -> dict[str, object]:
        return self._record("type_text", params)

    @_macos_driver_tool("pressKey", description="Press a macOS key or shortcut.")
    def press_key(self, params: MacOSPressKeyParams) -> dict[str, object]:
        return self._record("press_key", params)

    @_macos_driver_tool("hoverOn", description="Hover over a macOS element or point.")
    def hover_on(self, params: MacOSHoverOnParams) -> dict[str, object]:
        return self._record("hover_on", params)

    @_macos_driver_tool("scrollOn", description="Scroll on a macOS element or point.")
    def scroll_on(self, params: MacOSScrollOnParams) -> dict[str, object]:
        return self._record("scroll_on", params)

    @_macos_driver_tool("dragTo", description="Drag between macOS elements or points.")
    def drag_to(self, params: MacOSDragToParams) -> dict[str, object]:
        return self._record("drag_to", params)

    @_macos_driver_tool("takeScreenshot", description="Capture a macOS screenshot.")
    def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes:
        recorded = params.model_dump(mode="json", exclude_none=True)
        self.calls.append(("take_screenshot", recorded))
        return _valid_png_bytes()

    @_macos_driver_tool("uiSnapshot", description="Return the macOS accessibility tree.")
    def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        recorded = params.model_dump(mode="json", exclude_none=True)
        self.calls.append(("ui_snapshot", recorded))
        return {"title": "Finder", "snapshot_type": "macos_accessibility_tree"}

    @_macos_driver_tool("inspectElement", description="Inspect a macOS element.")
    def inspect_element(self, params: MacOSInspectElementParams) -> dict[str, object]:
        recorded = params.model_dump(mode="json", exclude_none=True)
        self.calls.append(("inspect_element", recorded))
        return {"status": "passed", "output": {"attributes": {attr: {"state": "value", "value": "Save"} for attr in params.attributes}}}

    @_macos_driver_tool("inspectWithAI", description="Inspect current macOS frame with AI.")
    def inspect_with_ai(self, params: MacOSInspectWithAIParams) -> dict[str, object]:
        return self._record("inspect_with_ai", params)

    @_macos_driver_tool("assertVisible", description="Assert a macOS element is visible.")
    def assert_visible(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        return self._record("assert_visible", params)

    @_macos_driver_tool("assertNotVisible", description="Assert a macOS element is not visible.")
    def assert_not_visible(self, params: MacOSAssertNotVisibleParams) -> dict[str, object]:
        return self._record("assert_not_visible", params)

    @_macos_driver_tool("assert", description="Assert macOS element state or text.")
    def assert_state(self, params: MacOSAssertStateParams) -> dict[str, object]:
        return self._record("assert_state", params)

    @_macos_driver_tool("assertElementsOrder", description="Assert macOS elements appear in order.")
    def assert_elements_order(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        return self._record("assert_elements_order", params)

    @_macos_driver_tool("assertWithAI", description="Evaluate a macOS visual assertion with AI.")
    def assert_with_ai(self, params: MacOSAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        recorded = params.model_dump(mode="json", exclude_none=True) if hasattr(params, "model_dump") else ({} if isinstance(params, dict) else None)
        self.calls.append(("screenshot", recorded))
        return _valid_png_bytes()


def _step(action_name: str, params: dict[str, Any] | None = None) -> ExecutableStep:
    return ExecutableStep(step_id="step-1", kind="action", action_name=action_name, params=params or {})


def test_macos_harness_dispatches_fsq_action_names_to_driver() -> None:
    driver = FakeMacOSDriver()
    harness = MacOSHarness(driver=driver)

    context = harness.get_context()

    cases = [
        ("launchApp", {"bundle_id": "com.example.MacApp"}, "launch_app"),
        ("clickOn", {"target": "File"}, "click_on"),
        ("doubleClickOn", {"locator": {"accessibilityId": "Open"}}, "double_click_on"),
        ("rightClickOn", {"point": {"x": 10, "y": 20}}, "right_click_on"),
        ("typeText", {"target": "Search", "text": "hello"}, "type_text"),
        ("pressKey", {"key": "Enter"}, "press_key"),
        ("hoverOn", {"target": "Menu"}, "hover_on"),
        ("scrollOn", {"target": "List", "delta_y": -120}, "scroll_on"),
        ("dragTo", {"source": {"point": {"x": 1, "y": 2}}, "destination": {"target": "Trash"}}, "drag_to"),
        ("takeScreenshot", {}, "take_screenshot"),
        ("uiSnapshot", {}, "ui_snapshot"),
        ("inspectElement", {"target": "Save", "attributes": ["label", "enabled"]}, "inspect_element"),
        ("assertVisible", {"target": "Save"}, "assert_visible"),
        ("assertNotVisible", {"target": "Spinner"}, "assert_not_visible"),
        ("assert", {"element": {"accessibilityId": "Save", "enabled": True}}, "assert_state"),
        ("assertElementsOrder", {"elements": [{"target": "File"}, {"target": "Edit"}]}, "assert_elements_order"),
        ("killApp", {}, "kill_app"),
    ]
    for action_name, params, _method_name in cases:
        result = harness.invoke_action(_step(action_name, params), context)
        assert result.status == "passed"
        assert result.action_name == action_name

    assert isinstance(harness, HarnessInterface)
    assert context == HarnessContext(
        platform="macos",
        session_id="mac2:session",
        current_url=None,
        screen_size=(1440, 900),
        capabilities={"platformName": "Mac"},
        metadata={"backend": "fake-appium-connect-macos", "bundle_id_configured": True},
    )
    assert [method_name for method_name, _params in driver.calls] == ["context"] + [method_name for _action, _params, method_name in cases]
    type_text_call = next(params for method_name, params in driver.calls if method_name == "type_text")
    assert type_text_call == {"target": "Search", "text": "hello", "textType": "literal"}


def test_macos_harness_action_space_returns_catalog_backed_schemas() -> None:
    harness = MacOSHarness(driver=FakeMacOSDriver())

    schemas = {schema.name: schema for schema in harness.action_space()}

    assert "click_on" in schemas
    assert "scroll_on" in schemas
    assert "ui_snapshot" in schemas
    assert "inspect_element" in schemas
    assert "inspect_with_ai" in schemas
    assert "assert_not_visible" in schemas
    assert "assert_state" in schemas
    assert "assert_elements_order" in schemas
    assert "assert_with_ai" in schemas
    assert schemas["click_on"].fsq_action_name == "clickOn"
    assert schemas["click_on"].platform == "macos"
    assert schemas["click_on"].metadata["driver_class"] == "FakeMacOSDriver"
    assert schemas["click_on"].metadata["backend"] == "fake-appium-connect-macos"
    assert schemas["click_on"].metadata["replay"] == {"kind": "fsq_command", "alias": "clickOn"}
    assert "target" in schemas["click_on"].params_json_schema["properties"]
    assert "target, non-empty locator, or point" in schemas["click_on"].params_json_schema["description"]
    assert "macOS screen point" in schemas["click_on"].params_json_schema["properties"]["point"]["description"]
    assert schemas["ui_snapshot"].driver_method == "ui_snapshot"
    assert schemas["ui_snapshot"].fsq_action_name == "uiSnapshot"
    assert schemas["assert_elements_order"].fsq_action_name == "assertElementsOrder"


def test_macos_harness_validation_failure_does_not_call_driver_method() -> None:
    driver = FakeMacOSDriver()
    harness = MacOSHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"locator": {"unknown": "Login"}}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Invalid macOS parameters for clickOn."
    assert result.metadata["validation_errors"]
    assert driver.calls == [("context", None)]


def test_macos_harness_captures_screenshot_and_ui_snapshot_with_artifact_store(tmp_path) -> None:
    driver = FakeMacOSDriver()
    harness = MacOSHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    context = harness.get_context()

    screenshot_ref = harness.capture_artifact(
        kind="screenshot",
        reason="after click",
        context=context,
        step_id="step-1",
        phase="invoke",
    )
    snapshot_ref = harness.capture_artifact(
        kind="ui_snapshot",
        reason="after click",
        context=context,
        step_id="step-1",
        phase="finalize",
    )

    assert (tmp_path / screenshot_ref.path).read_bytes() == _valid_png_bytes()
    assert "Finder" in (tmp_path / snapshot_ref.path).read_text(encoding="utf-8")
    assert driver.calls[0] == ("context", None)
    assert ("screenshot", {}) in driver.calls
    assert ("ui_snapshot", {}) in driver.calls


def test_macos_harness_reports_unlaunched_session_without_implicit_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.MacApp")
    called = False

    def fake_ensure_session(params=None):
        nonlocal called
        called = True
        return FakeMacSession({})

    monkeypatch.setattr(driver, "_ensure_session", fake_ensure_session)
    harness = MacOSHarness(driver=driver)

    result = harness.invoke_action(_step("uiSnapshot", {}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Appium Mac2 session is not available. Call launchApp before macOS Appium actions."
    assert called is False


def test_macos_harness_records_unavailable_artifact_before_launch(tmp_path) -> None:
    harness = MacOSHarness(driver=AppiumConnectMacOSDriver(bundle_id="com.example.MacApp"), artifact_store=ArtifactStore(run_dir=tmp_path))

    artifact_ref = harness.capture_artifact(
        kind="ui_snapshot",
        reason="before click",
        context=harness.get_context(),
        step_id="step-1",
        phase="prepare",
    )

    payload = (tmp_path / artifact_ref.path).read_text(encoding="utf-8")
    assert artifact_ref.kind == "json"
    assert "macos_session_not_started" in payload


def test_macos_harness_assert_with_ai_uses_injected_evaluator(tmp_path) -> None:
    class FakeEvaluator:
        def __init__(self) -> None:
            self.requests: list[AIAssertionRequest] = []

        def evaluate(self, request: AIAssertionRequest, *, cancellation_check=None) -> AIAssertionResult:
            self.requests.append(request)
            return AIAssertionResult(
                status="passed",
                outcome="passed",
                passed=True,
                explanation="The expected macOS canvas chart is visible.",
                provider="fake",
                model="fake-model",
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
            )

    evaluator = FakeEvaluator()
    harness = MacOSHarness(
        driver=FakeMacOSDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )

    result = harness.invoke_action(
        _step(
            "assertWithAI",
            {
                "assertion": "The preview canvas displays a blue bar chart.",
                "surface": "custom_canvas",
                "full_screen_reason": "The preview canvas fills the window.",
            },
        ),
        harness.get_context(),
    )

    assert result.status == "passed"
    assert result.action_name == "assertWithAI"
    assert len(evaluator.requests) == 1
    assert evaluator.requests[0].platform == "macos"
    assert evaluator.requests[0].prompt == "The preview canvas displays a blue bar chart."


def test_macos_harness_assert_with_ai_without_evaluator_returns_incomplete(tmp_path) -> None:
    harness = MacOSHarness(
        driver=FakeMacOSDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            frame_sensitivity="non_sensitive",
        ),
    )

    result = harness.invoke_action(
        _step(
            "assertWithAI",
            {
                "assertion": "The chart is visible.",
                "surface": "custom_canvas",
                "full_screen_reason": "Full window canvas.",
            },
        ),
        harness.get_context(),
    )

    assert result.status == "incomplete"
    assert result.failure_category is None
    assert result.output["outcome"] == "inconclusive"
    assert result.output["error_code"] == "provider_unavailable"


def test_macos_harness_rejects_unknown_action() -> None:
    harness = MacOSHarness(driver=FakeMacOSDriver())

    result = harness.invoke_action(_step("unsupportedAction", {}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert "Unsupported macOS action" in (result.error_message or "")


def _xml_to_tree_node(xml_el: ElementTree.Element) -> Any:
    attribs = dict(xml_el.attrib)
    x = attribs.pop("x", None)
    y = attribs.pop("y", None)
    w = attribs.pop("width", None)
    h = attribs.pop("height", None)
    bounds = _bounds(int(x), int(y), int(w), int(h)) if all(v is not None for v in (x, y, w, h)) else None

    identifier = attribs.pop("identifier", None)
    label = attribs.pop("label", None)
    title = attribs.pop("title", None)
    value = attribs.pop("value", None)
    placeholder = attribs.pop("placeholderValue", None)
    element_type_raw = attribs.pop("elementType", None)
    enabled_raw = attribs.pop("enabled", None)
    selected_raw = attribs.pop("selected", None)
    visible_raw = attribs.pop("visible", None)

    visibility = "unknown"
    if visible_raw is not None:
        visibility = "visible" if visible_raw.lower() == "true" else "not_visible"

    return SimpleNamespace(
        type=_obs(xml_el.tag),
        id=_obs(identifier, "value" if identifier is not None else "missing"),
        text=_obs(value or label or title, "value" if (value or label or title) else "missing"),
        label=_obs(label, "value" if label is not None else "missing"),
        title=_obs(title, "value" if title is not None else "missing"),
        value=_obs(value, "value" if value is not None else "missing"),
        placeholder_value=_obs(placeholder, "value" if placeholder is not None else "missing"),
        element_type=_obs(int(element_type_raw), "value") if element_type_raw and element_type_raw.isdigit() else _obs(None, "missing"),
        enabled=_obs(enabled_raw.lower() == "true") if enabled_raw is not None else _obs(None, "missing"),
        selected=_obs(selected_raw.lower() == "true") if selected_raw is not None else _obs(None, "missing"),
        bounds=_obs(bounds, "value" if bounds is not None else "unknown"),
        visibility=visibility,
        viewport_relation="inside",
        extra_attributes=attribs,
        raw_text=(xml_el.text or ""),
        children=tuple(_xml_to_tree_node(child) for child in list(xml_el)),
    )


class FakeMacElement:
    def __init__(
        self,
        *,
        x: int = 0,
        y: int = 0,
        width: int = 10,
        height: int = 10,
        value: str | None = "",
        label: str | None = None,
        title: str | None = None,
        text: str | None = None,
        placeholder_value: str | None = None,
        elem_id: str | None = None,
        elem_type: str = "XCUIElementTypeTextField",
        displayed: bool = True,
        enabled: bool = True,
        focused: bool = False,
        selected: bool = False,
        hittable: bool = True,
        accepts_element_input: bool = True,
    ) -> None:
        self.bounds = _bounds(x, y, width, height)
        self.value = value
        self.label = label
        self.title = title
        self.text = text
        self.placeholder_value = placeholder_value
        self.elem_id = elem_id
        self.elem_type = elem_type
        self.displayed = displayed
        self.enabled = enabled
        self.focused = focused
        self.selected = selected
        self.hittable = hittable
        self.accepts_element_input = accepts_element_input
        self.inspected_attributes: list[tuple[str, ...]] = []
        self.typed: list[str] = []
        self.macos_keys_sent: list[list[Any]] = []
        self.clicks = 0
        self.double_clicks = 0
        self.right_clicks = 0
        self.hovers = 0
        self.clears = 0
        self.owner_session: FakeMacSession | None = None

    def inspect(self, *, attributes: tuple[str, ...], control: Any = None) -> Any:
        del control
        self.inspected_attributes.append(tuple(attributes))
        results = []
        for name in attributes:
            if name == "bounds":
                results.append(_attr_obs("bounds", self.bounds))
            elif name == "type":
                results.append(_attr_obs("type", self.elem_type))
            elif name == "id":
                results.append(_attr_obs("id", self.elem_id, state="value" if self.elem_id is not None else "missing"))
            elif name == "value":
                results.append(_attr_obs("value", self.value, state="value" if self.value is not None else "missing"))
            elif name == "label":
                results.append(_attr_obs("label", self.label, state="value" if self.label is not None else "missing"))
            elif name == "title":
                results.append(_attr_obs("title", self.title, state="value" if self.title is not None else "missing"))
            elif name == "text":
                results.append(_attr_obs("text", self.text, state="value" if self.text is not None else "missing"))
            elif name == "placeholder_value":
                results.append(_attr_obs("placeholder_value", self.placeholder_value, state="value" if self.placeholder_value is not None else "missing"))
            elif name == "displayed":
                results.append(_attr_obs("displayed", self.displayed))
            elif name == "enabled":
                results.append(_attr_obs("enabled", self.enabled))
            elif name == "focused":
                results.append(_attr_obs("focused", self.focused))
            elif name == "selected":
                results.append(_attr_obs("selected", self.selected))
            elif name == "hittable":
                results.append(_attr_obs("hittable", self.hittable))
            else:
                results.append(_attr_obs(name, None, state="missing"))
        return SimpleNamespace(attributes=tuple(results))

    def click(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del key_modifier_flags, wait_timeout_seconds, control
        self.clicks += 1

    def double_click(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del key_modifier_flags, wait_timeout_seconds, control
        self.double_clicks += 1

    def right_click(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del key_modifier_flags, wait_timeout_seconds, control
        self.right_clicks += 1

    def hover(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del key_modifier_flags, wait_timeout_seconds, control
        self.hovers += 1

    def clear(self, *, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds, control
        self.clears += 1
        self.value = ""

    def send_keys(self, text: str, *, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds, control
        self.typed.append(text)
        if self.accepts_element_input and self.value is not None:
            self.value += text

    def send_macos_keys(self, keys: list[Any], *, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds, control
        self.macos_keys_sent.append(list(keys))
        if self.owner_session is not None and self.owner_session.system_input_element is self and self.value is not None:
            self.value += "".join(key for key in keys if isinstance(key, str) and len(key) == 1)


class FakeMacDevice:
    def __init__(self, session: FakeMacSession) -> None:
        self.session = session

    def activate_app(self, bundle_id: str, *, control: Any = None) -> None:
        del control
        self.session.lifecycle_calls.append(("activate_app", bundle_id))

    def terminate_app(self, bundle_id: str, *, control: Any = None) -> bool:
        del control
        self.session.lifecycle_calls.append(("terminate_app", bundle_id))
        return True

    def send_keys(self, keys: list[Any], *, control: Any = None) -> None:
        del control
        normalized = []
        for item in keys:
            if hasattr(item, "key") and hasattr(item, "modifier_flags"):
                normalized.append({"key": item.key, "modifierFlags": item.modifier_flags})
            else:
                normalized.append(item)
        self.session.device_keys.append(normalized)
        if isinstance(self.session.system_input_element, FakeMacElement) and self.session.system_input_element.value is not None:
            self.session.system_input_element.value += "".join(item for item in normalized if isinstance(item, str) and len(item) == 1)

    def click_at(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        double: bool = False,
        key_modifier_flags: int | None = None,
        control: Any = None,
    ) -> None:
        del control
        self.session.pointer_calls.append(("click_at", {"x": x, "y": y, "button": button, "double": double, "modifiers": key_modifier_flags}))

    def hover_at(self, x: int, y: int, *, key_modifier_flags: int | None = None, control: Any = None) -> None:
        del control
        self.session.pointer_calls.append(("hover_at", {"x": x, "y": y, "modifiers": key_modifier_flags}))

    def scroll_at(
        self,
        x: int,
        y: int,
        *,
        delta_x: float = 0.0,
        delta_y: float = 0.0,
        key_modifier_flags: int | None = None,
        control: Any = None,
    ) -> None:
        del control
        self.session.pointer_calls.append(("scroll_at", {"x": x, "y": y, "delta_x": delta_x, "delta_y": delta_y, "modifiers": key_modifier_flags}))

    def swipe_between(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        duration_ms: int = 250,
        key_modifier_flags: int | None = None,
        control: Any = None,
    ) -> None:
        del control
        self.session.pointer_calls.append(("swipe_between", {"startX": start_x, "startY": start_y, "endX": end_x, "endY": end_y, "duration_ms": duration_ms, "modifiers": key_modifier_flags}))

    def take_screenshot(self, *, control: Any = None) -> bytes:
        del control
        return _valid_png_bytes()


class _MissingElementProxy:
    def inspect(self, *, attributes: tuple[str, ...], control: Any = None) -> Any:
        del attributes, control
        raise TargetError("target_not_found")


class FakeMacSession:
    def __init__(self, elements: dict[str, FakeMacElement], *, page_source: str = "", session_id: str = "mac2:session") -> None:
        self.elements = elements
        for el in elements.values():
            el.owner_session = self
        self.page_source = page_source
        self.session_id = session_id
        self.state = "active"
        self.device = FakeMacDevice(self)
        self.lifecycle_calls: list[tuple[str, str | None]] = []
        self.device_keys: list[list[Any]] = []
        self.pointer_calls: list[tuple[str, dict[str, object]]] = []
        self.system_input_element: FakeMacElement | None = None

    def session_info(self) -> Any:
        return SimpleNamespace(
            id=self.session_id,
            appium_session_id=self.session_id,
            state=self.state,
            platform="macos",
        )

    def element(self, by: str, value: str, *, parent: Any = None, predicate: str | None = None) -> Any:
        del parent
        for name, candidate_el in self.elements.items():
            if by == "accessibility id" and (value == name or value == candidate_el.elem_id):
                return candidate_el
            if f'"{name}"' in value or f"'{name}'" in value or (predicate and f'"{name}"' in predicate):
                return candidate_el
            if by == "-ios predicate string" and "amRect.x ==" in value:
                bx, by_y, bw, bh = candidate_el.bounds.x, candidate_el.bounds.y, candidate_el.bounds.width, candidate_el.bounds.height
                rect_clause = f"amRect.x == {bx} AND amRect.y == {by_y} AND amRect.width == {bw} AND amRect.height == {bh}"
                if rect_clause in value and f'amType == "{candidate_el.elem_type}"' in value:
                    return candidate_el
        return _MissingElementProxy()

    def observe_tree(self, *, control: Any = None) -> Any:
        del control
        viewport = SimpleNamespace(width=_obs(1440), height=_obs(900), rotation=_obs(None, "not_applicable"))
        if not self.page_source.strip():
            roots: tuple[Any, ...] = ()
        else:
            xml_root = ElementTree.fromstring(self.page_source)  # noqa: S314
            roots = (_xml_to_tree_node(xml_root),)
        return SimpleNamespace(
            coverage="requested_scope",
            complete_for_requested_scope=True,
            viewport=viewport,
            roots=roots,
        )

    def quit(self, *, control: Any = None) -> None:
        del control
        self.state = "closed"
        self.lifecycle_calls.append(("quit", None))

    def dispose(self, *, control: Any = None) -> None:
        del control
        self.state = "disposed"
        self.lifecycle_calls.append(("dispose", None))


@pytest.mark.usefixtures("android_sdk")
def test_appium_connect_macos_driver_uses_independent_new_command_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeScope:
        def __enter__(self) -> object:
            return "fake-connection"

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        managed_cleanup_pending = False

        def __init__(self, settings: object) -> None:
            self.settings = settings

        def start_managed(self, **kwargs: object) -> FakeScope:
            return FakeScope()

    def fake_create_mac2_session(connection: object, **kwargs: object) -> FakeMacSession:
        assert connection == "fake-connection"
        captured_kwargs.update(kwargs)
        return FakeMacSession({})

    monkeypatch.setattr("appium_connect_agent.runtime.Runtime", FakeRuntime)
    monkeypatch.setattr("appium_connect_agent.automation.create_mac2_session", fake_create_mac2_session)

    driver = AppiumConnectMacOSDriver(
        bundle_id="com.example.App",
        action_timeout_seconds=10,
        new_command_timeout_seconds=420,
    )
    try:
        driver.launch_app(MacOSLaunchAppParams())
        assert captured_kwargs["new_command_timeout_seconds"] == 420.0
        assert captured_kwargs["bundle_id"] == "com.example.App"
    finally:
        driver.close()


def test_appium_connect_macos_driver_launch_activates_bundle_in_existing_session() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.Configured", session=session)

    result = driver.launch_app(MacOSLaunchAppParams(bundle_id="com.example.Override"))

    assert result["status"] == "passed"
    assert result["output"]["bundle_id"] == "com.example.Override"
    assert session.lifecycle_calls == [("activate_app", "com.example.Override")]


def test_appium_connect_macos_driver_launch_supports_cross_app_activation_sequence() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    driver.launch_app(MacOSLaunchAppParams(bundle_id="com.example.AppA"))
    driver.launch_app(MacOSLaunchAppParams(bundle_id="com.example.AppB"))

    assert session.lifecycle_calls == [
        ("activate_app", "com.example.AppA"),
        ("activate_app", "com.example.AppB"),
    ]


def test_appium_connect_macos_driver_launch_reuse_rejects_session_creation_inputs() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.App", session=session)

    with pytest.raises(ConfigurationError, match="new_session=true"):
        driver.launch_app(MacOSLaunchAppParams(arguments=["--fresh"]))

    assert session.lifecycle_calls == []


def test_appium_connect_macos_driver_launch_reuse_requires_bundle_id() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(app_path="/Applications/Example.app", session=session)

    with pytest.raises(ConfigurationError, match="bundle_id"):
        driver.launch_app(MacOSLaunchAppParams())

    assert session.lifecycle_calls == []


def test_appium_connect_macos_driver_launch_new_session_replaces_existing_session(monkeypatch: pytest.MonkeyPatch) -> None:
    old_session = FakeMacSession({}, session_id="mac2:old")
    new_session = FakeMacSession({}, session_id="mac2:new")
    driver = AppiumConnectMacOSDriver(session=old_session)

    def create_session(params: MacOSLaunchAppParams) -> object:
        assert driver.context()["session_id"] is None
        driver._session = new_session
        driver._borrowed_session = True
        return new_session

    monkeypatch.setattr(driver, "_create_session", create_session)

    result = driver.launch_app(MacOSLaunchAppParams(bundle_id="com.example.New", new_session=True))

    assert result["status"] == "passed"
    assert old_session.lifecycle_calls == [("quit", None)]
    assert driver.context()["session_id"] == "mac2:new"


def test_appium_connect_macos_driver_launch_new_session_validates_identity_before_quit() -> None:
    old_session = FakeMacSession({}, session_id="mac2:old")
    driver = AppiumConnectMacOSDriver(session=old_session)

    with pytest.raises(ConfigurationError, match="bundle_id or app_path"):
        driver.launch_app(MacOSLaunchAppParams(new_session=True))

    assert old_session.lifecycle_calls == []
    assert driver.context()["session_id"] == "mac2:old"


def test_appium_connect_macos_driver_failed_session_replacement_clears_old_session(monkeypatch: pytest.MonkeyPatch) -> None:
    old_session = FakeMacSession({}, session_id="mac2:old")
    driver = AppiumConnectMacOSDriver(session=old_session)

    def fail_creation(params: MacOSLaunchAppParams) -> object:
        assert params.bundle_id == "com.example.App"
        raise RuntimeError("SECRET boom")

    monkeypatch.setattr(driver, "_create_session", fail_creation)

    result = driver.launch_app(MacOSLaunchAppParams(bundle_id="com.example.App", new_session=True))

    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert "SECRET" not in json.dumps(result)
    assert old_session.lifecycle_calls == [("quit", None)]
    assert driver.context()["session_id"] is None


def test_appium_connect_macos_driver_kill_retains_session_by_default() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.App", session=session)

    result = driver.kill_app(MacOSKillAppParams())

    assert result["status"] == "passed"
    assert session.lifecycle_calls == [("terminate_app", "com.example.App")]
    assert driver.context()["session_id"] == "mac2:session"


def test_appium_connect_macos_driver_kill_without_bundle_id_requires_identity() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    with pytest.raises(ConfigurationError, match="bundle_id"):
        driver.kill_app(MacOSKillAppParams())

    assert session.lifecycle_calls == []


def test_appium_connect_macos_driver_kill_can_close_session_without_bundle_id() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.kill_app(MacOSKillAppParams(close_session=True))

    assert result["status"] == "passed"
    assert session.lifecycle_calls == [("quit", None)]
    assert driver.context()["session_id"] is None


def test_appium_connect_macos_driver_kill_closes_session_without_calling_terminate() -> None:
    class TerminationForbiddenDevice(FakeMacDevice):
        def terminate_app(self, bundle_id: str, *, control: Any = None) -> bool:
            raise AssertionError(f"terminate_app must not be called for {bundle_id}")

    session = FakeMacSession({})
    session.device = TerminationForbiddenDevice(session)
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.App", session=session)

    result = driver.kill_app(MacOSKillAppParams(close_session=True))

    assert result["status"] == "passed"
    assert session.lifecycle_calls == [("quit", None)]
    assert driver.context()["session_id"] is None


def test_appium_connect_macos_driver_type_text_uses_element_input_and_verifies_value() -> None:
    element = FakeMacElement(value="old")
    session = FakeMacSession({"Search": element})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="www.bing.com", target="Search", clear=True))

    assert result["status"] == "passed"
    assert element.macos_keys_sent == []
    assert element.typed == ["www.bing.com"]
    assert element.value == "www.bing.com"
    assert element.clicks == 1
    assert element.clears == 1


def test_appium_connect_macos_driver_type_text_falls_back_to_macos_keys_and_rechecks_value() -> None:
    element = FakeMacElement(accepts_element_input=False)
    session = FakeMacSession({"Search": element})
    session.system_input_element = element
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="https://www.bing.com", target="Search", clear=True))

    assert result["status"] == "passed"
    assert element.typed == ["https://www.bing.com"]
    assert element.clicks == 1
    assert element.clears == 2
    assert element.macos_keys_sent == [list("https://www.bing.com")]
    assert element.value == "https://www.bing.com"


def test_appium_connect_macos_driver_type_text_fails_when_fallback_does_not_update_value() -> None:
    element = FakeMacElement(accepts_element_input=False)
    session = FakeMacSession({"Search": element})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="https://www.bing.com", target="Search", clear=True))

    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert element.typed == ["https://www.bing.com"]
    assert element.clicks == 1
    assert element.clears == 2
    assert element.macos_keys_sent == [list("https://www.bing.com")]


def test_appium_connect_macos_driver_type_text_does_not_accept_preexisting_text_as_new_input() -> None:
    element = FakeMacElement(value="Microsoft Bing", accepts_element_input=False)
    session = FakeMacSession({"Search": element})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="Bing", target="Search"))

    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert element.typed == ["Bing"]
    assert element.macos_keys_sent == [list("Bing")]


def test_appium_connect_macos_driver_type_text_untargeted_delivers_device_keys() -> None:
    active_element = FakeMacElement(value="")
    session = FakeMacSession({})
    session.system_input_element = active_element
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="Microsoft"))

    assert result["status"] == "passed"
    assert session.device_keys == [list("Microsoft")]
    assert active_element.value == "Microsoft"


def test_appium_connect_macos_driver_type_text_clicks_point_then_delivers_device_keys() -> None:
    active_element = FakeMacElement(value="")
    session = FakeMacSession({})
    session.system_input_element = active_element
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="point text", point=MacOSPoint(x=12, y=34)))

    assert result["status"] == "passed"
    assert session.pointer_calls == [("click_at", {"x": 12, "y": 34, "button": "left", "double": False, "modifiers": None})]
    assert session.device_keys == [list("point text")]


def test_appium_connect_macos_driver_type_text_untargeted_clear_rejects_before_clicking_point() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="point text", point=MacOSPoint(x=12, y=34), clear=True))

    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert session.pointer_calls == []
    assert session.device_keys == []


def test_appium_connect_macos_driver_type_text_does_not_fall_back_when_target_is_missing() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.type_text(MacOSTypeTextParams(text="must not leak", target="Missing"))

    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert session.device_keys == []


def test_appium_connect_macos_driver_press_key_uses_explicit_command_modifier() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.press_key(MacOSPressKeyParams(key="l", modifiers=["COMMAND"]))

    assert result["status"] == "passed"
    assert session.device_keys == [[{"key": "l", "modifierFlags": 1 << 4}]]


def test_appium_connect_macos_driver_press_key_maps_enter_without_modifier() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    result = driver.press_key(MacOSPressKeyParams(key="Enter"))

    assert result["status"] == "passed"
    assert session.device_keys == [["XCUIKeyboardKeyReturn"]]


def test_appium_connect_macos_driver_assert_elements_order_returns_structured_pass_output() -> None:
    driver = AppiumConnectMacOSDriver(
        session=FakeMacSession(
            {
                "First": FakeMacElement(x=10, y=10),
                "Second": FakeMacElement(x=10, y=40),
            }
        )
    )

    result = driver.assert_elements_order(MacOSAssertElementsOrderParams.model_validate({"elements": [{"target": "First"}, {"target": "Second"}]}))

    assert result["status"] == "passed"
    assert result["output"] == {
        "direction": "vertical",
        "elements_found": 2,
        "elements_total": 2,
        "actual_order": [0, 1],
        "expected_order": [0, 1],
        "positions": [
            {"index": 0, "coordinate": 15.0, "rect": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0}},
            {"index": 1, "coordinate": 45.0, "rect": {"x": 10.0, "y": 40.0, "width": 10.0, "height": 10.0}},
        ],
    }


def test_appium_connect_macos_driver_assert_elements_order_fails_on_wrong_order() -> None:
    driver = AppiumConnectMacOSDriver(
        session=FakeMacSession(
            {
                "First": FakeMacElement(x=10, y=10),
                "Second": FakeMacElement(x=40, y=10),
            }
        )
    )

    result = driver.assert_elements_order(MacOSAssertElementsOrderParams.model_validate({"elements": [{"target": "First"}, {"target": "Second"}], "direction": "horizontal", "expected_order": [1, 0]}))

    assert result["status"] == "failed"
    assert result["failure_category"] == "assertion_error"
    assert result["output"]["direction"] == "horizontal"
    assert result["output"]["actual_order"] == [0, 1]
    assert result["output"]["expected_order"] == [1, 0]


def test_appium_connect_macos_driver_assert_elements_order_reports_missing_required_elements() -> None:
    driver = AppiumConnectMacOSDriver(session=FakeMacSession({"First": FakeMacElement(x=10, y=10)}))

    result = driver.assert_elements_order(MacOSAssertElementsOrderParams.model_validate({"elements": [{"target": "First"}, {"target": "Missing"}]}))

    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"] == {"missing_indexes": [1]}
    assert result["output"]["elements_found"] == 1
    assert result["output"]["elements_total"] == 2


def test_appium_connect_macos_driver_ui_snapshot_simplifies_tree_observation_to_max_depth() -> None:
    driver = AppiumConnectMacOSDriver(
        session=FakeMacSession(
            {},
            page_source=(
                '<XCUIElementTypeApplication identifier="Root"><Wrapper><XCUIElementTypeWindow label="Main"><Wrapper>'
                '<XCUIElementTypeGroup label="Toolbar"><XCUIElementTypeButton label="Save" custom="hidden" /></XCUIElementTypeGroup>'
                '</Wrapper></XCUIElementTypeWindow></Wrapper><XCUIElementTypeWindow label="Other" /></XCUIElementTypeApplication>'
            ),
        ),
        page_source_max_depth=2,
    )

    snapshot = driver.ui_snapshot(MacOSUiSnapshotParams())

    assert "source" not in snapshot
    assert snapshot["snapshot_type"] == "macos_accessibility_tree"
    assert snapshot["max_depth"] == 2
    page_source = snapshot["page_source"]
    assert page_source["format"] == "xml"
    assert page_source["node_count"] == 7
    assert page_source["root"] == {
        "type": "XCUIElementTypeApplication",
        "attributes": {"identifier": "Root"},
        "children": [
            {"type": "XCUIElementTypeWindow", "attributes": {"label": "Main"}, "children_truncated": 1},
            {"type": "XCUIElementTypeWindow", "attributes": {"label": "Other"}},
        ],
    }


def test_appium_connect_macos_driver_ui_snapshot_handles_empty_observation() -> None:
    driver = AppiumConnectMacOSDriver(session=FakeMacSession({}, page_source=""))

    snapshot = driver.ui_snapshot(MacOSUiSnapshotParams())

    assert snapshot["page_source"] == {"format": "xml", "root": None, "source_length": 0}


def test_appium_connect_macos_driver_ui_snapshot_compacts_semantic_signals() -> None:
    long_label = "Save " + ("document " * 8)
    driver = AppiumConnectMacOSDriver(
        session=FakeMacSession(
            {},
            page_source=(
                '<XCUIElementTypeApplication identifier="Root"><Wrapper>'
                f'<XCUIElementTypeButton identifier="save" label="{long_label}" role="AXButton" enabled="true" visible="true" '
                'selected="false" x="10" y="20" width="80" height="30" custom="drop" />'
                '<Noise enabled="true" visible="true" x="0" y="0" width="0" height="0" />'
                '<Hidden visible="false" x="0" y="0" width="0" height="0" />'
                "</Wrapper></XCUIElementTypeApplication>"
            ),
        ),
    )

    snapshot = driver.ui_snapshot(MacOSUiSnapshotParams(max_depth=4))

    page_source = snapshot["page_source"]
    assert page_source["node_count"] == 5
    assert page_source["root"] == {
        "type": "XCUIElementTypeApplication",
        "attributes": {"identifier": "Root"},
        "children": [
            {
                "type": "XCUIElementTypeButton",
                "attributes": {
                    "identifier": "save",
                    "label": long_label[:50],
                    "role": "AXButton",
                    "x": "10",
                    "y": "20",
                    "width": "80",
                    "height": "30",
                },
                "truncated_attributes": ["label"],
            },
            {
                "type": "Hidden",
                "attributes": {"visible": "false", "x": "0", "y": "0", "width": "0", "height": "0"},
            },
        ],
    }


def test_appium_connect_macos_driver_ui_snapshot_reduces_noise_heavy_output() -> None:
    noise = "".join(f'<Noise enabled="true" visible="true" x="{index}" y="0" width="0" height="0" />' for index in range(100))
    source = f'<XCUIElementTypeApplication identifier="Root"><Wrapper>{noise}</Wrapper></XCUIElementTypeApplication>'
    driver = AppiumConnectMacOSDriver(session=FakeMacSession({}, page_source=source))

    snapshot = driver.ui_snapshot(MacOSUiSnapshotParams())

    page_source = snapshot["page_source"]
    assert page_source["node_count"] == 102
    assert page_source["root"] == {"type": "XCUIElementTypeApplication", "attributes": {"identifier": "Root"}}
    assert len(json.dumps(page_source)) < len(source) // 10


def test_appium_connect_macos_driver_ui_snapshot_compacts_included_attributes_and_text() -> None:
    long_text = "x" * 60
    driver = AppiumConnectMacOSDriver(
        session=FakeMacSession(
            {},
            page_source=f'<XCUIElementTypeApplication identifier="Root"><XCUIElementTypeButton label="Action" custom="keep" empty="   " enabled="true">{long_text}</XCUIElementTypeButton></XCUIElementTypeApplication>',
        ),
    )

    snapshot = driver.ui_snapshot(MacOSUiSnapshotParams(max_depth=3, include_attributes=True))

    assert snapshot["page_source"]["root"] == {
        "type": "XCUIElementTypeApplication",
        "attributes": {"identifier": "Root"},
        "children": [
            {
                "type": "XCUIElementTypeButton",
                "attributes": {"label": "Action", "custom": "keep"},
                "text": long_text[:50],
                "text_truncated": True,
            }
        ],
    }


def test_appium_connect_macos_driver_ui_snapshot_bounds_unexpected_compaction_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '<XCUIElementTypeApplication identifier="Root" />'
    driver = AppiumConnectMacOSDriver(session=FakeMacSession({}, page_source=source))

    def fail_compaction(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("SECRET compaction failed")

    monkeypatch.setattr(driver, "_snapshot_attributes", fail_compaction)

    snapshot = driver.ui_snapshot(MacOSUiSnapshotParams())

    assert snapshot["page_source"]["format"] == "unparsed_xml"
    assert snapshot["page_source"]["parse_error"] == "RuntimeError"
    assert "SECRET" not in json.dumps(snapshot)


def test_appium_connect_macos_driver_requires_explicit_launch_before_session_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.MacApp")
    called = False

    def fake_ensure_session(params: MacOSLaunchAppParams | None = None) -> FakeMacSession:
        nonlocal called
        called = True
        return FakeMacSession({})

    monkeypatch.setattr(driver, "_ensure_session", fake_ensure_session)

    with pytest.raises(ConfigurationError, match="Call launchApp before macOS Appium actions"):
        driver.ui_snapshot(MacOSUiSnapshotParams())

    assert called is False


def test_appium_connect_macos_driver_close_leaves_borrowed_session_open() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(session=session)

    driver.close()

    assert session.lifecycle_calls == []
    assert session.state == "active"


@pytest.mark.usefixtures("android_sdk")
def test_appium_connect_macos_driver_close_retains_owned_handles_when_managed_cleanup_fails_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_attempts = 0

    class FakeScope:
        def __enter__(self) -> object:
            return "fake-connection"

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        managed_cleanup_pending = True

        def __init__(self, settings: object) -> None:
            self.settings = settings

        def start_managed(self, **kwargs: object) -> FakeScope:
            return FakeScope()

        def cleanup_managed(self, *, control: object = None) -> None:
            nonlocal cleanup_attempts
            del control
            cleanup_attempts += 1
            if cleanup_attempts == 1:
                raise RuntimeError("managed cleanup locked")
            FakeRuntime.managed_cleanup_pending = False

    monkeypatch.setattr("appium_connect_agent.runtime.Runtime", FakeRuntime)
    monkeypatch.setattr("appium_connect_agent.automation.create_mac2_session", lambda connection, **kwargs: FakeMacSession({}))

    driver = AppiumConnectMacOSDriver(bundle_id="com.example.App")
    driver.launch_app(MacOSLaunchAppParams())

    with pytest.raises(RuntimeError, match="managed cleanup locked"):
        driver.close()

    assert driver._runtime is not None
    driver.close()
    assert cleanup_attempts == 2
    assert driver._runtime is None
    assert driver._session is None


def test_macos_inspect_element_withholds_secure_text_field_and_masks_without_querying_value() -> None:
    secure_el = FakeMacElement(
        elem_id="pwd",
        elem_type="XCUIElementTypeSecureTextField",
        value="top-secret-password",
        label="Password",
    )
    driver = AppiumConnectMacOSDriver(session=FakeMacSession({"pwd": secure_el}))

    result = driver.inspect_element(MacOSInspectElementParams(target="pwd", attributes=("type", "label", "value", "text")))

    assert result["status"] == "passed"
    attrs = result["output"]["attributes"]
    assert attrs["type"] == {"state": "value", "value": "XCUIElementTypeSecureTextField"}
    assert attrs["label"] == {"state": "value", "value": "Password"}
    assert attrs["value"] == {"state": "unavailable", "value": None, "reason": "protected_text"}
    assert attrs["text"] == {"state": "unavailable", "value": None, "reason": "protected_text"}
    # Never queried "value" or "text" after observing SecureTextField type!
    for batch in secure_el.inspected_attributes:
        assert "value" not in batch
        assert "text" not in batch
    assert "top-secret-password" not in json.dumps(result)

    masked_el = FakeMacElement(elem_id="pin", elem_type="XCUIElementTypeTextField", value="••••••")
    masked_driver = AppiumConnectMacOSDriver(session=FakeMacSession({"pin": masked_el}))
    masked_res = masked_driver.inspect_element(MacOSInspectElementParams(target="pin", attributes=("value",)))
    assert masked_res["output"]["attributes"]["value"] == {"state": "unavailable", "value": None, "reason": "protected_text"}


def test_macos_scroll_on_resolves_element_center_without_coordinate_fallback_on_miss() -> None:
    scroll_view = FakeMacElement(x=100, y=200, width=400, height=300, elem_type="XCUIElementTypeScrollView")
    session = FakeMacSession({"ResultsList": scroll_view})
    driver = AppiumConnectMacOSDriver(session=session)

    passed = driver.scroll_on(MacOSScrollOnParams(target="ResultsList", delta_y=-240, modifiers=["SHIFT"]))
    assert passed["status"] == "passed"
    assert passed["output"] == {"point": {"x": 300, "y": 350}, "delta_x": 0.0, "delta_y": -240.0, "modifiers": ["SHIFT"]}
    assert session.pointer_calls[-1] == ("scroll_at", {"x": 300, "y": 350, "delta_x": 0.0, "delta_y": -240.0, "modifiers": 1 << 1})

    missed = driver.scroll_on(MacOSScrollOnParams(target="MissingList", point=MacOSPoint(x=10, y=20), delta_y=-100))
    assert missed["status"] == "failed"
    assert missed["failure_category"] == "target_resolution_error"
    assert len(session.pointer_calls) == 1


def test_macos_assert_not_visible_passes_on_hidden_or_missing_and_fails_when_visible() -> None:
    hidden = FakeMacElement(displayed=False)
    visible = FakeMacElement(displayed=True)
    driver = AppiumConnectMacOSDriver(session=FakeMacSession({"HiddenDialog": hidden, "OpenDialog": visible}), action_timeout_seconds=1)

    assert driver.assert_not_visible(MacOSAssertNotVisibleParams(target="HiddenDialog"))["status"] == "passed"
    assert driver.assert_not_visible(MacOSAssertNotVisibleParams(target="AbsentDialog"))["status"] == "passed"

    still_visible = driver.assert_not_visible(MacOSAssertNotVisibleParams(target="OpenDialog"))
    assert still_visible["status"] == "failed"
    assert still_visible["failure_category"] == "assertion_error"

    with pytest.raises(ValidationError, match="coordinate-only point is rejected"):
        MacOSAssertNotVisibleParams.model_validate({"locator": {"point": {"x": 10, "y": 20}}})


def test_macos_assert_state_branch_minimal_text_boolean_and_existence() -> None:
    toggle = FakeMacElement(
        elem_id="dark_toggle",
        elem_type="XCUIElementTypeCheckBox",
        label="Dark Mode",
        value="1",
        enabled=True,
        selected=True,
        focused=False,
    )
    secure = FakeMacElement(elem_id="secret_box", elem_type="XCUIElementTypeSecureTextField", value="hunter2")
    driver = AppiumConnectMacOSDriver(session=FakeMacSession({"dark_toggle": toggle, "secret_box": secure}))

    # Existence-only branch inspects only ("enabled",)
    toggle.inspected_attributes.clear()
    exists_res = driver.assert_state(MacOSAssertStateParams.model_validate({"element": {"accessibilityId": "dark_toggle"}}))
    assert exists_res["status"] == "passed"
    assert exists_res["output"] == {"exists": True}
    assert toggle.inspected_attributes[-1] == ("enabled",)

    # Boolean state branch inspects only requested booleans in canonical order
    toggle.inspected_attributes.clear()
    state_res = driver.assert_state(MacOSAssertStateParams.model_validate({"element": {"accessibilityId": "dark_toggle", "selected": True, "focused": False}}))
    assert state_res["status"] == "passed"
    assert toggle.inspected_attributes[-1] == ("focused", "selected")

    # Text precedence over mismatching state
    text_res = driver.assert_state(
        MacOSAssertStateParams.model_validate(
            {
                "element": {"accessibilityId": "dark_toggle", "selected": False},
                "text": {"equals": "Dark Mode", "attribute": "label"},
            }
        )
    )
    assert text_res["status"] == "passed"

    # SecureTextField text assertion fails safely without reading plaintext
    secure.inspected_attributes.clear()
    sec_res = driver.assert_state(
        MacOSAssertStateParams.model_validate(
            {
                "element": {"accessibilityId": "secret_box"},
                "text": {"contains": "hunter", "attribute": "value"},
            }
        )
    )
    assert sec_res["status"] == "failed"
    assert sec_res["failure_category"] == "assertion_error"
    assert sec_res["metadata"]["reason"] == "protected_or_unreadable_text"
    assert all("value" not in batch for batch in secure.inspected_attributes)


def test_macos_type_text_reanchors_text_dependent_locator_and_supports_secure_text_field() -> None:
    # Element matched initially by mutable value="Draft", no accessibilityId -> binds by amType + amRect so post-write readback succeeds!
    mutable_field = FakeMacElement(x=50, y=60, width=120, height=24, value="Draft", elem_type="XCUIElementTypeTextField")

    class MutableValueSession(FakeMacSession):
        def element(self, by: str, value: str, *, parent: Any = None, predicate: str | None = None) -> Any:
            del parent, predicate
            if by == "-ios predicate string" and 'value == "Draft"' in value:
                return mutable_field if mutable_field.value == "Draft" else _MissingElementProxy()
            if by == "-ios predicate string" and "amRect.x == 50" in value:
                return mutable_field
            return _MissingElementProxy()

    driver = AppiumConnectMacOSDriver(session=MutableValueSession({"Draft": mutable_field}))
    res = driver.type_text(MacOSTypeTextParams(locator=MacOSLocator(value="Draft"), text="Final Title", clear=True))
    assert res["status"] == "passed"
    assert mutable_field.value == "Final Title"

    # Disabled element rejected before side effects
    disabled_field = FakeMacElement(enabled=False, value="init")
    disabled_driver = AppiumConnectMacOSDriver(session=FakeMacSession({"Disabled": disabled_field}))
    dis_res = disabled_driver.type_text(MacOSTypeTextParams(target="Disabled", text="abc"))
    assert dis_res["status"] == "failed"
    assert dis_res["failure_category"] == "target_resolution_error"
    assert disabled_field.clicks == 0
    assert disabled_field.typed == []

    # SecureTextField types without reading value or leaking secret
    secure_field = FakeMacElement(elem_id="login_pwd", elem_type="XCUIElementTypeSecureTextField", value="••••")
    sec_driver = AppiumConnectMacOSDriver(session=FakeMacSession({"login_pwd": secure_field}))
    sec_res = sec_driver.type_text(MacOSTypeTextParams(locator=MacOSLocator(accessibilityId="login_pwd"), text="S3cr3t!", clear=True))
    assert sec_res["status"] == "passed"
    assert sec_res["output"] == {"protected": True}
    assert secure_field.typed == ["S3cr3t!"]
    assert all("value" not in batch for batch in secure_field.inspected_attributes)


def test_macos_harness_validation_redacts_secret_input_and_enforces_deterministic_conflict(tmp_path) -> None:
    driver = FakeMacOSDriver()
    harness = MacOSHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=SimpleNamespace(
            evaluate=lambda req, cancellation_check=None: AIAssertionResult(
                status="passed",
                outcome="passed",
                passed=True,
                explanation="Model claimed passed.",
            )
        ),
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )

    # Invalid parameter value must not be echoed in validation_errors
    bad = harness.invoke_action(_step("typeText", {"text": "TOP_SECRET_VALUE", "extra_bad": "LEAK_ME"}), harness.get_context())
    assert bad.status == "failed"
    serialized_bad = json.dumps(bad.model_dump(mode="json"))
    assert "TOP_SECRET_VALUE" not in serialized_bad
    assert "LEAK_ME" not in serialized_bad

    # Deterministic counterevidence forces failed with deterministic_conflict=True without model pass overriding it
    conflict = harness.invoke_action(
        _step(
            "assertWithAI",
            {
                "assertion": "The Save button label says Cancel.",
                "surface": "appearance",
                "full_screen_reason": "Dialog check.",
                "expect": {"kind": "visual_property", "property": "label", "value": "Cancel", "target": "Save"},
            },
        ),
        harness.get_context(),
    )
    assert conflict.status == "failed"
    assert conflict.failure_category == "assertion_error"
    assert conflict.output["deterministic_conflict"] is True


@pytest.mark.usefixtures("android_sdk")
def test_macos_inspect_with_ai_extract_and_locate_pointer_mapping_unavailable(tmp_path) -> None:
    png_bytes = _valid_png_bytes(64, 48)

    class SDKLikeDevice(FakeMacDevice):
        def inspect_with_ai(self, prompt: str, *, provider: Any, mode: str = "extract", region: Any = None, max_candidates: int = 5, control: Any = None) -> Any:
            del region, control
            if mode == "locate":
                err = RuntimeError("locate unsupported on macOS")
                err.code = "pointer_mapping_unavailable"  # type: ignore[attr-defined]
                err.request_state = "not_sent"  # type: ignore[attr-defined]
                raise err
            req = SimpleNamespace(
                png_bytes=png_bytes,
                prompt=prompt,
                mode=mode,
                max_candidates=max_candidates,
                image_width=64,
                image_height=48,
                remaining_timeout_seconds=15.0,
            )
            resp = provider.inspect(req)
            import hashlib

            return SimpleNamespace(
                summary=resp.summary,
                mode="extract",
                image_size=SimpleNamespace(width=64, height=48),
                pointer_size=None,
                png_sha256=hashlib.sha256(png_bytes).hexdigest(),
                candidates=(),
            )

    session = FakeMacSession({})
    session.device = SDKLikeDevice(session)
    driver = AppiumConnectMacOSDriver(session=session)

    class FakeVisualEvaluator:
        def evaluate(self, request: AIAssertionRequest, *, cancellation_check=None) -> AIAssertionResult:
            raise AssertionError("evaluate must not be called")

        def inspect(self, request: AIVisualInspectionRequest, *, cancellation_check=None) -> AIVisualInspectionResult:
            return AIVisualInspectionResult(summary="Extracted chart value: 42%", candidates=())

    harness = MacOSHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=FakeVisualEvaluator(),
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )

    extract_res = harness.invoke_action(_step("inspectWithAI", {"prompt": "Read chart percentage", "mode": "extract"}), harness.get_context())
    assert extract_res.status == "passed"
    assert extract_res.output["summary"] == "Extracted chart value: 42%"
    assert extract_res.output["candidates"] == []
    assert len(extract_res.artifact_refs) == 1

    locate_res = harness.invoke_action(_step("inspectWithAI", {"prompt": "Find chart icon", "mode": "locate"}), harness.get_context())
    assert locate_res.status == "failed"
    assert locate_res.failure_category == "observation_error"
    assert locate_res.output["reason"] == "pointer_mapping_unavailable"


@pytest.mark.usefixtures("android_sdk")
def test_macos_driver_worker_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    del monkeypatch
    started_evt = threading.Event()
    observed_cancel: list[bool] = []

    class SlowDevice(FakeMacDevice):
        def take_screenshot(self, *, control: Any = None) -> bytes:
            started_evt.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if control is not None and getattr(control, "cancellation", None) is not None and control.cancellation.is_set():
                    observed_cancel.append(True)
                    break
                time.sleep(0.01)
            return _valid_png_bytes()

    session = FakeMacSession({})
    session.device = SlowDevice(session)
    driver = AppiumConnectMacOSDriver(session=session)

    class CustomCancel(BaseException):
        pass

    def cancel_once_started() -> None:
        if started_evt.is_set():
            raise CustomCancel()

    with pytest.raises(CustomCancel):
        driver.screenshot({"cancellation_check": cancel_once_started, "timeout_seconds": 5.0})
    assert observed_cancel == [True]
    driver.close()


def test_macos_driver_ensure_alive_and_quit_unknown_settlement() -> None:
    import time

    # Owned session idle >= 45s invokes ensure_alive; session_quit_unknown absorbed when dispose settles
    ensure_alive_calls = 0
    dispose_calls = 0

    class ResourceStateError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("quit timed out")
            self.code = "session_quit_unknown"
            self.request_state = "response_unknown"

    class OwnedSession(FakeMacSession):
        def ensure_alive(self, *, control: Any = None) -> None:
            nonlocal ensure_alive_calls
            del control
            ensure_alive_calls += 1

        def quit(self, *, control: Any = None) -> None:
            del control
            self.state = "uncertain"
            raise ResourceStateError()

        def dispose(self, *, control: Any = None) -> None:
            nonlocal dispose_calls
            del control
            dispose_calls += 1
            self.state = "disposed"

    owned_driver = AppiumConnectMacOSDriver(bundle_id="com.example.App")
    owned_session = OwnedSession({})
    owned_driver._session = owned_session
    owned_driver._borrowed_session = False
    owned_driver._last_operation_finished_at = time.monotonic() - 60.0

    snap = owned_driver.ui_snapshot(MacOSUiSnapshotParams())
    assert snap["snapshot_type"] == "macos_accessibility_tree"
    assert ensure_alive_calls == 1

    # close() settles session_quit_unknown via dispose() without raising
    owned_driver.close()
    assert dispose_calls == 1
    assert owned_driver._session is None


@pytest.mark.usefixtures("android_sdk")
def test_macos_new_command_timeout_maps_none_to_zero_and_rejects_above_86400(monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.models import MacOSHarnessSettings

    assert MacOSHarnessSettings(new_command_timeout_seconds=None).new_command_timeout_seconds is None
    assert MacOSHarnessSettings(new_command_timeout_seconds=86_400).new_command_timeout_seconds == 86_400.0
    with pytest.raises(ValidationError):
        MacOSHarnessSettings(new_command_timeout_seconds=86_401)

    captured_kwargs: dict[str, object] = {}

    class FakeScope:
        def __enter__(self) -> object:
            return "fake-connection"

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        managed_cleanup_pending = False

        def __init__(self, settings: object) -> None:
            self.settings = settings

        def start_managed(self, **kwargs: object) -> FakeScope:
            return FakeScope()

    def fake_create_mac2_session(connection: object, **kwargs: object) -> FakeMacSession:
        assert connection == "fake-connection"
        captured_kwargs.update(kwargs)
        return FakeMacSession({})

    monkeypatch.setattr("appium_connect_agent.runtime.Runtime", FakeRuntime)
    monkeypatch.setattr("appium_connect_agent.automation.create_mac2_session", fake_create_mac2_session)

    driver = AppiumConnectMacOSDriver(bundle_id="com.example.App", new_command_timeout_seconds=None)
    try:
        res = driver.launch_app(MacOSLaunchAppParams())
        assert res["status"] == "passed"
        assert captured_kwargs["new_command_timeout_seconds"] == 0.0
    finally:
        driver.close()


def test_macos_ensure_alive_failure_quarantines_owned_session_and_observations_normalize_errors(tmp_path) -> None:
    import time

    class CommandError(RuntimeError):
        def __init__(self, code: str = "command_failed") -> None:
            super().__init__("remote Mac2 session died")
            self.code = code
            self.request_state = "response_received"

    class DeadOwnedSession(FakeMacSession):
        def ensure_alive(self, *, control: Any = None) -> None:
            del control
            raise CommandError("command_failed")

    driver = AppiumConnectMacOSDriver(bundle_id="com.example.App")
    dead_session = DeadOwnedSession({})
    driver._session = dead_session
    driver._borrowed_session = False
    driver._last_operation_finished_at = time.monotonic() - 60.0
    driver._session_settled = True  # avoid real quit in test teardown

    try:
        snap_res = driver.ui_snapshot(MacOSUiSnapshotParams())
        assert snap_res["status"] == "failed"
        assert snap_res["failure_category"] == "observation_error"
        assert snap_res["metadata"]["reason"] == "session_unavailable"
        assert snap_res["metadata"]["backend_code"] == "command_failed"
        assert driver._cleanup_pending is True

        shot_res = driver.take_screenshot(MacOSTakeScreenshotParams())
        assert isinstance(shot_res, dict)
        assert shot_res["status"] == "failed"
        assert shot_res["failure_category"] == "observation_error"
        assert shot_res["metadata"]["reason"] == "cleanup_pending"

        # Automatic harness ui_snapshot capture still raises ToolExecutionError on failed dict
        from fsq_agent.models import ToolExecutionError

        harness = MacOSHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
        with pytest.raises(ToolExecutionError) as cap_err:
            harness.capture_artifact("ui_snapshot", "after-fail", harness.get_context(), "step-1", "finalize")
        assert cap_err.value.context.get("capture_diagnostics", {}).get("reason") == "cleanup_pending"
    finally:
        driver._session = None
        driver._cleanup_pending = False
        driver.close()


def test_macos_visual_facts_fail_closed_on_ambiguous_or_error_and_short_circuit_matching_exact(tmp_path) -> None:
    class InspectConfigurableDriver(FakeMacOSDriver):
        def __init__(self, inspect_response: dict[str, object]) -> None:
            super().__init__()
            self.inspect_response = inspect_response
            self.screenshot_calls = 0

        def inspect_element(self, params: MacOSInspectElementParams) -> dict[str, object]:
            return self.inspect_response

        def screenshot(self, params: object | None = None) -> bytes:
            self.screenshot_calls += 1
            return _valid_png_bytes()

    evaluator = SimpleNamespace(
        evaluate=lambda req, cancellation_check=None: pytest.fail("evaluator must not be called when pre-check fails or exact fact exists"),
    )

    def policy_fn(_ctx: object, _seq: int) -> AndroidVisualAssertionPolicy:
        return AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        )

    # 1. Ambiguous target during _facts fails closed with capture_failed without taking screenshot or uploading
    amb_driver = InspectConfigurableDriver(
        {
            "status": "failed",
            "failure_category": "target_resolution_error",
            "error_message": "ambiguous",
            "metadata": {"resolution_reason": "ambiguous", "backend_code": "target_ambiguous", "session_state": "active"},
        }
    )
    amb_harness = MacOSHarness(
        driver=amb_driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "amb"),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=policy_fn,
    )
    amb_res = amb_harness.invoke_action(
        _step(
            "assertWithAI",
            {
                "assertion": "Save label matches",
                "surface": "appearance",
                "full_screen_reason": "Dialog check",
                "expect": {"kind": "visual_property", "property": "label", "value": "Save", "target": "Save"},
            },
        ),
        amb_harness.get_context(),
    )
    assert amb_res.status == "incomplete"
    assert amb_res.output["error_code"] == "capture_failed"
    assert amb_res.output["metadata"]["diagnostics"]["backend_code"] == "target_ambiguous"
    assert amb_driver.screenshot_calls == 0

    # 2. Exact accessible property match returns unsupported_surface (inconclusive/incomplete) without screenshot upload
    exact_driver = InspectConfigurableDriver({"status": "passed", "output": {"attributes": {"label": {"state": "value", "value": "Save"}}}})
    exact_harness = MacOSHarness(
        driver=exact_driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "exact"),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=policy_fn,
    )
    exact_res = exact_harness.invoke_action(
        _step(
            "assertWithAI",
            {
                "assertion": "Save label says Save",
                "surface": "appearance",
                "full_screen_reason": "Dialog check",
                "expect": {"kind": "visual_property", "property": "label", "value": "Save", "target": "Save"},
            },
        ),
        exact_harness.get_context(),
    )
    assert exact_res.status == "incomplete"
    assert exact_res.output["error_code"] == "unsupported_surface"
    assert exact_driver.screenshot_calls == 0


def test_macos_inspect_with_ai_locate_without_evaluator_fails_provider_unavailable_before_session(tmp_path) -> None:
    unlaunched_driver = AppiumConnectMacOSDriver(bundle_id="com.example.App")
    harness = MacOSHarness(
        driver=unlaunched_driver,
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=None,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            frame_sensitivity="non_sensitive",
        ),
    )
    res = harness.invoke_action(_step("inspectWithAI", {"prompt": "Locate icon", "mode": "locate"}), harness.get_context())
    assert res.status == "failed"
    assert res.failure_category == "observation_error"
    assert res.output["reason"] == "provider_unavailable"


def test_macos_assert_not_visible_handles_toctou_disappearance_during_displayed_inspect() -> None:
    class VanishingOnDisplayedElement(FakeMacElement):
        def __init__(self) -> None:
            super().__init__(displayed=True)
            self.calls = 0

        def inspect(self, *, attributes: tuple[str, ...], control: Any = None) -> Any:
            self.calls += 1
            if "displayed" in attributes:
                raise TargetError("target_not_found")
            return super().inspect(attributes=attributes, control=control)

    driver = AppiumConnectMacOSDriver(session=FakeMacSession({"Spinner": VanishingOnDisplayedElement()}), action_timeout_seconds=1)
    res = driver.assert_not_visible(MacOSAssertNotVisibleParams(target="Spinner"))
    assert res["status"] == "passed"
    assert res["output"] == {"visible": False}


def test_macos_take_screenshot_persists_artifact_ref_and_normalizes_tool_execution_error(tmp_path) -> None:
    from fsq_agent.core.interfaces import DriverFactory, HarnessFactory
    from fsq_agent.models import HarnessSettings, MacOSHarnessSettings, ToolExecutionError

    png_bytes = _valid_png_bytes()

    class ScreenshotAndFailureMacDriver(FakeMacOSDriver):
        @_macos_driver_tool("takeScreenshot", description="Capture a macOS screenshot.")
        def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes:
            del params
            return png_bytes

        @_macos_driver_tool("uiSnapshot", description="Return the macOS accessibility tree.")
        def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
            del params
            raise ToolExecutionError("Mac2 UI snapshot failed.", context={"stage": "ui_snapshot"})

    store = ArtifactStore(tmp_path / "run-artifacts")
    harness = MacOSHarness(driver=ScreenshotAndFailureMacDriver(), artifact_store=store)
    shot_res = harness.invoke_action(ExecutableStep(step_id="shot-1", kind="action", action_name="takeScreenshot", params={}), harness.get_context())
    assert shot_res.status == "passed"
    assert shot_res.output == {"byte_length": len(png_bytes)}
    assert len(shot_res.artifact_refs) == 1
    ref = shot_res.artifact_refs[0]
    assert ref.kind == "screenshot"
    assert ref.size_bytes == len(png_bytes)
    assert ref.path is not None
    assert (tmp_path / "run-artifacts" / ref.path).read_bytes() == png_bytes

    # ToolExecutionError raised directly from a driver capability method normalizes to failed action_error
    err_res = harness.invoke_action(ExecutableStep(step_id="snap-1", kind="action", action_name="uiSnapshot", params={}), harness.get_context())
    assert err_res.status == "failed"
    assert err_res.failure_category == "action_error"
    assert err_res.error_message == "Mac2 UI snapshot failed."
    assert err_res.metadata == {"context": {"stage": "ui_snapshot"}}

    # DriverFactory and HarnessFactory honor runtime bundle_id override for macOS
    macos_settings = MacOSHarnessSettings()
    macos_settings.bundle_id = "com.default.App"
    override_driver = DriverFactory().create_macos_driver(
        macos_settings,
        bundle_id="com.override.App",
    )
    assert override_driver.bundle_id == "com.override.App"
    override_harness = HarnessFactory().create_harness(
        platform="macos",
        harness_settings=HarnessSettings(platform="macos", macos=macos_settings),
        bundle_id="com.override.HarnessApp",
    )
    try:
        assert override_harness.driver.bundle_id == "com.override.HarnessApp"
    finally:
        override_harness.close()
        override_driver.close()


def test_macos_recording_metadata_and_preset_pre_plan_alignment(tmp_path) -> None:
    from pathlib import Path

    import yaml

    from fsq_agent.config import Settings
    from fsq_agent.execution.recording import _metadata_doc
    from fsq_agent.models import HarnessSettings, Task

    preset = yaml.safe_load((Path(__file__).resolve().parents[1] / "fsq_agent" / "config" / "config.macos.yaml").read_text(encoding="utf-8"))
    assert preset["agent_context"]["knowledge"]["pre_plan"] == {"dir": "."}

    settings = Settings(harness=HarnessSettings(platform="macos"))
    settings.harness.macos.bundle_id = "com.apple.TextEdit"
    doc = _metadata_doc(
        task=Task(name="Edit note", description="Open note", planning_reference_kind="goal", planning_reference_text="Open note"),
        result=SimpleNamespace(),
        settings=settings,
        required_secret_names=[],
        warnings=[],
        draft=False,
    )
    assert doc["platform"] == "macos"
    assert doc["appId"] == "com.apple.TextEdit"


@pytest.mark.usefixtures("android_sdk")
def test_appium_connect_macos_driver_forwards_skip_app_kill_on_initial_and_replacement_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fsq_agent.core.interfaces import DriverFactory
    from fsq_agent.models import MacOSHarnessSettings, MacOSLaunchAppParams

    assert MacOSHarnessSettings().skip_app_kill is True
    assert MacOSHarnessSettings(skip_app_kill=False).skip_app_kill is False
    for invalid in (None, 1, 0, "true", "false"):
        with pytest.raises(ValidationError):
            MacOSHarnessSettings(skip_app_kill=invalid)  # type: ignore[arg-type]

    created_calls: list[dict[str, object]] = []
    sessions: list[FakeMacSession] = []
    managed_starts = 0

    class FakeScope:
        def __enter__(self) -> str:
            return "fake-connection"

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

    class FakeRuntime:
        managed_cleanup_pending = False

        def __init__(self, settings: object) -> None:
            self.settings = settings

        def start_managed(self, **kwargs: object) -> FakeScope:
            nonlocal managed_starts
            managed_starts += 1
            return FakeScope()

    def fake_create_mac2_session(
        connection: object,
        *,
        bundle_id: str | None = None,
        app_path: object = None,
        arguments: list[str] | None = None,
        no_reset: bool = True,
        skip_app_kill: bool = True,
        new_command_timeout_seconds: float | None = 60.0,
        control: object = None,
    ) -> FakeMacSession:
        assert connection == "fake-connection"
        created_calls.append(
            {
                "bundle_id": bundle_id,
                "no_reset": no_reset,
                "skip_app_kill": skip_app_kill,
                "new_command_timeout_seconds": new_command_timeout_seconds,
            }
        )
        session = FakeMacSession({}, session_id=f"mac2:{len(sessions) + 1}")
        sessions.append(session)
        return session

    monkeypatch.setattr("appium_connect_agent.runtime.Runtime", FakeRuntime)
    monkeypatch.setattr("appium_connect_agent.automation.create_mac2_session", fake_create_mac2_session)

    for configured_skip_app_kill in (True, False):
        created_calls.clear()
        sessions.clear()
        settings = MacOSHarnessSettings(skip_app_kill=configured_skip_app_kill)
        settings.bundle_id = "com.apple.Notes"
        driver = DriverFactory().create_macos_driver(settings)
        try:
            first = driver.launch_app(MacOSLaunchAppParams(new_session=True))
            assert first["status"] == "passed"
            second = driver.launch_app(MacOSLaunchAppParams(bundle_id="com.apple.Preview", new_session=True))
            assert second["status"] == "passed"
            assert sessions[0].lifecycle_calls == [("quit", None)]
            assert [call["bundle_id"] for call in created_calls] == ["com.apple.Notes", "com.apple.Preview"]
            assert [call["skip_app_kill"] for call in created_calls] == [configured_skip_app_kill, configured_skip_app_kill]
        finally:
            driver.close()

    # Legacy SDK signature lacking skip_app_kill: default True remains compatible, explicit False fails fast.
    legacy_calls: list[dict[str, object]] = []

    def legacy_create_mac2_session(
        connection: object,
        *,
        bundle_id: str | None = None,
        app_path: object = None,
        arguments: list[str] | None = None,
        no_reset: bool = True,
        new_command_timeout_seconds: float | None = 60.0,
        control: object = None,
    ) -> FakeMacSession:
        legacy_calls.append({"bundle_id": bundle_id, "no_reset": no_reset})
        return FakeMacSession({}, session_id="mac2:legacy")

    monkeypatch.setattr("appium_connect_agent.automation.create_mac2_session", legacy_create_mac2_session)

    default_driver = AppiumConnectMacOSDriver(bundle_id="com.apple.Notes")
    try:
        assert default_driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
        assert legacy_calls == [{"bundle_id": "com.apple.Notes", "no_reset": True}]
    finally:
        default_driver.close()

    starts_before_reject = managed_starts
    strict_kill_driver = AppiumConnectMacOSDriver(bundle_id="com.apple.Notes", skip_app_kill=False)
    try:
        with pytest.raises(ConfigurationError, match="skip_app_kill"):
            strict_kill_driver.launch_app(MacOSLaunchAppParams(new_session=True))
        assert managed_starts == starts_before_reject
    finally:
        strict_kill_driver.close()

    with pytest.raises(ConfigurationError, match="skip_app_kill"):
        AppiumConnectMacOSDriver(bundle_id="com.apple.Notes", skip_app_kill="yes")  # type: ignore[arg-type]

    launch_schema = MacOSLaunchAppParams.model_json_schema()
    assert "new_session" in launch_schema["description"]
    assert "activate" in launch_schema["properties"]["new_session"]["description"].lower()


@pytest.mark.usefixtures("android_sdk")
def test_macos_frontmost_attachment_auto_and_omitted_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_bundles: list[str | None] = []

    class FakeScope:
        def __enter__(self) -> object:
            return "fake-connection"

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        managed_cleanup_pending = False

        def __init__(self, settings: object) -> None:
            self.settings = settings

        def start_managed(self, **kwargs: object) -> FakeScope:
            del kwargs
            return FakeScope()

    def fake_create_mac2_session(connection: object, *, bundle_id: str | None = None, **kwargs: object) -> FakeMacSession:
        del connection, kwargs
        captured_bundles.append(bundle_id)
        return FakeMacSession({}, session_id=f"mac2:{len(captured_bundles)}")

    monkeypatch.setattr("appium_connect_agent.runtime.Runtime", FakeRuntime)
    monkeypatch.setattr("appium_connect_agent.automation.create_mac2_session", fake_create_mac2_session)
    monkeypatch.setattr("appium_connect_agent.automation.frontmost_macos_app", lambda: "com.mitchellh.ghostty", raising=False)

    for configured_bundle in ("Auto", None):
        captured_bundles.clear()
        driver = AppiumConnectMacOSDriver(bundle_id=configured_bundle)
        try:
            first = driver.launch_app(MacOSLaunchAppParams(new_session=True))
            assert first["status"] == "passed"
            assert first["output"]["bundle_id"] == "com.mitchellh.ghostty"
            assert captured_bundles == ["com.mitchellh.ghostty"]

            # Reuse without explicit bundle_id activates the cached initial frontmost bundle.
            reused = driver.launch_app(MacOSLaunchAppParams(new_session=False))
            assert reused["status"] == "passed"
            assert reused["output"]["bundle_id"] == "com.mitchellh.ghostty"
            assert driver._session.lifecycle_calls == [("activate_app", "com.mitchellh.ghostty")]
        finally:
            driver.close()

    # Excluded security/login bundles fall back through lsappinfo / Finder without attaching to loginwindow.
    monkeypatch.setattr("appium_connect_agent.automation.frontmost_macos_app", lambda: "com.apple.loginwindow", raising=False)
    monkeypatch.setattr(
        "fsq_agent.drivers.macos._appium_connect.subprocess.run",
        lambda cmd, **_kw: SimpleNamespace(
            returncode=0,
            stdout="ASN:0x0-0x123:" if "front" in cmd else '"CFBundleIdentifier"="com.apple.SecurityAgent"',
        ),
    )
    captured_bundles.clear()
    fallback_driver = AppiumConnectMacOSDriver(bundle_id="auto")
    try:
        res = fallback_driver.launch_app(MacOSLaunchAppParams(new_session=True))
        assert res["status"] == "passed"
        assert res["output"]["bundle_id"] == "com.apple.finder"
        assert captured_bundles == ["com.apple.finder"]
    finally:
        fallback_driver.close()


def test_macos_press_key_normalizes_desktop_aliases_and_rejects_unknown_multichar() -> None:
    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.App", session=session)

    alias_expectations = [
        ("ArrowDown", ["COMMAND"], {"key": "XCUIKeyboardKeyDownArrow", "modifierFlags": 16}),
        ("arrow-up", None, "XCUIKeyboardKeyUpArrow"),
        ("Left Arrow", None, "XCUIKeyboardKeyLeftArrow"),
        ("RightArrow", None, "XCUIKeyboardKeyRightArrow"),
        ("ForwardDelete", None, "XCUIKeyboardKeyForwardDelete"),
        ("Del", None, "XCUIKeyboardKeyForwardDelete"),
        ("Bksp", None, "XCUIKeyboardKeyDelete"),
        ("PgDn", None, "XCUIKeyboardKeyPageDown"),
        ("PgUp", None, "XCUIKeyboardKeyPageUp"),
        ("CapsLock", None, "XCUIKeyboardKeyCapsLock"),
        ("NumpadEnter", None, "XCUIKeyboardKeyEnter"),
        ("Spacebar", None, " "),
        ("Insert", None, "XCUIKeyboardKeyHelp"),
        ("xcuikeyboardkeyescape", None, "XCUIKeyboardKeyEscape"),
    ]
    for raw_key, modifiers, expected in alias_expectations:
        res = driver.press_key(MacOSPressKeyParams(key=raw_key, modifiers=modifiers))
        assert res["status"] == "passed", raw_key
        assert session.device_keys[-1] == [expected], raw_key

    with pytest.raises(ConfigurationError, match="Unsupported macOS key"):
        driver.press_key(MacOSPressKeyParams(key="UnknownMultiCharKey"))


def test_macos_launch_app_reuse_resolves_app_path_and_installed_app_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import plistlib

    apps_dir = tmp_path / "Applications"
    safari_bundle = apps_dir / "Custom Browser.app"
    (safari_bundle / "Contents").mkdir(parents=True)
    with (safari_bundle / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleIdentifier": "com.example.CustomBrowser",
                "CFBundleDisplayName": "Custom Browser",
                "CFBundleName": "CustomBrowser",
            },
            stream,
        )
    monkeypatch.setattr("fsq_agent.drivers.macos._appium_connect._iter_local_application_bundles", lambda: [safari_bundle])

    session = FakeMacSession({})
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.Initial", session=session)

    by_path = driver.launch_app(MacOSLaunchAppParams(app_path=str(safari_bundle), new_session=False))
    assert by_path["status"] == "passed"
    assert by_path["output"]["bundle_id"] == "com.example.CustomBrowser"
    assert by_path["output"]["app_path"] == str(safari_bundle)

    by_name = driver.launch_app(MacOSLaunchAppParams(bundle_id="Custom Browser", new_session=False))
    assert by_name["status"] == "passed"
    assert by_name["output"]["bundle_id"] == "com.example.CustomBrowser"

    by_case_id = driver.launch_app(MacOSLaunchAppParams(bundle_id="com.example.custombrowser", new_session=False))
    assert by_case_id["status"] == "passed"
    assert by_case_id["output"]["bundle_id"] == "com.example.CustomBrowser"

    assert session.lifecycle_calls == [
        ("activate_app", "com.example.CustomBrowser"),
        ("activate_app", "com.example.CustomBrowser"),
        ("activate_app", "com.example.CustomBrowser"),
    ]

    with pytest.raises(ConfigurationError, match="CFBundleIdentifier"):
        driver.launch_app(MacOSLaunchAppParams(bundle_id="NonexistentAppName", new_session=False))

    # Unbundled existing executable falls back to `open -a <app_path>` when no CFBundleIdentifier exists.
    unbundled_bin = tmp_path / "custom-cli-gui"
    unbundled_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    open_calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: object) -> SimpleNamespace:
        open_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("fsq_agent.drivers.macos._appium_connect.subprocess.run", fake_run)
    by_unbundled = driver.launch_app(MacOSLaunchAppParams(app_path=str(unbundled_bin), new_session=False))
    assert by_unbundled["status"] == "passed"
    assert by_unbundled["output"]["app_path"] == str(unbundled_bin)
    assert len(open_calls) == 1
    assert open_calls[0][1:] == ["-a", str(unbundled_bin)]

    with pytest.raises(ConfigurationError, match="existing application path"):
        driver.launch_app(MacOSLaunchAppParams(app_path=str(tmp_path / "missing-app"), new_session=False))


def test_macos_type_text_permits_displayed_textarea_with_enabled_false_and_rejects_disabled_button() -> None:
    editor = FakeMacElement(
        elem_id="notes_body",
        elem_type="XCUIElementTypeTextArea",
        value="",
        enabled=False,
        displayed=True,
        x=20,
        y=40,
        width=400,
        height=300,
    )
    disabled_button = FakeMacElement(
        elem_id="submit_btn",
        elem_type="XCUIElementTypeButton",
        value="",
        enabled=False,
        displayed=True,
        x=20,
        y=360,
        width=100,
        height=32,
    )
    session = FakeMacSession({"notes_body": editor, "submit_btn": disabled_button})
    driver = AppiumConnectMacOSDriver(bundle_id="com.apple.Notes", session=session)

    ok = driver.type_text(MacOSTypeTextParams(locator={"accessibilityId": "notes_body"}, text="echo hello", clear=True))
    assert ok["status"] == "passed"
    assert editor.clicks == 1
    assert editor.value == "echo hello"

    rejected = driver.type_text(MacOSTypeTextParams(locator={"accessibilityId": "submit_btn"}, text="nope"))
    assert rejected["status"] == "failed"
    assert rejected["failure_category"] == "target_resolution_error"
    assert rejected["metadata"]["reason"] == "invalid_input_target"
    assert disabled_button.clicks == 0


def test_macos_ui_snapshot_falls_back_to_frontmost_window_and_adaptively_compacts_wide_trees() -> None:
    class OversizedFullTreeSession(FakeMacSession):
        def __init__(self, window_xml: str) -> None:
            super().__init__({}, page_source=window_xml)
            self.observe_parents: list[Any] = []

        def element(self, by: str, value: str, *, parent: Any = None, predicate: str | None = None) -> Any:
            if by == "class name" and value == "XCUIElementTypeWindow":
                return SimpleNamespace(by=by, value=value)
            return super().element(by, value, parent=parent, predicate=predicate)

        def observe_tree(self, *, parent: Any = None, control: Any = None) -> Any:
            self.observe_parents.append(parent)
            if parent is None:
                err = RuntimeError("tree exceeded budget")
                err.code = "observation_too_large"  # type: ignore[attr-defined]
                raise err
            return super().observe_tree(control=control)

    buttons_xml = "".join(
        f'<XCUIElementTypeButton identifier="item_{idx}" label="Item Label Number {idx:03d} With Extra Context" enabled="true" visible="true" x="10" y="{idx * 20}" width="240" height="20"/>'
        for idx in range(220)
    )
    window_xml = f'<XCUIElementTypeWindow title="Main Window">{buttons_xml}</XCUIElementTypeWindow>'
    session = OversizedFullTreeSession(window_xml)
    driver = AppiumConnectMacOSDriver(bundle_id="com.example.WideApp", session=session)

    snap = driver.ui_snapshot(MacOSUiSnapshotParams())
    assert snap["snapshot_type"] == "macos_accessibility_tree"
    assert len(session.observe_parents) == 2
    assert session.observe_parents[0] is None
    assert session.observe_parents[1].value == "XCUIElementTypeWindow"
    assert snap["diagnostics"]["window_scoped_fallback"] is True
    assert snap["diagnostics"]["coverage"] == "frontmost_window_fallback"
    assert snap["diagnostics"]["complete_for_requested_scope"] is False
    assert snap["diagnostics"]["adaptive_compaction"] is True
    root = snap["page_source"]["root"]
    assert root["children_truncated"] > 0
    assert len(json.dumps(root)) <= 28_000


@pytest.mark.usefixtures("android_sdk")
def test_macos_inspect_with_ai_timeout_marks_uncertain_session_for_lossless_recovery_and_idle_replacement_refreshes_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    created_sessions: list[FakeMacSession] = []

    class FakeScope:
        def __enter__(self) -> object:
            return "fake-connection"

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        managed_cleanup_pending = False

        def __init__(self, settings: object) -> None:
            self.settings = settings

        def start_managed(self, **kwargs: object) -> FakeScope:
            del kwargs
            return FakeScope()

    class TimeoutInspectDevice(FakeMacDevice):
        def inspect_with_ai(self, *args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            self.session.state = "uncertain"
            err = RuntimeError("inspect_with_ai timed out")
            err.code = "operation_timeout"  # type: ignore[attr-defined]
            err.request_state = "response_unknown"  # type: ignore[attr-defined]
            raise err

    def fake_create_mac2_session(connection: object, *, bundle_id: str | None = None, **kwargs: object) -> FakeMacSession:
        del connection, bundle_id, kwargs
        sess = FakeMacSession({"ok_btn": FakeMacElement(elem_id="ok_btn", label="OK")}, session_id=f"mac2:{len(created_sessions) + 1}")
        if not created_sessions:
            sess.device = TimeoutInspectDevice(sess)
            sess.ensure_alive = lambda **_kw: (_ for _ in ()).throw(RuntimeError("still dead"))  # type: ignore[method-assign]
        created_sessions.append(sess)
        return sess

    monkeypatch.setattr("appium_connect_agent.runtime.Runtime", FakeRuntime)
    monkeypatch.setattr("appium_connect_agent.automation.create_mac2_session", fake_create_mac2_session)

    store = ArtifactStore(tmp_path / "artifacts")

    def policy_fn(_ctx: object, _seq: int) -> AndroidVisualAssertionPolicy:
        return AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        )

    class FakeEvaluator:
        def inspect(self, _req: AIVisualInspectionRequest) -> AIVisualInspectionResult:
            return AIVisualInspectionResult(summary="ok", observations=["ok"])

        def close(self) -> None:
            return None

    driver = AppiumConnectMacOSDriver(bundle_id="com.apple.Notes", skip_app_kill=True)
    harness = MacOSHarness(
        driver=driver,
        artifact_store=store,
        ai_assertion_evaluator=FakeEvaluator(),  # type: ignore[arg-type]
        visual_assertion_policy_resolver=policy_fn,
    )
    try:
        assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
        first_session = created_sessions[0]
        res = harness.invoke_action(
            _step("inspectWithAI", {"prompt": "Read chart", "mode": "extract"}),
            harness.get_context(),
        )
        assert res.status == "failed", res.output
        assert first_session.state == "uncertain"
        assert driver._uncertain_from_timeout is True

        # Next tool call recovers losslessly by replacing the dead uncertain session because skip_app_kill=True.
        vis = driver.assert_visible(MacOSAssertVisibleParams(locator={"accessibilityId": "ok_btn"}))
        assert vis["status"] == "passed"
        assert len(created_sessions) == 2
        assert driver._session is created_sessions[1]

        # Simulate >=45s idle where ensure_alive fails: replacement session is created and _last_operation_finished_at refreshes.
        stale_ts = time.monotonic() - 120.0
        driver._last_operation_finished_at = stale_ts
        created_sessions[1].ensure_alive = lambda **_kw: (_ for _ in ()).throw(RuntimeError("idle session died"))  # type: ignore[method-assign]
        vis_after_idle = driver.assert_visible(MacOSAssertVisibleParams(locator={"accessibilityId": "ok_btn"}))
        assert vis_after_idle["status"] == "passed"
        assert len(created_sessions) == 3
        assert driver._last_operation_finished_at is not None
        assert driver._last_operation_finished_at > stale_ts
    finally:
        harness.close()
