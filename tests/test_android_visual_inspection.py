# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
from asyncio import CancelledError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from PIL import Image

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.core import ArtifactStore, EvidenceRecorder, StepRunner
from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver
from fsq_agent.harnesses._android import AndroidHarness
from fsq_agent.models import (
    AIVisualCandidate,
    AIVisualInspectionResult,
    AndroidVisualAssertionPolicy,
    AssertionBudget,
    ExecutableStep,
    HarnessContext,
    VisualImageBounds,
)


def _png():
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), (20, 100, 220)).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def sdk_inspection(tmp_path, macos_android_sdk):
    # Only the Appium wire peer is synthetic: screenshot acquisition, display
    # binding, provider validation and pointer mapping use the real public SDK.
    from appium_connect_agent.automation import create_session
    from appium_connect_agent.runtime import LocalSettings, Runtime

    state = SimpleNamespace(png=_png(), display="80x60", calls=[], threads=[], disconnect=False)

    class Handler(BaseHTTPRequestHandler):
        def respond(self, value):
            body = json.dumps({"value": value}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state.calls.append(("GET", self.path, None))
            if self.path == "/status":
                self.respond({"ready": True, "build": {"version": "3.7.0"}})
            elif self.path == "/session/visual-session/screenshot":
                self.respond(base64.b64encode(state.png).decode())
            else:
                self.send_error(404)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            state.calls.append(("POST", self.path, payload))
            if self.path == "/session":
                self.respond({"sessionId": "visual-session", "capabilities": {"platformName": "Android", "appium:automationName": "UiAutomator2"}})
            elif self.path == "/session/visual-session/execute/sync" and payload["script"] == "mobile: deviceInfo":
                self.respond({"realDisplaySize": state.display})
            else:
                self.send_error(404)

        def do_DELETE(self):
            state.calls.append(("DELETE", self.path, None))
            self.respond(None)

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    drivers = []
    try:
        runtime = Runtime(LocalSettings(server_url=f"http://127.0.0.1:{server.server_port}"))
        with runtime.connect() as connection:
            session = create_session(connection, "inspection-test-device")
            state.calls.clear()

            class Driver(AppiumConnectAndroidDriver):
                def context(self):
                    state.threads.append(threading.get_ident())
                    return {"screen_size": (80, 60), "metadata": {"sdk_session_id": session.session_info().id}}

            def create(evaluator, *, policy=None, resolver=None, store=None):
                driver = Driver(app_id="com.example.app", session=session)
                drivers.append(driver)
                harness = AndroidHarness(
                    driver,
                    store if store is not None else ArtifactStore(tmp_path),
                    evaluator,
                    visual_assertion_policy_resolver=resolver or (lambda context, sequence: policy or AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive")),
                )
                return harness, driver

            yield state, create
            for driver in reversed(drivers):
                driver.close()
            session.quit()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Evaluator:
    def __init__(self, *, failure=None, candidate=None):
        self.requests = []
        self.failure = failure
        self.candidate = candidate
        self.threads = []

    def inspect(self, request, *, cancellation_check=None):
        self.requests.append(request)
        self.threads.append(threading.get_ident())
        if cancellation_check is not None:
            cancellation_check()
        if self.failure is not None:
            raise self.failure
        return AIVisualInspectionResult(
            summary="Blue canvas and a small symbol.",
            candidates=(AIVisualCandidate(label="symbol", image_bounds=self.candidate or VisualImageBounds(x=1, y=2, width=3, height=2), confidence=0.9),) if request.mode == "locate" else (),
        )

    def close(self):
        pass


def _step(**params):
    return ExecutableStep(step_id="inspect", step_execution_id="inspect-1", kind="observation", action_name="inspect_with_ai", params={"prompt": "Describe the canvas", **params})


def _invoke(harness, **params):
    caller_thread = threading.get_ident()

    def cancellation_check():
        assert threading.get_ident() == caller_thread

    context = HarnessContext(platform="android")
    context.cancellation_check = cancellation_check
    return harness.invoke_action(_step(**params), context)


def test_real_sdk_locate_maps_once_and_persists_exact_callback_before_upload(sdk_inspection, tmp_path):
    state, create = sdk_inspection

    class PersistedEvaluator(Evaluator):
        def inspect(self, request, **kwargs):
            paths = list(tmp_path.rglob("*.png"))
            assert len(paths) == 1
            assert paths[0].read_bytes() == request.png_bytes == state.png
            return super().inspect(request, **kwargs)

    evaluator = PersistedEvaluator()
    decisions = []

    def resolver(context, sequence):
        decisions.append((context.metadata["capture_invocation"], sequence))
        return AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive")

    harness, driver = create(evaluator, resolver=resolver)
    result = _invoke(harness, mode="locate", region={"left": 1, "top": 1, "right": 7, "bottom": 6})
    assert result.status == "passed"
    candidate = result.output["candidates"][0]
    assert candidate["image_bounds"] == {"x": 1, "y": 2, "width": 3, "height": 2}
    assert candidate["pointer_bounds"] == {"x": 10, "y": 20, "width": 30, "height": 20}
    assert candidate["pointer_center"] == {"x": 25, "y": 30}
    assert candidate["confidence"] == 0.9
    assert result.output["png_sha256"] == hashlib.sha256(state.png).hexdigest()
    assert result.output["image_size"] == {"width": 8, "height": 6}
    assert result.output["pointer_size"] == {"width": 80, "height": 60}
    assert len(result.artifact_refs) == 1
    assert result.artifact_refs[0].sha256 == result.output["png_sha256"]
    assert len(evaluator.requests) == 1
    assert len(decisions) == 1
    assert decisions[0][0]["step_execution_id"] == "inspect-1"
    assert decisions[0][1] == 1
    assert evaluator.threads[0] != threading.get_ident()
    assert [path for method, path, _ in state.calls if method == "GET"] == ["/session/visual-session/screenshot"]
    assert len(state.calls) == 2
    assert "png_bytes" not in result.model_dump_json()
    assert getattr(driver, "_ai_assertion_tool_invocation", None) is None


def test_extract_uses_no_display_query_or_pointer_candidates(sdk_inspection):
    state, create = sdk_inspection
    harness, _ = create(Evaluator())
    result = _invoke(harness)
    assert result.status == "passed"
    assert result.output["candidates"] == []
    assert result.output["pointer_size"] is None
    assert len(state.calls) == 1
    assert state.calls[0][:2] == ("GET", "/session/visual-session/screenshot")


@pytest.mark.parametrize(
    "policy", [AndroidVisualAssertionPolicy(), AndroidVisualAssertionPolicy(allow_provider_upload=True), AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="sensitive")]
)
def test_privacy_rejection_prevents_sdk_capture_and_persistence(sdk_inspection, tmp_path, policy):
    state, create = sdk_inspection
    evaluator = Evaluator()
    harness, _ = create(evaluator, policy=policy)
    result = _invoke(harness)
    assert result.status != "passed"
    assert result.failure_category != "assertion_error"
    assert result.output["reason"] == "privacy_blocked"
    assert not state.calls
    assert not evaluator.requests
    assert not list(tmp_path.rglob("*.png"))


@pytest.mark.parametrize(
    "params",
    [
        {"prompt": " "},
        {"max_candidates": True},
        {"max_candidates": 21},
        {"mode": "tap"},
        {"region": {"left": False, "top": 0, "right": 5, "bottom": 5}},
        {"point": {"x": 1, "y": 1}},
        {"upload_authorized": True},
    ],
)
def test_schema_failure_precedes_policy_and_device_side_effects(sdk_inspection, params):
    state, create = sdk_inspection
    decisions = []
    harness, _ = create(Evaluator(), resolver=lambda context, sequence: decisions.append(sequence))
    result = _invoke(harness, **params)
    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert not state.calls
    assert not decisions


def test_missing_or_incompatible_provider_remains_recognized(sdk_inspection):
    state, create = sdk_inspection
    harness, _ = create(None)
    assert "inspect_with_ai" in {schema.name for schema in harness.action_space()}
    result = _invoke(harness)
    assert result.output["reason"] == "provider_unavailable"
    assert not state.calls


def test_sdk_mapping_refusal_does_not_invent_coordinates_or_call_provider(sdk_inspection, tmp_path):
    state, create = sdk_inspection
    state.display = "81x60"
    evaluator = Evaluator()
    harness, _ = create(evaluator)
    result = _invoke(harness, mode="locate")
    assert result.status != "passed"
    assert result.output["reason"] == "pointer_mapping_unavailable"
    assert not result.output["candidates"]
    assert not evaluator.requests
    assert not list(tmp_path.rglob("*.png"))
    assert len(state.calls) == 2


def test_provider_failure_preserves_evidence_and_safe_error(sdk_inspection, tmp_path):
    state, create = sdk_inspection
    evaluator = Evaluator(failure=RuntimeError("private token and raw model payload"))
    harness, _ = create(evaluator)
    result = _invoke(harness)
    assert result.status != "passed"
    assert result.failure_category == "observation_error"
    assert result.output["reason"] == "provider_error"
    assert len(result.artifact_refs) == 1
    assert (tmp_path / result.artifact_refs[0].path).read_bytes() == state.png
    assert "private" not in result.model_dump_json()
    assert len(state.calls) == 1


def test_sdk_invalid_candidate_preserves_uploaded_frame_without_fallback(sdk_inspection):
    state, create = sdk_inspection
    evaluator = Evaluator(candidate=VisualImageBounds(x=7, y=1, width=3, height=2))
    harness, _ = create(evaluator)
    result = _invoke(harness, mode="locate")
    assert result.status != "passed"
    assert result.output["reason"] == "invalid_visual_response"
    assert not result.output["candidates"]
    assert len(result.artifact_refs) == 1
    assert len(evaluator.requests) == 1
    assert len(state.calls) == 2


def test_artifact_failure_prevents_provider_upload(sdk_inspection, tmp_path):
    state, create = sdk_inspection

    class RejectedStore(ArtifactStore):
        def write_bytes(self, **kwargs):
            raise ValueError("private escaped filesystem path")

    evaluator = Evaluator()
    harness, _ = create(evaluator, store=RejectedStore(tmp_path))
    result = _invoke(harness)
    assert result.output["reason"] == "artifact_containment_violation"
    assert not evaluator.requests
    assert not result.artifact_refs
    assert "private" not in result.model_dump_json()
    assert len(state.calls) == 1


def test_budget_refusal_uses_actual_sdk_frame_before_persistence(sdk_inspection, tmp_path):
    state, create = sdk_inspection
    evaluator = Evaluator()
    policy = AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive", budget=AssertionBudget(max_image_pixels=47))
    harness, _ = create(evaluator, policy=policy)
    result = _invoke(harness)
    assert result.output["reason"] == "budget_exceeded"
    assert not evaluator.requests
    assert not list(tmp_path.rglob("*.png"))
    assert len(state.calls) == 1


def test_cancelled_callback_propagates_and_keeps_captured_evidence(sdk_inspection, tmp_path):
    state, create = sdk_inspection
    evaluator = Evaluator(failure=CancelledError())
    harness, _ = create(evaluator)
    with pytest.raises(CancelledError):
        _invoke(harness)
    assert len(list(tmp_path.rglob("*.png"))) == 1
    assert len(state.calls) == 1


def test_nonrecorded_observation_retains_sdk_evidence_without_generic_screenshot(sdk_inspection, tmp_path):
    state, create = sdk_inspection
    evaluator = Evaluator()
    harness, _ = create(evaluator)
    recorder = EvidenceRecorder(run_id="inspection", output_dir=tmp_path)
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"), evidence_sink=recorder, capture_action_before=False)
    result = runner.run_step("inspection", _step())
    assert result.status == "passed"
    screenshots = [event for event in runner.events if event.event_type == "artifact_captured" and event.payload.get("kind") == "screenshot"]
    assert len(screenshots) == 1
    assert len(state.calls) == 1
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    assert len([artifact for artifact in recovered.artifacts if artifact.kind == "screenshot"]) == 1


def test_inspection_alias_and_observation_metadata_are_catalog_driven(sdk_inspection):
    _, create = sdk_inspection
    harness, _ = create(Evaluator())
    definition = harness._capability_for("inspectWithAI")
    assert definition.name == "inspect_with_ai"
    assert definition.step_kind == "observation"
    assert definition.metadata["driver_method"] == "inspect_with_ai"


@pytest.mark.parametrize("resolver", [lambda context, sequence: None, lambda context, sequence: "not a trusted policy"])
def test_missing_or_incompatible_frame_authorization_fails_closed(sdk_inspection, resolver):
    state, create = sdk_inspection
    evaluator = Evaluator()
    harness, _ = create(evaluator, resolver=resolver)
    result = _invoke(harness)
    assert result.output["reason"] == "privacy_blocked"
    assert not state.calls
    assert not evaluator.requests


def test_each_inspection_requires_new_context_and_capture_sequence(sdk_inspection):
    state, create = sdk_inspection
    evaluator = Evaluator()
    decisions = []

    def resolver(context, sequence):
        decisions.append((context.metadata["capture_invocation"]["step_execution_id"], sequence))
        return AndroidVisualAssertionPolicy(allow_provider_upload=sequence == 1, frame_sensitivity="non_sensitive")

    harness, _ = create(evaluator, resolver=resolver)
    assert _invoke(harness).status == "passed"
    assert _invoke(harness).output["reason"] == "privacy_blocked"
    assert decisions == [("inspect-1", 1), ("inspect-1", 2)]
    assert len(evaluator.requests) == 1
    assert len(state.calls) == 1


def test_capture_age_is_checked_after_persistence_before_upload(sdk_inspection, tmp_path, monkeypatch):
    from fsq_agent.drivers.android import _visual_inspection

    state, create = sdk_inspection
    clock = [100]
    monkeypatch.setattr(_visual_inspection, "time", SimpleNamespace(monotonic_ns=lambda: clock[0]))

    class SlowStore(ArtifactStore):
        def write_bytes(self, **kwargs):
            ref = super().write_bytes(**kwargs)
            clock[0] = 200
            return ref

    evaluator = Evaluator()
    policy = AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive", budget=AssertionBudget(max_capture_age_ns=1))
    harness, _ = create(evaluator, policy=policy, store=SlowStore(tmp_path))
    result = _invoke(harness)
    assert result.output["reason"] == "capture_stale"
    assert not evaluator.requests
    assert len(result.artifact_refs) == 1
    assert len(state.calls) == 1


def test_provider_operational_failure_keeps_safe_dispatch_provenance(sdk_inspection):
    from fsq_agent.agent_engine import EngineError

    _, create = sdk_inspection
    error = EngineError("network", "safe provider failure")
    error.fsq_provider_provenance = {"provider_upload_occurred": None, "request_state": "response_unknown", "raw_error": "private payload"}
    harness, _ = create(Evaluator(failure=error))
    result = _invoke(harness)
    assert result.metadata["provider_provenance"] == {"provider_upload_occurred": None, "request_state": "response_unknown"}
    assert "private" not in result.model_dump_json()
    assert len(result.artifact_refs) == 1


def test_invalid_arguments_never_echo_image_or_credential_payloads(sdk_inspection):
    state, create = sdk_inspection
    harness, _ = create(Evaluator())
    result = _invoke(harness, png_bytes=b"private-image-and-credential-payload")
    assert result.failure_category == "configuration_error"
    assert "private-image-and-credential-payload" not in result.model_dump_json()
    assert not state.calls


def test_custom_caller_cancellation_settles_sdk_worker_and_preserves_identity(sdk_inspection):
    import time

    class TaskCancelledError(RuntimeError):
        pass

    class InflightEvaluator(Evaluator):
        def inspect(self, request, *, cancellation_check=None):
            self.requests.append(request)
            try:
                while True:
                    cancellation_check()
                    time.sleep(0.001)
            except CancelledError as error:
                error.fsq_provider_provenance = {"provider_upload_occurred": None, "request_state": "response_unknown"}
                raise

    state, create = sdk_inspection
    evaluator = InflightEvaluator()
    harness, _ = create(evaluator)
    interruption = TaskCancelledError("private cancellation payload")
    caller_thread = threading.get_ident()

    def cancel():
        assert threading.get_ident() == caller_thread
        if evaluator.requests:
            raise interruption

    context = HarnessContext(platform="android")
    context.cancellation_check = cancel
    with pytest.raises(TaskCancelledError) as caught:
        harness.invoke_action(_step(), context)
    assert caught.value is interruption
    assert caught.value.fsq_provider_provenance == {"provider_upload_occurred": None, "request_state": "response_unknown"}
    assert len(state.calls) == 1


@pytest.mark.parametrize("stage", ["selection", "dispatch", "response", "cancellation"])
def test_production_provider_state_reaches_runner_and_durable_evidence(sdk_inspection, tmp_path, stage):
    import asyncio

    from fsq_agent.agent_engine import EngineError, ModelResult
    from fsq_agent.ai_services import AIAssertionEvaluator
    from fsq_agent.providers import ModelProviderSession
    from fsq_agent.providers._client_config import ProviderClientConfig

    state, create = sdk_inspection
    dispatched = threading.Event()
    closed = []

    class TaskCancelledError(RuntimeError):
        pass

    interruption = TaskCancelledError("private caller cancellation")

    class Provider:
        def get_model(self, model):
            if stage == "selection":
                raise EngineError("authentication", "private selection failure")
            return self

        async def complete(self, request):
            dispatched.set()
            if stage == "dispatch":
                raise EngineError("network", "private provider failure")
            if stage == "cancellation":
                await asyncio.Future()
            return ModelResult("unparsed response", parsed_output=None)

        async def aclose(self):
            closed.append(True)

    session = ModelProviderSession(
        ProviderClientConfig(provider="openai", model="selected", api_key="synthetic-secret", base_url="https://api.openai.com/v1/"),
        provider_factory=lambda **kwargs: Provider(),
    )
    harness, _ = create(AIAssertionEvaluator(session))
    recorder = EvidenceRecorder(run_id="inspection", output_dir=tmp_path)

    def cancel():
        if stage == "cancellation" and dispatched.is_set():
            raise interruption

    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"), evidence_sink=recorder, cancellation_check=cancel)
    expected = {
        "provider_upload_occurred": False if stage == "selection" else True if stage == "response" else None,
        "request_state": {"selection": "not_sent", "response": "response_received"}.get(stage, "response_unknown"),
    }
    if stage == "cancellation":
        with pytest.raises(TaskCancelledError) as caught:
            runner.run_step("inspection", _step())
        assert caught.value is interruption
        assert caught.value.fsq_provider_provenance == expected
    else:
        result = runner.run_step("inspection", _step())
        assert result.status == "failed"
        invoke = next(phase for phase in result.phase_reports if phase.phase == "invoke")
        assert invoke.metadata["harness_metadata"]["provider_provenance"] == expected
        assert "private" not in result.model_dump_json()
        assert "synthetic-secret" not in result.model_dump_json()
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    assert len([artifact for artifact in recovered.artifacts if artifact.kind == "screenshot"]) == 1
    saved_invoke = next(phase for phase in recovered.steps[0].phase_reports if phase.phase == "invoke")
    saved_metadata = saved_invoke.metadata if stage == "cancellation" else saved_invoke.metadata["harness_metadata"]
    assert saved_metadata["provider_provenance"] == expected
    assert len(state.calls) == 1
    assert closed == [True]
