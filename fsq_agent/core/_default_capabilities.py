# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.drivers._capabilities import _discover_driver_capability_definitions
from fsq_agent.drivers._factory import _driver_class_for_backend
from fsq_agent.models import CapabilityDefinition, HarnessPlatform


class CapabilityDefinitionFactory:
    def platform_definitions(
        self,
        *,
        platform: HarnessPlatform,
        backend: str | None = None,
        include_ai_assertion: bool = True,
    ) -> list[CapabilityDefinition]:
        return _platform_capability_definitions(
            platform=platform,
            backend=backend,
            include_ai_assertion=include_ai_assertion,
        )


def _platform_capability_definitions(
    *,
    platform: HarnessPlatform,
    backend: str | None = None,
    include_ai_assertion: bool = True,
) -> list[CapabilityDefinition]:
    driver_class = _driver_class_for_backend(platform, backend)
    metadata: dict[str, object] = {
        "driver_class": driver_class.__name__,
        "backend": str(getattr(driver_class, "backend", backend or "")),
    }
    definitions = _discover_driver_capability_definitions(
        driver_class,
        platform=platform,
        metadata=metadata,
    )
    if not include_ai_assertion:
        definitions = [definition for definition in definitions if definition.name != "assert_with_ai"]
    return definitions


def android_capability_definitions(*, include_ai_assertion: bool = True) -> list[CapabilityDefinition]:
    return _platform_capability_definitions(platform="android", include_ai_assertion=include_ai_assertion)


def web_capability_definitions(*, include_ai_assertion: bool = True) -> list[CapabilityDefinition]:
    return _platform_capability_definitions(platform="web", include_ai_assertion=include_ai_assertion)


def windows_capability_definitions(*, include_ai_assertion: bool = True) -> list[CapabilityDefinition]:
    return _platform_capability_definitions(platform="windows", include_ai_assertion=include_ai_assertion)


def macos_capability_definitions(*, include_ai_assertion: bool = True) -> list[CapabilityDefinition]:
    return _platform_capability_definitions(platform="macos", include_ai_assertion=include_ai_assertion)


def ios_capability_definitions(*, include_ai_assertion: bool = True) -> list[CapabilityDefinition]:
    return _platform_capability_definitions(platform="ios", include_ai_assertion=include_ai_assertion)
