# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import io
import struct
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from PIL import Image, UnidentifiedImageError

from fsq_agent.harnesses._visual import _terminal_ref
from fsq_agent.models import AndroidVisualAssertionPolicy, HarnessArtifactRef, HarnessContext, HostCaptureEnvelopeV1

if TYPE_CHECKING:
    from pydantic import BaseModel


@dataclass(frozen=True)
class _HostVisualAssertionInvocation:
    run_id: str
    step_id: str
    step_execution_id: str
    capture_sequence: int
    params: BaseModel
    context: HarnessContext


@dataclass(frozen=True)
class _HostVisualAssertionCapture:
    invocation: _HostVisualAssertionInvocation
    policy: AndroidVisualAssertionPolicy
    artifact: HarnessArtifactRef


class _CaptureRejectedError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _png_image(data: bytes, budget: Any) -> Image.Image:
    if len(data) > budget.max_image_bytes:
        raise _CaptureRejectedError("budget_exceeded")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise _CaptureRejectedError("capture_failed")
    offset = 8
    dimensions: tuple[int, int] | None = None
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


def _capture_host_visual(
    driver: Any,
    store: Any,
    invocation: _HostVisualAssertionInvocation,
    policy: AndroidVisualAssertionPolicy,
    evaluator_available: bool,
    phase: str,
) -> HarnessArtifactRef:
    context = invocation.context
    step_id = invocation.step_id
    step_execution_id = invocation.step_execution_id
    params: Any = invocation.params
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
    started = time.monotonic_ns()
    try:
        raw = driver.screenshot()
        if not isinstance(raw, (bytes, bytearray)):
            return _terminal_ref(step_id, "capture_failed", capture_failed=True)
        data = bytes(raw)
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
                step_execution_id=step_execution_id,
                capture_sequence=invocation.capture_sequence,
                sdk_session_id=context.metadata.get("sdk_session_id") or context.session_id,
                host_capture_started_monotonic_ns=started,
                host_capture_finished_monotonic_ns=finished,
                captured_wall_clock=datetime.now(UTC).isoformat(),
                display_query_monotonic_ns=None,
                png_sha256=hashlib.sha256(data).hexdigest(),
                png_size_bytes=len(data),
                image_width=width,
                image_height=height,
                display_width=None,
                display_height=None,
                pointer_mapping_state="unverified",
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
        return _terminal_ref(step_id, error.code, capture_failed=error.code in {"capture_failed", "frame_not_single"})
    except Exception as error:
        if getattr(error, "fsq_evidence_fatal", False):
            raise
        diagnostics = getattr(error, "context", {}).get("capture_diagnostics", {}) if isinstance(getattr(error, "context", None), dict) else {}
        return _terminal_ref(step_id, "capture_failed", diagnostics=diagnostics, capture_failed=True)
    if check is not None:
        check()
    try:
        ref = store.write_bytes(kind="screenshot", step_id=step_id, phase=phase, name="assert-with-ai", data=data)
    except ValueError:
        return _terminal_ref(step_id, "artifact_containment_violation")
    return HarnessArtifactRef.model_validate(ref.model_dump(exclude={"step_id", "step_execution_id", "phase"})).model_copy(
        update={
            "metadata": {**ref.metadata, "capture": envelope.model_dump(mode="json"), "deterministic_evidence": []},
        }
    )
