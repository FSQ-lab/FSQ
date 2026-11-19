# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

from fsq_agent.application.contracts import EnvironmentSummary, WorkspaceRequest
from fsq_agent.application.workspace import require_initialized_workspace
from fsq_agent.config import load_workspace_platform_settings, validate_strict_core_settings

__all__ = ["list_environments"]


def list_environments(current_directory: Path, platform: str | None = None) -> list[EnvironmentSummary]:
    workspace = require_initialized_workspace(WorkspaceRequest(current_directory=current_directory))
    platforms = [platform] if platform else ["android", "web", "windows", "macos", "ios"]
    environments = []
    for item in platforms:
        try:
            settings = load_workspace_platform_settings(workspace.workspace, item)
            validate_strict_core_settings(settings)
        except Exception as exc:  # noqa: BLE001 - diagnostic operation returns safe readiness.
            environments.append(EnvironmentSummary(name=f"local-{item}", platform=item, ready=False, message=str(exc)))
        else:
            environments.append(EnvironmentSummary(name=f"local-{item}", platform=item, ready=True, message="ready"))
    return environments
