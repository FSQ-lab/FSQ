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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from PIL import Image, UnidentifiedImageError

from fsq_agent.models import (
    AndroidAssertWithAIParams,
    AndroidInspectElementParams,
    AndroidVisualAssertionPolicy,
    AssertionBudget,
    DeterministicFact,
    ExecutableStep,
    HarnessArtifactRef,
    HarnessContext,
    HostCaptureEnvelopeV1,
    IOSAssertWithAIParams,
    IOSInspectElementParams,
    MacOSAssertWithAIParams,
    MacOSInspectElementParams,
    StepPhase,
    ToolExecutionError,
)

if TYPE_CHECKING:
    from fsq_agent.core.evidence import ArtifactStore
    from fsq_agent.core.interfaces import DriverObservationInterface

_AssertionParams = AndroidAssertWithAIParams | IOSAssertWithAIParams | MacOSAssertWithAIParams
_CaptureArtifact = Callable[[str, str, HarnessContext, str, StepPhase], HarnessArtifactRef]

_ASSERTION_PARAMS: dict[str, type[_AssertionParams]] = {"android": AndroidAssertWithAIParams, "ios": IOSAssertWithAIParams, "macos": MacOSAssertWithAIParams}
_INSPECT_PROPERTIES = {
    "android": (AndroidInspectElementParams, frozenset({"text", "desc", "enabled", "checked", "selected", "clickable", "focused", "displayed"})),
    "ios": (IOSInspectElementParams, frozenset({"name", "label", "value", "enabled", "selected", "focused", "displayed", "accessible"})),
    "macos": (
        MacOSInspectElementParams,
        frozenset({"id", "type", "label", "title", "value", "placeholder_value", "text", "enabled", "focused", "selected", "hittable", "displayed"}),
    ),
}


@dataclass(frozen=True)
class _VisualAssertionCapture:
    run_id: str
    step_id: str
    step_execution_id: str
    params: _AssertionParams
    policy: AndroidVisualAssertionPolicy
    artifact: HarnessArtifactRef


class _ScopedVisualTools:
    """Run-local visual composition; eligibility never authorizes an image."""

    def __init__(
        self,
        platform: str,
        driver: DriverObservationInterface,
        store: ArtifactStore | None,
        evaluator: object,
        resolver: Callable[[HarnessContext, int], AndroidVisualAssertionPolicy | None] | None,
        budget: AssertionBudget | None,
        provider_available: bool | None,
    ) -> None:
        self.platform = platform
        self.driver = driver
        self.store = store
        self.evaluator = evaluator
        self.resolver = resolver
        self.budget = budget or AssertionBudget()
        self.provider_available = provider_available
        self._capture: _VisualAssertionCapture | None = None
        configure = getattr(driver, "configure_ai_assertion_tool", None)
        if callable(configure):
            configure(platform=platform, artifact_store=store, ai_assertion_evaluator=evaluator)

    @property
    def available_tools(self) -> tuple[str, ...]:
        if self.provider_available is False or self.store is None or not callable(self.resolver):
            return ()
        return tuple(name for name, method in (("assert_with_ai", "evaluate"), ("inspect_with_ai", "inspect")) if callable(getattr(self.evaluator, method, None)))

    def _frame_policy(self, context: HarnessContext, sequence: int) -> AndroidVisualAssertionPolicy:
        if context.cancellation_check is not None:
            context.cancellation_check()
        try:
            resolved = self.resolver(context, sequence) if self.resolver is not None else None
        except Exception:  # noqa: BLE001 - incompatible host decisions fail closed without disclosing resolver errors.
            resolved = None
        if context.cancellation_check is not None:
            context.cancellation_check()
        policy = resolved if isinstance(resolved, AndroidVisualAssertionPolicy) else AndroidVisualAssertionPolicy()
        available = policy.provider_available if policy.provider_available is not None else self.provider_available
        return policy.model_copy(
            update={
                "provider_available": False if self.provider_available is False else available,
                "budget": AssertionBudget(**{name: min(getattr(policy.budget, name), getattr(self.budget, name)) for name in AssertionBudget.model_fields}),
            }
        )

    def _capture_context(self, context: HarnessContext, identity: dict, step_id: str) -> tuple[HarnessContext, dict]:
        identity = {
            **identity,
            "run_id": identity.get("run_id") or (self.store.run_dir.name if self.store is not None else "unallocated"),
            "step_id": step_id,
            "step_execution_id": identity.get("step_execution_id") or step_id,
            "capture_sequence": self.store.allocate_capture_sequence() if self.store is not None else 1,
        }
        return context.model_copy(update={"metadata": {**context.metadata, "capture_invocation": identity}}), identity

    def capture_assertion(self, context: HarnessContext, identity: dict, step_id: str, phase: StepPhase) -> HarnessArtifactRef:
        self.discard_capture()
        context, identity = self._capture_context(context, identity, step_id)
        params = _ASSERTION_PARAMS[self.platform].model_validate(identity["params"])
        policy = self._frame_policy(context, identity["capture_sequence"])
        artifact = _capture_visual(self.driver, self.store, context, identity, params, policy, callable(getattr(self.evaluator, "evaluate", None)), phase, platform=self.platform)
        self._capture = _VisualAssertionCapture(identity["run_id"], step_id, identity["step_execution_id"], params, policy, artifact)
        return artifact

    def prepare(self, step: ExecutableStep, context: HarnessContext, action_name: str | None, capture_artifact: _CaptureArtifact) -> None:
        binding, self._capture = self._capture, None
        policy = None
        if action_name == "inspect_with_ai":
            context, identity = self._capture_context(
                context, {"run_id": step.metadata.get("run_id"), "step_execution_id": step.step_execution_id, "action_name": action_name, "params": step.params}, step.step_id
            )
            policy = self._frame_policy(context, identity["capture_sequence"])
        elif action_name == "assert_with_ai" and binding is not None:
            screenshot = next((ref for ref in step.metadata.get("prepared_artifacts", []) if ref.get("kind") == "screenshot"), None)
            if (
                binding.run_id == step.metadata.get("run_id")
                and binding.step_id == step.step_id
                and binding.step_execution_id == (step.step_execution_id or step.step_id)
                and binding.params == _ASSERTION_PARAMS[self.platform].model_validate(step.params)
                and screenshot is not None
                and screenshot.get("artifact_id") == binding.artifact.artifact_id
                and screenshot.get("metadata", {}).get("capture") == binding.artifact.metadata.get("capture")
            ):
                policy = binding.policy
        prepare = getattr(self.driver, "prepare_ai_assertion_tool_invocation", None)
        if callable(prepare):
            prepare(context=context, step_id=step.step_id, action_name=step.action_name, metadata=step.metadata, capture_artifact=capture_artifact, visual_assertion_policy=policy)

    def discard_capture(self) -> None:
        self._capture = None

    def clear(self) -> None:
        self.discard_capture()
        clear = getattr(self.driver, "clear_ai_assertion_tool_invocation", None)
        if callable(clear):
            clear()


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


def _facts(driver, params, run_id, step_execution_id, *, platform: str) -> list[DeterministicFact]:
    expect = params.expect
    inspect_params_cls, allowed_props = _INSPECT_PROPERTIES[platform]
    if expect is None or expect.property not in allowed_props:
        return []
    subject = _subject(expect)
    if subject is None:
        return []
    inspect = getattr(driver, "inspect_element", None)
    if not callable(inspect):
        return []
    observed = inspect(inspect_params_cls(target=expect.target, locator=expect.locator, attributes=(expect.property,)))
    if isinstance(observed, dict) and observed.get("status") == "failed":
        if platform != "macos":
            return []
        metadata = observed.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if observed.get("failure_category") != "target_resolution_error" or metadata.get("resolution_reason") != "not_found":
            raise ToolExecutionError("macOS deterministic visual pre-check failed.", context={"capture_diagnostics": metadata})
        state: Literal["value", "unknown", "not_applicable", "missing"] = "missing"
        value = None
    else:
        payload = observed.get("output", observed) if isinstance(observed, dict) else None
        attributes = payload.get("attributes") if isinstance(payload, dict) else None
        attribute = attributes.get(expect.property) if isinstance(attributes, dict) else None
        raw_state = attribute.get("state") if isinstance(attribute, dict) else None
        state = cast('Literal["value", "unknown", "not_applicable", "missing"]', raw_state) if raw_state in ("value", "unknown", "not_applicable", "missing") else "unknown"
        value = attribute.get("value") if isinstance(attribute, dict) and state == "value" else None
        if type(value) not in {str, bool, int, float, type(None)}:
            state, value = "unknown", None
    return [DeterministicFact(subject=subject, property=expect.property, value=value, state=state, run_id=run_id, step_execution_id=step_execution_id, observed_monotonic_ns=time.monotonic_ns())]


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


def _capture_visual(driver, store, context, identity, params, policy, evaluator_available, phase, *, platform: str) -> HarnessArtifactRef:
    step_id = identity["step_id"]
    run_id = identity["run_id"]
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
        facts = _facts(driver, params, run_id, identity["step_execution_id"], platform=platform)
    except Exception as error:  # noqa: BLE001 - optional SDK observations have no shared exception hierarchy.
        return _terminal_ref(step_id, "capture_failed", diagnostics=getattr(error, "context", {}).get("capture_diagnostics", {}))
    exact = [fact for fact in facts if fact.state == "value" and fact.exact]
    if exact:
        conflict = any(type(fact.value) is not type(params.expect.value) or fact.value != params.expect.value for fact in exact)
        return _terminal_ref(step_id, "unsupported_surface", conflict=conflict, facts=[fact.model_dump(mode="json") for fact in facts])
    started = time.monotonic_ns()
    try:
        capture = driver.capture_visual_screenshot if platform == "macos" else driver.screenshot
        data = capture({"cancellation_check": check, "timeout_seconds": min(20.0, policy.budget.timeout_seconds)})
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
                step_execution_id=identity["step_execution_id"],
                capture_sequence=identity["capture_sequence"],
                sdk_session_id=context.metadata.get("sdk_session_id") or (context.session_id if platform == "macos" else None),
                host_capture_started_monotonic_ns=started,
                host_capture_finished_monotonic_ns=finished,
                captured_wall_clock=datetime.now(UTC).isoformat(),
                png_sha256=hashlib.sha256(data).hexdigest(),
                png_size_bytes=len(data),
                image_width=width,
                image_height=height,
                display_width=context.screen_size[0] if platform != "macos" and context.screen_size else None,
                display_height=context.screen_size[1] if platform != "macos" and context.screen_size else None,
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
