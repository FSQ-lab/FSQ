# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel

from fsq_agent.models import AIAssertionRequest, AIAssertionResult, AndroidVisualAssertionPolicy, FailureCategory, HarnessArtifactRef, HarnessContext, HarnessPlatform, StepPhase

CaptureArtifact = Callable[[str, str, HarnessContext, str, StepPhase], HarnessArtifactRef]

_PLATFORM_LABELS: dict[str, str] = {
    "android": "Android",
    "ios": "iOS",
    "macos": "macOS",
    "windows": "Windows",
    "web": "Web",
}

@dataclass(frozen=True)
class _AIAssertionToolRuntime:
    platform: HarnessPlatform
    artifact_store: Any | None
    ai_assertion_evaluator: Any | None


@dataclass(frozen=True)
class _AIAssertionToolInvocation:
    context: HarnessContext
    step_id: str
    action_name: str
    metadata: dict[str, object]
    capture_artifact: CaptureArtifact
    visual_assertion_policy: AndroidVisualAssertionPolicy | None = None


class AIAssertionBackendToolMixin:
    def configure_ai_assertion_tool(
        self,
        *,
        platform: HarnessPlatform,
        artifact_store: Any | None = None,
        ai_assertion_evaluator: Any | None = None,
    ) -> None:
        self._ai_assertion_tool_runtime = _AIAssertionToolRuntime(
            platform=platform,
            artifact_store=artifact_store,
            ai_assertion_evaluator=ai_assertion_evaluator,
        )

    def prepare_ai_assertion_tool_invocation(
        self,
        *,
        context: HarnessContext,
        step_id: str,
        action_name: str,
        metadata: dict[str, object],
        capture_artifact: CaptureArtifact,
        visual_assertion_policy: AndroidVisualAssertionPolicy | None = None,
    ) -> None:
        self._ai_assertion_tool_invocation = _AIAssertionToolInvocation(
            context=context,
            step_id=step_id,
            action_name=action_name,
            metadata=metadata,
            capture_artifact=capture_artifact,
            visual_assertion_policy=visual_assertion_policy,
        )

    def clear_ai_assertion_tool_invocation(self) -> None:
        self._ai_assertion_tool_invocation = None

    def _run_ai_assertion_tool(self, params: BaseModel) -> dict[str, object]:
        runtime = getattr(self, "_ai_assertion_tool_runtime", None)
        if runtime is None:
            result = AIAssertionResult(status="inconclusive", outcome="inconclusive", passed=False, explanation="A current execution and approved image are required.", error_code="capture_failed")
            return self._android_assertion_output(result, [], getattr(params, "assertion", ""))
        return self._run_android_ai_assertion_tool(params, runtime)
    def _run_android_ai_assertion_tool(self, params, runtime):
        invocation = getattr(self, "_ai_assertion_tool_invocation", None)
        if invocation is None:
            result = AIAssertionResult(status="inconclusive", outcome="inconclusive", passed=False, explanation="A current execution and approved image are required.", error_code="capture_failed")
            return self._android_assertion_output(result, [], params.assertion)
        refs = [HarnessArtifactRef.model_validate(ref) for ref in invocation.metadata.get("prepared_artifacts", [])]
        screenshot = next((ref for ref in refs if ref.kind == "screenshot"), None)
        terminal = screenshot.metadata.get("visual_assertion") if screenshot is not None else None
        if isinstance(terminal, dict):
            return self._android_assertion_output(AIAssertionResult.model_validate(terminal), [], params.assertion)
        if screenshot is None or screenshot.availability != "available":
            result = AIAssertionResult(status="inconclusive", outcome="inconclusive", passed=False, explanation="Current image capture is unavailable.", error_code="capture_failed")
            return self._android_assertion_output(result, [], params.assertion)
        if runtime.ai_assertion_evaluator is None:
            result = AIAssertionResult(
                status="inconclusive",
                outcome="inconclusive",
                passed=False,
                explanation="An available provider is required; visual assertions cannot replay offline.",
                error_code="provider_unavailable",
            )
            return self._android_assertion_output(result, [screenshot], params.assertion)
        policy = invocation.visual_assertion_policy
        if policy is None or not policy.allow_provider_upload or policy.frame_sensitivity != "non_sensitive":
            result = AIAssertionResult(status="inconclusive", outcome="inconclusive", passed=False, explanation="A trusted decision for the current capture is required.", error_code="privacy_blocked")
            return self._android_assertion_output(result, [screenshot], params.assertion)
        request = AIAssertionRequest(
            platform=runtime.platform,
            prompt=params.assertion,
            surface=params.surface,
            region=params.region,
            expect=params.expect,
            run_id=invocation.metadata.get("run_id"),
            step_id=invocation.step_id,
            action_name=invocation.action_name,
            screenshot_path=screenshot.path,
            screenshot_artifact_ref=screenshot,
            run_dir=runtime.artifact_store.run_dir if runtime.artifact_store else None,
            capture=screenshot.metadata.get("capture"),
            deterministic_evidence=screenshot.metadata.get("deterministic_evidence", []),
            budget=policy.budget,
            upload_authorized=policy.allow_provider_upload,
            frame_sensitivity=policy.frame_sensitivity,
            ui_context={"surface": params.surface, "full_screen_reason": params.full_screen_reason},
        )
        result = runtime.ai_assertion_evaluator.evaluate(request, cancellation_check=invocation.context.cancellation_check)
        if result.outcome is None:
            platform_label = _PLATFORM_LABELS.get(runtime.platform, "Android")
            result = AIAssertionResult(
                status="inconclusive",
                outcome="inconclusive",
                passed=False,
                explanation=f"The evaluator did not return a {platform_label} visual verdict.",
                error_code="invalid_output",
                capture=request.capture,
            )
        return self._android_assertion_output(result, self._unique_ai_assertion_artifact_refs([screenshot, *result.artifact_refs]), params.assertion)

    def _android_assertion_output(self, result, artifact_refs, assertion):
        output = result.model_dump(mode="json")
        return {
            "status": "incomplete" if result.outcome == "inconclusive" else result.outcome,
            "output": output,
            "artifact_refs": artifact_refs,
            "failure_category": "assertion_error" if result.outcome == "failed" else None,
            "error_message": result.error,
            "metadata": {"ai_assertion": output, "prompt": assertion, "owner": "driver"},
        }

    def _failed_ai_assertion_tool(self, failure_category: FailureCategory, error_message: str) -> dict[str, object]:
        return {
            "status": "failed",
            "failure_category": failure_category,
            "error_message": error_message,
            "metadata": {"owner": "driver"},
        }

    def _unique_ai_assertion_artifact_refs(self, refs: list[HarnessArtifactRef]) -> list[HarnessArtifactRef]:
        seen: set[str] = set()
        unique: list[HarnessArtifactRef] = []
        for ref in refs:
            if ref.artifact_id in seen:
                continue
            seen.add(ref.artifact_id)
            unique.append(ref)
        return unique
