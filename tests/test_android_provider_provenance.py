# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_android_visual_capture import ImageDriver

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.agent_engine import EngineError, ModelResult
from fsq_agent.ai_services import AIAssertionEvaluator
from fsq_agent.core import ArtifactStore, EvidenceRecorder, StepRunner
from fsq_agent.harnesses._android import AndroidHarness
from fsq_agent.models import AndroidVisualAssertionPolicy, ExecutableStep
from fsq_agent.providers import ModelProviderSession
from fsq_agent.providers._client_config import ProviderClientConfig


def test_provider_cleanup_failure_remains_failed_with_durable_dispatch_facts(tmp_path):
    calls = []

    class Driver(ImageDriver):
        def close(self):
            calls.append("driver-close")

    class Provider:
        def get_model(self, model):
            return self

        async def complete(self, request):
            calls.append("request")
            text = '{"outcome":"passed","explanation":"The logo is blue.","confidence":null}'
            return ModelResult(text, parsed_output=request.output.parse(text))

        async def aclose(self):
            calls.append("cleanup")
            raise EngineError("cleanup", "Provider cleanup failed.")

    session = ModelProviderSession(
        ProviderClientConfig(provider="openai", model="selected", api_key="synthetic", base_url="https://api.openai.com/v1/"),
        provider_factory=lambda **kwargs: Provider(),
    )
    recorder = EvidenceRecorder(run_id="cleanup", output_dir=tmp_path)
    harness = AndroidHarness(
        driver=Driver(),
        artifact_store=ArtifactStore(tmp_path),
        ai_assertion_evaluator=AIAssertionEvaluator(session),
        visual_assertion_policy_resolver=lambda context, sequence: AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive"),
    )
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"), evidence_sink=recorder)
    try:
        result = runner.run_step(
            "cleanup",
            ExecutableStep(
                step_id="logo",
                kind="assertion",
                action_name="assert_with_ai",
                params={"assertion": "The logo is blue", "surface": "appearance", "full_screen_reason": "The logo fills this image"},
            ),
        )
    finally:
        harness.close()
    assert result.status == "failed"
    assert result.failure_category != "assertion_error"
    assert calls == ["request", "cleanup", "driver-close"]
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    step = recovered.steps[0]
    assert step.status == "failed"
    invoke = next(phase for phase in step.phase_reports if phase.phase == "invoke")
    assert invoke.metadata["provider_provenance"] == {"provider_upload_occurred": True, "request_state": "response_received"}


class TaskCancelledError(RuntimeError):
    pass


@pytest.fixture
def assertion_runner(tmp_path):
    harnesses = []

    def create(evaluator, *, name="provenance", cancellation_check=None):
        class Driver(ImageDriver):
            def close(self):
                self.calls.append(("close", None))

        run_dir = tmp_path / name
        harness = AndroidHarness(
            driver=Driver(),
            artifact_store=ArtifactStore(run_dir),
            ai_assertion_evaluator=evaluator,
            visual_assertion_policy_resolver=lambda context, sequence: AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive"),
        )
        harnesses.append(harness)
        runner = StepRunner(
            harness,
            capability_registry=build_capability_registry(platform="android"),
            evidence_sink=EvidenceRecorder(run_id=name, output_dir=run_dir),
            cancellation_check=cancellation_check,
        )
        return runner, run_dir

    yield create
    for harness in reversed(harnesses):
        harness.close()


def _assertion_step():
    return ExecutableStep(
        step_id="logo",
        kind="assertion",
        action_name="assert_with_ai",
        params={"assertion": "The logo is blue", "surface": "appearance", "full_screen_reason": "The logo fills this image"},
    )


@pytest.mark.parametrize("interruption_type", [asyncio.CancelledError, TaskCancelledError])
@pytest.mark.parametrize("stage", ["inflight", "inflight_cleanup_failure", "response_received"])
def test_provider_cancellation_retains_identity_and_durable_upload_disposition(assertion_runner, interruption_type, stage):
    calls = []
    interruption = interruption_type("private cancellation payload")
    interruption.fsq_provider_provenance = {"raw_error": "private stale metadata"}

    def cancellation_check():
        if (stage.startswith("inflight") and "request" in calls) or (stage == "response_received" and "cleanup" in calls):
            raise interruption

    class Provider:
        def get_model(self, model):
            calls.append("model")
            return self

        async def complete(self, request):
            calls.append("request")
            try:
                if stage.startswith("inflight"):
                    await asyncio.Future()
            except asyncio.CancelledError:
                calls.append("cancel-signal")
            finally:
                calls.append("settled")
            text = '{"outcome":"passed","explanation":"The logo is blue.","confidence":null}'
            return ModelResult(text, parsed_output=request.output.parse(text))

        async def aclose(self):
            calls.append("cleanup")
            if stage == "inflight_cleanup_failure":
                raise EngineError("cleanup", "private secondary cleanup payload")

    def factory(**kwargs):
        calls.append("provider")
        return Provider()

    session = ModelProviderSession(
        ProviderClientConfig(provider="openai", model="selected", api_key="synthetic-secret", base_url="https://api.openai.com/v1/"),
        provider_factory=factory,
    )
    runner, run_dir = assertion_runner(AIAssertionEvaluator(session), cancellation_check=cancellation_check)
    with pytest.raises(interruption_type) as caught:
        runner.run_step("provenance", _assertion_step())
    assert caught.value is interruption
    received = stage == "response_received"
    expected = {"provider_upload_occurred": True if received else None, "request_state": "response_received" if received else "response_unknown"}
    assert interruption.fsq_provider_provenance == expected
    assert calls == ["provider", "model", "request", *(["cancel-signal"] if stage.startswith("inflight") else []), "settled", "cleanup"]
    assert runner.last_step_result.status == "cancelled"
    assert runner.last_capability_execution_result is None
    recovered = EvidenceRecorder.recover_bundle(run_dir)
    step = recovered.steps[0]
    assert step.status == "cancelled"
    assert step.failure_category == "cancelled"
    invoke = next(phase for phase in step.phase_reports if phase.phase == "invoke")
    assert invoke.status == "cancelled"
    assert invoke.metadata["provider_provenance"] == expected
    assert "harness_metadata" not in invoke.metadata
    serialized = recovered.model_dump_json()
    for secret in ("private cancellation payload", "private stale metadata", "private secondary cleanup payload", "synthetic-secret"):
        assert secret not in serialized
    action_results = [event for event in runner.events if event.event_type == "action_result"]
    assert len(action_results) == 1
    assert action_results[0].payload["status"] == "cancelled"


@pytest.mark.parametrize("interruption_type", [asyncio.CancelledError, TaskCancelledError])
@pytest.mark.parametrize("busy", [False, True])
def test_gate_and_busy_evaluator_cancellation_persist_not_sent_without_closing_active_call(assertion_runner, interruption_type, busy):
    calls = []
    entered, release = threading.Event(), threading.Event()
    interruption = interruption_type("private gate cancellation")
    evaluating = False

    class Provider:
        def get_model(self, model):
            calls.append("model")
            return self

        async def complete(self, request):
            calls.append("request")
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
            calls.append("settled")
            text = '{"outcome":"passed","explanation":"The logo is blue.","confidence":null}'
            return ModelResult(text, parsed_output=request.output.parse(text))

        async def aclose(self):
            calls.append("cleanup")

    def factory(**kwargs):
        calls.append("provider")
        return Provider()

    session = ModelProviderSession(
        ProviderClientConfig(provider="openai", model="selected", api_key="synthetic-secret", base_url="https://api.openai.com/v1/"),
        provider_factory=factory,
    )
    evaluator = AIAssertionEvaluator(session)

    class CancelAtEvaluation:
        def evaluate(self, request, *, cancellation_check=None):
            nonlocal evaluating
            evaluating = True
            return evaluator.evaluate(request, cancellation_check=cancellation_check)

        def close(self):
            evaluator.close()

    def cancellation_check():
        if evaluating:
            raise interruption

    runner, run_dir = assertion_runner(CancelAtEvaluation(), cancellation_check=cancellation_check)
    with ThreadPoolExecutor(max_workers=1) as executor:
        active = None
        try:
            if busy:
                active_runner, active_dir = assertion_runner(evaluator, name="active")
                active = executor.submit(active_runner.run_step, "active", _assertion_step())
                assert entered.wait(5)
            with pytest.raises(interruption_type) as caught:
                runner.run_step("provenance", _assertion_step())
            assert caught.value is interruption
            assert calls == (["provider", "model", "request"] if busy else [])
            if active is not None:
                assert not active.done()
        finally:
            release.set()
            if active is not None:
                assert active.result(timeout=5).status == "passed"
    expected = {"provider_upload_occurred": False, "request_state": "not_sent"}
    assert interruption.fsq_provider_provenance == expected
    recovered = EvidenceRecorder.recover_bundle(run_dir)
    step = recovered.steps[0]
    assert step.status == "cancelled"
    invoke = next(phase for phase in step.phase_reports if phase.phase == "invoke")
    assert invoke.status == "cancelled"
    assert invoke.metadata["provider_provenance"] == expected
    assert runner.last_capability_execution_result is None
    assert calls == (["provider", "model", "request", "settled", "cleanup"] if busy else [])
    if busy:
        active_step = EvidenceRecorder.recover_bundle(active_dir).steps[0]
        active_invoke = next(phase for phase in active_step.phase_reports if phase.phase == "invoke")
        metadata = active_invoke.metadata["harness_metadata"]["ai_assertion"]["metadata"]
        assert metadata["provider_upload_occurred"] is True
        assert metadata["request_state"] == "response_received"


@pytest.mark.parametrize("failure_kind", ["cleanup", "cancelled", "interrupted"])
def test_final_session_close_escape_retains_current_safe_provenance(assertion_runner, monkeypatch, failure_kind):
    calls = []
    failure = {
        "cleanup": EngineError("cleanup", "Model provider cleanup failed."),
        "cancelled": asyncio.CancelledError("private final cancellation payload"),
        "interrupted": SystemExit("private final interruption payload"),
    }[failure_kind]
    failure.fsq_provider_provenance = {"raw_error": "private stale metadata"}

    class Provider:
        def get_model(self, model):
            return self

        async def complete(self, request):
            calls.append("request")
            text = '{"outcome":"passed","explanation":"The logo is blue.","confidence":null}'
            return ModelResult(text, parsed_output=request.output.parse(text))

        async def aclose(self):
            calls.append("cleanup")

    session = ModelProviderSession(
        ProviderClientConfig(provider="openai", model="selected", api_key="synthetic-secret", base_url="https://api.openai.com/v1/"),
        provider_factory=lambda **kwargs: Provider(),
    )
    runner, run_dir = assertion_runner(AIAssertionEvaluator(session))

    def fail_final_close():
        calls.append("final-close")
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(session, "close_sync", fail_final_close)
        if failure_kind == "cleanup":
            result = runner.run_step("provenance", _assertion_step())
            assert result.status == "failed"
            assert result.failure_category != "assertion_error"
        else:
            with pytest.raises(type(failure)) as caught:
                runner.run_step("provenance", _assertion_step())
            assert caught.value is failure
    expected = {"provider_upload_occurred": True, "request_state": "response_received"}
    assert failure.fsq_provider_provenance == expected
    assert calls == ["request", "cleanup", "final-close"]
    recovered = EvidenceRecorder.recover_bundle(run_dir)
    step = recovered.steps[0]
    assert step.status == {"cleanup": "failed", "cancelled": "cancelled", "interrupted": "incomplete"}[failure_kind]
    invoke = next(phase for phase in step.phase_reports if phase.phase == "invoke")
    assert invoke.status == step.status
    assert invoke.metadata["provider_provenance"] == expected
    assert runner.last_capability_execution_result is None
    serialized = recovered.model_dump_json()
    for secret in ("private final cancellation payload", "private final interruption payload", "private stale metadata", "synthetic-secret"):
        assert secret not in serialized
