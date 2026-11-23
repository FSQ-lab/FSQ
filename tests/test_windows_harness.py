# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import io
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
from fsq_agent.drivers._host_visual_inspection import _run_host_visual_inspection
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    AIVisualInspectionResult,
    AndroidVisualAssertionPolicy,
    ExecutableStep,
    HarnessContext,
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
    assert "assert_with_ai" in schemas
    assert set(schemas["launch_app"].params_json_schema["properties"]) == {"extra_args"}
    assert schemas["click_on"].driver_method == "click_on"
    assert schemas["click_on"].fsq_action_name == "clickOn"
    assert schemas["click_on"].platform == "windows"
    assert schemas["click_on"].metadata["driver_class"] == "FakeWindowsDriver"
    assert schemas["click_on"].metadata["backend"] == "fake-pywinauto"
    assert schemas["click_on"].metadata["replay"] == {"kind": "fsq_command", "alias": "clickOn"}
    assert "locator" in schemas["click_on"].params_json_schema["properties"]
    assert "locator" in schemas["click_on"].params_json_schema["required"]
    assert "target" in schemas["click_on"].params_json_schema["properties"]
    assert "target" not in schemas["click_on"].params_json_schema["required"]
    assert "non-empty locator" in schemas["click_on"].params_json_schema["description"]
    assert "descriptive" in schemas["click_on"].params_json_schema["properties"]["target"]["description"]
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
    assert "locator:" in result.error_message
    assert "valid dictionary or instance of WindowsLocator" in result.error_message
    assert result.metadata["validation_errors"][0]["loc"] == ("locator",)
    assert driver.calls == [("context", None)]


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

    locate_res = harness.invoke_action(_step("inspectWithAI", {"prompt": "Find chart legend", "mode": "locate"}), harness.get_context())
    assert locate_res.status == "failed"
    assert locate_res.output["reason"] == "pointer_mapping_unavailable"
    assert len(evaluator.inspect_requests) == 1

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
