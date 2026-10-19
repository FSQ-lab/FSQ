# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApplicationLookupHint(BaseModel):
    """A model-inferred concrete reference, distinct from explicit provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)

    kind: Literal["app_id", "bundle_id", "app_path"]
    value: str = Field(min_length=1, max_length=2048)

    @field_validator("value")
    @classmethod
    def safe_value(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Lookup hints must not contain control characters.")
        return value


class ApplicationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: str = Field(min_length=1, max_length=4096)
    label: str = Field(min_length=1, max_length=512)
    app_path: Path | None = None
    app_id: str | None = None
    bundle_id: str | None = None


class ApplicationLookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["complete", "failed"]
    candidates: tuple[ApplicationCandidate, ...] = ()
    code: Literal["complete", "invalid_query", "discovery_failed", "discovery_timeout"] = "complete"
