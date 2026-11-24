# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import io
from typing import Any

import pytest
from PIL import Image

from fsq_agent.core import ArtifactStore, HarnessInterface
from fsq_agent.core.harness._ai_assertion_tool import AIAssertionBackendToolMixin
from fsq_agent.core.harness._driver_tools import _web_driver_tool
from fsq_agent.core.harness._web import WebHarness
from fsq_agent.drivers._host_visual_inspection import _run_host_visual_inspection
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    AIVisualInspectionResult,
    AndroidVisualAssertionPolicy,
    ExecutableStep,
    HarnessContext,
    WebAssertNotVisibleParams,
    WebAssertTextParams,
    WebAssertVisibleParams,
    WebAssertWithAIParams,
    WebClickOnParams,
    WebCloseBrowserParams,
    WebHoverOnParams,
    WebInspectWithAIParams,
    WebNavigateBackParams,
    WebNavigateToParams,
    WebPressKeyParams,
    WebSelectOptionParams,
    WebStartBrowserParams,
    WebTakeScreenshotParams,
    WebTypeTextParams,
    WebUiSnapshotParams,
    WebWaitForParams,
)


def _valid_png_bytes(width: int = 80, height: int = 60, color: str = "navy") -> bytes:
    buf = io.BytesIO()
    with Image.new("RGB", (width, height), color) as image:
        image.putpixel((width // 2, height // 2), (255, 255, 255))
        image.save(buf, format="PNG")
    return buf.getvalue()


class FakeWebDriver(AIAssertionBackendToolMixin):
    backend = "fake-playwright"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def context(self) -> dict[str, object]:
        self.calls.append(("context", None))
        return {
            "session_id": "web-session-1",
            "current_url": "https://www.bing.com",
            "screen_size": (1280, 720),
            "metadata": {"channel": "chrome", "browser_executable_configured": True},
        }

    def _record(self, method_name: str, params: object) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append((method_name, recorded))
        return {method_name: True}

    @_web_driver_tool("startBrowser", description="Start or reuse the configured Web browser.")
    def start_browser(self, params: WebStartBrowserParams) -> dict[str, object]:
        return self._record("start_browser", params)

    @_web_driver_tool("closeBrowser", description="Close the active Web browser.")
    def close_browser(self, params: WebCloseBrowserParams) -> dict[str, object]:
        return self._record("close_browser", params)

    @_web_driver_tool("navigateTo", description="Navigate the current Web page to a URL.")
    def navigate_to(self, params: WebNavigateToParams) -> dict[str, object]:
        return self._record("navigate_to", params)

    @_web_driver_tool("navigateBack", description="Navigate the current Web page back in browser history.")
    def navigate_back(self, params: WebNavigateBackParams) -> dict[str, object]:
        return self._record("navigate_back", params)

    @_web_driver_tool("clickOn", description="Click a Web page target resolved from the page snapshot.")
    def click_on(self, params: WebClickOnParams) -> dict[str, object]:
        return self._record("click_on", params)

    @_web_driver_tool("typeText", description="Type text into a Web page target resolved from the page snapshot.")
    def type_text(self, params: WebTypeTextParams) -> dict[str, object]:
        return self._record("type_text", params)

    @_web_driver_tool("selectOption", description="Select an option in a Web select target.")
    def select_option(self, params: WebSelectOptionParams) -> dict[str, object]:
        return self._record("select_option", params)

    @_web_driver_tool("hoverOn", description="Hover over a Web page target resolved from the page snapshot.")
    def hover_on(self, params: WebHoverOnParams) -> dict[str, object]:
        return self._record("hover_on", params)

    @_web_driver_tool("pressKey", description="Press a keyboard key in the current Web page.")
    def press_key(self, params: WebPressKeyParams) -> dict[str, object]:
        return self._record("press_key", params)

    @_web_driver_tool("waitFor", description="Wait for a Web page target, text, URL, or timeout condition.")
    def wait_for(self, params: WebWaitForParams) -> dict[str, object]:
        return self._record("wait_for", params)

    @_web_driver_tool("takeScreenshot", description="Capture a Web page screenshot for evidence or debugging.")
    def take_screenshot(self, params: WebTakeScreenshotParams) -> dict[str, object]:
        return self._record("take_screenshot", params)

    @_web_driver_tool("uiSnapshot", description="Return the current Web page accessibility snapshot.")
    def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append(("ui_snapshot", recorded))
        return {"url": "https://www.bing.com", "snapshot": {"role": "WebArea", "name": "Bing"}}

    @_web_driver_tool("inspectWithAI", description="Read pixel-only Web page visual content without performing an action.")
    def inspect_with_ai(self, params: WebInspectWithAIParams) -> dict[str, object]:
        return _run_host_visual_inspection(self, params, platform="web")

    @_web_driver_tool("assertVisible", description="Assert that a Web page target is visible.")
    def assert_visible(self, params: WebAssertVisibleParams) -> dict[str, object]:
        return self._record("assert_visible", params)

    @_web_driver_tool("assertNotVisible", description="Assert that a Web page target is not visible.")
    def assert_not_visible(self, params: WebAssertNotVisibleParams) -> dict[str, object]:
        return self._record("assert_not_visible", params)

    @_web_driver_tool("assertText", description="Assert text on a Web page target.")
    def assert_text(self, params: WebAssertTextParams) -> dict[str, object]:
        return self._record("assert_text", params)

    @_web_driver_tool("assertWithAI", description="Evaluate an explicit Web visual assertion with AI.")
    def assert_with_ai(self, params: WebAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        self.calls.append(("screenshot", params.model_dump(mode="json", exclude_none=True) if hasattr(params, "model_dump") else params))
        return _valid_png_bytes()


def _step(action_name: str, params: dict[str, Any] | None = None) -> ExecutableStep:
    return ExecutableStep(step_id="step-1", kind="action", action_name=action_name, params=params or {})


def test_web_harness_dispatches_fsq_action_names_to_driver() -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver)

    context = harness.get_context()

    cases = [
        ("startBrowser", {}, "start_browser"),
        ("navigateTo", {"url": "https://www.bing.com"}, "navigate_to"),
        ("navigateBack", {}, "navigate_back"),
        ("clickOn", {"locator": {"role": "textbox", "name": "Search box"}}, "click_on"),
        ("typeText", {"locator": {"role": "textbox", "name": "Search box"}, "text": "playwright", "textType": "literal"}, "type_text"),
        ("selectOption", {"locator": {"role": "combobox", "name": "Region"}, "labels": ["United States"]}, "select_option"),
        ("hoverOn", {"locator": {"role": "button", "name": "Menu"}}, "hover_on"),
        ("pressKey", {"key": "Enter"}, "press_key"),
        ("waitFor", {"locator": {"role": "main", "name": "Results"}, "timeout_ms": 5000}, "wait_for"),
        ("takeScreenshot", {}, "take_screenshot"),
        ("uiSnapshot", {}, "ui_snapshot"),
        ("assertVisible", {"locator": {"role": "main", "name": "Results"}}, "assert_visible"),
        ("assertNotVisible", {"locator": {"role": "dialog"}}, "assert_not_visible"),
        ("assertText", {"locator": {"role": "main", "name": "Results"}, "text": {"contains": "playwright"}}, "assert_text"),
        ("closeBrowser", {}, "close_browser"),
    ]

    for action_name, params, _method_name in cases:
        result = harness.invoke_action(_step(action_name, params), context)
        assert result.status == "passed"
        assert result.action_name == action_name

    assert isinstance(harness, HarnessInterface)
    assert context == HarnessContext(
        platform="web",
        session_id="web-session-1",
        current_url="https://www.bing.com",
        screen_size=(1280, 720),
        metadata={"channel": "chrome", "browser_executable_configured": True},
    )
    assert driver.calls == [("context", None)] + [(method_name, params) for _action_name, params, method_name in cases]


def test_web_harness_action_space_returns_catalog_backed_schemas() -> None:
    harness = WebHarness(driver=FakeWebDriver())

    schemas = {schema.name: schema for schema in harness.action_space()}
    click_locator_schema = schemas["click_on"].params_json_schema["$defs"]["WebLocator"]

    assert "click_on" in schemas
    assert "start_browser" in schemas
    assert "close_browser" in schemas
    assert "ui_snapshot" in schemas
    assert "inspect_with_ai" in schemas
    assert "assert_with_ai" in schemas
    assert schemas["start_browser"].driver_method == "start_browser"
    assert schemas["start_browser"].fsq_action_name == "startBrowser"
    assert schemas["start_browser"].metadata["replay"] == {"kind": "fsq_command", "alias": "startBrowser"}
    assert schemas["close_browser"].driver_method == "close_browser"
    assert schemas["close_browser"].fsq_action_name == "closeBrowser"
    assert schemas["click_on"].driver_method == "click_on"
    assert schemas["click_on"].fsq_action_name == "clickOn"
    assert schemas["click_on"].platform == "web"
    assert schemas["click_on"].metadata["driver_class"] == "FakeWebDriver"
    assert schemas["click_on"].metadata["backend"] == "fake-playwright"
    assert schemas["click_on"].metadata["replay"] == {"kind": "fsq_command", "alias": "clickOn"}
    assert schemas["click_on"].params_json_schema["required"] == ["locator"]
    assert "target" not in schemas["click_on"].params_json_schema["properties"]
    assert "semantic locator" in schemas["click_on"].params_json_schema["description"]
    assert set(click_locator_schema["properties"]) == {"role", "name", "within", "index"}
    assert "ref" not in click_locator_schema["properties"]
    assert schemas["ui_snapshot"].driver_method == "ui_snapshot"
    assert schemas["ui_snapshot"].fsq_action_name == "uiSnapshot"
    assert schemas["ui_snapshot"].params_json_schema.get("properties") == {}


def test_web_harness_validation_failure_does_not_call_driver_method() -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"locator": {"role": "button", "unknown": "Login"}}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Invalid Web parameters for clickOn."
    assert result.metadata["validation_errors"]
    assert driver.calls == [("context", None)]


def test_web_harness_captures_screenshot_and_ui_snapshot_with_artifact_store(tmp_path) -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    context = harness.get_context()

    screenshot_ref = harness.capture_artifact(
        kind="screenshot",
        reason="after click",
        context=context,
        step_id="step-1",
        phase="invoke",
    )
    ui_snapshot_ref = harness.capture_artifact(
        kind="ui_snapshot",
        reason="after click",
        context=context,
        step_id="step-1",
        phase="finalize",
    )

    assert screenshot_ref.path.parent.as_posix() == "artifacts/screenshots"
    assert (tmp_path / screenshot_ref.path).is_file()
    assert screenshot_ref.sha256 is not None
    assert (tmp_path / screenshot_ref.path).read_bytes() == _valid_png_bytes()
    assert ui_snapshot_ref.path.parent.as_posix() == "artifacts/ui-snapshots"
    assert (tmp_path / ui_snapshot_ref.path).is_file()
    assert ui_snapshot_ref.sha256 is not None
    assert "WebArea" in (tmp_path / ui_snapshot_ref.path).read_text(encoding="utf-8")
    assert driver.calls == [("context", None), ("screenshot", {}), ("ui_snapshot", {})]


@pytest.mark.parametrize("regional", [False, True])
def test_web_harness_assert_with_ai_uses_injected_evaluator_and_single_frame_prepare_reuse(tmp_path, regional) -> None:
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.core import EvidenceRecorder, StepRunner

    class FakeEvaluator:
        def __init__(self) -> None:
            self.requests: list[AIAssertionRequest] = []

        def evaluate(self, request: AIAssertionRequest, *, cancellation_check=None) -> AIAssertionResult:
            self.requests.append(request)
            return AIAssertionResult(
                status="passed",
                outcome="passed",
                passed=True,
                explanation="The expected chart is visible.",
                provider="fake",
                model="fake-model",
                capture=request.capture,
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
            )

    driver = FakeWebDriver()
    evaluator = FakeEvaluator()
    harness = WebHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )
    schemas = {schema.name: schema for schema in harness.action_space()}
    assert "assert_with_ai" in schemas
    assert "inspect_with_ai" in schemas
    assert schemas["assert_with_ai"].metadata["owner"] == "driver"

    recorder = EvidenceRecorder(run_id="run-web", output_dir=tmp_path)
    runner = StepRunner(harness=harness, capability_registry=build_capability_registry(platform="web"), evidence_sink=recorder)
    step_result = runner.run_step(
        run_id="run-web",
        step=ExecutableStep(
            step_id="step-visual",
            kind="assertion",
            action_name="assertWithAI",
            params={
                "assertion": "Verify chart bars are blue",
                "surface": "custom_canvas",
                **({"region": {"kind": "image_rect", "rect": {"left": 5, "top": 5, "right": 50, "bottom": 40}}} if regional else {"full_screen_reason": "The chart extends below the fold"}),
            },
        ),
    )

    assert step_result.status == "passed"
    assert len(evaluator.requests) == 1
    assert evaluator.requests[0].platform == "web"
    assert evaluator.requests[0].prompt == "Verify chart bars are blue"
    screenshot_calls = [call for call in driver.calls if call[0] == "screenshot"]
    assert [params["fullPage"] for _, params in screenshot_calls] == [not regional]
    prepare_refs = step_result.phase_reports[0].artifact_refs
    assert len(prepare_refs) == 1
    assert evaluator.requests[0].screenshot_artifact_ref.artifact_id == prepare_refs[0].artifact_id
    with Image.open(tmp_path / prepare_refs[0].path) as recorded:
        assert recorded.size == ((45, 35) if regional else (80, 60))
        assert recorded.getpixel((0, 0)) == (0, 0, 128)


def test_web_harness_assert_with_ai_offline_and_privacy_blocked_and_verifier_reconciliation(tmp_path) -> None:
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.agent._verifier import Verifier
    from fsq_agent.core import StepRunner
    from fsq_agent.models import VerificationResult

    driver = FakeWebDriver()
    offline_harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path / "offline"))
    offline_res = StepRunner(harness=offline_harness, capability_registry=build_capability_registry(platform="web")).run_step(
        "offline",
        ExecutableStep(
            step_id="assert-chart",
            kind="assertion",
            action_name="assertWithAI",
            params={"assertion": "Chart visible", "surface": "custom_canvas", "full_screen_reason": "Canvas fills viewport"},
        ),
    )
    assert offline_res.status == "incomplete"
    assert [c for c in driver.calls if c[0] == "screenshot"] == []

    class DummyEvaluator:
        def evaluate(self, request, *, cancellation_check=None):
            raise AssertionError("Unauthorized upload must not call evaluator")

    blocked_harness = WebHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "blocked"),
        ai_assertion_evaluator=DummyEvaluator(),
    )
    blocked_res = blocked_harness.invoke_action(
        _step("assertWithAI", {"assertion": "Chart visible", "surface": "custom_canvas", "full_screen_reason": "Canvas fills viewport"}),
        blocked_harness.get_context(),
    )
    assert blocked_res.status == "incomplete"
    assert blocked_res.output["outcome"] == "inconclusive"
    assert blocked_res.output["error_code"] == "privacy_blocked"

    reconciled = Verifier.reconcile_execution_evidence(
        VerificationResult(status="success", summary="Looks good"),
        [offline_res.model_dump(mode="json")],
    )
    assert reconciled.status == "inconclusive"
    assert reconciled.diagnostics == ["assert-chart: provider_unavailable"]


@pytest.mark.parametrize("browser_started", [False, True])
def test_web_visual_assertion_neither_uploads_blank_frame_nor_captures_without_browser(tmp_path, browser_started) -> None:
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.core import StepRunner

    class Driver(FakeWebDriver):
        def context(self):
            return {"metadata": {"browser_started": browser_started}}

        def screenshot(self, params=None):
            self.calls.append(("screenshot", params))
            with io.BytesIO() as buffer, Image.new("RGB", (40, 30), "white") as blank:
                blank.save(buffer, format="PNG")
                return buffer.getvalue()

    class Evaluator:
        def evaluate(self, *_args, **_kwargs):
            pytest.fail("An unlaunched or blank frame must not be evaluated")

    driver = Driver()
    harness = WebHarness(
        driver,
        ArtifactStore(tmp_path),
        Evaluator(),
        visual_assertion_policy_resolver=lambda *_: AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive"),
    )
    result = StepRunner(harness, capability_registry=build_capability_registry(platform="web")).run_step(
        "visual",
        ExecutableStep(
            step_id="assert-chart",
            kind="assertion",
            action_name="assertWithAI",
            params={"assertion": "Chart visible", "surface": "custom_canvas", "full_screen_reason": "Page contains the chart"},
        ),
    )
    assert result.status == "incomplete"
    [artifact] = result.phase_reports[0].artifact_refs
    assert artifact.availability == ("failed" if browser_started else "not_applicable")
    assert artifact.unavailable_reason == ("capture_failed" if browser_started else "browser_not_started")
    assert len(driver.calls) == int(browser_started)
    assert not list(tmp_path.rglob("*.png"))


def test_web_harness_inspect_with_ai_extract_locate_and_privacy(tmp_path, monkeypatch) -> None:
    class InspectEvaluator:
        def __init__(self) -> None:
            self.inspect_requests = []

        def evaluate(self, request, *, cancellation_check=None):
            raise AssertionError("inspect_with_ai must not call evaluate")

        def inspect(self, request, *, cancellation_check=None) -> AIVisualInspectionResult:
            self.inspect_requests.append(request)
            return AIVisualInspectionResult(summary="Canvas shows Q4 revenue 42M", candidates=())

    driver = FakeWebDriver()
    evaluator = InspectEvaluator()
    harness = WebHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "extract"),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=lambda _ctx, _seq: AndroidVisualAssertionPolicy(
            allow_provider_upload=True,
            provider_available=True,
            frame_sensitivity="non_sensitive",
        ),
    )
    extract_res = harness.invoke_action(_step("inspectWithAI", {"prompt": "Read chart total", "mode": "extract"}), harness.get_context())
    assert extract_res.status == "passed"
    assert extract_res.output["summary"] == "Canvas shows Q4 revenue 42M"
    assert len(extract_res.artifact_refs) == 1
    assert (tmp_path / "extract" / extract_res.artifact_refs[0].path).is_file()
    assert len(evaluator.inspect_requests) == 1
    assert evaluator.inspect_requests[0].platform == "web"

    invalid_png = _valid_png_bytes() + b"trailing private bytes"
    monkeypatch.setattr(driver, "screenshot", lambda *_: invalid_png)
    rejected = harness.invoke_action(_step("inspectWithAI", {"prompt": "Read chart total"}), harness.get_context())
    assert rejected.output["reason"] == "capture_failed"
    assert not rejected.artifact_refs
    assert len(evaluator.inspect_requests) == 1
    assert len(list((tmp_path / "extract").rglob("*.png"))) == 1

    locate_res = harness.invoke_action(_step("inspectWithAI", {"prompt": "Find chart legend", "mode": "locate"}), harness.get_context())
    assert locate_res.status == "failed"
    assert locate_res.output["reason"] == "pointer_mapping_unavailable"
    assert len(evaluator.inspect_requests) == 1

    blocked_driver = FakeWebDriver()
    blocked_harness = WebHarness(
        driver=blocked_driver,
        artifact_store=ArtifactStore(run_dir=tmp_path / "privacy"),
        ai_assertion_evaluator=evaluator,
    )
    privacy_res = blocked_harness.invoke_action(_step("inspectWithAI", {"prompt": "Read chart total"}), blocked_harness.get_context())
    assert privacy_res.status == "failed"
    assert privacy_res.output["reason"] == "privacy_blocked"
    assert privacy_res.artifact_refs == []
    assert [c for c in blocked_driver.calls if c[0] == "screenshot"] == []


def test_web_harness_requires_artifact_store_for_capture() -> None:
    harness = WebHarness(driver=FakeWebDriver())

    with pytest.raises(RuntimeError, match="Artifact capture requires an ArtifactStore"):
        harness.capture_artifact(
            kind="ui_snapshot",
            reason="after click",
            context=harness.get_context(),
            step_id="step-1",
            phase="finalize",
        )


def test_web_harness_explicit_ui_snapshot_persists_artifact_and_satisfies_web_search_evidence(tmp_path) -> None:
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.core import EvidenceRecorder, StepRunner
    from fsq_agent.execution.dynamic import _has_web_search_evidence

    class AriaWebDriver(FakeWebDriver):
        @_web_driver_tool("uiSnapshot", description="Return the current Web page accessibility snapshot.")
        def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
            self.calls.append(("ui_snapshot", {}))
            return {
                "url": "https://www.bing.com/search?q=fsq",
                "snapshot_type": "aria",
                "snapshot": '- link "FSQ Official"',
                "coverage": {"status": "complete", "scope": "observed_aria_snapshot"},
                "truncated": False,
            }

    store = ArtifactStore(run_dir=tmp_path)
    recorder = EvidenceRecorder(run_id=tmp_path.name, output_dir=tmp_path)
    harness = WebHarness(driver=AriaWebDriver(), artifact_store=store)
    runner = StepRunner(harness=harness, capability_registry=build_capability_registry(platform="web"), evidence_sink=recorder)

    result = runner.run_step(
        run_id=tmp_path.name,
        step=ExecutableStep(step_id="obs-1", kind="observation", action_name="uiSnapshot", params={}),
    )

    assert result.status == "passed"
    invoke_refs = result.phase_reports[1].artifact_refs
    assert [ref.kind for ref in invoke_refs] == ["ui_snapshot"]
    assert result.phase_reports[0].artifact_refs == []
    assert result.phase_reports[3].artifact_refs == []
    bundle = recorder.build_bundle()
    assert _has_web_search_evidence(bundle, tmp_path) is True


def test_web_harness_ui_snapshot_artifact_write_failure_records_failed_evidence(tmp_path, monkeypatch) -> None:
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.core import EvidenceRecorder, StepRunner
    from fsq_agent.execution.dynamic import _has_web_search_evidence

    class AriaWebDriver(FakeWebDriver):
        @_web_driver_tool("uiSnapshot", description="Return the current Web page accessibility snapshot.")
        def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
            return {
                "url": "https://www.bing.com/search?q=fsq",
                "snapshot_type": "aria",
                "snapshot": '- link "FSQ Official"',
                "coverage": {"status": "complete", "scope": "observed_aria_snapshot"},
                "truncated": False,
            }

    store = ArtifactStore(run_dir=tmp_path)
    monkeypatch.setattr(store, "write_json", lambda **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    recorder = EvidenceRecorder(run_id=tmp_path.name, output_dir=tmp_path)
    harness = WebHarness(driver=AriaWebDriver(), artifact_store=store)
    runner = StepRunner(harness=harness, capability_registry=build_capability_registry(platform="web"), evidence_sink=recorder)

    result = runner.run_step(
        run_id=tmp_path.name,
        step=ExecutableStep(step_id="obs-1", kind="observation", action_name="uiSnapshot", params={}),
    )

    assert result.action_status == "passed"
    assert result.status == "failed"
    assert result.failure_category == "artifact_error"
    assert len(result.evidence_errors) == 1
    invoke_refs = result.phase_reports[1].artifact_refs
    assert len(invoke_refs) == 1
    assert invoke_refs[0].kind == "ui_snapshot"
    assert invoke_refs[0].availability == "failed"
    assert _has_web_search_evidence(recorder.build_bundle(), tmp_path) is False


def test_web_harness_ui_snapshot_propagates_fatal_journal_acknowledgement_error(tmp_path) -> None:
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.core import StepRunner

    class AriaWebDriver(FakeWebDriver):
        @_web_driver_tool("uiSnapshot", description="Return the current Web page accessibility snapshot.")
        def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
            return {"url": "https://example.com", "snapshot_type": "aria", "snapshot": '- heading "Example"'}

    class FatalSink:
        def allocate_step_identity(self, step):
            return step.model_copy(update={"step_execution_id": step.step_id, "source_step_id": step.step_id, "invocation_path": (step.step_id, "1")})

        def record_event(self, event):
            if event.event_type == "artifact_captured":
                raise OSError("journal append failed")

        def record_step_result(self, result):
            pass

    store = ArtifactStore(run_dir=tmp_path)
    harness = WebHarness(driver=AriaWebDriver(), artifact_store=store)
    runner = StepRunner(harness=harness, capability_registry=build_capability_registry(platform="web"), evidence_sink=FatalSink())

    with pytest.raises(OSError, match="journal append failed") as caught:
        runner.run_step(
            run_id=tmp_path.name,
            step=ExecutableStep(step_id="obs-fatal", kind="observation", action_name="uiSnapshot", params={}),
        )
    assert getattr(caught.value, "fsq_evidence_fatal", False) is True
