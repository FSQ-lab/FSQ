# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from fsq_agent.core import ArtifactStore, HarnessInterface
from fsq_agent.core.harness._ai_assertion_tool import AIAssertionBackendToolMixin
from fsq_agent.core.harness._appium_connect_macos_driver import AppiumConnectMacOSDriver
from fsq_agent.core.harness._driver_tools import _macos_driver_tool
from fsq_agent.core.harness._macos import MacOSHarness
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    ConfigurationError,
    ExecutableStep,
    HarnessContext,
    MacOSAssertElementsOrderParams,
    MacOSAssertVisibleParams,
    MacOSAssertWithAIParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSPoint,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
    MacOSTakeScreenshotParams,
    MacOSTypeTextParams,
    MacOSUiSnapshotParams,
)


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

    @_macos_driver_tool("dragTo", description="Drag between macOS elements or points.")
    def drag_to(self, params: MacOSDragToParams) -> dict[str, object]:
        return self._record("drag_to", params)

    @_macos_driver_tool("takeScreenshot", description="Capture a macOS screenshot.")
    def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes:
        recorded = params.model_dump(mode="json", exclude_none=True)
        self.calls.append(("take_screenshot", recorded))
        return b"fake-png"

    @_macos_driver_tool("uiSnapshot", description="Return the macOS accessibility tree.")
    def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        recorded = params.model_dump(mode="json", exclude_none=True)
        self.calls.append(("ui_snapshot", recorded))
        return {"title": "Finder", "snapshot_type": "macos_accessibility_tree"}

    @_macos_driver_tool("assertVisible", description="Assert a macOS element is visible.")
    def assert_visible(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        return self._record("assert_visible", params)

    @_macos_driver_tool("assertElementsOrder", description="Assert macOS elements appear in order.")
    def assert_elements_order(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        return self._record("assert_elements_order", params)

    @_macos_driver_tool("assertWithAI", description="Evaluate a macOS visual assertion with AI.")
    def assert_with_ai(self, params: MacOSAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        recorded = params.model_dump(mode="json", exclude_none=True) if hasattr(params, "model_dump") else None
        self.calls.append(("screenshot", recorded))
        return b"fake-png"


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
        ("dragTo", {"source": {"point": {"x": 1, "y": 2}}, "destination": {"target": "Trash"}}, "drag_to"),
        ("takeScreenshot", {}, "take_screenshot"),
        ("uiSnapshot", {}, "ui_snapshot"),
        ("assertVisible", {"target": "Save"}, "assert_visible"),
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
    assert "ui_snapshot" in schemas
    assert "assert_elements_order" in schemas
    assert "assert_with_ai" not in schemas
    assert schemas["click_on"].driver_method == "click_on"
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

    assert (tmp_path / screenshot_ref.path).read_bytes() == b"fake-png"
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
                passed=True,
                explanation="The expected macOS window is visible.",
                provider="fake",
                model="fake-model",
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
            )

    evaluator = FakeEvaluator()
    harness = MacOSHarness(
        driver=FakeMacOSDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=evaluator,
    )

    result = harness.invoke_action(_step("assertWithAI", {"prompt": "The Save dialog is visible."}), harness.get_context())

    assert result.status == "passed"
    assert result.action_name == "assertWithAI"
    assert len(evaluator.requests) == 1
    assert evaluator.requests[0].platform == "macos"
    assert evaluator.requests[0].prompt == "The Save dialog is visible."


def test_macos_harness_assert_with_ai_requires_evaluator() -> None:
    harness = MacOSHarness(driver=FakeMacOSDriver())

    result = harness.invoke_action(_step("assertWithAI", {"prompt": "anything"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"


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
        displayed: bool = True,
        enabled: bool = True,
        accepts_element_input: bool = True,
    ) -> None:
        self.bounds = _bounds(x, y, width, height)
        self.value = value
        self.displayed = displayed
        self.enabled = enabled
        self.accepts_element_input = accepts_element_input
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
        results = []
        for name in attributes:
            if name == "bounds":
                results.append(_attr_obs("bounds", self.bounds))
            elif name == "value":
                if self.value is None:
                    results.append(_attr_obs("value", None, state="missing"))
                else:
                    results.append(_attr_obs("value", self.value))
            elif name == "displayed":
                results.append(_attr_obs("displayed", self.displayed))
            elif name == "enabled":
                results.append(_attr_obs("enabled", self.enabled))
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
        return b"\x89PNG\r\n\x1a\nfake"


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

    def element(self, by: str, value: str, *, predicate: str | None = None) -> Any:
        for name, candidate_el in self.elements.items():
            if by == "accessibility id" and value == name:
                return candidate_el
            if f'"{name}"' in value or f"'{name}'" in value or (predicate and f'"{name}"' in predicate):
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
        raise RuntimeError("boom")

    monkeypatch.setattr(driver, "_create_session", fail_creation)

    with pytest.raises(RuntimeError, match="boom"):
        driver.launch_app(MacOSLaunchAppParams(bundle_id="com.example.App", new_session=True))

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
