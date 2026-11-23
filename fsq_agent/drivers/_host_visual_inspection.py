# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import io
import struct
import time
from typing import Any

from PIL import Image, UnidentifiedImageError

from fsq_agent.models import AIVisualInspectionRequest, AIVisualInspectionResult, ConfigurationError, HarnessArtifactRef, ToolExecutionError

_PLATFORM_LABELS: dict[str, str] = {
    "web": "Web",
    "windows": "Windows",
}

_INPUT_REASONS = frozenset({"privacy_blocked", "budget_exceeded", "capture_stale", "capture_failed", "region_invalid", "frame_not_single", "artifact_containment_violation"})
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
        "session_unavailable",
        "execution_unknown",
    }
)


class _CaptureRejectedError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _inspection_image_size(data: bytes, budget: Any) -> tuple[int, int]:
    if len(data) > budget.max_image_bytes:
        raise _CaptureRejectedError("budget_exceeded")
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise _CaptureRejectedError("capture_failed")
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise _CaptureRejectedError("frame_not_single")
        offset += 12 + length
        if offset > len(data):
            raise _CaptureRejectedError("capture_failed")
        if kind == b"IEND":
            break
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


def _provider_reason(error: Exception) -> str:
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


def _safe_diagnostics(error: BaseException, *, stage: str) -> dict[str, object]:
    diagnostics: dict[str, object] = {"stage": stage, "error_type": type(error).__name__}
    if isinstance(error, TimeoutError):
        diagnostics["reason"] = "operation_timeout"
    return diagnostics


def _failure(
    reason: str,
    *,
    platform: str,
    artifact: HarnessArtifactRef | None = None,
    capture: dict[str, object] | None = None,
    provenance: dict[str, object] | None = None,
    diagnostics: dict[str, object] | None = None,
) -> dict[str, object]:
    label = _PLATFORM_LABELS.get(platform, platform)
    refs = [artifact] if artifact is not None else []
    return {
        "status": "failed",
        "failure_category": "observation_error",
        "error_message": f"{label} visual inspection is unavailable.",
        "output": {"reason": reason, "summary": None, "candidates": [], "capture": capture},
        "artifact_refs": refs,
        "metadata": {
            "reason": reason,
            **(diagnostics or {}),
            **({"provider_provenance": provenance} if provenance else {}),
        },
    }


def _run_host_visual_inspection(driver: Any, params: Any, *, platform: str) -> dict[str, object]:
    runtime = getattr(driver, "_ai_assertion_tool_runtime", None)
    invocation = getattr(driver, "_ai_assertion_tool_invocation", None)
    if runtime is None or invocation is None:
        return _failure("privacy_blocked", platform=platform)
    check = invocation.context.cancellation_check
    if check is not None:
        check()
    policy = invocation.visual_assertion_policy
    if policy is None or not policy.allow_provider_upload or policy.frame_sensitivity != "non_sensitive":
        return _failure("privacy_blocked", platform=platform)
    if params.mode == "locate":
        return _failure("pointer_mapping_unavailable", platform=platform)
    evaluator = runtime.ai_assertion_evaluator
    if policy.provider_available is False or not callable(getattr(evaluator, "inspect", None)):
        return _failure("provider_unavailable", platform=platform)
    if runtime.artifact_store is None:
        return _failure("capture_failed", platform=platform)
    budget = policy.budget
    if len(params.prompt.encode("utf-8")) > budget.max_prompt_bytes:
        return _failure("budget_exceeded", platform=platform)

    artifact: HarnessArtifactRef | None = None
    capture: dict[str, object] | None = None
    provenance: dict[str, object] = {}
    interruptions: list[BaseException] = []

    def check_on_caller() -> None:
        if check is not None:
            try:
                check()
            except BaseException as error:
                interruptions.append(error)
                raise
    def _reject(code: str) -> Any:
        raise _CaptureRejectedError(code)

    try:
        check_on_caller()
        started = time.monotonic_ns()
        try:
            png_bytes = driver.screenshot()
        except TimeoutError as error:
            raise _CaptureRejectedError("operation_timeout") from error
        except RuntimeError as error:
            message = str(error).lower()
            if "not started" in message or "not launched" in message or "not available" in message:
                raise _CaptureRejectedError("session_unavailable") from error
            raise _CaptureRejectedError("capture_failed") from error
        except Exception as error:
            raise _CaptureRejectedError("capture_failed") from error
        if not isinstance(png_bytes, (bytes, bytearray)):
            _reject("capture_failed")
        png_bytes = bytes(png_bytes)
        width, height = _inspection_image_size(png_bytes, budget)
        finished = time.monotonic_ns()
        if started <= 0 or finished < started or finished - started > budget.max_capture_span_ns:
            _reject("capture_stale")
        region = params.region
        if region is not None and not (0 <= region.left < region.right <= width and 0 <= region.top < region.bottom <= height):
            _reject("region_invalid")
        digest = hashlib.sha256(png_bytes).hexdigest()
        identity = invocation.context.metadata.get("capture_invocation")
        if not isinstance(identity, dict):
            _reject("capture_failed")
        capture = {
            "schema_version": "visual_inspection_capture/v1",
            "source_tool": "inspect_with_ai",
            "run_id": identity["run_id"],
            "step_id": invocation.step_id,
            "step_execution_id": identity["step_execution_id"],
            "capture_sequence": identity["capture_sequence"],
            "capture_started_monotonic_ns": started,
            "capture_finished_monotonic_ns": finished,
            "image_width": width,
            "image_height": height,
            "png_sha256": digest,
            "png_size_bytes": len(png_bytes),
            "region": region.model_dump(mode="json") if region is not None else None,
        }
        check_on_caller()
        try:
            ref = runtime.artifact_store.write_bytes(
                kind="screenshot",
                step_id=invocation.step_id,
                phase="invoke",
                name="inspect-with-ai",
                data=png_bytes,
            )
        except ValueError as error:
            raise _CaptureRejectedError("artifact_containment_violation") from error
        except OSError as error:
            raise _CaptureRejectedError("capture_failed") from error
        artifact = HarnessArtifactRef.model_validate(ref.model_dump(exclude={"step_id", "step_execution_id", "phase"})).model_copy(
            update={"metadata": {**ref.metadata, "capture": capture}}
        )
        if artifact.sha256 != digest:
            _reject("capture_failed")
        check_on_caller()
        now = time.monotonic_ns()
        if now < finished or now - finished > budget.max_capture_age_ns:
            _reject("capture_stale")
        remaining = budget.timeout_seconds - (now - started) / 1_000_000_000
        if remaining <= 0:
            _reject("budget_exceeded")
        neutral = AIVisualInspectionRequest(
            platform=platform,
            png_bytes=png_bytes,
            prompt=params.prompt,
            mode=params.mode,
            region=region,
            max_candidates=params.max_candidates,
            image_width=width,
            image_height=height,
            image_sha256=digest,
            timeout_seconds=remaining,
            budget=budget,
            upload_authorized=policy.allow_provider_upload,
            frame_sensitivity=policy.frame_sensitivity,
            capture_started_monotonic_ns=started,
            capture_finished_monotonic_ns=finished,
        )
        try:
            result = evaluator.inspect(neutral, cancellation_check=check_on_caller)
        except BaseException as error:
            raw_prov = getattr(error, "fsq_provider_provenance", None)
            if (
                isinstance(raw_prov, dict)
                and "provider_upload_occurred" in raw_prov
                and (raw_prov["provider_upload_occurred"] is None or type(raw_prov["provider_upload_occurred"]) is bool)
                and raw_prov.get("request_state") in {"not_sent", "response_received", "response_unknown"}
            ):
                provenance = {name: raw_prov[name] for name in ("provider_upload_occurred", "request_state")}
            raise
        if not isinstance(result, AIVisualInspectionResult):
            _reject("invalid_output")
        check_on_caller()
        output = {
            "summary": result.summary,
            "mode": params.mode,
            "image_size": {"width": width, "height": height},
            "pointer_size": None,
            "png_sha256": digest,
            "region": region.model_dump(mode="json") if region is not None else None,
            "candidates": [],
            "capture": capture,
        }
    except BaseException as error:
        if interruptions:
            interruption = interruptions[0]
            if provenance:
                interruption.fsq_provider_provenance = dict(provenance)  # type: ignore[attr-defined]
            raise interruption from None
        if isinstance(error, ConfigurationError):
            raise
        if not isinstance(error, Exception):
            if provenance:
                error.fsq_provider_provenance = dict(provenance)  # type: ignore[attr-defined]
            raise
        if isinstance(error, _CaptureRejectedError):
            reason = error.code
        elif isinstance(error, ToolExecutionError):
            code = getattr(error, "code", None) or error.context.get("reason")
            reason = str(code) if code in _SDK_REASONS else "capture_failed"
        else:
            reason = _provider_reason(error)
        diagnostics = _safe_diagnostics(error, stage="inspect_with_ai")
        return _failure(reason, platform=platform, artifact=artifact, capture=capture, provenance=provenance, diagnostics=diagnostics)
    return {"status": "passed", "output": output, "artifact_refs": [artifact]}
