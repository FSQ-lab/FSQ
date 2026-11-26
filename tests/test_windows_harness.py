# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import io
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from fsq_agent.core import (
    ArtifactStore,
    CapabilityDefinitionFactory,
    CapabilityRegistry,
    HarnessInterface,
    StepRunner,
)
from fsq_agent.core.harness._ai_assertion_tool import AIAssertionBackendToolMixin
from fsq_agent.core.harness._driver_tools import _windows_driver_tool
from fsq_agent.core.harness._windows import WindowsHarness
from fsq_agent.drivers._color_sample import _sample_local_png_color
from fsq_agent.drivers._host_visual_inspection import _run_host_visual_inspection
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    AIVisualCandidate,
    AIVisualInspectionResult,
    AndroidVisualAssertionPolicy,
    ExecutableStep,
    HarnessContext,
    SampleColorParams,
    VisualImageBounds,
    WindowsAssertVisibleParams,
    WindowsAssertWithAIParams,
    WindowsClickOnParams,
    WindowsDoubleClickOnParams,
    WindowsDragToParams,
    WindowsHoverOnParams,
    WindowsInspectWithAIParams,
    WindowsKillAppParams,
    WindowsLaunchAppParams,
    WindowsPressKeyParams,
    WindowsRightClickOnParams,
    WindowsScrollOnParams,
    WindowsTypeTextParams,
    WindowsUiSnapshotParams,
)


def _valid_png_bytes(width: int = 80, height: int = 60, color: str = "teal") -> bytes:
    buf = io.BytesIO()
    with Image.new("RGB", (width, height), color) as image:
        image.putpixel((width // 2, height // 2), (255, 255, 255))
        image.save(buf, format="PNG")
    return buf.getvalue()


class FakeWindowsDriver(AIAssertionBackendToolMixin):
    backend = "fake-pywinauto"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def context(self) -> dict[str, object]:
        self.calls.append(("context", None))
        return {
            "session_id": "pywinauto:uia",
            "current_url": None,
            "screen_size": (1920, 1080),
            "metadata": {"backend_kind": "uia", "app_path_configured": True},
        }

    def _record(self, method_name: str, params: object) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append((method_name, recorded))
        return {method_name: True}

    @_windows_driver_tool(
        "launchApp",
        description="Launch the configured Windows desktop application.",
    )
    def launch_app(self, params: WindowsLaunchAppParams) -> dict[str, object]:
        return self._record("launch_app", params)

    @_windows_driver_tool("killApp", description="Stop the launched Windows desktop application.")
    def kill_app(self, params: WindowsKillAppParams) -> dict[str, object]:
        return self._record("kill_app", params)

    @_windows_driver_tool("clickOn", description="Click a Windows control resolved from the UI snapshot.")
    def click_on(self, params: WindowsClickOnParams) -> dict[str, object]:
        return self._record("click_on", params)

    @_windows_driver_tool("doubleClickOn", description="Double-click a Windows control.")
    def double_click_on(self, params: WindowsDoubleClickOnParams) -> dict[str, object]:
        return self._record("double_click_on", params)

    @_windows_driver_tool("rightClickOn", description="Right-click a Windows control.")
    def right_click_on(self, params: WindowsRightClickOnParams) -> dict[str, object]:
        return self._record("right_click_on", params)

    @_windows_driver_tool("typeText", description="Type text into a Windows control.")
    def type_text(self, params: WindowsTypeTextParams) -> dict[str, object]:
        return self._record("type_text", params)

    @_windows_driver_tool("pressKey", description="Send a keyboard key sequence to the active Windows window.")
    def press_key(self, params: WindowsPressKeyParams) -> dict[str, object]:
        return self._record("press_key", params)

    @_windows_driver_tool("hoverOn", description="Move the mouse over a Windows control.")
    def hover_on(self, params: WindowsHoverOnParams) -> dict[str, object]:
        return self._record("hover_on", params)

    @_windows_driver_tool("scrollOn", description="Scroll over a Windows control.")
    def scroll_on(self, params: WindowsScrollOnParams) -> dict[str, object]:
        return self._record("scroll_on", params)

    @_windows_driver_tool("dragTo", description="Drag between Windows controls or points.")
    def drag_to(self, params: WindowsDragToParams) -> dict[str, object]:
        return self._record("drag_to", params)

    @_windows_driver_tool("assertVisible", description="Assert that a Windows control is visible.")
    def assert_visible(self, params: WindowsAssertVisibleParams) -> dict[str, object]:
        return self._record("assert_visible", params)

    @_windows_driver_tool("assertWithAI", description="Evaluate an explicit Windows visual assertion with AI.")
    def assert_with_ai(self, params: WindowsAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    @_windows_driver_tool("inspectWithAI", description="Read pixel-only Windows visual content without performing an action.")
    def inspect_with_ai(self, params: WindowsInspectWithAIParams) -> dict[str, object]:
        return _run_host_visual_inspection(self, params, platform="windows")

    @_windows_driver_tool("sampleColor", description="Measure exact local RGB from a current Windows frame point or region.")
    def sample_color(self, params: SampleColorParams) -> dict[str, object]:
        self.calls.append(("sample_color", params.model_dump(mode="json", exclude_none=True)))
        return {"status": "passed", "output": _sample_local_png_color(_valid_png_bytes(), params, window_rect=(100, 200, 180, 260), pointer_size=(1920, 1080))}

    @_windows_driver_tool("uiSnapshot", description="Return the current Windows window control tree snapshot.")
    def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append(("ui_snapshot", recorded))
        return {"format": "xml", "xml": '<Window title="Notepad" />'}

    def screenshot(self, params: object | None = None) -> bytes:
        self.calls.append(("screenshot", None))
        return _valid_png_bytes()


def _step(action_name: str, params: dict[str, Any] | None = None) -> ExecutableStep:
    return ExecutableStep(step_id="step-1", kind="action", action_name=action_name, params=params or {})


def test_windows_harness_dispatches_fsq_action_names_to_driver() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    context = harness.get_context()

    cases = [
        ("launchApp", {}, "launch_app"),
        ("clickOn", {"target": "Open the File menu", "locator": {"title": "File"}}, "click_on"),
        ("doubleClickOn", {"target": "Open the document", "locator": {"title": "Document", "control_type": "Edit"}}, "double_click_on"),
        ("rightClickOn", {"target": "Open the File context menu", "locator": {"title": "File"}}, "right_click_on"),
        ("typeText", {"target": "Enter document text", "locator": {"title": "Document"}, "text": "hello", "textType": "literal"}, "type_text"),
        ("pressKey", {"key": "^s"}, "press_key"),
        ("hoverOn", {"target": "Hover Save", "locator": {"title": "Save"}}, "hover_on"),
        ("scrollOn", {"target": "Scroll results", "locator": {"automation_id": "Results"}, "wheel_dist": -5}, "scroll_on"),
        (
            "dragTo",
            {
                "target": "Move item",
                "source": {"locator": {"title": "Item"}},
                "destination": {"offset": {"x": 20, "y": 0}},
                "mouse_button": "left",
            },
            "drag_to",
        ),
        ("assertVisible", {"target": "Verify Save is visible", "locator": {"title": "Save"}}, "assert_visible"),
        ("uiSnapshot", {}, "ui_snapshot"),
        ("killApp", {}, "kill_app"),
    ]

    for action_name, params, _method_name in cases:
        result = harness.invoke_action(_step(action_name, params), context)
        assert result.status == "passed"
        assert result.action_name == action_name

    assert isinstance(harness, HarnessInterface)
    assert context == HarnessContext(
        platform="windows",
        session_id="pywinauto:uia",
        current_url=None,
        screen_size=(1920, 1080),
        metadata={"backend_kind": "uia", "app_path_configured": True},
    )
    assert driver.calls == [("context", None)] + [(method_name, params) for _action_name, params, method_name in cases]


def test_windows_harness_action_space_returns_catalog_backed_schemas() -> None:
    harness = WindowsHarness(driver=FakeWindowsDriver())

    schemas = {schema.name: schema for schema in harness.action_space()}

    assert "click_on" in schemas
    assert "ui_snapshot" in schemas
    assert "inspect_with_ai" in schemas
    assert "sample_color" in schemas
    assert "assert_with_ai" in schemas
    assert set(schemas["launch_app"].params_json_schema["properties"]) == {"extra_args"}
    assert schemas["click_on"].driver_method == "click_on"
    assert schemas["click_on"].fsq_action_name == "clickOn"
    assert schemas["click_on"].platform == "windows"
    assert schemas["click_on"].metadata["driver_class"] == "FakeWindowsDriver"
    assert schemas["click_on"].metadata["backend"] == "fake-pywinauto"
    assert schemas["click_on"].metadata["replay"] == {"kind": "fsq_command", "alias": "clickOn"}
    assert "locator" in schemas["click_on"].params_json_schema["properties"]
    assert "point" in schemas["click_on"].params_json_schema["properties"]
    assert "required" not in schemas["click_on"].params_json_schema
    assert "target" in schemas["click_on"].params_json_schema["properties"]
    assert "locator" in schemas["type_text"].params_json_schema["required"]
    assert "point" not in schemas["type_text"].params_json_schema["properties"]
    assert "exactly one of non-empty locator or explicit screen point" in schemas["click_on"].params_json_schema["description"]
    assert "descriptive" in schemas["click_on"].params_json_schema["properties"]["target"]["description"]
    assert schemas["sample_color"].fsq_action_name == "sampleColor"
    assert schemas["drag_to"].fsq_action_name == "dragTo"
    assert schemas["ui_snapshot"].driver_method == "ui_snapshot"
    assert schemas["ui_snapshot"].fsq_action_name == "uiSnapshot"


@pytest.mark.parametrize("private_param", ["app_path", "wait_for"])
def test_windows_harness_rejects_internal_launch_params(private_param: str) -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("launchApp", {private_param: "not-exposed"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message is not None
    assert f"{private_param}: Extra inputs are not permitted" in result.error_message
    assert driver.calls == [("context", None)]


def test_windows_harness_validation_failure_does_not_call_driver_method() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(
        _step("clickOn", {"target": "Click Login", "locator": {"unknown": "Login"}}),
        harness.get_context(),
    )

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Invalid Windows parameters for clickOn. locator.unknown: Extra inputs are not permitted"
    assert result.metadata["validation_errors"]
    assert driver.calls == [("context", None)]


def test_windows_harness_reports_invalid_locator_type() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"target": "Click Login", "locator": "Login"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message is not None
    assert "locator:" in result.error_message
    assert "valid dictionary or instance of WindowsLocator" in result.error_message
    assert result.metadata["validation_errors"][0]["loc"] == ("locator",)
    assert driver.calls == [("context", None)]


def test_windows_harness_reports_none_locator() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"target": "Click Login", "locator": None}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message is not None
    assert "requires exactly one of locator or point" in result.error_message
    assert result.metadata["validation_errors"][0]["loc"] == ()
    assert driver.calls == [("context", None)]

    type_none = harness.invoke_action(_step("typeText", {"target": "Type Login", "locator": None, "text": "alice"}), harness.get_context())
    assert type_none.status == "failed"
    assert type_none.failure_category == "configuration_error"
    assert "locator:" in (type_none.error_message or "")
    assert type_none.metadata["validation_errors"][0]["loc"] == ("locator",)


def test_windows_harness_allows_missing_target() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"locator": {"title": "Login"}}), harness.get_context())

    assert result.status == "passed"
    assert result.failure_category is None
    assert driver.calls == [("context", None), ("click_on", {"locator": {"title": "Login"}})]


def test_windows_mouse_parameter_models_validate_modes_and_distances() -> None:
    params = WindowsDragToParams.model_validate(
        {
            "target": "Move item",
            "source": {"point": {"x": 10, "y": 20}},
            "destination": {"locator": {"automation_id": "DropTarget"}},
        }
    )

    assert params.mouse_button == "left"
    assert params.source.point is not None
    assert params.destination.locator is not None

    with pytest.raises(ValueError, match="exactly one"):
        WindowsDragToParams.model_validate(
            {
                "target": "Move item",
                "source": {"point": {"x": 1, "y": 2}, "locator": {"title": "Item"}},
                "destination": {"point": {"x": 3, "y": 4}},
            }
        )
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        WindowsDragToParams.model_validate(
            {
                "target": "Move item",
                "source": {"point": {"x": -1, "y": 2}},
                "destination": {"point": {"x": 3, "y": 4}},
            }
        )
    with pytest.raises(ValueError, match="non-zero offset"):
        WindowsDragToParams.model_validate(
            {
                "target": "Move item",
                "source": {"point": {"x": 1, "y": 2}},
                "destination": {"offset": {"x": 0, "y": 0}},
            }
        )
    with pytest.raises(ValueError, match="non-zero wheel_dist"):
        WindowsScrollOnParams.model_validate({"target": "Scroll results", "locator": {"title": "Results"}, "wheel_dist": 0})


def test_windows_harness_captures_screenshot_and_ui_snapshot_with_artifact_store(tmp_path) -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
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
    assert "Notepad" in (tmp_path / snapshot_ref.path).read_text(encoding="utf-8")
    assert driver.calls == [("context", None), ("screenshot", None), ("ui_snapshot", {})]


@pytest.mark.parametrize("phase", ["prepare", "finalize"])
def test_windows_harness_uses_empty_artifacts_when_evidence_window_capture_fails(tmp_path, phase) -> None:
    class UnavailableWindowDriver(FakeWindowsDriver):
        def screenshot(self, params: object | None = None) -> bytes:
            raise RuntimeError("window is unavailable")

        def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
            raise RuntimeError("window is unavailable")

    harness = WindowsHarness(
        driver=UnavailableWindowDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
    )
    context = harness.get_context()

    for kind in ("screenshot", "ui_snapshot"):
        with pytest.raises(RuntimeError, match="window is unavailable"):
            harness.capture_artifact(kind=kind, reason="capture", context=context, step_id="step-1", phase=phase)
    assert not list(tmp_path.rglob("*.png"))


def test_windows_harness_does_not_hide_invoke_capture_failures(tmp_path) -> None:
    class UnavailableWindowDriver(FakeWindowsDriver):
        def screenshot(self, params: object | None = None) -> bytes:
            raise RuntimeError("window is unavailable")

    harness = WindowsHarness(
        driver=UnavailableWindowDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
    )

    with pytest.raises(RuntimeError, match="window is unavailable"):
        harness.capture_artifact(
            kind="screenshot",
            reason="ai-assertion",
            context=harness.get_context(),
            step_id="step-1",
            phase="invoke",
        )


@pytest.mark.parametrize(
    ("action_name", "params"),
    [("launchApp", {}), ("killApp", {})],
)
def test_windows_runner_lifecycle_captures_before_and_after_when_window_capture_fails(
    tmp_path,
    action_name,
    params,
) -> None:
    class UnavailableWindowDriver(FakeWindowsDriver):
        def screenshot(self, params: object | None = None) -> bytes:
            raise RuntimeError("window is unavailable")

        def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
            raise RuntimeError("window is unavailable")

    definitions = CapabilityDefinitionFactory().platform_definitions(
        platform="windows",
        backend="pywinauto",
        include_ai_assertion=False,
    )
    harness = WindowsHarness(
        driver=UnavailableWindowDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
    )
    runner = StepRunner(
        harness=harness,
        capability_registry=CapabilityRegistry.from_definitions(definitions),
    )

    result = runner.run_step(
        run_id="run-1",
        step=_step(action_name, params),
    )

    assert result.status == "failed"
    assert result.failure_category == "artifact_error"
    assert result.action_status == "passed"
    assert len(result.evidence_errors) == 1
    assert all(ref.availability == "failed" for phase in result.phase_reports for ref in phase.artifact_refs)


def test_windows_harness_assert_with_ai_uses_injected_evaluator_and_single_frame_prepare_reuse(tmp_path) -> None:
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.core import EvidenceRecorder

    class FakeEvaluator:
        def __init__(self) -> None:
            self.requests: list[AIAssertionRequest] = []

        def evaluate(self, request: AIAssertionRequest, *, cancellation_check=None) -> AIAssertionResult:
            self.requests.append(request)
            return AIAssertionResult(
                status="passed",
                outcome="passed",
                passed=True,
                explanation="The expected window chart is visible.",
                provider="fake",
                model="fake-model",
                capture=request.capture,
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
            )

    driver = FakeWindowsDriver()
    evaluator = FakeEvaluator()
    harness = WindowsHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )

    recorder = EvidenceRecorder(run_id="run-win", output_dir=tmp_path)
    runner = StepRunner(harness=harness, capability_registry=build_capability_registry(platform="windows"), evidence_sink=recorder)
    step_result = runner.run_step(
        run_id="run-win",
        step=ExecutableStep(
            step_id="step-win-visual",
            kind="assertion",
            action_name="assertWithAI",
            params={
                "assertion": "The preview chart is visible.",
                "surface": "custom_canvas",
                "region": {"kind": "image_rect", "rect": {"left": 4, "top": 4, "right": 60, "bottom": 45}},
            },
        ),
    )

    assert step_result.status == "passed"
    assert len(evaluator.requests) == 1
    assert evaluator.requests[0].platform == "windows"
    assert evaluator.requests[0].prompt == "The preview chart is visible."
    screenshot_calls = [call for call in driver.calls if call[0] == "screenshot"]
    assert len(screenshot_calls) == 1
    prepare_refs = step_result.phase_reports[0].artifact_refs
    assert len(prepare_refs) == 1
    assert evaluator.requests[0].screenshot_artifact_ref.artifact_id == prepare_refs[0].artifact_id


def test_windows_harness_assert_with_ai_without_evaluator_or_privacy_blocked_returns_incomplete(tmp_path) -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path / "offline"))

    result = harness.invoke_action(
        _step("assertWithAI", {"assertion": "Chart visible", "surface": "custom_canvas", "full_screen_reason": "Canvas fills window"}),
        harness.get_context(),
    )

    assert result.status == "incomplete"
    assert result.failure_category is None
    assert result.output["outcome"] == "inconclusive"
    assert result.output["error_code"] == "provider_unavailable"
    assert [c for c in driver.calls if c[0] == "screenshot"] == []


def test_windows_harness_inspect_with_ai_extract_locate_and_privacy(tmp_path) -> None:
    class InspectEvaluator:
        def __init__(self) -> None:
            self.inspect_requests = []

        def evaluate(self, request, *, cancellation_check=None):
            raise AssertionError("inspect_with_ai must not call evaluate")

        def inspect(self, request, *, cancellation_check=None) -> AIVisualInspectionResult:
            self.inspect_requests.append(request)
            return AIVisualInspectionResult(summary="Preview pane shows 98% pass rate", candidates=())

    driver = FakeWindowsDriver()
    evaluator = InspectEvaluator()
    harness = WindowsHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "extract"),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )
    extract_res = harness.invoke_action(_step("inspectWithAI", {"prompt": "Read chart metric", "mode": "extract"}), harness.get_context())
    assert extract_res.status == "passed"
    assert extract_res.output["summary"] == "Preview pane shows 98% pass rate"
    assert len(extract_res.artifact_refs) == 1
    assert (tmp_path / "extract" / extract_res.artifact_refs[0].path).is_file()
    assert len(evaluator.inspect_requests) == 1
    assert evaluator.inspect_requests[0].platform == "windows"

    locate_unmapped = harness.invoke_action(_step("inspectWithAI", {"prompt": "Find chart legend", "mode": "locate"}), harness.get_context())
    assert locate_unmapped.status == "failed"
    assert locate_unmapped.output["reason"] == "pointer_mapping_unavailable"
    assert len(evaluator.inspect_requests) == 1

    class MappedInspectEvaluator:
        def __init__(self) -> None:
            self.requests = []

        def evaluate(self, request, *, cancellation_check=None):
            raise AssertionError("inspect_with_ai must not call evaluate")

        def inspect(self, request, *, cancellation_check=None) -> AIVisualInspectionResult:
            self.requests.append(request)
            return AIVisualInspectionResult(
                summary="Located custom chart bar",
                candidates=(
                    AIVisualCandidate(
                        label="Q4 Bar",
                        image_bounds=VisualImageBounds(x=10, y=20, width=16, height=12),
                        confidence=0.92,
                    ),
                ),
            )

    class MappedWindowsDriver(FakeWindowsDriver):
        def __init__(self, rect: tuple[int, int, int, int], screen: tuple[int, int]) -> None:
            super().__init__()
            self._rect = rect
            self._screen = screen

        def capture_visual_frame_with_mapping(self):
            self.calls.append(("capture_visual_frame_with_mapping", None))
            return _valid_png_bytes(width=80, height=60), self._rect, self._screen

    mapped_eval = MappedInspectEvaluator()
    mapped_driver = MappedWindowsDriver((300, 150, 380, 210), (1920, 1080))
    mapped_harness = WindowsHarness(
        driver=mapped_driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "locate_ok"),
        ai_assertion_evaluator=mapped_eval,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )
    locate_ok = mapped_harness.invoke_action(_step("inspectWithAI", {"prompt": "Find Q4 bar", "mode": "locate"}), mapped_harness.get_context())
    assert locate_ok.status == "passed"
    assert locate_ok.output["pointer_size"] == {"width": 1920, "height": 1080}
    assert locate_ok.output["candidates"] == [
        {
            "label": "Q4 Bar",
            "image_bounds": {"x": 10, "y": 20, "width": 16, "height": 12},
            "pointer_bounds": {"x": 310, "y": 170, "width": 16, "height": 12},
            "pointer_center": {"x": 318, "y": 176},
            "confidence": 0.92,
        }
    ]

    # Non-1:1 DPI scaling mismatch fails closed before provider upload.
    scaled_driver = MappedWindowsDriver((300, 150, 420, 240), (1920, 1080))
    scaled_harness = WindowsHarness(
        driver=scaled_driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "locate_scaled"),
        ai_assertion_evaluator=mapped_eval,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )
    locate_scaled = scaled_harness.invoke_action(_step("inspectWithAI", {"prompt": "Find Q4 bar", "mode": "locate"}), scaled_harness.get_context())
    assert locate_scaled.status == "failed"
    assert locate_scaled.output["reason"] == "pointer_mapping_unavailable"
    assert len(mapped_eval.requests) == 1

    # Local sampleColor succeeds offline without evaluator or upload policy.
    sample_res = WindowsHarness(driver=FakeWindowsDriver(), artifact_store=ArtifactStore(run_dir=tmp_path / "sample"))
    color_res = sample_res.invoke_action(
        _step("sampleColor", {"point": {"x": 140, "y": 230}, "coordinate_space": "pointer"}),
        sample_res.get_context(),
    )
    assert color_res.status == "passed"
    assert color_res.output["hex"] == "#FFFFFF"
    assert color_res.output["image_point"] == (40, 30)
    assert color_res.output["pointer_point"] == (140, 230)
    blocked_driver = FakeWindowsDriver()
    blocked_harness = WindowsHarness(
        driver=blocked_driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "privacy"),
        ai_assertion_evaluator=evaluator,
    )
    privacy_res = blocked_harness.invoke_action(_step("inspectWithAI", {"prompt": "Read chart metric"}), blocked_harness.get_context())
    assert privacy_res.status == "failed"
    assert privacy_res.output["reason"] == "privacy_blocked"
    assert privacy_res.artifact_refs == []
    assert [c for c in blocked_driver.calls if c[0] == "screenshot"] == []


def test_windows_harness_rejects_unknown_action() -> None:
    harness = WindowsHarness(driver=FakeWindowsDriver())

    result = harness.invoke_action(_step("unsupportedAction", {}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert "Unsupported Windows action" in (result.error_message or "")


def test_windows_harness_classifies_main_window_timeout() -> None:
    harness = WindowsHarness(driver=FakeWindowsDriver())

    category = harness.classify_error(TimeoutError("main window timeout"), "invoke", _step("launchApp"))

    assert category == "timeout_error"


def _make_fake_windows_desktop():
    import re

    class FakeRect:
        def width(self) -> int:
            return 1280

        def height(self) -> int:
            return 920

    class FakeWindowEntry:
        def __init__(
            self,
            desktop: "FakeDesktop",
            *,
            handle: int,
            process_id: int,
            title: str,
            visible: bool = True,
            control_type: str = "Window",
        ) -> None:
            self.desktop = desktop
            self.handle = handle
            self.process_id = process_id
            self.rich_text = title
            self.name = title
            self.visible = visible
            self.control_type = control_type
            self.waited: list[str] = []
            self.typed_keys: list[str] = []
            self.closed = False
            self.close_error: Exception | None = None

        def wait(self, state: str, timeout: float | None = None) -> "FakeWindowEntry":
            if self.closed or not self.visible or self.handle not in self.desktop.windows:
                raise LookupError(f"window {self.handle} is not available")
            self.waited.append(state)
            return self

        def type_keys(self, key: str, **kwargs: object) -> None:
            self.desktop.events.append(("type_keys", self.handle, self.process_id, key))
            self.typed_keys.append(key)

        def close(self) -> None:
            if self.close_error is not None:
                raise self.close_error
            self.closed = True
            self.visible = False
            self.desktop.windows.pop(self.handle, None)
            self.desktop.events.append(("close_window", self.handle, self.process_id))

        def rectangle(self) -> FakeRect:
            return FakeRect()

    class FakeDesktop:
        def __init__(self) -> None:
            self.processes: dict[int, dict[str, Any]] = {}
            self.windows: dict[int, FakeWindowEntry] = {}
            self.active_handle: int | None = None
            self.events: list[tuple[Any, ...]] = []
            self._next_pid = 1000
            self._next_handle = 10000
            self.on_start = None
            self.kill_error: Exception | None = None

        def add_process(self, exe_path: str, *, pid: int | None = None, accessible: bool = True, alive: bool = True) -> int:
            proc_id = pid if pid is not None else self._next_pid
            self._next_pid = max(self._next_pid, proc_id + 1)
            self.processes[proc_id] = {"path": exe_path, "accessible": accessible, "alive": alive}
            return proc_id

        def list_pids(self) -> list[int]:
            return [pid for pid, info in self.processes.items() if info["alive"]]

        def add_window(
            self,
            process_id: int,
            title: str,
            *,
            handle: int | None = None,
            visible: bool = True,
            active: bool = False,
            control_type: str = "Window",
        ) -> FakeWindowEntry:
            hwnd = handle if handle is not None else self._next_handle
            self._next_handle = max(self._next_handle, hwnd + 1)
            entry = FakeWindowEntry(self, handle=hwnd, process_id=process_id, title=title, visible=visible, control_type=control_type)
            self.windows[hwnd] = entry
            if active:
                self.active_handle = hwnd
            return entry

        def find_top_windows(self, **kwargs: object) -> list[FakeWindowEntry]:
            self.events.append(("find_top_windows", dict(kwargs)))
            title_re = kwargs.get("title_re")
            visible_only = kwargs.get("visible_only", True)
            process = kwargs.get("process")
            handle = kwargs.get("handle")
            control_type = kwargs.get("control_type")
            results = [w for w in self.windows.values() if not w.closed]
            if handle is not None:
                results = [w for w in results if w.handle == handle]
            if process is not None:
                results = [w for w in results if w.process_id == process]
            if control_type is not None:
                results = [w for w in results if w.control_type == control_type]
            if visible_only:
                results = [w for w in results if w.visible]
            if isinstance(title_re, str):
                pattern = re.compile(title_re)
                results = [w for w in results if pattern.match(w.rich_text)]
            return results

        def process_executable(self, pid: int) -> str | None:
            info = self.processes.get(pid)
            if info is None or not info["alive"]:
                raise LookupError(f"process {pid} not found")
            if not info["accessible"]:
                raise PermissionError(f"access denied for pid {pid}")
            return info["path"]

        def make_app_cls(self):
            desktop = self

            class FakeApplication:
                def __init__(self, backend: str = "uia") -> None:
                    self.backend = backend
                    self.process: int | None = None
                    self.started_cmd: str | None = None

                def start(self, cmd: str) -> "FakeApplication":
                    self.started_cmd = cmd
                    desktop.events.append(("start", cmd))
                    if desktop.on_start is not None:
                        self.process = desktop.on_start(desktop, cmd)
                    else:
                        pid = desktop.add_process(cmd.split(maxsplit=1)[0].strip('"'))
                        desktop.add_window(pid, "Launched Window", active=True)
                        self.process = pid
                    return self

                def connect(self, **kwargs: object) -> "FakeApplication":
                    desktop.events.append(("connect", dict(kwargs)))
                    if kwargs.get("active_only"):
                        if desktop.active_handle is None or desktop.active_handle not in desktop.windows:
                            raise LookupError("no active window")
                        self.process = desktop.windows[desktop.active_handle].process_id
                        return self
                    if "handle" in kwargs:
                        hwnd = int(kwargs["handle"])  # type: ignore[arg-type]
                        if hwnd not in desktop.windows:
                            raise LookupError(f"invalid handle {hwnd}")
                        self.process = desktop.windows[hwnd].process_id
                        return self
                    if "process" in kwargs:
                        pid = int(kwargs["process"])  # type: ignore[arg-type]
                        info = desktop.processes.get(pid)
                        if info is None or not info["alive"]:
                            raise LookupError(f"pid {pid} not running")
                        self.process = pid
                        return self
                    if "title_re" in kwargs:
                        matches = desktop.find_top_windows(title_re=kwargs["title_re"])
                        if not matches:
                            raise LookupError("window not found")
                        if len(matches) > 1:
                            raise RuntimeError(f"ambiguous title_re matches: {len(matches)}")
                        self.process = matches[0].process_id
                        return self
                    raise RuntimeError(f"unsupported connect kwargs: {kwargs}")

                def window(self, **kwargs: object) -> FakeWindowEntry:
                    query = dict(kwargs)
                    if "handle" not in query:
                        query["process"] = self.process
                    matches = desktop.find_top_windows(**query)
                    if len(matches) != 1:
                        raise LookupError(f"expected 1 window for {kwargs}, got {len(matches)}")
                    return matches[0]

                def top_window(self) -> FakeWindowEntry:
                    matches = desktop.find_top_windows(process=self.process)
                    if not matches:
                        raise LookupError(f"no windows for pid {self.process}")
                    return matches[0]

                def kill(self) -> bool:
                    if desktop.kill_error is not None:
                        raise desktop.kill_error
                    if self.process is not None and self.process in desktop.processes:
                        desktop.processes[self.process]["alive"] = False
                        for hwnd in [h for h, w in desktop.windows.items() if w.process_id == self.process]:
                            desktop.windows.pop(hwnd, None)
                    desktop.events.append(("kill_process", self.process))
                    return True

            return FakeApplication

    return FakeDesktop()


def test_pywinauto_driver_launch_app_uses_launch_args_and_window_title_re(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    beta_exe = r"C:\Program Files (x86)\Microsoft\Edge Beta\Application\msedge.exe"
    stable_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    stable_pid = desktop.add_process(stable_exe, pid=1100)
    desktop.add_window(stable_pid, "Docs - Microsoft Edge", active=True)

    def on_start(d, cmd: str) -> int:
        pid = d.add_process(beta_exe, pid=2200)
        d.add_window(pid, "Helper Frame", active=False)
        d.add_window(pid, "New Tab - Microsoft Edge Beta", active=True)
        return pid

    desktop.on_start = on_start
    driver = PywinautoWindowsDriver(
        app_path=beta_exe,
        window_title_re=".*Microsoft.*Edge Beta",
        launch_args=["--no-first-run", "--window-size=1280,920"],
    )
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        result = driver.launch_app(WindowsLaunchAppParams(extra_args=["--incognito"]))
        assert result["status"] == "passed"
        start_events = [event for event in desktop.events if event[0] == "start"]
        assert start_events == [("start", f'"{beta_exe}" --no-first-run --window-size=1280,920 --incognito')]
        driver.press_key(WindowsPressKeyParams(key="^l"))
        typed = [event for event in desktop.events if event[0] == "type_keys"]
        assert len(typed) == 1
        assert typed[0][2] == 2200
    finally:
        driver.close()

    killed = [event for event in desktop.events if event[0] == "kill_process"]
    assert killed == [("kill_process", 2200)]
    assert desktop.processes[stable_pid]["alive"] is True


def test_pywinauto_driver_main_window_timeout_includes_resolution_context(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    class FailingApp:
        def connect(self, **kwargs: object) -> object:
            raise LookupError("window not found")

    driver = _pywinauto.PywinautoWindowsDriver(window_title_re=".*Edge Beta")
    monkeypatch.setattr(driver, "_application_cls", lambda: lambda backend: FailingApp())
    monotonic_values = iter([100.0, 131.0])
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: next(monotonic_values, 131.0)))

    with pytest.raises(
        TimeoutError,
        match=r"Timed out after 30\.0 seconds resolving Windows main window.*title_re='\.\*Edge Beta'.*wait_for='exists visible'",
    ) as error:
        driver._resolve_main_window(wait=True)  # type: ignore[attr-defined]

    assert isinstance(error.value.__cause__, LookupError)
    assert "window not found" in str(error.value)


def test_pywinauto_driver_main_window_immediate_failure_includes_resolution_context(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FailingApp:
        def connect(self, **kwargs: object) -> object:
            raise LookupError("window not found")

    driver = PywinautoWindowsDriver(window_title_re=".*Edge Beta")
    monkeypatch.setattr(driver, "_application_cls", lambda: lambda backend: FailingApp())

    with pytest.raises(
        RuntimeError,
        match=r"Failed to resolve Windows main window.*title_re='\.\*Edge Beta'.*wait_for='exists visible'",
    ) as error:
        driver._resolve_main_window()  # type: ignore[attr-defined]

    assert isinstance(error.value.__cause__, LookupError)


def test_pywinauto_driver_main_window_failure_preserves_lookup_error_when_findwindows_unavailable(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver
    from fsq_agent.models import ConfigurationError

    class FailingApp:
        def connect(self, **kwargs: object) -> object:
            raise LookupError("window not found")

    def _missing_pywinauto(**kwargs: object) -> list[object]:
        raise ConfigurationError("pywinauto is required for PywinautoWindowsDriver.")

    driver = PywinautoWindowsDriver(window_title_re=".*Edge Beta")
    monkeypatch.setattr(driver, "_application_cls", lambda: lambda backend: FailingApp())
    monkeypatch.setattr(driver, "_list_top_windows", _missing_pywinauto)

    with pytest.raises(
        RuntimeError,
        match=r"Failed to resolve Windows main window.*title_re='\.\*Edge Beta'.*wait_for='exists visible'",
    ) as error:
        driver._resolve_main_window()  # type: ignore[attr-defined]

    assert isinstance(error.value.__cause__, LookupError)


def test_pywinauto_driver_issue_27_never_attaches_to_or_kills_edge_stable_without_title_re(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    stable_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    canary_exe = r"C:\Users\Example\AppData\Local\Microsoft\Edge SxS\Application\msedge.exe"
    stable_pid = desktop.add_process(stable_exe, pid=101)
    stable_win = desktop.add_window(stable_pid, "FSQ Control Plane - Microsoft Edge", handle=501, active=True)

    def on_start(d, _cmd: str) -> int:
        canary_pid = d.add_process(canary_exe, pid=202)
        # Simulate Edge Stable retaining OS active focus while Canary opens its window!
        d.add_window(canary_pid, "New tab - Microsoft Edge Canary", handle=602, active=False)
        d.active_handle = stable_win.handle
        return canary_pid

    desktop.on_start = on_start
    driver = PywinautoWindowsDriver(app_path=canary_exe)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        # 1. Before launch_app, context() and _require_window() must NOT attach to Edge Stable.
        pre_ctx = driver.context()
        assert pre_ctx["screen_size"] is None
        with pytest.raises(RuntimeError, match="Failed to resolve Windows main window"):
            driver._require_window()  # type: ignore[attr-defined]

        # 2. Launch Canary while Edge Stable still owns foreground focus.
        launched = driver.launch_app(WindowsLaunchAppParams())
        assert launched["status"] == "passed"

        # 3. Focus stays on Edge Stable; press_key must still target Canary (handle 602, pid 202).
        desktop.active_handle = stable_win.handle
        pressed = driver.press_key(WindowsPressKeyParams(key="^l"))
        assert pressed["status"] == "passed"
        assert [e for e in desktop.events if e[0] == "type_keys"] == [("type_keys", 602, 202, "^l")]
        assert not any(e[0] == "connect" and e[1].get("active_only") for e in desktop.events)
    finally:
        driver.close()

    # 4. Cleanup kills owned Canary process (202), never Edge Stable (101).
    assert desktop.processes[stable_pid]["alive"] is True
    assert desktop.processes[202]["alive"] is False
    assert [e for e in desktop.events if e[0] == "kill_process"] == [("kill_process", 202)]


def test_pywinauto_driver_handles_multi_pid_resident_handoff_and_closes_only_new_window(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    canary_exe = r"C:\Users\Example\AppData\Local\Microsoft\Edge SxS\Application\msedge.exe"
    # Resident Canary browser PID with an existing user window + 3 windowless child PIDs (GPU/renderer/utility)
    browser_pid = desktop.add_process(canary_exe, pid=300)
    existing_user_win = desktop.add_window(browser_pid, "Work Mail - Microsoft Edge Canary", handle=700, active=True)
    for child_pid in (301, 302, 303):
        desktop.add_process(canary_exe, pid=child_pid)
    # Elevated unrelated process throwing PermissionError on module inspection
    elevated_pid = desktop.add_process(r"C:\Windows\System32\Taskmgr.exe", pid=999, accessible=False)
    desktop.add_window(elevated_pid, "Task Manager", handle=990, active=False)

    def on_start(d, _cmd: str) -> int:
        # Launcher stub process exits immediately after handing off a new window to resident browser_pid
        stub_pid = d.add_process(canary_exe.lower().replace("\\", "/"), pid=350, alive=False)
        d.add_window(browser_pid, "New tab - Microsoft Edge Canary", handle=701, active=True)
        return stub_pid

    desktop.on_start = on_start
    driver = PywinautoWindowsDriver(app_path=canary_exe)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        result = driver.launch_app(WindowsLaunchAppParams())
        assert result["status"] == "passed"
        driver.press_key(WindowsPressKeyParams(key="{F5}"))
        assert [e for e in desktop.events if e[0] == "type_keys"] == [("type_keys", 701, 300, "{F5}")]
    finally:
        driver.close()

    # Resident browser process and pre-existing user window (700) MUST remain open; only new window (701) closes!
    assert desktop.processes[browser_pid]["alive"] is True
    assert existing_user_win.closed is False
    assert 700 in desktop.windows
    assert 701 not in desktop.windows
    assert [e for e in desktop.events if e[0] == "close_window"] == [("close_window", 701, 300)]
    assert [e for e in desktop.events if e[0] == "kill_process"] == []


@pytest.mark.parametrize(
    ("backend", "reported_handle", "reported_pid", "native_pid", "binds", "queries_native_pid"),
    [
        pytest.param("uia", 701, 0, 300, True, True, id="zero-uia-pid"),
        pytest.param("uia", 701, -1, 300, True, True, id="negative-uia-pid"),
        pytest.param("uia", 701, None, 300, True, True, id="missing-uia-pid"),
        pytest.param("uia", 701, "300", 300, True, True, id="string-uia-pid"),
        pytest.param("uia", 701, True, 300, True, True, id="boolean-uia-pid"),
        pytest.param("uia", None, 0, 300, False, False, id="missing-hwnd"),
        pytest.param("uia", "701", 0, 300, False, False, id="string-hwnd"),
        pytest.param("uia", 0, 0, 300, False, False, id="zero-hwnd"),
        pytest.param("uia", -1, 0, 300, False, False, id="negative-hwnd"),
        pytest.param("uia", True, 0, 300, False, False, id="boolean-hwnd"),
        pytest.param("uia", 701, 0, None, False, True, id="missing-native-pid"),
        pytest.param("uia", 701, 0, 0, False, True, id="zero-native-pid"),
        pytest.param("uia", 701, 0, -1, False, True, id="negative-native-pid"),
        pytest.param("uia", 701, 0, "300", False, True, id="string-native-pid"),
        pytest.param("uia", 701, 0, 400, False, True, id="different-executable"),
        pytest.param("uia", 701, 0, 500, False, True, id="unverified-executable"),
        pytest.param("uia", 701, 300, 400, True, False, id="valid-uia-pid"),
        pytest.param("win32", 701, 0, 300, False, False, id="no-fallback-for-win32-backend"),
    ],
)
def test_pywinauto_driver_launch_candidate_native_pid_fallback(monkeypatch, backend, reported_handle, reported_pid, native_pid, binds, queries_native_pid) -> None:
    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=300)
    desktop.add_process(r"C:\Apps\Other\other.exe", pid=400)
    desktop.add_process(edge_exe, pid=500, accessible=False)
    existing = desktop.add_window(resident_pid, "Work Mail - Microsoft Edge", handle=700, active=True)
    hwnd = 701

    def on_start(d, _cmd: str) -> int:
        d.add_window(resident_pid, "Blank tab", handle=702)
        d.add_window(resident_pid, "New tab - Microsoft Edge", handle=hwnd, active=True)
        launch_started["done"] = True
        return d.add_process(edge_exe, pid=350, alive=False)

    def list_top_windows(**query: object) -> list:
        candidates = desktop.find_top_windows(**query)
        if query.get("handle") is not None:
            return candidates
        pid_attr = {} if reported_pid is None else {"process_id": reported_pid}
        return [SimpleNamespace(handle=reported_handle if w.handle == hwnd else w.handle, visible=w.visible, **pid_attr) for w in candidates]

    def lookup_native_pid(handle: int) -> object:
        if launch_started["done"]:
            post_launch_native_queries.append(handle)
        else:
            pre_launch_native_queries.append(handle)
        return {existing.handle: resident_pid, hwnd: native_pid}.get(handle)

    launch_started = {"done": False}
    pre_launch_native_queries: list[int] = []
    post_launch_native_queries: list[int] = []
    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe, backend_kind=backend, window_title_re="New tab - Microsoft Edge")
    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", list_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)
    monkeypatch.setattr(driver, "_window_process_id", lookup_native_pid)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle in desktop.windows)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: desktop.active_handle)

    try:
        if binds:
            assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
            assert [event for event in desktop.events if event[0] == "connect"] == [("connect", {"handle": hwnd})]
        else:
            with pytest.raises(TimeoutError, match="No visible top-level window matched"):
                driver.launch_app(WindowsLaunchAppParams())
            assert not any(event[0] == "connect" for event in desktop.events)
        expected_pre = [existing.handle] if backend == "uia" and (not isinstance(reported_pid, int) or isinstance(reported_pid, bool) or reported_pid <= 0) else []
        assert pre_launch_native_queries == expected_pre
        assert post_launch_native_queries == ([hwnd] if queries_native_pid else [])
    finally:
        driver.close()

    assert desktop.processes[resident_pid]["alive"] is True
    assert existing.closed is False
    assert (hwnd in desktop.windows) is not binds


def test_pywinauto_driver_native_pid_fallback_does_not_authorize_keyboard_input_without_foreground(monkeypatch) -> None:
    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=300)
    unrelated_pid = desktop.add_process(r"C:\Apps\Other\other.exe", pid=400)
    unrelated = desktop.add_window(unrelated_pid, "Other App", handle=800, active=True)
    hwnd = 701

    def on_start(d, _cmd: str) -> int:
        d.add_window(resident_pid, "New tab - Microsoft Edge", handle=hwnd, active=True)
        return d.add_process(edge_exe, pid=350, alive=False)

    def list_top_windows(**query: object) -> list:
        candidates = desktop.find_top_windows(**query)
        if query.get("handle") is not None:
            return candidates
        return [SimpleNamespace(handle=hwnd, visible=w.visible, process_id=0) if w.handle == hwnd else w for w in candidates]

    def lookup_native_pid(handle: int) -> int:
        desktop.events.append(("native_pid", handle))
        return desktop.windows[handle].process_id

    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe, window_title_re="New tab - Microsoft Edge")
    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", list_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)
    monkeypatch.setattr(driver, "_window_process_id", lookup_native_pid)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle in desktop.windows)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: desktop.active_handle)

    try:
        assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert [event for event in desktop.events if event[0] in {"native_pid", "connect"}] == [
            ("native_pid", hwnd),
            ("connect", {"handle": hwnd}),
        ]
        target = desktop.windows[hwnd]
        monkeypatch.setattr(target, "set_focus", lambda: desktop.events.append(("set_focus", hwnd)), raising=False)
        desktop.active_handle = unrelated.handle

        with pytest.raises(RuntimeError, match="Could not verify the Windows target or an owned popup as the foreground window"):
            driver.press_key(WindowsPressKeyParams(key="{F5}"))

        assert desktop.active_handle == unrelated.handle
        assert [event for event in desktop.events if event[0] in {"set_focus", "type_keys"}] == [("set_focus", hwnd)]
    finally:
        driver.close()


def test_pywinauto_driver_title_re_is_scoped_to_configured_executable_and_rebinds_after_splash(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    stable_exe = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    canary_exe = r"C:\Users\Example\AppData\Local\Microsoft\Edge SxS\Application\msedge.exe"
    # Canary already open with overlapping '.*Edge' title
    canary_pid = desktop.add_process(canary_exe, pid=401)
    desktop.add_window(canary_pid, "Preview - Microsoft Edge Canary", handle=801, active=True)

    def on_start(d, _cmd: str) -> int:
        pid = d.add_process(stable_exe, pid=402)
        d.add_window(pid, "Splash - Microsoft Edge", handle=802, active=False)
        return pid

    desktop.on_start = on_start
    # Broad '.*Edge' matches both Canary and Stable titles, as in applications.windows.yaml
    driver = PywinautoWindowsDriver(app_path=stable_exe, window_title_re=".*Edge")
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        driver.press_key(WindowsPressKeyParams(key="a"))
        # Splash window closes and main window opens under the same owned process
        desktop.windows[802].close()
        desktop.add_window(402, "Home - Microsoft Edge", handle=803, active=False)
        driver.press_key(WindowsPressKeyParams(key="b"))
        assert [e for e in desktop.events if e[0] == "type_keys"] == [
            ("type_keys", 802, 402, "a"),
            ("type_keys", 803, 402, "b"),
        ]
    finally:
        driver.close()

    assert desktop.processes[canary_pid]["alive"] is True
    assert desktop.processes[402]["alive"] is False


def test_pywinauto_driver_supports_application_frame_host_uwp_handoff_when_title_re_configured(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    calc_exe = r"C:\Windows\System32\calc.exe"
    frame_host_exe = r"C:\Windows\System32\ApplicationFrameHost.exe"
    host_pid = desktop.add_process(frame_host_exe, pid=500)
    settings_win = desktop.add_window(host_pid, "Settings", handle=901, active=True)

    def on_start(d, _cmd: str) -> int:
        stub_pid = d.add_process(calc_exe, pid=501, alive=False)
        d.add_window(host_pid, "Calculator", handle=902, active=True)
        return stub_pid

    desktop.on_start = on_start
    driver = PywinautoWindowsDriver(app_path=calc_exe, window_title_re=r".*(?:Calculator|计算器).*")
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        driver.press_key(WindowsPressKeyParams(key="1"))
        assert [e for e in desktop.events if e[0] == "type_keys"] == [("type_keys", 902, 500, "1")]
    finally:
        driver.close()

    # Shared ApplicationFrameHost.exe must NEVER be killed; only Calculator frame window (902) closes!
    assert desktop.processes[host_pid]["alive"] is True
    assert settings_win.closed is False
    assert 902 not in desktop.windows
    assert [e for e in desktop.events if e[0] == "kill_process"] == []


def test_pywinauto_driver_close_without_launch_performs_no_process_termination(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    pid = desktop.add_process(r"C:\Windows\System32\notepad.exe", pid=600)
    desktop.add_window(pid, "Untitled - Notepad", handle=950, active=True)
    driver = PywinautoWindowsDriver(app_path=r"C:\Windows\System32\notepad.exe")
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    assert driver.context()["screen_size"] is None
    driver.close()

    assert desktop.processes[pid]["alive"] is True
    assert desktop.events == []


def test_pywinauto_driver_win32_backend_omits_uia_control_type_and_preserves_windows_app_path(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    notepad_exe = r"C:\Windows\System32\notepad.exe"
    driver = PywinautoWindowsDriver(app_path=notepad_exe, backend_kind="win32")
    assert driver.app_path == notepad_exe

    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        result = driver.launch_app(WindowsLaunchAppParams())
        assert result["status"] == "passed"
        assert result["output"]["app_path"] == notepad_exe
        queries = [kwargs for kind, kwargs in desktop.events if kind == "find_top_windows"]
        assert queries
        assert all("control_type" not in kwargs for kwargs in queries)
    finally:
        driver.close()


def test_pywinauto_driver_windowless_resident_process_handoff_closes_new_window_without_killing_process(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    canary_exe = r"C:\Users\Tester\AppData\Local\Microsoft\Edge SxS\Application\msedge.exe"
    resident_pid = desktop.add_process(canary_exe, pid=700)

    def on_start(d, cmd: str) -> int:
        launcher_pid = d.add_process(canary_exe, pid=701, alive=False)
        d.add_window(resident_pid, "New tab - Microsoft Edge Canary", handle=1701, active=True)
        return launcher_pid

    desktop.on_start = on_start
    driver = PywinautoWindowsDriver(app_path=canary_exe)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
    driver.close()

    assert desktop.processes[resident_pid]["alive"] is True
    assert 1701 not in desktop.windows
    assert [e for e in desktop.events if e[0] == "kill_process"] == []
    assert [e for e in desktop.events if e[0] == "close_window"] == [("close_window", 1701, resident_pid)]


def test_pywinauto_driver_minimized_pre_existing_window_is_treated_as_borrowed_on_restore(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=710)
    minimized_win = desktop.add_window(resident_pid, "Existing Tab - Microsoft Edge", handle=1710, visible=False)

    def on_start(d, cmd: str) -> int:
        launcher_pid = d.add_process(edge_exe, pid=711, alive=False)
        minimized_win.visible = True
        d.active_handle = minimized_win.handle
        return launcher_pid

    desktop.on_start = on_start
    driver = PywinautoWindowsDriver(app_path=edge_exe)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
    driver.close()

    assert desktop.processes[resident_pid]["alive"] is True
    assert minimized_win.closed is False
    assert [e for e in desktop.events if e[0] in ("kill_process", "close_window")] == []


def test_pywinauto_driver_async_handoff_waits_for_new_window_without_hijacking_visible_fsq_ui_edge(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=300)
    fsq_ui_win = desktop.add_window(
        resident_pid,
        "FSQ Control Plane and 1 more page - Work - Microsoft Edge",
        handle=700,
        visible=True,
        active=True,
    )
    poll_calls = {"count": 0}
    launched_state: dict[str, int] = {}

    def on_start(d, _cmd: str) -> int:
        # Launcher stub starts and is still handing off over IPC during initial polls; new window is not created yet.
        stub_pid = d.add_process(edge_exe, pid=350, alive=True)
        launched_state["stub_pid"] = stub_pid
        return stub_pid

    def list_top_windows(**query: object) -> list:
        if query.get("handle") is None and driver._launched:
            poll_calls["count"] += 1
            if poll_calls["count"] == 3:
                desktop.processes[launched_state["stub_pid"]]["alive"] = False
                desktop.add_window(resident_pid, "New tab - Microsoft Edge", handle=701, visible=True, active=True)
        return desktop.find_top_windows(**query)

    now = {"t": 100.0}

    def fake_sleep(seconds: float) -> None:
        now["t"] += seconds

    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe, window_title_re=".*Edge")
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now["t"], sleep=fake_sleep))
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", list_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        result = driver.launch_app(WindowsLaunchAppParams())
        assert result["status"] == "passed"
        assert poll_calls["count"] == 3
        assert driver._bound_handle == 701
        driver.press_key(WindowsPressKeyParams(key="^l"))
        assert [e for e in desktop.events if e[0] == "type_keys"] == [("type_keys", 701, resident_pid, "^l")]
        assert fsq_ui_win.typed_keys == []
    finally:
        driver.close()

    assert fsq_ui_win.closed is False
    assert 700 in desktop.windows
    assert 701 not in desktop.windows


def test_pywinauto_driver_never_falls_back_to_already_visible_pre_launch_window_on_timeout(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=300)
    fsq_ui_win = desktop.add_window(
        resident_pid,
        "FSQ Control Plane - Microsoft Edge",
        handle=700,
        visible=True,
        active=True,
    )

    def on_start(d, _cmd: str) -> int:
        # Launcher exits without opening any new window while FSQ UI window was already visible pre-launch.
        return d.add_process(edge_exe, pid=350, alive=False)

    now = {"t": 100.0}

    def fake_sleep(seconds: float) -> None:
        now["t"] += seconds

    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe, window_title_re=".*Edge")
    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now["t"], sleep=fake_sleep))
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        with pytest.raises(TimeoutError, match=r"Timed out after 2\.0 seconds resolving Windows main window"):
            driver.launch_app(WindowsLaunchAppParams())
        assert driver._bound_handle is None
        assert not any(e[0] == "connect" and e[1].get("handle") == 700 for e in desktop.events)
    finally:
        driver.close()

    assert fsq_ui_win.closed is False
    assert desktop.processes[resident_pid]["alive"] is True


@pytest.mark.parametrize(
    ("config_args", "extra_args", "expected_handle"),
    [
        (['--user-data-dir="C:\\Temp\\fsq-iso"', "--new-window"], None, 801),
        ([], ["--USER-DATA-DIR=C:\\Temp\\fsq-iso"], None),
        (["--New-Window"], None, None),
        (["--new-window"], None, 703),
        ([], ["--user-data-dir", "C:\\Temp\\fsq-iso"], None),
    ],
)
def test_pywinauto_driver_issue_86_isolated_or_new_window_flags_forbid_pre_existing_window_and_resident_pid(
    monkeypatch,
    config_args: list[str],
    extra_args: list[str] | None,
    expected_handle: int | None,
) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=300)
    # Minimized pre-existing window that even becomes visible mid-launch must STILL be rejected under isolated/new-window flags.
    minimized_existing = desktop.add_window(resident_pid, "Daily Profile - Microsoft Edge", handle=700, visible=False)
    polls = {"count": 0}
    isolated_state: dict[str, int] = {}
    has_user_data_dir = any("user-data-dir" in arg.lower() for arg in [*config_args, *(extra_args or [])])

    def on_start(d, _cmd: str) -> int:
        minimized_existing.visible = True
        # Even if resident_pid opens an unrelated new tab/window (handle 702) when --user-data-dir is requested, it must be rejected.
        if has_user_data_dir:
            d.add_window(resident_pid, "Resident Window - Microsoft Edge", handle=702, visible=True)
        elif expected_handle == 703:
            # Without --user-data-dir, --new-window allows a newly created post-launch handle in the resident process after handoff exit.
            d.add_window(resident_pid, "New Window - Microsoft Edge", handle=703, visible=True, active=True)
        launcher_alive = expected_handle == 801
        iso_pid = d.add_process(edge_exe, pid=450, alive=launcher_alive)
        isolated_state["pid"] = iso_pid
        return iso_pid

    def list_top_windows(**query: object) -> list:
        if query.get("handle") is None and driver._launched:
            polls["count"] += 1
            if expected_handle == 801 and polls["count"] == 3:
                desktop.add_window(isolated_state["pid"], "New tab - Microsoft Edge", handle=801, visible=True, active=True)
        return desktop.find_top_windows(**query)

    now = {"t": 200.0}

    def fake_sleep(seconds: float) -> None:
        now["t"] += seconds

    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe, launch_args=config_args)
    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 1.5)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now["t"], sleep=fake_sleep))
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", list_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    try:
        if expected_handle is not None:
            res = driver.launch_app(WindowsLaunchAppParams(extra_args=extra_args))
            assert res["status"] == "passed"
            assert driver._bound_handle == expected_handle
        else:
            with pytest.raises(TimeoutError, match=r"Timed out after 1\.5 seconds resolving Windows main window"):
                driver.launch_app(WindowsLaunchAppParams(extra_args=extra_args))
            assert driver._bound_handle is None
            assert not any(e[0] == "connect" and e[1].get("handle") in {700, 702} for e in desktop.events)
    finally:
        driver.close()

    assert minimized_existing.closed is False
    assert desktop.processes[resident_pid]["alive"] is True


def test_pywinauto_driver_pre_launch_snapshot_uses_win32_pid_fallback_when_uia_pid_is_zero(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=300)
    existing = desktop.add_window(resident_pid, "Existing - Microsoft Edge", handle=700, visible=True, active=True)

    def on_start(d, _cmd: str) -> int:
        stub_pid = d.add_process(edge_exe, pid=350, alive=False)
        d.add_window(resident_pid, "New tab - Microsoft Edge", handle=701, visible=True, active=True)
        return stub_pid

    def list_top_windows(**query: object) -> list:
        raw = desktop.find_top_windows(**query)
        if query.get("handle") is not None:
            return raw
        return [SimpleNamespace(handle=w.handle, visible=w.visible, process_id=0) for w in raw]

    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", list_top_windows)
    # Simulate EnumProcesses unavailable so pre-launch matching_pids relies strictly on window enumeration + Win32 PID fallback.
    monkeypatch.setattr(driver, "_list_process_ids", list)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)
    monkeypatch.setattr(driver, "_window_process_id", lambda hwnd: desktop.windows[hwnd].process_id if hwnd in desktop.windows else None)

    assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
    assert driver._bound_handle == 701
    assert driver._owns_process is False
    driver.close()

    assert desktop.processes[resident_pid]["alive"] is True
    assert existing.closed is False
    assert 701 not in desktop.windows


def test_pywinauto_driver_standalone_ui_snapshot_succeeds_without_configured_secrets() -> None:
    from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver
    from fsq_agent.models import WindowsUiSnapshotParams

    class FakeRect:
        left = 0
        top = 0
        right = 400
        bottom = 300

    class FakeElementInfo:
        control_type = "Window"
        name = "Demo Window"
        automation_id = "Main"
        class_name = "DemoWnd"
        runtime_id = (1, 2)
        rectangle = FakeRect()
        visible = True
        enabled = True

    class FakeWindow:
        element_info = FakeElementInfo()

        def window_text(self) -> str:
            return "Demo Window"

        def children(self) -> list:
            return []

    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **_kwargs: FakeWindow()  # type: ignore[method-assign]
    try:
        snap = driver.ui_snapshot(WindowsUiSnapshotParams())
        assert snap["format"] == "xml"
        assert "Demo Window" in str(snap["xml"])
    finally:
        driver.close()


def test_pywinauto_driver_rebind_preserves_initial_binding_mode_and_restored_handle(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    # Case A: Post-launch splash closes after launcher exits -> must NOT fall back to a restored pre-launch handle.
    desktop = _make_fake_windows_desktop()
    app_exe = r"C:\Apps\Demo\demo.exe"
    resident_pid = desktop.add_process(app_exe, pid=300)
    restored_pre = desktop.add_window(resident_pid, "Restored Old", handle=700, visible=False)
    splash_state: dict[str, Any] = {}

    def on_start_splash(d, _cmd: str) -> int:
        restored_pre.visible = True
        splash_state["win"] = d.add_window(resident_pid, "Splash", handle=701, visible=True, active=True)
        return d.add_process(app_exe, pid=350, alive=False)

    now = {"t": 50.0}
    desktop.on_start = on_start_splash
    driver = _pywinauto.PywinautoWindowsDriver(app_path=app_exe)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now["t"], sleep=lambda s: now.__setitem__("t", now["t"] + s)))
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)
    try:
        assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert driver._bound_handle == 701
        splash_state["win"].closed = True
        desktop.windows.pop(701, None)
        now["t"] += 5.0
        with pytest.raises(RuntimeError, match="No visible top-level window matched"):
            driver._require_window()
    finally:
        driver.close()

    # Case B: Borrowed session rebinds only its originally borrowed handle, never another pre-existing handle.
    desktop2 = _make_fake_windows_desktop()
    res_pid2 = desktop2.add_process(app_exe, pid=400)
    borrowed_first = desktop2.add_window(res_pid2, "First Restored", handle=800, visible=False)
    other_pre = desktop2.add_window(res_pid2, "Other Restored", handle=801, visible=False)

    def on_start_borrow(d, _cmd: str) -> int:
        borrowed_first.visible = True
        other_pre.visible = True
        d.active_handle = borrowed_first.handle
        return d.add_process(app_exe, pid=450, alive=False)

    now2 = {"t": 100.0}
    desktop2.on_start = on_start_borrow
    driver2 = _pywinauto.PywinautoWindowsDriver(app_path=app_exe)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now2["t"], sleep=lambda s: now2.__setitem__("t", now2["t"] + s)))
    monkeypatch.setattr(driver2, "_application_cls", desktop2.make_app_cls)
    monkeypatch.setattr(driver2, "_list_top_windows", desktop2.find_top_windows)
    monkeypatch.setattr(driver2, "_list_process_ids", desktop2.list_pids)
    monkeypatch.setattr(driver2, "_process_executable_path", desktop2.process_executable)
    try:
        assert driver2.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert driver2._bound_handle == 800
        borrowed_first.closed = True
        desktop2.windows.pop(800, None)
        with pytest.raises(RuntimeError, match="No visible top-level window matched"):
            driver2._require_window()
        # Once a verified post-launch window opens, re-resolution switches to it cleanly and closes only that new handle.
        new_post = desktop2.add_window(res_pid2, "Replacement Post-Launch", handle=802, visible=True, active=True)
        rebound = driver2._require_window()
        assert rebound.handle == new_post.handle
        assert driver2._bound_handle == 802
    finally:
        driver2.close()
    assert other_pre.closed is False
    assert 802 not in desktop2.windows


def test_pywinauto_driver_pending_disabled_post_launch_window_blocks_borrowing_and_enforces_handoff_grace(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    resident_pid = desktop.add_process(edge_exe, pid=300)
    restored_old = desktop.add_window(resident_pid, "Restored Old - Microsoft Edge", handle=700, visible=False)
    new_win_holder: dict[str, object] = {"ready": False}

    def on_start(d, _cmd: str) -> int:
        restored_old.visible = True
        d.add_window(resident_pid, "Initializing New - Microsoft Edge", handle=701, visible=True, active=True)
        return d.add_process(edge_exe, pid=350, alive=False)

    now = {"t": 10.0}

    def fake_sleep(seconds: float) -> None:
        now["t"] += seconds
        if now["t"] >= 12.4:
            new_win_holder["ready"] = True

    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe)
    orig_try_connect = driver._try_connect_window_handle

    def gated_try_connect(app_cls, handle: int, *, wait_for: str):
        if handle == 701 and not new_win_holder["ready"]:
            return None
        return orig_try_connect(app_cls, handle, wait_for=wait_for)

    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now["t"], sleep=fake_sleep))
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)
    monkeypatch.setattr(driver, "_try_connect_window_handle", gated_try_connect)

    try:
        assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert driver._bound_handle == 701
    finally:
        driver.close()

    # Case B: Hidden newly created post-launch HWND (visible=False initially) also blocks borrowing restored pre-launch handle.
    desktop_hidden = _make_fake_windows_desktop()
    res_hidden = desktop_hidden.add_process(edge_exe, pid=310)
    restored_hidden_case = desktop_hidden.add_window(res_hidden, "Restored Old - Microsoft Edge", handle=710, visible=False)
    hidden_new_holder: dict[str, Any] = {}

    def on_start_hidden(d, _cmd: str) -> int:
        restored_hidden_case.visible = True
        hidden_new_holder["win"] = d.add_window(res_hidden, "Hidden Initializing - Microsoft Edge", handle=711, visible=False)
        return d.add_process(edge_exe, pid=360, alive=False)

    now_hidden = {"t": 20.0}

    def sleep_hidden(seconds: float) -> None:
        now_hidden["t"] += seconds
        if now_hidden["t"] >= 22.4:
            hidden_new_holder["win"].visible = True
            desktop_hidden.active_handle = 711

    desktop_hidden.on_start = on_start_hidden
    driver_hidden = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now_hidden["t"], sleep=sleep_hidden))
    monkeypatch.setattr(driver_hidden, "_application_cls", desktop_hidden.make_app_cls)
    monkeypatch.setattr(driver_hidden, "_list_top_windows", desktop_hidden.find_top_windows)
    monkeypatch.setattr(driver_hidden, "_list_process_ids", desktop_hidden.list_pids)
    monkeypatch.setattr(driver_hidden, "_process_executable_path", desktop_hidden.process_executable)
    monkeypatch.setattr(driver_hidden, "_is_win32_window", lambda handle: handle in desktop_hidden.windows)
    monkeypatch.setattr(driver_hidden, "_is_window_iconic", lambda _handle: False)
    monkeypatch.setattr(driver_hidden, "_is_win32_window_visible", lambda handle: bool(desktop_hidden.windows[handle].visible))
    monkeypatch.setattr(driver_hidden, "_foreground_window_handle", lambda: desktop_hidden.active_handle)
    try:
        assert driver_hidden.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert driver_hidden._bound_handle == 711
    finally:
        driver_hidden.close()

    # Case C: Failed process enumeration + pywinauto is_process_running() returning False on OpenProcess PermissionError must NOT authorize borrowing.
    desktop_alive = _make_fake_windows_desktop()
    res_alive = desktop_alive.add_process(edge_exe, pid=500)
    restored_alive = desktop_alive.add_window(res_alive, "Restored Old - Microsoft Edge", handle=900, visible=False)

    def on_start_alive(d, _cmd: str) -> int:
        restored_alive.visible = True
        return d.add_process(edge_exe, pid=550, alive=True, accessible=False)

    base_app_cls = desktop_alive.make_app_cls()

    class AccessDeniedApp(base_app_cls):  # type: ignore[valid-type, misc]
        def is_process_running(self) -> bool:
            # Mirrors pywinauto returning False when OpenProcess raises AccessDenied.
            return False

    now_alive = {"t": 50.0}
    desktop_alive.on_start = on_start_alive
    driver_alive = _pywinauto.PywinautoWindowsDriver(app_path=edge_exe)
    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now_alive["t"], sleep=lambda s: now_alive.__setitem__("t", now_alive["t"] + s)))
    monkeypatch.setattr(driver_alive, "_application_cls", lambda: AccessDeniedApp)
    monkeypatch.setattr(driver_alive, "_list_top_windows", desktop_alive.find_top_windows)
    monkeypatch.setattr(driver_alive, "_list_process_ids", list)
    monkeypatch.setattr(driver_alive, "_query_win32_process_exited", lambda _pid: None)
    monkeypatch.setattr(driver_alive, "_process_executable_path", desktop_alive.process_executable)
    try:
        with pytest.raises(TimeoutError):
            driver_alive.launch_app(WindowsLaunchAppParams())
        assert driver_alive._bound_handle is None
    finally:
        driver_alive.close()


def test_pywinauto_driver_isolated_profile_rejects_host_frame_backed_by_resident_pid(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    app_exe = r"C:\Apps\HostedBrowser\browser.exe"
    helper_exe = r"C:\Apps\HostedBrowser\helper.exe"
    host_exe = r"C:\Windows\System32\ApplicationFrameHost.exe"
    resident_pid = desktop.add_process(app_exe, pid=300)
    host_pid = desktop.add_process(host_exe, pid=600)
    post_launch_helper_Holder: dict[str, int] = {}

    def on_start(d, _cmd: str) -> int:
        # Frame 950 has resident_pid (whose path becomes unreadable post-launch) AND an unrelated new helper child PID:
        # neither matched_new_pid nor title_fallback may admit it!
        desktop.processes[resident_pid]["accessible"] = False
        post_launch_helper_Holder["pid"] = d.add_process(helper_exe, pid=610, alive=True)
        d.add_window(host_pid, "Hosted Browser", handle=950, visible=True, active=True)
        return d.add_process(app_exe, pid=450, alive=True)

    now = {"t": 10.0}
    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(
        app_path=app_exe,
        window_title_re=".*Hosted Browser.*",
        launch_args=["--user-data-dir=C:\\Temp\\iso"],
    )
    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now["t"], sleep=lambda s: now.__setitem__("t", now["t"] + s)))
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)
    monkeypatch.setattr(
        driver,
        "_host_frame_core_window_pids",
        lambda hwnd: [resident_pid, post_launch_helper_Holder["pid"]] if hwnd == 950 else [],
    )
    try:
        with pytest.raises(TimeoutError):
            driver.launch_app(WindowsLaunchAppParams())
        assert driver._bound_handle is None
    finally:
        driver.close()

    # Inverse transition: pre-launch resident PID path unreadable during snapshot (only in _pre_existing_all_pids),
    # then readable during launch — must STILL be rejected for both direct and host-frame windows under --user-data-dir.
    desktop_inv = _make_fake_windows_desktop()
    inv_res_pid = desktop_inv.add_process(app_exe, pid=305, accessible=False)
    inv_host_pid = desktop_inv.add_process(host_exe, pid=605)

    def on_start_inv(d, _cmd: str) -> int:
        desktop_inv.processes[inv_res_pid]["accessible"] = True
        d.add_window(inv_res_pid, "Hosted Browser Direct", handle=951, visible=True, active=True)
        d.add_window(inv_host_pid, "Hosted Browser Frame", handle=952, visible=True, active=True)
        return d.add_process(app_exe, pid=455, alive=True)

    now_inv = {"t": 20.0}
    desktop_inv.on_start = on_start_inv
    driver_inv = _pywinauto.PywinautoWindowsDriver(
        app_path=app_exe,
        window_title_re=".*Hosted Browser.*",
        launch_args=["--user-data-dir=C:\\Temp\\iso"],
    )
    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now_inv["t"], sleep=lambda s: now_inv.__setitem__("t", now_inv["t"] + s)))
    monkeypatch.setattr(driver_inv, "_application_cls", desktop_inv.make_app_cls)
    monkeypatch.setattr(driver_inv, "_list_top_windows", desktop_inv.find_top_windows)
    monkeypatch.setattr(driver_inv, "_list_process_ids", desktop_inv.list_pids)
    monkeypatch.setattr(driver_inv, "_process_executable_path", desktop_inv.process_executable)
    monkeypatch.setattr(driver_inv, "_host_frame_core_window_pids", lambda hwnd: [inv_res_pid] if hwnd == 952 else [])
    try:
        with pytest.raises(TimeoutError):
            driver_inv.launch_app(WindowsLaunchAppParams())
        assert driver_inv._bound_handle is None
    finally:
        driver_inv.close()


def test_pywinauto_driver_multi_candidate_nonblocking_probe_and_direct_priority(monkeypatch) -> None:
    from types import SimpleNamespace

    from fsq_agent.drivers.windows import _pywinauto

    app_exe = r"C:\Apps\Demo\demo.exe"

    # 1. A ready candidate behind 4 unready candidates is reached on the very first pass without blocking.
    desktop_ready = _make_fake_windows_desktop()
    now_ready = {"t": 200.0}
    probed_handles: list[int] = []

    def on_start_ready(d, _cmd: str) -> int:
        pid = d.add_process(app_exe, pid=360, alive=True)
        for idx in range(5):
            d.add_window(pid, f"Direct Candidate {idx}", handle=920 + idx, visible=True, active=(idx == 4))
        return pid

    desktop_ready.on_start = on_start_ready
    driver_ready = _pywinauto.PywinautoWindowsDriver(app_path=app_exe)
    orig_ready_connect = driver_ready._try_connect_window_handle

    def unready_prefix_connect(app_cls, handle: int, *, wait_for: str):
        probed_handles.append(handle)
        if handle < 924:
            return None
        return orig_ready_connect(app_cls, handle, wait_for=wait_for)

    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 4.0)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now_ready["t"], sleep=lambda s: now_ready.__setitem__("t", round(now_ready["t"] + s, 6))))
    monkeypatch.setattr(driver_ready, "_application_cls", desktop_ready.make_app_cls)
    monkeypatch.setattr(driver_ready, "_list_top_windows", desktop_ready.find_top_windows)
    monkeypatch.setattr(driver_ready, "_list_process_ids", desktop_ready.list_pids)
    monkeypatch.setattr(driver_ready, "_process_executable_path", desktop_ready.process_executable)
    monkeypatch.setattr(driver_ready, "_try_connect_window_handle", unready_prefix_connect)
    try:
        assert driver_ready.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert driver_ready._bound_handle == 924
        assert probed_handles == [920, 921, 922, 923, 924]
        assert now_ready["t"] == 200.0
    finally:
        driver_ready.close()

    # 2. Direct window appearing during host_frame probe still takes priority over host_frame fallback.
    desktop_prio = _make_fake_windows_desktop()
    host_exe = r"C:\Windows\System32\ApplicationFrameHost.exe"
    prio_host_pid = desktop_prio.add_process(host_exe, pid=630)
    now_prio = {"t": 300.0}
    prio_state: dict[str, Any] = {"pid": 0, "hidden_direct": None}

    def on_start_prio(d, _cmd: str) -> int:
        pid = d.add_process(app_exe, pid=370, alive=True)
        prio_state["pid"] = pid
        prio_state["hidden_direct"] = d.add_window(pid, "Direct Main", handle=940, visible=False, active=False)
        d.add_window(prio_host_pid, "Fallback Hosted Window", handle=949, visible=True, active=True)
        return pid

    desktop_prio.on_start = on_start_prio
    driver_prio = _pywinauto.PywinautoWindowsDriver(app_path=app_exe)
    orig_prio_connect = driver_prio._try_connect_window_handle

    def prio_connect(app_cls, handle: int, *, wait_for: str):
        if handle == 949:
            prio_state["hidden_direct"].visible = True
            desktop_prio.active_handle = 940
        return orig_prio_connect(app_cls, handle, wait_for=wait_for)

    monkeypatch.setattr(_pywinauto, "WINDOW_READY_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(_pywinauto, "time", SimpleNamespace(monotonic=lambda: now_prio["t"], sleep=lambda s: now_prio.__setitem__("t", round(now_prio["t"] + s, 6))))
    monkeypatch.setattr(driver_prio, "_application_cls", desktop_prio.make_app_cls)
    monkeypatch.setattr(driver_prio, "_list_top_windows", desktop_prio.find_top_windows)
    monkeypatch.setattr(driver_prio, "_list_process_ids", desktop_prio.list_pids)
    monkeypatch.setattr(driver_prio, "_process_executable_path", desktop_prio.process_executable)
    monkeypatch.setattr(driver_prio, "_host_frame_core_window_pids", lambda hwnd: [370] if hwnd == 949 else [])
    monkeypatch.setattr(driver_prio, "_try_connect_window_handle", prio_connect)
    try:
        assert driver_prio.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert driver_prio._bound_handle == 940
        assert driver_prio._owns_process is True
    finally:
        driver_prio.close()


def test_pywinauto_driver_win32_backend_binds_newly_launched_minimized_window_and_restores(monkeypatch) -> None:
    from fsq_agent.drivers.windows import _pywinauto

    desktop = _make_fake_windows_desktop()
    app_exe = r"C:\Apps\Demo\win32_min.exe"
    iconic_state = {"iconic": True}

    def on_start(d, _cmd: str) -> int:
        pid = d.add_process(app_exe, pid=380, alive=True)
        d.add_window(pid, "Minimized Win32 Launch", handle=980, visible=True, active=True)
        return pid

    def restore_win(hwnd: int, *, activate: bool = True) -> None:
        assert hwnd == 980
        iconic_state["iconic"] = False
        desktop.active_handle = 980

    desktop.on_start = on_start
    driver = _pywinauto.PywinautoWindowsDriver(app_path=app_exe, backend_kind="win32")
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)
    monkeypatch.setattr(driver, "_is_win32_window", lambda handle: handle == 980)
    monkeypatch.setattr(driver, "_is_win32_window_visible", lambda handle: handle == 980)
    monkeypatch.setattr(driver, "_is_window_iconic", lambda handle: handle == 980 and iconic_state["iconic"])
    monkeypatch.setattr(driver, "_restore_window", restore_win)
    monkeypatch.setattr(driver, "_foreground_window_handle", lambda: desktop.active_handle)
    try:
        assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
        assert driver._bound_handle == 980
        assert iconic_state["iconic"] is False
    finally:
        driver.close()


def test_pywinauto_driver_try_connect_window_handle_nonblocking_and_preserves_window_specification(monkeypatch) -> None:
    import time as real_time
    from concurrent.futures import ThreadPoolExecutor

    pywinauto_app = pytest.importorskip("pywinauto.application")
    pywinauto_fw = pytest.importorskip("pywinauto.findwindows")

    class _FakeElemInfo:
        def __init__(self, handle: int) -> None:
            self.handle = handle
            self.process_id = 4242

    class _FakeWrapper:
        def __init__(self, handle: int, *, visible: bool = True, enabled: bool = True) -> None:
            self.element_info = _FakeElemInfo(handle)
            self._visible = visible
            self._enabled = enabled

        def is_visible(self) -> bool:
            return self._visible

        def is_enabled(self) -> bool:
            return self._enabled

    def fake_find_element(**kwargs: Any):
        target_handle = kwargs.get("handle")
        if target_handle == 7777:
            raise pywinauto_fw.ElementNotFoundError({"handle": 7777})
        if target_handle == 8888:
            real_time.sleep(0.35)
            raise pywinauto_fw.ElementNotFoundError({"handle": 8888})
        return _FakeElemInfo(int(target_handle or 9999))

    class _NativeSpecApp:
        def __init__(self, backend: str = "uia") -> None:
            self.backend_name = backend
            self.process = 4242

        def connect(self, **_kwargs: Any) -> "_NativeSpecApp":
            return self

        def window(self, **kwargs: Any):
            spec = pywinauto_app.WindowSpecification({"handle": kwargs["handle"], "backend": self.backend_name})
            monkeypatch.setattr(spec.backend, "generic_wrapper_class", lambda elem: _FakeWrapper(elem.handle))
            return spec

    monkeypatch.setattr(pywinauto_fw, "find_element", fake_find_element)

    from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver

    driver_fast = PywinautoWindowsDriver(app_path=r"C:\Apps\Demo\demo.exe")
    driver_slow = PywinautoWindowsDriver(app_path=r"C:\Apps\Demo\demo.exe")
    try:
        # 1. Concurrent lock-free execution: slow probe on driver_slow (0.35s) must NOT block non-blocking probe on driver_fast.
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow_future = pool.submit(
                driver_slow._try_connect_window_handle,
                _NativeSpecApp,
                8888,
                wait_for="exists visible enabled",
            )
            real_time.sleep(0.03)
            started = real_time.monotonic()
            fast_result = driver_fast._try_connect_window_handle(
                _NativeSpecApp,
                7777,
                wait_for="exists visible enabled",
            )
            fast_elapsed = real_time.monotonic() - started
            assert fast_result is None
            assert fast_elapsed < 0.15
            assert slow_future.result() is None

        # 2. Successful bind returns a WindowSpecification (preserving .child_window(...) for locator lookup).
        ok = driver_fast._try_connect_window_handle(
            _NativeSpecApp,
            9999,
            wait_for="exists visible enabled",
        )
        assert ok is not None
        _conn, bound_spec = ok
        assert isinstance(bound_spec, pywinauto_app.WindowSpecification)
        child_spec = driver_fast._child_window(window=bound_spec, auto_id="SearchBox", control_type="Edit")
        assert isinstance(child_spec, pywinauto_app.WindowSpecification)

        # 3. Title-re WindowSpecification on a hidden/minimized window does NOT fall into pywinauto's 5s wrapper_object() wait.
        hidden_calls: list[dict[str, Any]] = []

        def hidden_title_find_element(**kwargs: Any):
            hidden_calls.append(dict(kwargs))
            if kwargs.get("visible_only", True):
                raise pywinauto_fw.ElementNotFoundError(kwargs)
            return _FakeElemInfo(5555)

        monkeypatch.setattr(pywinauto_fw, "find_element", hidden_title_find_element)
        title_spec = pywinauto_app.WindowSpecification({"title_re": ".*Hidden.*", "backend": "uia"})
        monkeypatch.setattr(title_spec.backend, "generic_wrapper_class", lambda elem: _FakeWrapper(elem.handle, visible=False, enabled=True))
        started_hidden = real_time.monotonic()
        with pytest.raises(pywinauto_fw.ElementNotFoundError):
            driver_fast._check_window_ready(title_spec, "exists visible")
        assert real_time.monotonic() - started_hidden < 0.2
        assert len(hidden_calls) == 1

        # Non-visible wait_for ("exists enabled") sets visible_only=False and succeeds without blocking.
        driver_fast._check_window_ready(title_spec, "exists enabled")
        assert hidden_calls[-1]["visible_only"] is False

        # 4. _extract_window_handle on a WindowSpecification must never trigger pywinauto's 5s __getattribute__ lookup.
        handle_spec = pywinauto_app.WindowSpecification({"handle": 99999999, "backend": "win32"})
        started_extract = real_time.monotonic()
        assert driver_fast._extract_window_handle(handle_spec) == 99999999
        assert real_time.monotonic() - started_extract < 0.2

        started_missing = real_time.monotonic()
        assert driver_fast._extract_window_handle(title_spec) is None
        assert real_time.monotonic() - started_missing < 0.2
    finally:
        driver_fast.close()
        driver_slow.close()


def test_pywinauto_driver_cleanup_surfaces_disposal_failure_and_keeps_executor_clean(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    desktop = _make_fake_windows_desktop()
    notepad_exe = r"C:\Windows\System32\notepad.exe"
    driver = PywinautoWindowsDriver(app_path=notepad_exe)
    monkeypatch.setattr(driver, "_application_cls", desktop.make_app_cls)
    monkeypatch.setattr(driver, "_list_top_windows", desktop.find_top_windows)
    monkeypatch.setattr(driver, "_list_process_ids", desktop.list_pids)
    monkeypatch.setattr(driver, "_process_executable_path", desktop.process_executable)

    assert driver.launch_app(WindowsLaunchAppParams())["status"] == "passed"
    desktop.kill_error = RuntimeError("TerminateProcess access denied")

    with pytest.raises(RuntimeError, match="TerminateProcess access denied"):
        driver.close()

    assert driver._executor is None


def test_normalize_windows_executable_path_expands_percent_vars_and_normalizes_separators(monkeypatch) -> None:
    from fsq_agent.drivers.windows._pywinauto import _normalize_windows_executable_path

    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Tester\AppData\Local")
    normalized = _normalize_windows_executable_path('"%LOCALAPPDATA%/Microsoft/Edge SxS/Application\\MSEDGE.EXE"')
    assert normalized == r"c:\users\tester\appdata\local\microsoft\edge sxs\application\msedge.exe"


def test_pywinauto_driver_resolves_window_on_every_use() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    windows = iter([object(), object()])
    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: next(windows)  # type: ignore[method-assign]

    first = driver._require_window()  # type: ignore[attr-defined]
    second = driver._require_window()  # type: ignore[attr-defined]

    assert first is not second
    assert not hasattr(driver, "_window")


def test_pywinauto_driver_context_tolerates_window_closed_after_resolution() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class ClosedWindow:
        def rectangle(self) -> object:
            raise RuntimeError("window is closed")

    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: ClosedWindow()  # type: ignore[method-assign]

    context = driver.context()

    assert context["screen_size"] is None


def test_pywinauto_driver_control_falls_back_from_exact_title_to_title_regex() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FakeWrapper:
        pass

    class FakeControl:
        def __init__(self, exists: bool) -> None:
            self._exists = exists
            self.wrapper = FakeWrapper()

        def exists(self) -> bool:
            return self._exists

        def wrapper_object(self) -> FakeWrapper:
            return self.wrapper

    class FakeWindow:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def child_window(self, **kwargs: object) -> FakeControl:
            self.calls.append(kwargs)
            return FakeControl(exists="title_re" in kwargs)

    window = FakeWindow()
    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: window  # type: ignore[method-assign]

    control = driver._control_from_kwargs(  # type: ignore[attr-defined]
        {"title": "Document.*Notepad", "control_type": "Edit", "automation_id": "15", "index": 2}
    )

    assert isinstance(control, FakeWrapper)
    assert window.calls == [
        {"title": "Document.*Notepad", "control_type": "Edit", "auto_id": "15", "found_index": 1},
        {"control_type": "Edit", "auto_id": "15", "found_index": 1, "title_re": "Document.*Notepad"},
    ]


def test_pywinauto_driver_control_returns_wrapper_for_exact_match() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    wrapper = object()

    class ExactControl:
        def exists(self) -> bool:
            return True

        def wrapper_object(self) -> object:
            return wrapper

    class FakeWindow:
        def child_window(self, **kwargs: object) -> ExactControl:
            return ExactControl()

    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: FakeWindow()  # type: ignore[method-assign]

    control = driver._control_from_kwargs({"automation_id": "15"})  # type: ignore[attr-defined]

    assert control is wrapper


def test_pywinauto_driver_control_raises_without_title_match() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class MissingControl:
        def exists(self) -> bool:
            return False

    class FakeWindow:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def child_window(self, **kwargs: object) -> MissingControl:
            self.calls.append(kwargs)
            return MissingControl()

    window = FakeWindow()
    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: window  # type: ignore[method-assign]

    with pytest.raises(
        LookupError,
        match=r'Windows control was not found\. query_dict=\{"auto_id": "15", "found_index": 0\}',
    ):
        driver._control_from_kwargs({"automation_id": "15"})  # type: ignore[attr-defined]

    assert window.calls == [{"auto_id": "15", "found_index": 0}]


def test_pywinauto_driver_hover_and_scroll_use_control_center(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class Rectangle:
        def mid_point(self) -> tuple[int, int]:
            return (120, 80)

    class Wrapper:
        def rectangle(self) -> Rectangle:
            return Rectangle()

    class FakeMouse:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def move(self, *, coords: tuple[int, int]) -> None:
            self.calls.append(("move", coords))

        def scroll(self, *, coords: tuple[int, int], wheel_dist: int) -> None:
            self.calls.append(("scroll", {"coords": coords, "wheel_dist": wheel_dist}))

    mouse = FakeMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_control", lambda params: Wrapper())
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    hover_result = driver.hover_on(WindowsHoverOnParams(target="Hover Save", locator={"title": "Save"}))
    scroll_result = driver.scroll_on(WindowsScrollOnParams(target="Scroll results", locator={"title": "Results"}, wheel_dist=-5))

    assert hover_result["status"] == "passed"
    assert scroll_result["status"] == "passed"
    assert mouse.calls == [
        ("move", (120, 80)),
        ("scroll", {"coords": (120, 80), "wheel_dist": -5}),
    ]


def test_pywinauto_driver_mouse_target_does_not_participate_in_lookup(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class Rectangle:
        def mid_point(self) -> tuple[int, int]:
            return (10, 20)

    class Wrapper:
        def rectangle(self) -> Rectangle:
            return Rectangle()

    class FakeMouse:
        def move(self, *, coords: tuple[int, int]) -> None:
            pass

        def scroll(self, *, coords: tuple[int, int], wheel_dist: int) -> None:
            pass

    queries: list[dict[str, object]] = []
    driver = PywinautoWindowsDriver()

    def resolve(locator: dict[str, object]) -> Wrapper:
        queries.append(locator)
        return Wrapper()

    monkeypatch.setattr(driver, "_control_from_kwargs", resolve)
    monkeypatch.setattr(driver, "_mouse_module", FakeMouse)

    driver.hover_on(WindowsHoverOnParams(target="Do not search this text", locator={"automation_id": "Save"}))
    driver.scroll_on(WindowsScrollOnParams(target="Also not a query", locator={"control_type": "List"}, wheel_dist=-1))

    assert queries == [{"automation_id": "Save"}, {"control_type": "List"}]


def test_pywinauto_driver_drag_to_offset_moves_and_releases(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FakeMouse:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def press(self, *, coords: tuple[int, int], button: str) -> None:
            self.calls.append(("press", {"coords": coords, "button": button}))

        def move(self, *, coords: tuple[int, int]) -> None:
            self.calls.append(("move", coords))

        def release(self, *, coords: tuple[int, int], button: str) -> None:
            self.calls.append(("release", {"coords": coords, "button": button}))

    mouse = FakeMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    result = driver.drag_to(
        WindowsDragToParams(
            target="Move item",
            source={"point": {"x": 10, "y": 20}},
            destination={"offset": {"x": 30, "y": 0}},
            mouse_button="right",
        )
    )

    assert result["status"] == "passed"
    assert mouse.calls[0] == ("press", {"coords": (10, 20), "button": "right"})
    assert mouse.calls[-1] == ("release", {"coords": (40, 20), "button": "right"})
    assert ("move", (40, 20)) in mouse.calls


@pytest.mark.parametrize(
    ("source", "destination", "expected_start", "expected_end"),
    [
        ({"locator": {"automation_id": "Source"}}, {"locator": {"automation_id": "Target"}}, (10, 20), (70, 80)),
        ({"locator": {"automation_id": "Source"}}, {"point": {"x": 50, "y": 60}}, (10, 20), (50, 60)),
        ({"point": {"x": 30, "y": 40}}, {"locator": {"automation_id": "Target"}}, (30, 40), (70, 80)),
        ({"point": {"x": 30, "y": 40}}, {"point": {"x": 50, "y": 60}}, (30, 40), (50, 60)),
    ],
)
def test_pywinauto_driver_drag_supports_locator_and_point_endpoints(
    source: dict[str, object],
    destination: dict[str, object],
    expected_start: tuple[int, int],
    expected_end: tuple[int, int],
    monkeypatch,
) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class Rectangle:
        def __init__(self, point: tuple[int, int]) -> None:
            self.point = point

        def mid_point(self) -> tuple[int, int]:
            return self.point

    class Wrapper:
        def __init__(self, point: tuple[int, int]) -> None:
            self.point = point

        def rectangle(self) -> Rectangle:
            return Rectangle(self.point)

    class FakeMouse:
        def __init__(self) -> None:
            self.press_point: tuple[int, int] | None = None
            self.release_point: tuple[int, int] | None = None

        def press(self, *, coords: tuple[int, int], button: str) -> None:
            self.press_point = coords

        def move(self, *, coords: tuple[int, int]) -> None:
            pass

        def release(self, *, coords: tuple[int, int], button: str) -> None:
            self.release_point = coords

    wrappers = {"Source": Wrapper((10, 20)), "Target": Wrapper((70, 80))}
    mouse = FakeMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_control_from_kwargs", lambda locator: wrappers[locator["automation_id"]])
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    result = driver.drag_to(WindowsDragToParams(target="Move item", source=source, destination=destination))

    assert result["status"] == "passed"
    assert mouse.press_point == expected_start
    assert mouse.release_point == expected_end


def test_pywinauto_driver_drag_releases_mouse_after_move_failure(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FailingMouse:
        def __init__(self) -> None:
            self.releases: list[tuple[tuple[int, int], str]] = []

        def press(self, *, coords: tuple[int, int], button: str) -> None:
            pass

        def move(self, *, coords: tuple[int, int]) -> None:
            raise RuntimeError("move failed")

        def release(self, *, coords: tuple[int, int], button: str) -> None:
            self.releases.append((coords, button))
            raise RuntimeError("release failed")

    mouse = FailingMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    with pytest.raises(RuntimeError, match="move failed"):
        driver.drag_to(
            WindowsDragToParams(
                target="Move item",
                source={"point": {"x": 10, "y": 20}},
                destination={"point": {"x": 40, "y": 20}},
            )
        )

    assert mouse.releases == [((40, 20), "left")]


def test_pywinauto_driver_ui_snapshot_compacts_deep_tree_withholds_passwords_and_sanitizes_xml(monkeypatch) -> None:
    from types import SimpleNamespace
    from xml.etree import ElementTree

    from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver
    from fsq_agent.models import RuntimeSecretSettings

    class FakeRect:
        def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
            self.left = left
            self.top = top
            self.right = right
            self.bottom = bottom

    class FakeElement:
        def __init__(
            self,
            *,
            control_type: str | None = None,
            title: str = "",
            automation_id: str | None = None,
            class_name: str | None = None,
            value: str | None = None,
            rect: tuple[int, int, int, int] = (0, 0, 100, 40),
            visible: bool = True,
            enabled: bool = True,
            selected: bool = False,
            toggle_state: int | None = None,
            focused: bool = False,
            is_password: bool = False,
            runtime_id: tuple[int, ...] | None = None,
            children: list["FakeElement"] | None = None,
        ) -> None:
            self.element_info = SimpleNamespace(
                control_type=control_type,
                automation_id=automation_id,
                class_name=class_name,
                runtime_id=runtime_id,
                is_password=is_password,
            )
            self._title = title
            self._value = value
            self._rect = FakeRect(*rect)
            self._visible = visible
            self._enabled = enabled
            self._selected = selected
            self._toggle_state = toggle_state
            self._focused = focused
            self._children = children or []

        def window_text(self) -> str:
            return self._title

        def get_value(self) -> str | None:
            return self._value

        def rectangle(self) -> FakeRect:
            return self._rect

        def is_visible(self) -> bool:
            return self._visible

        def is_enabled(self) -> bool:
            return self._enabled

        def is_selected(self) -> bool:
            return self._selected

        def get_toggle_state(self) -> int | None:
            return self._toggle_state

        def has_keyboard_focus(self) -> bool:
            return self._focused

        def children(self) -> list["FakeElement"]:
            return list(self._children)

    long_title = "Save \x00Document " + ("X" * 180)
    deep_button = FakeElement(
        control_type="Button",
        title=long_title,
        automation_id="btnSave",
        class_name="WinButton",
        rect=(10, 20, 110, 50),
        enabled=False,
        focused=True,
        runtime_id=(1, 2),
    )
    # Nest inside 30 unnamed Pane/Group wrappers (exceeding old depth=20 ceiling)
    nested: FakeElement = deep_button
    for idx in range(30):
        nested = FakeElement(
            control_type="Pane" if idx % 2 == 0 else "Group",
            rect=(0, 0, 800, 600),
            runtime_id=(100 + idx,),
            children=[nested],
        )
    password_edit = FakeElement(
        control_type="Edit",
        title="PASSWORD_PLAINTEXT_CANARY",
        automation_id="pwdInput",
        class_name="Edit",
        value="PASSWORD_PLAINTEXT_CANARY",
        is_password=True,
        rect=(10, 60, 210, 90),
        runtime_id=(2,),
    )
    password_box = FakeElement(
        control_type="PasswordBox",
        title="BOX_PASSWORD_CANARY",
        automation_id="pwdBox",
        value="BOX_PASSWORD_CANARY",
        rect=(10, 100, 210, 130),
        runtime_id=(3,),
    )
    checked_box = FakeElement(
        control_type="CheckBox",
        title="Remember me",
        automation_id="chkRemember",
        toggle_state=1,
        rect=(10, 140, 150, 165),
        runtime_id=(4,),
    )
    dead_zero_leaf = FakeElement(
        control_type="Pane",
        rect=(0, 0, 0, 0),
        runtime_id=(5,),
    )
    hidden_leaf = FakeElement(
        control_type="Button",
        title="HiddenButton",
        visible=False,
        rect=(10, 170, 90, 200),
        runtime_id=(6,),
    )
    private_value = "SecretTest&<Text>"
    weird_tag_leaf = FakeElement(
        control_type="123 Invalid Tag!",
        title="https://example.com/?page=1&sig=signed-value&keep=yes",
        automation_id="custom1",
        value=f"Localized message {private_value}",
        rect=(10, 210, 120, 240),
        runtime_id=(7,),
    )
    window = FakeElement(
        control_type="Window",
        title="Main\x07App",
        class_name="MainWindowClass",
        rect=(0, 0, 1024, 768),
        runtime_id=(1,),
        children=[nested, password_edit, password_box, checked_box, dead_zero_leaf, hidden_leaf, weird_tag_leaf],
    )

    driver = PywinautoWindowsDriver()
    try:
        monkeypatch.setattr(driver, "_require_window", lambda: window)
        secrets = RuntimeSecretSettings()
        secrets.set_values({"DESKTOP_SNAPSHOT_SECRET": private_value})
        WindowsHarness(driver=driver, runtime_secret_settings=secrets)
        snap = driver.ui_snapshot(WindowsUiSnapshotParams())
    finally:
        driver.close()

    assert snap["format"] == "xml"
    assert "root" not in snap
    assert "snapshot_type" not in snap
    assert "PASSWORD_PLAINTEXT_CANARY" not in snap["xml"]
    assert "BOX_PASSWORD_CANARY" not in snap["xml"]
    assert snap["node_count"] == 38
    assert snap["compaction"]["offscreen_nodes_omitted"] == 2
    assert snap["compaction"]["layout_nodes_removed"] == 30
    assert snap["compaction"]["clipped_attributes"] == 1
    assert snap["compaction"]["text_attribute_limit"] == 160
    assert snap["coverage"]["status"] == "partial"
    assert snap["coverage"]["reason"] == "semantic_compaction"

    parsed = ElementTree.fromstring(snap["xml"])  # noqa: S314
    assert parsed.tag == "Window"
    assert parsed.attrib["title"] == "MainApp"
    assert parsed.attrib["bounds"] == "[0,0][1024,768]"
    assert "enabled" not in parsed.attrib
    assert "visible" not in parsed.attrib

    by_auto_id = {child.attrib.get("automation_id"): child for child in parsed}
    btn = by_auto_id["btnSave"]
    assert btn.tag == "Button"
    assert btn.attrib["title"] == ("Save Document " + ("X" * 180))[:160]
    assert btn.attrib["enabled"] == "false"
    assert btn.attrib["focused"] == "true"
    assert btn.attrib["bounds"] == "[10,20][110,50]"

    pwd1 = by_auto_id["pwdInput"]
    assert "value" not in pwd1.attrib
    assert "title" not in pwd1.attrib
    assert "is_password" not in pwd1.attrib

    pwd2 = by_auto_id["pwdBox"]
    assert pwd2.tag == "PasswordBox"
    assert "value" not in pwd2.attrib
    assert "title" not in pwd2.attrib

    chk = by_auto_id["chkRemember"]
    assert chk.attrib["checked"] == "true"
    assert "selected" not in chk.attrib

    weird = by_auto_id["custom1"]
    assert weird.attrib["control_type"] == "123 Invalid Tag!"
    assert private_value not in weird.attrib["value"]
    assert "Localized message" in weird.attrib["value"]
    assert "signed-value" not in weird.attrib["title"]
    assert "keep=yes" in weird.attrib["title"]


@pytest.mark.parametrize("kind", ["screenshot", "ui_snapshot"])
def test_windows_harness_records_not_applicable_when_app_not_launched_or_killed(tmp_path, kind: str) -> None:
    class UnlaunchedDriver(FakeWindowsDriver):
        def __init__(self) -> None:
            super().__init__()
            self.app_path = "C:/Windows/System32/notepad.exe"
            self._launched = False

        def screenshot(self, params=None) -> bytes:
            raise AssertionError("screenshot() must not run before launch or after kill")

        def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
            raise AssertionError("ui_snapshot() must not run before launch or after kill")

    driver = UnlaunchedDriver()
    store = ArtifactStore(run_dir=tmp_path)
    harness = WindowsHarness(driver=driver, artifact_store=store)
    context = HarnessContext(
        platform="windows",
        session_id="pywinauto:uia",
        metadata={"app_path_configured": True, "window_title_re_configured": False},
    )

    ref = harness.capture_artifact(kind=kind, reason="before launchApp", context=context, step_id="step-1", phase="prepare")
    assert ref.availability == "not_applicable"
    assert ref.unavailable_reason == "windows_app_not_launched"
    assert ref.metadata["requested_artifact_kind"] == kind

    # Once launched, an active capture exception must still propagate so StepRunner records availability="failed".
    driver._launched = True
    with pytest.raises(AssertionError):
        harness.capture_artifact(kind=kind, reason="active window", context=context, step_id="step-2", phase="finalize")

    # After killApp / rollback resets _launched = False, capture returns to objective not_applicable.
    driver._launched = False
    after_kill_ref = harness.capture_artifact(kind=kind, reason="after killApp", context=context, step_id="step-3", phase="finalize")
    assert after_kill_ref.availability == "not_applicable"
    assert after_kill_ref.unavailable_reason == "windows_app_not_launched"
