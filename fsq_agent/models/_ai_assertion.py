# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fsq_agent.models._core import AssertionBudget, DeterministicFact, HarnessArtifactRef, HarnessPlatform, HostCaptureEnvelopeV1, VisualExpectation, VisualRegion

AIAssertionStatus = Literal["passed", "failed", "error", "inconclusive"]
AIAssertionOutcome = Literal["passed", "failed", "inconclusive"]
AIAssertionReason = Literal[
    "provider_unavailable",
    "provider_auth",
    "provider_rate_limited",
    "provider_refusal",
    "provider_timeout",
    "provider_unknown",
    "invalid_output",
    "capture_failed",
    "capture_stale",
    "region_unresolved",
    "region_invalid",
    "region_empty",
    "region_too_large",
    "privacy_blocked",
    "budget_exceeded",
    "cancelled",
    "unsupported_surface",
    "frame_not_single",
    "color_semantics_unverified",
    "display_identity_unverified",
    "artifact_containment_violation",
]


class AIAssertionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: HarnessPlatform
    prompt: str
    screenshot_path: Path | str | None = None
    screenshot_artifact_ref: HarnessArtifactRef | None = None
    ui_context: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
    step_id: str | None = None
    action_name: str | None = None
    provider: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    surface: Literal["custom_canvas", "image_content", "map_view", "appearance"] | None = None
    region: VisualRegion | None = None
    expect: VisualExpectation | None = None
    capture: HostCaptureEnvelopeV1 | None = None
    deterministic_evidence: list[DeterministicFact] = Field(default_factory=list)
    budget: AssertionBudget = Field(default_factory=AssertionBudget)
    run_dir: Path | None = None
    upload_authorized: bool = Field(default=False, strict=True)
    frame_sensitivity: Literal["unknown", "sensitive", "non_sensitive"] = "unknown"


class AIAssertionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AIAssertionStatus
    passed: bool = False
    explanation: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    artifact_refs: list[HarnessArtifactRef] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    outcome: AIAssertionOutcome | None = None
    error_code: AIAssertionReason | None = None
    deterministic_conflict: bool = False
    capture: HostCaptureEnvelopeV1 | None = None

    @model_validator(mode="before")
    @classmethod
    def derive_android_verdict(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("outcome") is not None:
            value = dict(value)
            if value.get("status") != value["outcome"]:
                raise ValueError("Assertion status must agree with the Android outcome.")
            if value["outcome"] != "inconclusive" and value.get("error_code") is not None:
                raise ValueError("A visual verdict cannot carry an inconclusive reason.")
            value["passed"] = value["outcome"] == "passed"
        elif isinstance(value, dict) and value.get("status") == "inconclusive":
            raise ValueError("An inconclusive assertion requires an Android outcome.")
        return value
