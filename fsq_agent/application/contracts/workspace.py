# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fsq_agent.application.contracts.doctor import DoctorPlatformResult
from fsq_agent.config import Settings
from fsq_agent.models import AndroidDevice, AndroidWorkspaceTarget, MacOSWorkspaceTarget, VerificationResult, WebWorkspaceTarget, WindowsWorkspaceTarget


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    current_directory: Path = Field(description="Current directory selected by the calling adapter.")


class WorkspaceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    current_directory: Path
    workspace: Path


class WorkspaceInitializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    current_directory: Path
    platform: Literal["android", "web", "windows", "macos"]
    name: str | None = None
    app_id: str | None = None
    browser_channel: Literal["chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"] | None = None
    browser_executable_path: Path | None = None
    app_path: Path | None = None
    window_title_re: str | None = None
    launch_args: str | None = None
    bundle_id: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    update_existing: bool = False
    user_config_root: Path | None = None


class WorkspaceInitializeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["initialized", "platform_added", "unchanged", "updated"]
    name: str
    root_path: Path
    platform: Literal["android", "web", "windows", "macos"]
    driver_status: Literal["ready"]
    browser_executable_path: Path | None = None


class QuickTestContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Literal["android", "web", "windows", "macos"]
    target_id: str | None = Field(default=None, strict=True, min_length=1, max_length=256)
    user_config_root: Path | None = None

    @model_validator(mode="after")
    def applicable_selection(self):
        if self.target_id is not None:
            if self.platform == "android":
                AndroidDevice(serial=self.target_id, state="device")
            elif self.platform == "web":
                if self.target_id not in get_args(WebWorkspaceTarget.model_fields["browser_channel"].annotation):
                    raise ValueError("Unsupported browser channel.")
            else:
                raise ValueError("Desktop application identity comes only from the goal.")
        return self


class QuickTestPrepareRequest(QuickTestContextRequest):
    goal: str = Field(strict=True, min_length=1, max_length=16000)
    url: str | None = Field(default=None, strict=True, min_length=1)

    @field_validator("goal")
    @classmethod
    def nonblank_goal(cls, value):
        if not value.strip() or any(ord(char) < 32 and char not in "\r\n\t" for char in value):
            raise ValueError("A current goal is required.")
        return value.strip()

    @model_validator(mode="after")
    def explicit_selection(self):
        if self.url is not None and self.platform != "web":
            raise ValueError("An explicit starting URL is supported only for Web.")
        if self.platform in {"android", "web"} and self.target_id is None:
            raise ValueError("Explicit device or browser selection is required.")
        return self


class QuickTestCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    status: Literal["ready", "unavailable", "error", "pending", "not_applicable"]
    message: str
    action: str = ""


class QuickTestChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    label: str
    selectable: bool
    status: Literal["ready", "unavailable", "error"]


class QuickTestContextResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Literal["android", "web", "windows", "macos"]
    target_id: str | None
    can_start: bool
    choices: tuple[QuickTestChoice, ...]
    checks: tuple[QuickTestCheck, ...]
    diagnosis: DoctorPlatformResult | None = None


class QuickTestPrepareResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace_name: str
    root_path: Path
    platform_revision: str
    target_id: str
    target: AndroidWorkspaceTarget | WebWorkspaceTarget | WindowsWorkspaceTarget | MacOSWorkspaceTarget
    application: str
    settings: Settings = Field(exclude=True, repr=False)
    requires_web_search_evidence: bool = False


class QuickTestRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace_name: str
    root_path: Path
    platform: Literal["android", "web", "windows", "macos"]
    platform_revision: str
    target_id: str
    target: AndroidWorkspaceTarget | WebWorkspaceTarget | WindowsWorkspaceTarget | MacOSWorkspaceTarget
    application: str
    run_id: str | None = None
    status: Literal["success", "failed", "inconclusive", "cancelled", "error"]
    summary: str
    verification: VerificationResult | None = None
    duration_ms: int | None = None
    report_path: Path | None = None
    execution_result_path: Path | None = None
    evidence_manifest_path: Path | None = None
    candidate_case_path: Path | None = None
    case_name: str | None = None
    recording_draft: bool | None = None
    warnings: list[str] = Field(default_factory=list)
