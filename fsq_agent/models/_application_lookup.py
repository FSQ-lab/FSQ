# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

_CatalogueText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class WindowsApplicationConfig(BaseModel):
    """Bundled application policy, not a persisted Workspace identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: _CatalogueText
    name: _CatalogueText
    description: _CatalogueText
    path_candidates: tuple[_CatalogueText, ...] = Field(min_length=1, max_length=8, strict=False)
    window_title_re: _CatalogueText

    @field_validator("window_title_re")
    @classmethod
    def valid_title_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error:
            raise ValueError("Invalid application title regex.") from None
        return value

    @model_validator(mode="after")
    def safe_text(self):
        for value in (self.id, self.name, self.description, self.window_title_re, *self.path_candidates):
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("Application configuration must not contain control characters.")
        return self


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
    candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: tuple[str, ...] = ()
    app_path: Path | None = None
    app_id: str | None = None
    bundle_id: str | None = None
    window_title_re: str | None = Field(default=None, min_length=1, max_length=2048)
    source: str | None = Field(default=None, min_length=1, max_length=64)

    def model_summary(self) -> dict[str, object]:
        """Return model-facing candidate metadata, stripping host-only fields."""
        summary: dict[str, object] = {
            "candidate_id": self.candidate_id or self.identity,
            "label": self.label,
        }
        if self.aliases:
            summary["aliases"] = list(self.aliases)
        if self.app_id is not None:
            summary["app_id"] = self.app_id
        if self.bundle_id is not None:
            summary["bundle_id"] = self.bundle_id
        if self.app_path is not None:
            summary["app_path"] = str(self.app_path)
        if self.source is not None:
            summary["source"] = self.source
        return summary


class ApplicationInventoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["complete", "failed", "unsupported"]
    candidates: tuple[ApplicationCandidate, ...] = ()
    code: Literal["complete", "invalid_query", "discovery_failed", "discovery_timeout", "unsupported"] = "complete"
    from_cache: bool = False
    fingerprint: str | None = None


class ApplicationLookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["complete", "failed"]
    candidates: tuple[ApplicationCandidate, ...] = ()
    code: Literal["complete", "invalid_query", "discovery_failed", "discovery_timeout"] = "complete"
