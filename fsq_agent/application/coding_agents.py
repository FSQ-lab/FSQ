# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.application._codex_install import install_codex
from fsq_agent.application._coding_skill_install import install_coding_skill
from fsq_agent.application.contracts import CodingAgentInstallRequest, CodingAgentInstallResult


def install_coding_agent(request: CodingAgentInstallRequest) -> CodingAgentInstallResult:
    if request.agent == "skill":
        return install_coding_skill(request)
    return install_codex(request)


__all__ = ["install_coding_agent"]
