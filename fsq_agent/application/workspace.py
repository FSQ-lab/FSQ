# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.application._quick_test import get_quick_test_context, prepare_quick_test
from fsq_agent.application._quick_test_execution import execute_prepared_quick_test, run_quick_test
from fsq_agent.application._workspace_init import (
    add_workspace_platform as _add_workspace_platform,
)
from fsq_agent.application._workspace_init import (
    create_workspace as _create_workspace,
)
from fsq_agent.application._workspace_init import (
    initialize_workspace as _initialize_workspace,
)
from fsq_agent.application._workspace_init import (
    resolve_workspace_target as _resolve_workspace_target,
)
from fsq_agent.application._workspace_init import (
    update_workspace_platform as _update_workspace_platform,
)
from fsq_agent.application.contracts import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    QuickTestCheck,
    QuickTestChoice,
    QuickTestContextRequest,
    QuickTestContextResult,
    QuickTestPrepareRequest,
    QuickTestPrepareResult,
    QuickTestRunResult,
    WorkspaceInitializeRequest,
    WorkspaceInitializeResult,
    WorkspaceRequest,
    WorkspaceResult,
)
from fsq_agent.config import inspect_registered_workspace, list_workspace_registry


def require_initialized_workspace(request: WorkspaceRequest) -> WorkspaceResult:
    current_directory = request.current_directory.expanduser().resolve()
    entry = next((item for item in list_workspace_registry() if item.root_path.resolve() == current_directory), None)
    if entry is None:
        raise ApplicationError(
            code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The current directory is not an initialized FSQ Workspace.",
            action="Run 'fsq init' here or change to an initialized FSQ Workspace.",
            details={
                "current_directory": str(current_directory),
                "workspace": str(current_directory),
            },
        )
    try:
        status = inspect_registered_workspace(entry.name)
    except Exception as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The registered FSQ Workspace is unavailable.",
            action="Repair it in Control Plane or initialize a valid workspace root.",
            details={"workspace": str(current_directory)},
        ) from exc
    if status.status == "unavailable":
        raise ApplicationError(
            code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The registered FSQ Workspace has no valid platform configuration.",
            action="Add or repair a platform configuration.",
            details={"workspace": str(current_directory)},
        )
    return WorkspaceResult(current_directory=current_directory, workspace=current_directory)


def initialize_workspace(request: WorkspaceInitializeRequest) -> WorkspaceInitializeResult:
    return _initialize_workspace(request)


def resolve_workspace_target(request: WorkspaceInitializeRequest):
    return _resolve_workspace_target(request)


def create_workspace(**kwargs):
    return _create_workspace(**kwargs)


def add_workspace_platform(**kwargs):
    return _add_workspace_platform(**kwargs)


def update_workspace_platform(**kwargs):
    return _update_workspace_platform(**kwargs)


__all__ = [
    "QuickTestCheck",
    "QuickTestChoice",
    "QuickTestContextRequest",
    "QuickTestContextResult",
    "QuickTestPrepareRequest",
    "QuickTestPrepareResult",
    "QuickTestRunResult",
    "add_workspace_platform",
    "create_workspace",
    "execute_prepared_quick_test",
    "get_quick_test_context",
    "initialize_workspace",
    "prepare_quick_test",
    "require_initialized_workspace",
    "resolve_workspace_target",
    "run_quick_test",
    "update_workspace_platform",
]
