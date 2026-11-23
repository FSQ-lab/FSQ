# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import hashlib
import io
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from fsq_agent.agent_engine import EngineError, ImageContent, ModelRequest, ModelResult, TextContent, TokenUsage
from fsq_agent.ai_services import AIAssertionEvaluator
from fsq_agent.models import AIAssertionRequest, ConfigurationError, HarnessArtifactRef, HostCaptureEnvelopeV1


class _Session:
    provider = "azure_openai"
    model = "selected-model"

    def __init__(self, response: ModelResult | BaseException, cleanup_error: BaseException | None = None) -> None:
        self.response = response
        self.cleanup_error = cleanup_error
        self.metadata = {"endpoint_family": "azure_openai"}
        self.requests: list[ModelRequest] = []
        self.closed = False

    def complete_sync(self, request: ModelRequest, **controls: object) -> ModelResult:
        self.requests.append(request)
        observe = controls.get("on_request_state")
        if callable(observe):
            observe("response_unknown")
        if isinstance(self.response, BaseException):
            raise self.response
        assert request.output is not None
        try:
            parsed = request.output.parse(self.response.text)
        except (ValueError, TypeError):
            raise EngineError("invalid_output", "Invalid model output.") from None
        if callable(observe):
            observe("response_received")
        return replace(self.response, parsed_output=parsed)

    def close_sync(self) -> None:
        self.closed = True
        if self.cleanup_error is not None:
            raise self.cleanup_error


def _valid_png_bytes(width: int = 16, height: int = 12, color: str = "blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _authorized_request(tmp_path: Path, *, platform: str = "web", prompt: str = "The banner is visible") -> tuple[AIAssertionRequest, bytes]:
    root = tmp_path / f"run-{platform}"
    root.mkdir(exist_ok=True)
    pixels = _valid_png_bytes()
    relative = Path("artifacts/screen.png")
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_bytes(pixels)
    now = time.monotonic_ns()
    sha256 = hashlib.sha256(pixels).hexdigest()
    capture = HostCaptureEnvelopeV1(
        run_id=f"run-{platform}",
        step_id="source-step",
        step_execution_id="execution-one",
        capture_sequence=1,
        host_capture_started_monotonic_ns=now - 1000,
        host_capture_finished_monotonic_ns=now,
        captured_wall_clock="2026-01-01T00:00:00Z",
        png_sha256=sha256,
        png_size_bytes=len(pixels),
        image_width=16,
        image_height=12,
    )
    artifact = HarnessArtifactRef(artifact_id="screen", kind="screenshot", path=relative, sha256=sha256, size_bytes=len(pixels))
    return (
        AIAssertionRequest(
            platform=platform,
            prompt=prompt,
            surface="image_content",
            run_id=f"run-{platform}",
            step_id="execution-one",
            run_dir=root,
            screenshot_path=relative,
            screenshot_artifact_ref=artifact,
            capture=capture,
            upload_authorized=True,
            frame_sensitivity="non_sensitive",
        ),
        pixels,
    )


@pytest.mark.parametrize("platform", ["android", "ios", "macos", "windows", "web"])
@pytest.mark.parametrize("outcome", ["passed", "failed", "inconclusive"])
def test_visual_assertion_uses_neutral_image_and_preserves_tristate_verdict(tmp_path: Path, platform: str, outcome: str) -> None:
    request, _pixels = _authorized_request(tmp_path, platform=platform)
    session = _Session(ModelResult(text=json.dumps({"outcome": outcome, "explanation": "Visible", "confidence": 0.8}), usage=TokenUsage(2, 3, 5)))

    result = AIAssertionEvaluator(session).evaluate(request)

    assert result.status == result.outcome == outcome
    assert result.passed is (outcome == "passed")
    assert result.explanation == "Visible"
    assert result.model == "selected-model"
    assert result.token_usage == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert len(session.requests) == 1
    assert not isinstance(session.requests[0].input, str)
    content = session.requests[0].input[0].content
    assert isinstance(content[0], TextContent)
    assert "The banner is visible" in content[0].text
    assert isinstance(content[1], ImageContent)
    assert content[1].mime_type == "image/png"
    assert set(session.requests[0].output.schema["properties"]) == {"outcome", "explanation", "confidence"}
    assert session.closed


@pytest.mark.parametrize("text", ["", " \n", "not JSON", "[]", "null", '{"passed":false,"explanation":"Absent"}', '{"passed":true,"explanation":"Visible","confidence":0.8}'])
def test_unparseable_or_legacy_boolean_assertion_is_inconclusive_invalid_output(text: str, tmp_path: Path) -> None:
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(ModelResult(text=text))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.error_code == "invalid_output"
    assert not result.passed
    assert result.token_usage == {}
    assert len(session.requests) == 1
    assert session.closed


def test_assertion_model_failure_is_safe_inconclusive(tmp_path: Path) -> None:
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(EngineError("timeout", "Model operation timed out."))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.error_code == "provider_timeout"
    assert not result.passed
    assert session.closed


def test_assertion_configuration_error_maps_to_provider_unavailable_inconclusive(tmp_path: Path) -> None:
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(ConfigurationError("Missing provider"))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.error_code == "provider_unavailable"
    assert session.closed


@pytest.mark.parametrize("verdict", ["false", "true", "", 0, 1, 1.0, None, [], [False], {}, {"verdict": False}])
def test_assertion_rejects_invalid_outcome_values(verdict: object, tmp_path: Path) -> None:
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(ModelResult(text=json.dumps({"outcome": verdict, "explanation": "Model supplied an invalid verdict.", "confidence": None})))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.passed is False
    assert result.error_code == "invalid_output"
    assert len(session.requests) == 1
    assert session.closed


@pytest.mark.parametrize("outcome", ["failed", "passed", "inconclusive"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_assertion_accepts_tristate_outcomes_but_rejects_markdown(outcome: str, wrapped: bool, tmp_path: Path) -> None:
    request, _ = _authorized_request(tmp_path, prompt="Check")
    text = json.dumps({"outcome": outcome, "explanation": "Checked", "confidence": 0.8})
    session = _Session(ModelResult(text=f"```json\n{text}\n```" if wrapped else text))
    result = AIAssertionEvaluator(session).evaluate(request)
    expected = "inconclusive" if wrapped else outcome
    assert result.status == result.outcome == expected
    assert result.passed is (outcome == "passed" and not wrapped)
    if wrapped:
        assert result.error_code == "invalid_output"
    else:
        assert result.confidence == 0.8
    assert session.closed


@pytest.mark.parametrize("confidence", [None, 0, 1, 0.5])
def test_assertion_normalizes_text_and_accepts_nullable_finite_confidence(confidence, tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(ModelResult(json.dumps({"outcome": "failed", "explanation": " Absent \n", "confidence": confidence})))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "failed"
    assert result.explanation == "Absent"
    assert result.confidence == confidence


@pytest.mark.parametrize(
    "field,value",
    [
        ("explanation", " "),
        ("explanation", 1),
        ("explanation", None),
        ("confidence", True),
        ("confidence", "0.5"),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("confidence", float("-inf")),
        ("extra", "private-content"),
    ],
)
def test_assertion_rejects_invalid_fields(field, value, tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    payload = {"outcome": "passed", "explanation": "Visible", "confidence": None, field: value}
    result = AIAssertionEvaluator(_Session(ModelResult(json.dumps(payload)))).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.error_code == "invalid_output"
    assert "private-content" not in result.model_dump_json()


@pytest.mark.parametrize("field", ["outcome", "explanation", "confidence"])
def test_assertion_requires_every_field(field, tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    payload = {"outcome": "passed", "explanation": "Visible", "confidence": None}
    del payload[field]
    result = AIAssertionEvaluator(_Session(ModelResult(json.dumps(payload)))).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.error_code == "invalid_output"


@pytest.mark.parametrize(
    "category,reason,expected_code",
    [
        ("refusal", None, "provider_refusal"),
        ("incomplete", "content_filter", "provider_refusal"),
        ("configuration", None, "provider_unavailable"),
        ("authentication", None, "provider_auth"),
        ("rate_limit", None, "provider_rate_limited"),
        ("timeout", None, "provider_timeout"),
    ],
)
def test_assertion_preserves_safe_engine_failure_metadata(category, reason, expected_code, tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(EngineError(category, "private-model-content", reason=reason))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.error_code == expected_code
    assert result.metadata["engine_error"] == {"category": category, **({"reason": reason} if reason is not None else {})}
    assert result.metadata["provider_metadata"] == session.metadata
    assert result.artifact_refs == [request.screenshot_artifact_ref]
    assert result.latency_ms >= 0
    assert "private-model-content" not in result.model_dump_json()


@pytest.mark.parametrize("primary", [EngineError("invalid_output", "private-output"), RuntimeError("private-runtime")])
@pytest.mark.parametrize("cleanup", [RuntimeError("cleanup failure"), asyncio.CancelledError("cleanup cancellation")])
def test_assertion_converted_inconclusive_wins_over_cleanup(primary, cleanup, tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(primary, cleanup)
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert session.closed


@pytest.mark.parametrize("primary", [asyncio.CancelledError(), KeyboardInterrupt()])
def test_assertion_primary_interruption_propagates_or_cancels_cleanly(primary, tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(primary, RuntimeError("cleanup failure"))
    if isinstance(primary, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt) as failure:
            AIAssertionEvaluator(session).evaluate(request)
        assert failure.value is primary
    else:
        result = AIAssertionEvaluator(session).evaluate(request)
        assert result.status == result.outcome == "inconclusive"
        assert result.error_code == "cancelled"
    assert session.closed


def test_assertion_cleanup_only_failure_preserves_settled_verdict(tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    session = _Session(ModelResult('{"outcome":"passed","explanation":"Visible","confidence":null}'), EngineError("cleanup", "Cleanup failed."))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "passed"
    assert result.metadata["cleanup_failed"] is True
    assert session.closed


@pytest.mark.parametrize("outcome", ["failed", "passed"])
def test_assertion_cleanup_keyboard_interrupt_propagates_inside_unrelated_except(outcome, tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")
    cleanup = KeyboardInterrupt()
    session = _Session(ModelResult(json.dumps({"outcome": outcome, "explanation": "Checked", "confidence": None})), cleanup)
    try:
        int("unrelated handled error")
    except ValueError:
        with pytest.raises(KeyboardInterrupt) as failure:
            AIAssertionEvaluator(session).evaluate(request)
        assert failure.value is cleanup
    assert session.closed


def test_assertion_requires_parsed_object_even_when_text_is_valid(tmp_path: Path):
    request, _ = _authorized_request(tmp_path, prompt="Check")

    class UnparsedSession(_Session):
        def complete_sync(self, req, **_controls):
            self.requests.append(req)
            return self.response

    session = UnparsedSession(ModelResult('{"outcome":"passed","explanation":"Visible","confidence":null}'))
    result = AIAssertionEvaluator(session).evaluate(request)
    assert result.status == result.outcome == "inconclusive"
    assert result.error_code == "invalid_output"
