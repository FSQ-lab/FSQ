# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import io
import json
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image
from test_android_harness import FakeAndroidDriver

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.core import ArtifactStore, StepRunner
from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol, HarnessFactory
from fsq_agent.harnesses._android import AndroidHarness
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    AndroidPressKeyParams,
    AndroidVisualAssertionPolicy,
    AssertionBudget,
    EvidenceBundle,
    ExecutableStep,
    HarnessSettings,
)
from fsq_agent.report import CoreEvidenceReportGenerator, RunReportService


def _png(color=(0, 120, 240), *, blank=False):
    stream = io.BytesIO()
    with Image.new("RGB", (8, 6), color) as image:
        if not blank:
            image.putpixel((3, 3), (255, 255, 255))
        image.save(stream, format="PNG")
    return stream.getvalue()


class ImageDriver(FakeAndroidDriver):
    def __init__(self, data=None):
        super().__init__()
        self.data = _png() if data is None else data

    def context(self):
        self.calls.append(("context", None))
        return {"session_id": "remote-session", "screen_size": (80, 60), "metadata": {"sdk_session_id": "local-session"}}

    def screenshot(self, params=None):
        self.calls.append(("screenshot", None))
        return self.data


class Evaluator:
    def __init__(self, outcome="passed"):
        self.requests = []
        self.cancellation_checks = []
        self.outcome = outcome

    def evaluate(self, request: AIAssertionRequest, *, cancellation_check=None) -> AIAssertionResult:
        self.requests.append(request)
        self.cancellation_checks.append(cancellation_check)
        return AIAssertionResult(status=self.outcome, outcome=self.outcome, passed=self.outcome == "passed", explanation="Observed image content.", capture=request.capture)

    def close(self):
        return None


def _run(tmp_path, *, data=None, policy=None, evaluator=True, region=None, extra=None, outcome="passed", cancellation_check=None):
    driver = ImageDriver(data)
    model: AIAssertionEvaluatorProtocol | None = Evaluator(outcome) if evaluator else None
    harness = AndroidHarness(
        driver=driver,
        artifact_store=ArtifactStore(tmp_path),
        ai_assertion_evaluator=model,
        visual_assertion_policy_resolver=lambda context, sequence: policy or AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive"),
    )
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"), cancellation_check=cancellation_check)
    params = {"assertion": "The canvas is blue.", "surface": "custom_canvas", "full_screen_reason": "The whole canvas is the test subject."}
    if region is not None:
        params["region"] = region
    params.update(extra or {})
    step = ExecutableStep(step_id="visual", kind="assertion", action_name="assert_with_ai", params=params)
    return runner.run_step("run-1", step), driver, model, runner


def test_runner_shares_one_current_capture_and_records_original_identity(tmp_path):
    result, driver, model, runner = _run(tmp_path, region={"kind": "image_rect", "rect": {"left": 1, "right": 5, "top": 2, "bottom": 5}})
    assert result.status == "passed"
    assert [name for name, _ in driver.calls].count("screenshot") == 1
    request = model.requests[0]
    assert request.capture.sdk_session_id == "local-session"
    assert request.capture.pointer_mapping_state == "unverified"
    assert (request.capture.image_width, request.capture.image_height) == (8, 6)
    assert request.capture.host_capture_started_monotonic_ns <= request.capture.host_capture_finished_monotonic_ns
    with Image.open(tmp_path / request.screenshot_artifact_ref.path) as image:
        assert image.size == (4, 3)
        assert image.getpixel((0, 0)) == (0, 120, 240)
    refs = [event.payload["artifact"] for event in runner.events if event.event_type == "artifact_captured" and event.payload.get("kind") == "screenshot"]
    assert {ref["artifact_id"] for ref in refs} == {request.screenshot_artifact_ref.artifact_id}
    assert _assertion(result)["capture"]["run_id"] == "run-1"


@pytest.mark.parametrize("region", [None, {"kind": "image_rect", "rect": {"left": 0, "top": 0, "right": 2, "bottom": 2}}])
def test_mobile_visual_assertion_rejects_blank_input_before_provider_or_persistence(tmp_path, region):
    result, _, model, _ = _run(tmp_path, data=_png(blank=region is None), region=region)
    assert result.status == "incomplete"
    assert _assertion(result)["error_code"] == ("capture_failed" if region is None else "region_empty")
    assert not model.requests
    assert not list(tmp_path.rglob("*.png"))


@pytest.mark.parametrize("platform", ["android", "ios", "macos"])
def test_authorized_assertion_before_sdk_session_does_not_start_it_or_capture(tmp_path, monkeypatch, platform):
    from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver
    from fsq_agent.drivers.ios._appium_connect import AppiumConnectIOSDriver
    from fsq_agent.drivers.macos._appium_connect import AppiumConnectMacOSDriver
    from fsq_agent.harnesses._ios import IOSHarness
    from fsq_agent.harnesses._macos import MacOSHarness

    match platform:
        case "android":
            driver = AppiumConnectAndroidDriver(app_id="com.example.app")
            harness_type = AndroidHarness
        case "ios":
            driver = AppiumConnectIOSDriver(bundle_id="com.example.app")
            harness_type = IOSHarness
        case _:
            driver = AppiumConnectMacOSDriver(bundle_id="com.example.app")
            harness_type = MacOSHarness

    def unexpected_capture(*args, **kwargs):
        pytest.fail("Unstarted session must not capture or create a session")

    monkeypatch.setattr(driver, "capture_visual_screenshot" if platform == "macos" else "screenshot", unexpected_capture)
    evaluator = Evaluator()
    harness = harness_type(
        driver,
        ArtifactStore(tmp_path),
        evaluator,
        visual_assertion_policy_resolver=lambda *_: AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive"),
    )
    with_context = StepRunner(harness, capability_registry=build_capability_registry(platform=platform))
    try:
        result = with_context.run_step(
            "inactive",
            ExecutableStep(
                step_id="visual",
                kind="assertion",
                action_name="assertWithAI",
                params={"assertion": "Chart visible", "surface": "custom_canvas", "full_screen_reason": "Inspect the chart"},
            ),
        )
    finally:
        harness.close()
    assert result.status == "incomplete"
    [artifact] = result.phase_reports[0].artifact_refs
    assert artifact.availability == "not_applicable"
    assert artifact.unavailable_reason == f"{platform}_session_not_started"
    assert not result.evidence_errors
    assert not evaluator.requests


@pytest.mark.parametrize(
    "policy", [AndroidVisualAssertionPolicy(), AndroidVisualAssertionPolicy(allow_provider_upload=True), AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="sensitive")]
)
def test_privacy_refusal_does_not_capture_persist_or_call_provider(tmp_path, policy):
    result, driver, model, _ = _run(tmp_path, policy=policy)
    assert result.status == "incomplete"
    assert result.failure_category is None
    assert _assertion(result)["error_code"] == "privacy_blocked"
    assert not model.requests
    assert not any(name == "screenshot" for name, _ in driver.calls)
    assert not list(tmp_path.rglob("*.png"))


def test_missing_evaluator_is_a_recognized_unverified_assertion(tmp_path):
    result, driver, _, _ = _run(tmp_path, evaluator=False)
    assert result.status == "incomplete"
    assert _assertion(result)["error_code"] == "provider_unavailable"
    assert result.failure_category is None
    assert not any(name == "screenshot" for name, _ in driver.calls)


@pytest.mark.parametrize("extra", [{"point": {"x": 1, "y": 1}}, {"region": {"kind": "element", "target": "Canvas"}}, {"coordinates": [1, 1]}])
def test_invalid_visual_driving_arguments_are_rejected_before_device_context(tmp_path, extra):
    result, driver, model, _ = _run(tmp_path, extra=extra)
    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert not driver.calls
    assert not model.requests


@pytest.mark.parametrize("rect", [{"left": -1, "right": 5, "top": 0, "bottom": 5}, {"left": 5, "right": 5, "top": 0, "bottom": 5}, {"left": 0, "right": 9, "top": 0, "bottom": 5}])
def test_invalid_crop_geometry_is_incomplete_not_assertion_failure(tmp_path, rect):
    result, _, model, _ = _run(tmp_path, region={"kind": "image_rect", "rect": rect})
    assert result.status == "incomplete"
    assert result.failure_category is None
    assert _assertion(result)["error_code"] == "region_invalid"
    assert not model.requests
    assert not list(tmp_path.rglob("*.png"))


def test_apng_control_chunks_are_rejected_before_persistence_or_upload(tmp_path):
    chunk_type = b"acTL"
    payload = struct.pack(">II", 1, 0)
    chunk = struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    png = _png()
    result, driver, model, _ = _run(tmp_path, data=png[:33] + chunk + png[33:])
    assert result.status == "incomplete"
    assert _assertion(result)["error_code"] == "frame_not_single"
    assert [name for name, _ in driver.calls].count("screenshot") == 1
    assert not model.requests
    assert not list(Path(tmp_path).rglob("*.png"))


def _assertion(result):
    invoke = next(report for report in result.phase_reports if report.phase == "invoke")
    return invoke.metadata["harness_metadata"]["ai_assertion"]


@pytest.mark.parametrize("request_state", ["not_sent", "response_unknown"])
def test_sdk_screenshot_failure_preserves_safe_dispatch_diagnostics(request_state):
    from test_appium_connect_android_driver import CommandError, FakeSession

    from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver
    from fsq_agent.models import ToolExecutionError

    session = FakeSession()
    failure = CommandError("operation_timeout", "private screenshot backend payload")
    failure.request_state = request_state
    session.screenshot_error = failure
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        with pytest.raises(ToolExecutionError) as caught:
            driver.screenshot()
        diagnostics = caught.value.context["capture_diagnostics"]
        assert diagnostics["backend_code"] == "operation_timeout"
        assert diagnostics["request_state"] == request_state
        assert diagnostics["session_state"] == "active"
        assert diagnostics["stage"] == "screenshot"
        assert "private screenshot" not in str(caught.value)
        assert [call[0] for call in session.calls].count("take_screenshot") == 1
    finally:
        driver.close()


def test_sdk_capture_cancellation_propagates_with_not_sent_facts():
    from asyncio import CancelledError

    from test_appium_connect_android_driver import CommandError, FakeSession

    from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver

    session = FakeSession()
    failure = CommandError("operation_cancelled", "private cancelled request")
    failure.request_state = "not_sent"
    session.screenshot_error = failure
    driver = AppiumConnectAndroidDriver(app_id="com.example.app", session=session)
    try:
        with pytest.raises(CancelledError) as caught:
            driver.screenshot()
        assert caught.value.fsq_capture_diagnostics["request_state"] == "not_sent"
        assert "private" not in str(caught.value)
    finally:
        driver.close()


def test_pre_cancelled_visual_step_makes_no_device_calls(tmp_path):
    from asyncio import CancelledError

    driver = ImageDriver()
    evaluator = Evaluator()

    def cancel():
        raise CancelledError()

    harness = AndroidHarness(driver=driver, artifact_store=ArtifactStore(tmp_path), ai_assertion_evaluator=evaluator)
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"), cancellation_check=cancel)
    with pytest.raises(CancelledError):
        runner.run_step(
            "cancelled",
            ExecutableStep(step_id="visual", kind="assertion", action_name="assert_with_ai", params={"assertion": "Canvas is blue", "surface": "appearance", "full_screen_reason": "Whole canvas"}),
        )
    assert not driver.calls
    assert not evaluator.requests
    assert runner.last_step_result.status == "cancelled"


@pytest.mark.parametrize("authorize_second", [False, True])
def test_each_new_frame_requires_its_own_trusted_invocation_decision(tmp_path, authorize_second):
    driver = ImageDriver()
    evaluator = Evaluator()
    decisions = []
    grants = {"execution-1"}

    def resolve(context, sequence):
        current = context.metadata["capture_invocation"]
        identity = current["step_execution_id"]
        decisions.append((current["run_id"], current["step_id"], identity, sequence))
        if identity not in grants:
            return None
        grants.remove(identity)
        return AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive", budget=AssertionBudget(max_image_pixels=48))

    harness = AndroidHarness(driver, ArtifactStore(tmp_path), evaluator, visual_assertion_policy_resolver=resolve)
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"))
    params = {"assertion": "The canvas is blue.", "surface": "custom_canvas", "full_screen_reason": "The whole canvas is the test subject."}
    first = runner.run_step("run-1", ExecutableStep(step_id="first", step_execution_id="execution-1", kind="assertion", action_name="assert_with_ai", params=params))
    assert first.status == "passed"
    persisted_before = set(tmp_path.rglob("*.png"))
    assert len(persisted_before) == 1
    assert evaluator.requests[0].budget.max_image_pixels == 48
    assert evaluator.requests[0].upload_authorized is True
    assert evaluator.requests[0].frame_sensitivity == "non_sensitive"

    driver.press_key(AndroidPressKeyParams(key="Back"))
    driver.data = _png((0, 121, 240))
    if authorize_second:
        grants.add("execution-2")
    second = runner.run_step("run-1", ExecutableStep(step_id="second", step_execution_id="execution-2", kind="assertion", action_name="assert_with_ai", params=params))

    assert decisions == [("run-1", "execution-1", "execution-1", 1), ("run-1", "execution-2", "execution-2", 2)]
    expected_calls = 2 if authorize_second else 1
    assert [name for name, _ in driver.calls].count("screenshot") == expected_calls
    assert len(evaluator.requests) == expected_calls
    assert len(list(tmp_path.rglob("*.png"))) == expected_calls
    if authorize_second:
        assert second.status == "passed"
        assert evaluator.requests[1].capture.step_execution_id == "execution-2"
        assert evaluator.requests[1].capture.capture_sequence == 2
    else:
        assert second.status == "incomplete"
        assert _assertion(second)["error_code"] == "privacy_blocked"
        assert set(tmp_path.rglob("*.png")) == persisted_before
    for request in evaluator.requests:
        assert "allow_provider_upload" not in json.dumps(request.screenshot_artifact_ref.metadata)
        assert "frame_sensitivity" not in json.dumps(request.screenshot_artifact_ref.metadata)


@pytest.mark.parametrize("fresh_decision", [False, True])
def test_factory_requires_fresh_resolver_rather_than_static_policy(tmp_path, fresh_decision):
    driver = ImageDriver()
    evaluator = Evaluator()
    decisions = []

    def resolve(context, sequence):
        decisions.append((context.metadata["capture_invocation"]["step_execution_id"], sequence))
        return AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive")

    class Drivers:
        def create_android_driver(self, settings, *, app_id=None, serial=None):
            return driver

    settings = HarnessSettings()
    settings.android.visual_assertion_policy = AndroidVisualAssertionPolicy(
        allow_provider_upload=True, frame_sensitivity="non_sensitive", provider_available=True, budget=AssertionBudget(max_image_pixels=48)
    )
    harness = HarnessFactory(Drivers()).create_harness(
        platform="android",
        harness_settings=settings,
        artifact_store=ArtifactStore(tmp_path),
        ai_assertion_evaluator=evaluator,
        visual_assertion_policy_resolver=resolve if fresh_decision else None,
    )
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"))
    result = runner.run_step(
        "run-1",
        ExecutableStep(step_id="visual", kind="assertion", action_name="assert_with_ai", params={"assertion": "Blue canvas?", "surface": "custom_canvas", "full_screen_reason": "Whole canvas"}),
    )
    if fresh_decision:
        assert result.status == "passed"
        assert decisions == [("visual", 1)]
        assert len(evaluator.requests) == 1
        assert evaluator.requests[0].budget.max_image_pixels == 48
    else:
        assert result.status == "incomplete"
        assert _assertion(result)["error_code"] == "privacy_blocked"
        assert not evaluator.requests
        assert not any(name == "screenshot" for name, _ in driver.calls)
        assert not list(tmp_path.rglob("*.png"))


def test_capture_decision_cannot_authorize_another_execution_or_be_replayed(tmp_path):
    driver = ImageDriver()
    evaluator = Evaluator()
    resolutions = []

    def resolve(context, sequence):
        resolutions.append((context.metadata["capture_invocation"]["step_execution_id"], sequence))
        return AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive")

    harness = AndroidHarness(driver, ArtifactStore(tmp_path), evaluator, visual_assertion_policy_resolver=resolve)
    params = {"assertion": "Blue canvas?", "surface": "custom_canvas", "full_screen_reason": "Whole canvas"}
    context = harness.get_context()
    context.metadata["capture_invocation"] = {"run_id": "run-1", "step_id": "execution-1", "step_execution_id": "execution-1", "action_name": "assert_with_ai", "params": params}
    artifact = harness.capture_artifact("screenshot", "assert-with-ai", context, "execution-1", "invoke")
    metadata = {"run_id": "run-1", "prepared_artifacts": [artifact.model_dump(mode="json")]}
    wrong = ExecutableStep(step_id="execution-2", step_execution_id="execution-2", kind="assertion", action_name="assert_with_ai", params=params, metadata=metadata)
    rejected = harness.invoke_action(wrong, context)
    assert rejected.status == "incomplete"
    assert rejected.metadata["ai_assertion"]["error_code"] == "privacy_blocked"
    original = wrong.model_copy(update={"step_id": "execution-1", "step_execution_id": "execution-1"})
    replayed = harness.invoke_action(original, context)
    assert replayed.status == "incomplete"
    assert replayed.metadata["ai_assertion"]["error_code"] == "privacy_blocked"
    assert resolutions == [("execution-1", 1)]
    assert not evaluator.requests
    assert [name for name, _ in driver.calls].count("screenshot") == 1
    assert len(list(tmp_path.rglob("*.png"))) == 1


def test_public_evaluator_protocol_receives_current_cancellation_callback(tmp_path):
    calls = []

    def check():
        calls.append("checked")

    result, _, evaluator, _ = _run(tmp_path, cancellation_check=check)
    assert isinstance(evaluator, AIAssertionEvaluatorProtocol)
    assert result.status == "passed"
    assert evaluator.cancellation_checks == [check]
    assert calls


@pytest.mark.parametrize("mode", ["passed", "failed", "inconclusive", "privacy_blocked", "provider_unavailable", "capture_failed"])
def test_reports_retain_the_original_android_assertion_question(tmp_path, mode):
    options = {}
    if mode == "privacy_blocked":
        options["policy"] = AndroidVisualAssertionPolicy()
    elif mode == "provider_unavailable":
        options["evaluator"] = False
    elif mode == "capture_failed":
        options["data"] = b"not-a-png"
    else:
        options["outcome"] = mode
    result, _, _, runner = _run(tmp_path, **options)
    invoke = next(report for report in result.phase_reports if report.phase == "invoke")
    original = "The canvas is blue."
    assert invoke.metadata["harness_metadata"]["prompt"] == original
    assert invoke.metadata["harness_metadata"]["ai_assertion"] == _assertion(result)
    bundle = EvidenceBundle(run_id="run-1", bundle_id="visual-report", steps=[result], events=runner.events)
    manifest = tmp_path / "evidence-manifest.json"
    manifest.write_text(bundle.model_dump_json(), encoding="utf-8")
    artifact = CoreEvidenceReportGenerator().generate_from_manifest(manifest)
    assert f"Prompt: {original}" in artifact.path.read_text(encoding="utf-8")
    public = RunReportService().project(tmp_path, {"run_id": "run-1", "platform": "android"}, bundle)
    assert public.steps[0]["assertion"]["prompt"] == original
