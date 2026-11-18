# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fsq_agent.core import (
    CapabilityDefinitionFactory,
    CapabilityRegistry,
    CommonPlatformTools,
)
from fsq_agent.tools import DefaultAgentToolProvider, FileOps

if TYPE_CHECKING:
    from pathlib import Path

    from fsq_agent.models import CapabilityDefinition, HarnessPlatform

_CAPABILITY_DEFINITION_FACTORY = CapabilityDefinitionFactory()


def common_capability_definitions() -> list[CapabilityDefinition]:
    return CommonPlatformTools.capability_definitions()


def build_capability_registry(*, platform: HarnessPlatform = "android", include_ai_assertion: bool = True) -> CapabilityRegistry:
    return CapabilityRegistry.from_definitions(
        [
            *common_capability_definitions(),
            *_platform_capability_definitions(platform, include_ai_assertion=include_ai_assertion),
        ]
    )


def ai_assertion_capability_names(platform: HarnessPlatform) -> frozenset[str]:
    full_snapshot = build_capability_registry(platform=platform, include_ai_assertion=True).snapshot()
    provider_free_names = build_capability_registry(platform=platform, include_ai_assertion=False).snapshot().by_name()
    return frozenset(capability.name for capability in full_snapshot.capabilities if capability.name not in provider_free_names)


def provider_required_capability_names(platform: HarnessPlatform) -> frozenset[str]:
    # Android Strict recognizes unavailable visual assertions as incomplete.
    return frozenset() if platform == "android" else ai_assertion_capability_names(platform)


def steps_require_provider(
    steps: list[Any],
    registry_snapshot: Any,
    provider_required_names: frozenset[str],
) -> bool:
    return any((capability := registry_snapshot.resolve(step.action_name)) is not None and capability.name in provider_required_names for step in steps)


def _platform_capability_definitions(platform: HarnessPlatform, *, include_ai_assertion: bool) -> list[CapabilityDefinition]:
    return _CAPABILITY_DEFINITION_FACTORY.platform_definitions(
        platform=platform,
        include_ai_assertion=include_ai_assertion,
    )


def build_agent_tool_provider(
    *,
    workspace_base: Path | None = None,
    read_roots: list[Path] | None = None,
    write_root: Path | None = None,
    runtime_secret_settings: Any = None,
    local_tool_output_settings: Any = None,
    runs_dir: Path | None = None,
    run_id: str = "",
) -> DefaultAgentToolProvider:
    return DefaultAgentToolProvider(
        FileOps(workspace_base=workspace_base, read_roots=read_roots, write_root=write_root),
        runtime_secret_settings=runtime_secret_settings,
        local_tool_output_settings=local_tool_output_settings,
        runs_dir=runs_dir,
        run_id=run_id,
    )
