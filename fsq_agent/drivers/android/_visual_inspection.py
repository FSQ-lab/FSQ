# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import io
import struct
import time
from asyncio import CancelledError
from threading import Event

from PIL import Image, UnidentifiedImageError

from fsq_agent.models import AIVisualInspectionRequest, AIVisualInspectionResult, HarnessArtifactRef


class _CaptureRejectedError(Exception):
    def __init__(self, code):
        self.code = code


def _inspection_image_size(data, budget):
    # The public SDK already validates PNG framing, CRCs and single-frame status.
    # Independently bound the actual callback pixels before decoding/persistence.
    if len(data) > budget.max_image_bytes:
        raise _CaptureRejectedError("budget_exceeded")
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise _CaptureRejectedError("capture_failed")
    width, height = struct.unpack_from(">II", data, 16)
    if not width or not height:
        raise _CaptureRejectedError("capture_failed")
    if height > budget.max_image_height or width * height > budget.max_image_pixels:
        raise _CaptureRejectedError("budget_exceeded")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise _CaptureRejectedError("frame_not_single")
            if image.size != (width, height):
                raise _CaptureRejectedError("capture_failed")
            image.load()
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise _CaptureRejectedError("capture_failed") from error
    return width, height


_INPUT_REASONS = frozenset({"privacy_blocked", "budget_exceeded", "capture_stale", "capture_failed", "region_invalid", "frame_not_single"})
_SDK_REASONS = frozenset(
    {
        "pointer_mapping_unavailable",
        "invalid_visual_response",
        "frame_not_single",
        "observation_too_large",
        "operation_timeout",
        "session_busy",
        "session_closed",
        "session_uncertain",
        "execution_unknown",
    }
)


def _provider_reason(error):
    reason = getattr(error, "reason", None)
    if reason in _INPUT_REASONS:
        return reason
    return {
        "invalid_output": "invalid_output",
        "configuration": "provider_unavailable",
        "authentication": "provider_auth",
        "rate_limit": "provider_rate_limited",
        "refusal": "provider_refusal",
        "timeout": "provider_timeout",
    }.get(getattr(error, "category", None), "provider_error")


def _failure(reason, provider=None, diagnostics=None):
    refs = [provider.artifact] if provider is not None and provider.artifact is not None else []
    capture = provider.capture if provider is not None else None
    return {
        "status": "failed",
        "failure_category": "observation_error",
        "error_message": "Android visual inspection is unavailable.",
        "output": {"reason": reason, "summary": None, "candidates": [], "capture": capture},
        "artifact_refs": refs,
        "metadata": {"reason": reason, **(diagnostics or {}), **({"provider_provenance": provider.provenance} if provider is not None and provider.provenance else {})},
    }


class _InspectionProvider:
    def __init__(self, runtime, invocation, params, cancellation, started):
        self.runtime = runtime
        self.invocation = invocation
        self.params = params
        self.cancellation = cancellation
        self.started = started
        self.artifact = None
        self.capture = None
        self.reason = None
        self.provenance = {}
        self.calls = 0

    def checkpoint(self):
        # Only the owner-worker Event crosses threads. The existing worker
        # bridge polls the trusted callback on its caller and preserves identity.
        if self.cancellation.is_set():
            raise CancelledError()

    def inspect(self, request):
        from appium_connect_agent.automation import Bounds, VisualImageMatch, VisualProviderResponse

        try:
            return self._inspect(request, Bounds, VisualImageMatch, VisualProviderResponse)
        except _CaptureRejectedError as error:
            self.reason = error.code
            raise

    def _inspect(self, request, bounds_type, match_type, response_type):
        self.calls += 1
        if self.calls != 1:
            raise _CaptureRejectedError("invalid_visual_response")
        self.checkpoint()
        policy = self.invocation.visual_assertion_policy
        budget = policy.budget
        if not policy.allow_provider_upload or policy.frame_sensitivity != "non_sensitive":
            raise _CaptureRejectedError("privacy_blocked")
        width, height = _inspection_image_size(request.png_bytes, budget)
        finished = time.monotonic_ns()
        if (width, height) != (request.image_width, request.image_height):
            raise _CaptureRejectedError("capture_failed")
        if self.started <= 0 or finished < self.started or finished - self.started > budget.max_capture_span_ns:
            raise _CaptureRejectedError("capture_stale")
        digest = hashlib.sha256(request.png_bytes).hexdigest()
        identity = self.invocation.context.metadata["capture_invocation"]
        self.capture = {
            "schema_version": "visual_inspection_capture/v1",
            "source_tool": "inspect_with_ai",
            "run_id": identity["run_id"],
            "step_id": self.invocation.step_id,
            "step_execution_id": identity["step_execution_id"],
            "capture_sequence": identity["capture_sequence"],
            "capture_started_monotonic_ns": self.started,
            "capture_finished_monotonic_ns": finished,
            "image_width": width,
            "image_height": height,
            "png_sha256": digest,
            "png_size_bytes": len(request.png_bytes),
            "region": self.params.region.model_dump(mode="json") if self.params.region is not None else None,
        }
        self.checkpoint()
        try:
            ref = self.runtime.artifact_store.write_bytes(kind="screenshot", step_id=self.invocation.step_id, phase="invoke", name="inspect-with-ai", data=request.png_bytes)
        except ValueError as error:
            raise _CaptureRejectedError("artifact_containment_violation") from error
        except OSError as error:
            raise _CaptureRejectedError("capture_failed") from error
        self.artifact = HarnessArtifactRef.model_validate(ref.model_dump(exclude={"step_id", "step_execution_id", "phase"})).model_copy(update={"metadata": {**ref.metadata, "capture": self.capture}})
        if self.artifact.sha256 != digest:
            raise _CaptureRejectedError("capture_failed")
        self.checkpoint()
        now = time.monotonic_ns()
        if now < finished or now - finished > budget.max_capture_age_ns:
            raise _CaptureRejectedError("capture_stale")
        remaining = budget.timeout_seconds - (now - self.started) / 1_000_000_000
        if request.remaining_timeout_seconds is not None:
            remaining = min(remaining, request.remaining_timeout_seconds)
        if remaining <= 0:
            raise _CaptureRejectedError("budget_exceeded")
        neutral = AIVisualInspectionRequest(
            png_bytes=request.png_bytes,
            prompt=request.prompt,
            mode=request.mode,
            region=self.params.region,
            max_candidates=request.max_candidates,
            image_width=width,
            image_height=height,
            image_sha256=digest,
            timeout_seconds=remaining,
            budget=budget,
            upload_authorized=policy.allow_provider_upload,
            frame_sensitivity=policy.frame_sensitivity,
            capture_started_monotonic_ns=self.started,
            capture_finished_monotonic_ns=finished,
        )
        try:
            result = self.runtime.ai_assertion_evaluator.inspect(neutral, cancellation_check=self.checkpoint)
        except BaseException as error:  # Retain dispatch facts without converting cancellation.
            if isinstance(error, Exception):
                self.reason = _provider_reason(error)
            provenance = getattr(error, "fsq_provider_provenance", None)
            if (
                isinstance(provenance, dict)
                and "provider_upload_occurred" in provenance
                and (provenance["provider_upload_occurred"] is None or type(provenance["provider_upload_occurred"]) is bool)
                and provenance.get("request_state") in {"not_sent", "response_received", "response_unknown"}
            ):
                self.provenance = {name: provenance[name] for name in ("provider_upload_occurred", "request_state")}
            raise
        if not isinstance(result, AIVisualInspectionResult):
            raise _CaptureRejectedError("invalid_output")
        return response_type(
            summary=result.summary,
            candidates=tuple(match_type(label=candidate.label, image_bounds=bounds_type(**candidate.image_bounds.model_dump()), confidence=candidate.confidence) for candidate in result.candidates),
        )


def _run_visual_inspection(driver, params):
    runtime = getattr(driver, "_ai_assertion_tool_runtime", None)
    invocation = getattr(driver, "_ai_assertion_tool_invocation", None)
    if runtime is None or invocation is None:
        return _failure("privacy_blocked")
    check = invocation.context.cancellation_check
    if check is not None:
        check()
    policy = invocation.visual_assertion_policy
    if policy is None or not policy.allow_provider_upload or policy.frame_sensitivity != "non_sensitive":
        return _failure("privacy_blocked")
    evaluator = runtime.ai_assertion_evaluator
    if policy.provider_available is False or not callable(getattr(evaluator, "inspect", None)):
        return _failure("provider_unavailable")
    if runtime.artifact_store is None:
        return _failure("capture_failed")
    if len(params.prompt.encode("utf-8")) > policy.budget.max_prompt_bytes:
        return _failure("budget_exceeded")
    cancellation = Event()
    provider = _InspectionProvider(runtime, invocation, params, cancellation, time.monotonic_ns())

    interruptions = []

    def check_on_caller():
        if check is not None:
            try:
                check()
            except BaseException as error:
                interruptions.append(error)
                raise

    def inspect_on_owner():
        from appium_connect_agent.automation import ImageRegion
        from appium_connect_agent.runtime import OperationControl

        session = driver._ensure_session()
        provider.started = time.monotonic_ns()
        return session.device.inspect_with_ai(
            params.prompt,
            provider=provider,
            mode=params.mode,
            region=ImageRegion(**params.region.model_dump()) if params.region is not None else None,
            max_candidates=params.max_candidates,
            control=OperationControl(timeout_seconds=policy.budget.timeout_seconds, cancellation=cancellation),
        )

    try:
        inspection = driver._run_on_worker(inspect_on_owner, cancellation_check=check_on_caller, cancellation_event=cancellation)
        check_on_caller()
        if provider.artifact is None or provider.calls != 1 or inspection.png_sha256 != provider.capture["png_sha256"]:
            return _failure("capture_failed", provider)
        # Serialization only: all pointer geometry below is produced by the SDK.
        output = {
            "summary": inspection.summary,
            "mode": inspection.mode,
            "image_size": {"width": inspection.image_size.width, "height": inspection.image_size.height},
            "pointer_size": {"width": inspection.pointer_size.width, "height": inspection.pointer_size.height} if inspection.pointer_size is not None else None,
            "png_sha256": inspection.png_sha256,
            "region": params.region.model_dump(mode="json") if params.region is not None else None,
            "candidates": [
                {
                    "label": candidate.label,
                    "image_bounds": {name: getattr(candidate.image_bounds, name) for name in ("x", "y", "width", "height")},
                    "pointer_bounds": {name: getattr(candidate.pointer_bounds, name) for name in ("x", "y", "width", "height")},
                    "pointer_center": {"x": candidate.pointer_center.x, "y": candidate.pointer_center.y},
                    "confidence": candidate.confidence,
                }
                for candidate in inspection.candidates
            ],
            "capture": provider.capture,
        }
    except BaseException as error:
        if interruptions:
            interruption = interruptions[0]
            if provider.provenance:
                interruption.fsq_provider_provenance = dict(provider.provenance)
            raise interruption from None
        if not isinstance(error, Exception):
            if provider.provenance:
                error.fsq_provider_provenance = dict(provider.provenance)
            raise
        driver._propagate_if_cancelled(error)
        code = getattr(error, "code", None)
        reason = provider.reason or (code if code in _SDK_REASONS else "capture_failed")
        diagnostics = driver._safe_diagnostics(error, stage="inspect_with_ai")
        return _failure(reason, provider, diagnostics)
    return {"status": "passed", "output": output, "artifact_refs": [provider.artifact]}
