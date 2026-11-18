# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import re
from urllib.parse import unquote

from pydantic import ValidationError

from fsq_agent.execution import RunLifecycleService
from fsq_agent.models import AgentFinalOutput, TaskResult


def _safe_text(text: str, secrets: tuple[str, ...]) -> str:
    def url(match):
        value = match.group()
        decoded = unquote(value)
        if any(secret and secret in decoded for secret in secrets):
            return "[redacted URL]"
        return value

    return RunLifecycleService.safe_text(re.sub(r"https?://[^\s<>\"']+", url, text, flags=re.IGNORECASE), secrets)


def _safe_encoded_values(value, secrets):
    if isinstance(value, str):
        return _safe_text(value, secrets)
    if isinstance(value, dict):
        return {key: _safe_encoded_values(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_encoded_values(item, secrets) for item in value]
    return value


def primary_summary(result: TaskResult) -> str:
    for step in reversed(result.steps):
        if step.tool_name != "agent_runtime.runner":
            continue
        raw = step.tool_output if step.tool_output is not None else step.actual_outcome
        try:
            output = AgentFinalOutput.model_validate_json(raw) if isinstance(raw, str) else AgentFinalOutput.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            break
        if output.summary.strip():
            return output.summary
        break
    return result.verification.summary
