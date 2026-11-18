# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import io
import json
import struct
import time
import uuid
import zlib
from datetime import UTC, datetime

from PIL import Image, UnidentifiedImageError

from fsq_agent.models import AndroidInspectElementParams, DeterministicFact, HarnessArtifactRef, HostCaptureEnvelopeV1


class _CaptureRejectedError(Exception):
    def __init__(self, code):
        self.code = code


def _terminal_ref(step_id, code, *, diagnostics=None, conflict=False, facts=()):
    diagnostics = {key: value for key, value in (diagnostics or {}).items() if key in {"backend_code", "request_state", "session_state", "stage"}}
    outcome = "failed" if conflict else "inconclusive"
    result = {
        "status": outcome,
        "outcome": outcome,
        "passed": False,
        "explanation": "Deterministic evidence contradicts the expectation." if conflict else f"Visual assertion is unverified ({code}).",
        "error_code": None if conflict else code,
        "deterministic_conflict": conflict,
        "capture": None,
        "metadata": {"diagnostics": diagnostics or {}, "deterministic_evidence": list(facts), "provider_upload_occurred": False},
    }
    return HarnessArtifactRef(
        artifact_id=f"visual-{step_id}-{uuid.uuid4().hex}",
        kind="screenshot",
        availability="not_applicable",
        unavailable_reason=code,
        metadata={"visual_assertion": result, "capture_required": False},
    )


def _subject(expect):
    if expect.target and expect.target.strip():
        return expect.target
    if expect.locator is not None:
        return json.dumps(expect.locator.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return None


def _facts(driver, params, run_id, step_id):
    expect = params.expect
    if expect is None or expect.property not in {"text", "desc", "enabled", "checked", "selected", "clickable", "focused", "displayed"}:
        return []
    subject = _subject(expect)
    if subject is None:
        return []
    observed = driver.inspect_element(AndroidInspectElementParams(target=expect.target, locator=expect.locator, attributes=[expect.property]))
    attribute = observed.get("attributes", {}).get(expect.property, {}) if isinstance(observed, dict) else {}
    state = attribute.get("state", "unknown")
    if state not in {"value", "unknown", "not_applicable", "missing"}:
        state = "unknown"
    value = attribute.get("value") if state == "value" else None
    if type(value) not in {str, bool, int, float, type(None)}:
        state, value = "unknown", None
    return [DeterministicFact(subject=subject, property=expect.property, value=value, state=state, run_id=run_id, step_execution_id=step_id, observed_monotonic_ns=time.monotonic_ns())]


def _png_image(data, budget):
    if len(data) > budget.max_image_bytes:
        raise _CaptureRejectedError("budget_exceeded")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise _CaptureRejectedError("capture_failed")
    offset = 8
    dimensions = None
    ended = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise _CaptureRejectedError("capture_failed")
        size = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(data):
            raise _CaptureRejectedError("capture_failed")
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise _CaptureRejectedError("frame_not_single")
        if zlib.crc32(memoryview(data)[offset + 4 : end - 4]) & 0xFFFFFFFF != struct.unpack_from(">I", data, end - 4)[0]:
            raise _CaptureRejectedError("capture_failed")
        if offset == 8:
            if kind != b"IHDR" or size != 13:
                raise _CaptureRejectedError("capture_failed")
            dimensions = struct.unpack_from(">II", data, offset + 8)
            width, height = dimensions
            if not width or not height:
                raise _CaptureRejectedError("capture_failed")
            if height > budget.max_image_height or width * height > budget.max_image_pixels:
                raise _CaptureRejectedError("budget_exceeded")
        elif kind == b"IHDR":
            raise _CaptureRejectedError("capture_failed")
        offset = end
        if kind == b"IEND":
            if size or offset != len(data):
                raise _CaptureRejectedError("capture_failed")
            ended = True
            break
    if not ended or dimensions is None:
        raise _CaptureRejectedError("capture_failed")
    image = None
    try:
        image = Image.open(io.BytesIO(data))
        if image.format != "PNG" or getattr(image, "n_frames", 1) != 1 or image.size != dimensions:
            image.close()
            raise _CaptureRejectedError("frame_not_single")
        image.load()
    except (OSError, ValueError, UnidentifiedImageError) as error:
        if image is not None:
            image.close()
        raise _CaptureRejectedError("capture_failed") from error
    return image


def _capture_visual(driver, store, invocation, policy, evaluator_available, phase):
    context = invocation.context
    step_id = invocation.step_id
    params = invocation.params
    run_id = invocation.run_id
    check = context.cancellation_check
    if check is not None:
        check()
    available = policy.provider_available if policy.provider_available is not None else evaluator_available
    if not available:
        return _terminal_ref(step_id, "provider_unavailable")
    if not policy.allow_provider_upload or policy.frame_sensitivity != "non_sensitive":
        return _terminal_ref(step_id, "privacy_blocked")
    if not evaluator_available:
        return _terminal_ref(step_id, "provider_unavailable")
    if params.expect is not None and params.expect.kind in {"exact_color", "display_identity"}:
        return _terminal_ref(step_id, "color_semantics_unverified" if params.expect.kind == "exact_color" else "display_identity_unverified")
    if store is None:
        return _terminal_ref(step_id, "capture_failed")
    try:
        facts = _facts(driver, params, run_id, step_id)
    except Exception as error:  # noqa: BLE001 - optional SDK observations have no shared exception hierarchy.
        return _terminal_ref(step_id, "capture_failed", diagnostics=getattr(error, "context", {}).get("capture_diagnostics", {}))
    exact = [fact for fact in facts if fact.state == "value" and fact.exact]
    if exact:
        conflict = any(type(fact.value) is not type(params.expect.value) or fact.value != params.expect.value for fact in exact)
        return _terminal_ref(step_id, "unsupported_surface", conflict=conflict, facts=[fact.model_dump(mode="json") for fact in facts])
    started = time.monotonic_ns()
    try:
        data = driver.screenshot({"cancellation_check": check, "timeout_seconds": min(20.0, policy.budget.timeout_seconds)})
        with _png_image(data, policy.budget) as image:
            finished = time.monotonic_ns()
            width, height = image.size
            rect = params.region.rect if params.region is not None else None
            if rect is not None:
                if not (0 <= rect.left < rect.right <= width and 0 <= rect.top < rect.bottom <= height):
                    return _terminal_ref(step_id, "region_invalid")
                if (rect.right - rect.left) * (rect.bottom - rect.top) > policy.budget.max_image_pixels:
                    return _terminal_ref(step_id, "region_too_large")
            if finished < started or finished - started > policy.budget.max_capture_span_ns:
                return _terminal_ref(step_id, "capture_stale")
            envelope = HostCaptureEnvelopeV1(
                run_id=run_id,
                step_id=step_id,
                step_execution_id=invocation.step_execution_id,
                capture_sequence=invocation.capture_sequence,
                sdk_session_id=context.metadata.get("sdk_session_id"),
                host_capture_started_monotonic_ns=started,
                host_capture_finished_monotonic_ns=finished,
                captured_wall_clock=datetime.now(UTC).isoformat(),
                png_sha256=hashlib.sha256(data).hexdigest(),
                png_size_bytes=len(data),
                image_width=width,
                image_height=height,
                display_width=context.screen_size[0] if context.screen_size else None,
                display_height=context.screen_size[1] if context.screen_size else None,
                region=rect,
            )
            if rect is not None:
                output = io.BytesIO()
                with image.crop((rect.left, rect.top, rect.right, rect.bottom)) as cropped:
                    cropped.save(output, format="PNG")
                data = output.getvalue()
                if len(data) > policy.budget.max_image_bytes:
                    return _terminal_ref(step_id, "budget_exceeded")
    except _CaptureRejectedError as error:
        return _terminal_ref(step_id, error.code)
    except Exception as error:  # noqa: BLE001 - Normalize optional backend failures without leaking SDK data.
        diagnostics = getattr(error, "context", {}).get("capture_diagnostics", {})
        return _terminal_ref(step_id, "capture_failed", diagnostics=diagnostics)
    if check is not None:
        check()
    try:
        ref = store.write_bytes(kind="screenshot", step_id=step_id, phase=phase, name="assert-with-ai", data=data)
    except ValueError:
        return _terminal_ref(step_id, "artifact_containment_violation")
    return HarnessArtifactRef.model_validate(ref.model_dump(exclude={"step_id", "step_execution_id", "phase"})).model_copy(
        update={
            "metadata": {**ref.metadata, "capture": envelope.model_dump(mode="json"), "deterministic_evidence": [fact.model_dump(mode="json") for fact in facts]},
        }
    )
