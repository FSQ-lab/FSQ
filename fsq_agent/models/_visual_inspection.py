# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.models._core import AndroidInspectWithAIParams, AssertionBudget


class VisualImageBounds(BaseModel):
    """Half-open image-space geometry; never a physical pointer location."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class AIVisualCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True, allow_inf_nan=False)

    label: str = Field(min_length=1, max_length=512)
    image_bounds: VisualImageBounds
    confidence: float | None = Field(default=None, ge=0, le=1)


class AIVisualInspectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=4096)
    candidates: tuple[AIVisualCandidate, ...] = Field(default=(), max_length=20)


class AIVisualInspectionRequest(AndroidInspectWithAIParams):
    """Trusted current-frame inputs shared by the driver and configured service."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True, allow_inf_nan=False)

    png_bytes: bytes = Field(exclude=True, repr=False)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_started_monotonic_ns: int = Field(gt=0)
    capture_finished_monotonic_ns: int = Field(gt=0)
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    budget: AssertionBudget = Field(default_factory=AssertionBudget)
    upload_authorized: bool = False
    frame_sensitivity: Literal["unknown", "sensitive", "non_sensitive"] = "unknown"
