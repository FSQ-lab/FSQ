# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from threading import Thread

from fsq_agent.application import ApplicationError, QuickTestContextRequest, QuickTestPrepareRequest, get_quick_test_context, prepare_quick_test
from fsq_agent.models import ConfigurationError

from ._evidence import configured_secret_values, safe_text
from ._execution import ExecutionHandle, prepare_run, start_execution
from ._readiness import AndroidPreflightError, MacOSPreflightError, diagnosed_readiness
from ._state import TaskCancelledError


def context(query, user_root):
    if set(query) - {"platform", "targetId"} or "platform" not in query or any(not isinstance(values, list) or len(values) != 1 for values in query.values()):
        raise ValueError("Invalid Quick Test context query.")
    request = QuickTestContextRequest(platform=query["platform"][0], target_id=query.get("targetId", [None])[0], user_config_root=user_root)
    result = get_quick_test_context(request)
    return {
        "platform": result.platform,
        "targetId": result.target_id,
        "canStart": result.can_start,
        "choices": [choice.model_dump(mode="json") for choice in result.choices],
        "checks": [check.model_dump(mode="json") for check in result.checks],
        "diagnostics": diagnosed_readiness(result.diagnosis, workspace_name=None, include_context=True) if result.diagnosis is not None else None,
    }


def start(server, body):
    if set(body) - {"platform", "goal", "targetId"}:
        raise ValueError("Invalid Quick Test fields.")
    request = QuickTestPrepareRequest(platform=body.get("platform"), goal=body.get("goal"), target_id=body.get("targetId"), user_config_root=server.options.user_config_root)
    # Reject even a null desktop override: it is not an applicable input field.
    if request.platform in {"windows", "macos"} and "targetId" in body:
        raise ValueError("Desktop targetId is not accepted.")
    request_id = server.state.reserve(workspace_name=None, platform=request.platform, target_id=request.target_id, mode="explore", source={"goal": request.goal}, workflow="quick-test")
    handle = ExecutionHandle(request_id=request_id)
    thread = Thread(target=_prepare, args=(server, request_id, request), name=f"fsq-quick-test-{request_id}", daemon=True)
    handle.thread = thread
    server._handles[request_id] = handle
    try:
        thread.start()
    except Exception:  # noqa: BLE001 -- retain accepted request even if the worker cannot start.
        server.state.finish(
            request_id,
            status="error",
            summary="Quick Test preparation could not start.",
            error={"code": "quick_test.preparation_failed", "message": "Quick Test preparation could not start.", "action": "Restart the service and try a new request."},
        )
    return {"requestId": request_id}


def _prepare(server, request_id, request):
    state = server.state
    try:
        state.transition(request_id, "preparing", summary="Checking prerequisites and identifying the current goal's application.")
        result = prepare_quick_test(request, check_cancelled=lambda: state.raise_if_cancelled(request_id))
        state.raise_if_cancelled(request_id)
        prepared = prepare_run(
            request_id=request_id,
            settings=result.settings,
            body={"workspaceName": result.workspace_name, "platform": request.platform, "targetId": result.target_id, "mode": "explore", "goal": request.goal},
            frozen_revision=result.platform_revision,
        )
        prepared.requires_web_search_evidence = result.requires_web_search_evidence
        prepared.goal = request.goal
        state.raise_if_cancelled(request_id)
        application = safe_text(result.application, secret_values=configured_secret_values(result.settings))
        state.bind_preparation(request_id, workspace_name=result.workspace_name, target_id=result.target_id, application=application, platform_revision=result.platform_revision)
        state.bind_cases_dir(request_id, result.settings.cases.dir)
        server._handles[request_id] = start_execution(prepared, state)
    except TaskCancelledError:
        state.finish(request_id, status="cancelled", summary="Quick Test cancelled.")
    except Exception as error:  # noqa: BLE001 -- preparation must retain only safe classified facts.
        if state.is_cancel_requested(request_id):
            state.finish(request_id, status="cancelled", summary="Quick Test cancelled.")
            return
        if isinstance(error, ApplicationError):
            facts = {"code": error.code.value, "message": error.message, "action": error.action or ""}
        elif isinstance(error, (AndroidPreflightError, MacOSPreflightError, ConfigurationError, ValueError, TypeError)):
            facts = {
                "code": "quick_test.prerequisites",
                "message": "Quick Test runtime prerequisites are not ready.",
                "action": "Recheck Provider, platform, and selected device or browser readiness.",
            }
        else:
            facts = {"code": "quick_test.preparation_failed", "message": "Quick Test preparation could not be completed.", "action": "Check platform prerequisites and the dedicated Workspace."}
        state.finish(request_id, status="error", summary=facts["message"], error=facts)
