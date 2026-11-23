# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import hashlib
import io
import json
import time

import pytest
from PIL import Image
from pydantic import ValidationError

from fsq_agent.agent_engine import EngineError, ImageContent, ModelRequest, ModelResult
from fsq_agent.ai_services import AIAssertionEvaluator
from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol, AIVisualInspectionEvaluatorProtocol
from fsq_agent.models import (
    ANDROID_ACTION_DEFINITIONS_BY_NAME,
    WEB_ACTION_DEFINITIONS_BY_NAME,
    AIVisualCandidate,
    AIVisualInspectionRequest,
    AIVisualInspectionResult,
    AndroidInspectWithAIParams,
    AssertionBudget,
    VisualImageBounds,
    VisualRect,
    WebInspectWithAIParams,
    WindowsInspectWithAIParams,
)
from fsq_agent.providers import ModelProviderSession
from fsq_agent.providers._client_config import ProviderClientConfig


class _Session:
    def __init__(self, payload=None, *, failure=None, cleanup=None, on_complete=None):
        self.payload = payload if payload is not None else {"summary": " Readable heading ", "candidates": []}
        self.failure = failure
        self.cleanup = cleanup
        self.on_complete = on_complete
        self.requests = []
        self.controls = []
        self.closes = 0

    def complete_sync(self, request: ModelRequest, **controls):
        self.requests.append(request)
        self.controls.append(controls)
        if self.on_complete:
            self.on_complete()
        if self.failure:
            raise self.failure
        assert request.output is not None
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        try:
            parsed = request.output.parse(text)
        except (ValueError, TypeError):
            raise EngineError("invalid_output", "private parser details") from None
        return ModelResult(text=text, parsed_output=parsed)

    def close_sync(self):
        self.closes += 1
        if self.cleanup:
            raise self.cleanup


def _request(**updates):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, format="PNG")
    image = buffer.getvalue()
    now = time.monotonic_ns()
    values = {
        "png_bytes": image,
        "prompt": "Read the heading",
        "image_width": 40,
        "image_height": 30,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "capture_started_monotonic_ns": now - 2,
        "capture_finished_monotonic_ns": now - 1,
        "upload_authorized": True,
        "frame_sensitivity": "non_sensitive",
    }
    values.update(updates)
    return AIVisualInspectionRequest(**values)


def _candidate(**updates):
    values = {"label": " Heading ", "image_bounds": {"x": 4, "y": 5, "width": 10, "height": 8}, "confidence": None}
    values.update(updates)
    return values


def test_inspection_parameters_and_catalog_are_observation_only():
    params = AndroidInspectWithAIParams(prompt=" Read text ")
    assert params.prompt == "Read text"
    assert params.mode == "extract"
    assert params.max_candidates == 5
    assert params.region is None
    definition = ANDROID_ACTION_DEFINITIONS_BY_NAME["inspectWithAI"]
    assert definition.driver_method == "inspect_with_ai"
    assert definition.params_model is AndroidInspectWithAIParams
    assert definition.step_kind == "observation"

    web_params = WebInspectWithAIParams(prompt=" Read Web chart ")
    assert web_params.prompt == "Read Web chart"
    assert web_params.mode == "extract"
    web_def = WEB_ACTION_DEFINITIONS_BY_NAME["inspectWithAI"]
    assert web_def.driver_method == "inspect_with_ai"
    assert web_def.params_model is WebInspectWithAIParams
    assert web_def.step_kind == "observation"

    win_params = WindowsInspectWithAIParams(prompt=" Read Windows chart ")
    assert win_params.prompt == "Read Windows chart"
    assert win_params.mode == "extract"

@pytest.mark.parametrize(
    "updates",
    [
        {"prompt": "  "},
        {"prompt": "界" * 1366},
        {"mode": "tap"},
        {"max_candidates": 0},
        {"max_candidates": True},
        {"max_candidates": 101},
        {"upload_authorized": True},
        {"point": {"x": 1, "y": 2}},
        {"region": {"left": 5, "top": 0, "right": 4, "bottom": 1}},
        {"region": {"left": 0, "top": False, "right": 4, "bottom": 1}},
    ],
)
def test_inspection_rejects_invalid_authored_parameters(updates):
    with pytest.raises(ValidationError):
        AndroidInspectWithAIParams.model_validate({"prompt": "Read", **updates})


def test_valid_extract_uses_one_strict_tool_free_request_and_shared_protocol():
    session = _Session()
    evaluator = AIAssertionEvaluator(session, reasoning_effort="high")
    assert isinstance(evaluator, AIVisualInspectionEvaluatorProtocol)
    assert isinstance(evaluator, AIAssertionEvaluatorProtocol)
    request = _request()
    result = evaluator.inspect(request)
    assert result == AIVisualInspectionResult(summary="Readable heading")
    assert len(session.requests) == 1
    call = session.requests[0]
    assert call.reasoning_effort == "high"
    assert isinstance(call.input[0].content[1], ImageContent)
    assert call.input[0].content[1].data is request.png_bytes
    assert session.closes == 1
    assert "png_bytes" not in request.model_dump()
    assert "png_bytes" not in repr(request)
    assert not hasattr(result, "passed")
    assert call.output.schema["additionalProperties"] is False
    assert set(call.output.schema["required"]) == {"summary", "candidates"}

    for platform, expected_label in (("windows", "Windows"), ("web", "Web")):
        plat_session = _Session()
        plat_req = _request(platform=platform)
        plat_res = AIAssertionEvaluator(plat_session).inspect(plat_req)
        assert plat_res == AIVisualInspectionResult(summary="Readable heading")
        assert expected_label in plat_session.requests[0].input[0].content[0].text

def test_valid_locate_returns_full_image_bounds_in_requested_region():
    session = _Session({"summary": "Found heading", "candidates": [_candidate()]})
    request = _request(mode="locate", region=VisualRect(left=3, top=4, right=30, bottom=25))
    result = AIAssertionEvaluator(session).inspect(request)
    assert result.candidates == (AIVisualCandidate(label="Heading", image_bounds=VisualImageBounds(x=4, y=5, width=10, height=8)),)
    assert "full-image" in session.requests[0].input[0].content[0].text
    assert set(result.candidates[0].model_dump()) == {"label", "image_bounds", "confidence"}


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "Found", "candidates": [_candidate(image_bounds={"x": -1, "y": 1, "width": 2, "height": 2})]},
        {"summary": "Found", "candidates": [_candidate(image_bounds={"x": True, "y": 1, "width": 2, "height": 2})]},
        {"summary": "Found", "candidates": [_candidate(image_bounds={"x": 1, "y": 1, "width": 0, "height": 2})]},
        {"summary": "Found", "candidates": [_candidate(image_bounds={"x": 39, "y": 1, "width": 2, "height": 2})]},
        {"summary": "Found", "candidates": [_candidate(point={"x": 1, "y": 2})]},
        {"summary": "Found", "candidates": [_candidate(image_bounds={"x": 1, "y": 1, "width": 2, "height": 2, "tap": True})]},
        {"summary": "Found", "candidates": [_candidate(confidence=float("nan"))]},
        {"summary": "Found", "candidates": [{"label": "target", "image_bounds": {"x": 1, "y": 1, "width": 2, "height": 2}}]},
        {"summary": "Found", "candidates": [], "action": "tap"},
        {"summary": " ", "candidates": []},
        {"summary": "Found"},
        '```json\n{"summary":"Found","candidates":[]}\n```',
        "[]",
    ],
)
def test_inspection_rejects_malformed_or_executable_model_output(payload):
    session = _Session(payload)
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(session).inspect(_request(mode="locate"))
    assert failure.value.category == "invalid_output"
    assert "private" not in str(failure.value)
    assert len(session.requests) == 1
    assert session.closes == 1


@pytest.mark.parametrize("updates", [{}, {"mode": "locate", "max_candidates": 1}, {"mode": "locate", "region": VisualRect(left=10, top=10, right=30, bottom=25)}])
def test_inspection_enforces_extract_count_and_region(updates):
    candidates = [_candidate(), _candidate()]
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(_Session({"summary": "Found", "candidates": candidates})).inspect(_request(**updates))
    assert failure.value.category == "invalid_output"


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"upload_authorized": False}, "privacy_blocked"),
        ({"frame_sensitivity": "unknown"}, "privacy_blocked"),
        ({"frame_sensitivity": "sensitive"}, "privacy_blocked"),
        ({"png_bytes": b"private invalid image"}, "capture_failed"),
        ({"image_sha256": "0" * 64}, "capture_failed"),
        ({"image_width": 41}, "capture_failed"),
        ({"region": VisualRect(left=0, top=0, right=41, bottom=1)}, "region_invalid"),
        ({"budget": AssertionBudget(max_image_bytes=1)}, "budget_exceeded"),
        ({"budget": AssertionBudget(max_image_pixels=1)}, "budget_exceeded"),
        ({"budget": AssertionBudget(max_prompt_bytes=1)}, "budget_exceeded"),
        ({"capture_started_monotonic_ns": 1, "capture_finished_monotonic_ns": 2}, "capture_stale"),
    ],
)
def test_inspection_input_gates_never_upload(updates, reason):
    session = _Session()
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(session).inspect(_request(**updates))
    assert failure.value.reason == reason
    assert not session.requests
    assert session.closes == 1


def test_inspection_rejects_response_observed_after_deadline(monkeypatch):
    request = _request(timeout_seconds=0.1)
    clock = [time.monotonic()]
    monkeypatch.setattr("fsq_agent.ai_services._visual_inspection.time.monotonic", lambda: clock[0])
    session = _Session(on_complete=lambda: clock.__setitem__(0, clock[0] + 1))
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(session).inspect(request)
    assert failure.value.category == "timeout"
    assert session.controls[0]["deadline_monotonic"] == pytest.approx(clock[0] - 0.9)
    assert session.closes == 1


@pytest.mark.parametrize("cancellation", [asyncio.CancelledError(), KeyboardInterrupt(), RuntimeError("trusted cancellation")])
def test_inspection_cancellation_preserves_identity_and_cleanup(cancellation):
    session = _Session(cleanup=RuntimeError("cleanup"))

    def cancel():
        raise cancellation

    with pytest.raises(type(cancellation)) as failure:
        AIAssertionEvaluator(session).inspect(_request(), cancellation_check=cancel)
    assert failure.value is cancellation
    assert not session.requests
    assert session.closes == 1


@pytest.mark.parametrize("category", ["refusal", "timeout", "authentication", "rate_limit", "invalid_output"])
def test_inspection_safe_provider_failure_wins_over_cleanup(category):
    session = _Session(failure=EngineError(category, "private model response"), cleanup=RuntimeError("private cleanup"))
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(session).inspect(_request())
    assert failure.value.category == category
    assert "private" not in str(failure.value)
    assert failure.value.__cause__ is None
    assert session.closes == 1


def test_inspection_cleanup_only_failure_cannot_be_success():
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(_Session(cleanup=RuntimeError("private cleanup"))).inspect(_request())
    assert failure.value.category == "cleanup"
    assert "private" not in str(failure.value)


def test_inspection_requires_engine_parsed_output():
    class UnparsedSession(_Session):
        def complete_sync(self, request, **controls):
            return ModelResult(text='{"summary":"Readable","candidates":[]}')

    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(UnparsedSession()).inspect(_request())
    assert failure.value.category == "invalid_output"


def test_inspection_concurrency_does_not_close_active_session():
    session = _Session()
    evaluator = AIAssertionEvaluator(session)
    evaluator._evaluation_lock.acquire()
    try:
        with pytest.raises(EngineError) as failure:
            evaluator.inspect(_request())
        assert failure.value.reason == "budget_exceeded"
        assert not session.requests
        assert session.closes == 0
    finally:
        evaluator._evaluation_lock.release()


def test_inspection_session_is_reusable_and_explicit_close_delegates():
    session = _Session()
    evaluator = AIAssertionEvaluator(session)
    assert evaluator.inspect(_request()).summary == "Readable heading"
    assert evaluator.inspect(_request()).summary == "Readable heading"
    evaluator.close()
    assert session.closes == 3


def test_inspection_rechecks_freshness_immediately_before_upload(monkeypatch):
    import fsq_agent.ai_services._visual_inspection as implementation

    request = _request()
    now = [request.capture_finished_monotonic_ns + 1]
    monkeypatch.setattr(implementation.time, "monotonic_ns", lambda: now[0])
    original = implementation._model_input

    def age_while_preparing(*args):
        value = original(*args)
        now[0] += request.budget.max_capture_age_ns + 1
        return value

    monkeypatch.setattr(implementation, "_model_input", age_while_preparing)
    session = _Session()
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(session).inspect(request)
    assert failure.value.reason == "capture_stale"
    assert not session.requests
    assert session.closes == 1


def test_inspection_normalizes_native_provider_timeout():
    session = _Session(failure=TimeoutError("private timeout details"))
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(session).inspect(_request())
    assert failure.value.category == "timeout"
    assert "private" not in str(failure.value)
    assert session.closes == 1


def _production_session(factory):
    return ModelProviderSession(
        ProviderClientConfig(provider="openai", model="selected", api_key="synthetic-secret", base_url="https://api.openai.com/v1/"),
        provider_factory=factory,
    )


@pytest.mark.parametrize("stage", ["input", "construction", "selection"])
def test_production_inspection_preserves_not_sent_provenance(stage):
    calls = []
    original = EngineError("authentication", "private provider details")
    original.fsq_provider_provenance = {"provider_upload_occurred": True, "request_state": "response_received", "raw_error": "private metadata"}

    class Provider:
        def get_model(self, model):
            calls.append("model")
            raise original

        async def aclose(self):
            calls.append("cleanup")

    def factory(**kwargs):
        calls.append("provider")
        if stage == "construction":
            raise original
        return Provider()

    session = _production_session(factory)
    with pytest.raises(EngineError) as caught:
        AIAssertionEvaluator(session).inspect(_request(upload_authorized=stage != "input"))
    assert caught.value.fsq_provider_provenance == {"provider_upload_occurred": False, "request_state": "not_sent"}
    assert caught.value is not original
    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert calls == {"input": [], "construction": ["provider"], "selection": ["provider", "model", "cleanup"]}[stage]


@pytest.mark.parametrize("control", ["deadline", "cancellation", "cooperative_exception"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_production_inspection_retains_unknown_dispatch_and_settles_before_escape(control, cleanup_fails):
    calls = []
    interruption = RuntimeError("private cooperative cancellation") if control == "cooperative_exception" else asyncio.CancelledError("private cancellation")
    interruption.fsq_provider_provenance = {"raw_error": "private stale metadata"}

    def check_cancellation():
        if control != "deadline" and "request" in calls:
            raise interruption

    class Provider:
        def get_model(self, model):
            calls.append("model")
            return self

        async def complete(self, request):
            calls.append("request")
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                calls.append("settled")
                return ModelResult("private late response")

        async def aclose(self):
            calls.append("cleanup")
            if cleanup_fails:
                raise EngineError("cleanup", "private secondary cleanup")

    session = _production_session(lambda **kwargs: Provider())
    expected_error = EngineError if control == "deadline" else type(interruption)
    with pytest.raises(expected_error) as caught:
        AIAssertionEvaluator(session).inspect(_request(timeout_seconds=0.1), cancellation_check=check_cancellation)
    assert calls == ["model", "request", "settled", "cleanup"]
    assert caught.value.fsq_provider_provenance == {"provider_upload_occurred": None, "request_state": "response_unknown"}
    if control == "deadline":
        assert caught.value.category == "timeout"
        assert caught.value.reason == "response_unknown"
        assert "private" not in str(caught.value)
    else:
        assert caught.value is interruption


@pytest.mark.parametrize("stage", ["invalid_output", "provider_failure", "provider_cleanup", "service_cleanup", "cancellation", "cleanup_interruption", "service_cleanup_interruption"])
def test_production_inspection_tracks_received_response_through_validation_and_cleanup(stage):
    calls = []
    interruption = asyncio.CancelledError("private interruption")
    interruption.fsq_provider_provenance = {"raw_error": "private stale metadata"}
    provider_count = 0

    class Provider:
        def __init__(self, retained):
            self.retained = retained

        def get_model(self, model):
            return self

        async def complete(self, request):
            calls.append("request")
            if stage == "provider_failure":
                error = EngineError("refusal", "private provider refusal")
                error.fsq_provider_provenance = {"raw_error": "private injected metadata"}
                raise error
            text = '{"summary":"Readable","candidates":[]}'
            return ModelResult(text, parsed_output=None if stage == "invalid_output" else request.output.parse(text))

        async def aclose(self):
            calls.append("retained-cleanup" if self.retained else "cleanup")
            if stage == "provider_cleanup" or (stage == "service_cleanup" and self.retained):
                raise EngineError("cleanup", "private cleanup details")
            if stage == "cleanup_interruption" or (stage == "service_cleanup_interruption" and self.retained):
                raise interruption

    def factory(**kwargs):
        nonlocal provider_count
        provider_count += 1
        return Provider(retained=stage.startswith("service_cleanup") and provider_count == 1)

    def check_cancellation():
        if stage == "cancellation" and "cleanup" in calls:
            raise interruption

    session = _production_session(factory)
    if stage.startswith("service_cleanup"):
        session.get_model()
    expected_error = asyncio.CancelledError if stage in {"cancellation", "cleanup_interruption", "service_cleanup_interruption"} else EngineError
    with pytest.raises(expected_error) as caught:
        AIAssertionEvaluator(session).inspect(_request(), cancellation_check=check_cancellation)
    received = stage != "provider_failure"
    assert caught.value.fsq_provider_provenance == {"provider_upload_occurred": True if received else None, "request_state": "response_received" if received else "response_unknown"}
    assert calls == ["request", "cleanup", *(["retained-cleanup"] if stage.startswith("service_cleanup") else [])]
    if expected_error is asyncio.CancelledError:
        assert caught.value is interruption
    else:
        assert "private" not in str(caught.value)
        assert caught.value.category == {"invalid_output": "invalid_output", "provider_failure": "refusal", "provider_cleanup": "cleanup", "service_cleanup": "cleanup"}[stage]


def test_production_inspection_initial_cancellation_retains_identity_without_dispatch():
    calls = []
    interruption = asyncio.CancelledError("private cancellation")
    interruption.fsq_provider_provenance = {"raw_error": "private stale metadata"}

    def check_cancellation():
        raise interruption

    session = _production_session(lambda **kwargs: calls.append("provider"))
    with pytest.raises(asyncio.CancelledError) as caught:
        AIAssertionEvaluator(session).inspect(_request(), cancellation_check=check_cancellation)
    assert caught.value is interruption
    assert caught.value.fsq_provider_provenance == {"provider_upload_occurred": False, "request_state": "not_sent"}
    assert calls == []
