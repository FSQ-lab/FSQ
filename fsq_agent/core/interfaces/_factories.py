# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from fsq_agent.core.evidence import ArtifactStore
    from fsq_agent.models import AndroidVisualAssertionPolicy, HarnessContext, HarnessPlatform, HarnessSettings, RuntimeSecretSettings


class DriverFactory:
    def _implementation(self) -> Any:
        from fsq_agent.drivers._factory import _DriverFactoryImplementation

        return _DriverFactoryImplementation()

    def create_android_driver(self, settings: Any, *, app_id: str | None = None, serial: str | None = None) -> Any:
        return self._implementation().create_android_driver(settings, app_id=app_id, serial=serial)

    def create_web_driver(self, settings: Any) -> Any:
        return self._implementation().create_web_driver(settings)

    def create_windows_driver(self, settings: Any) -> Any:
        return self._implementation().create_windows_driver(settings)

    def create_macos_driver(self, settings: Any, *, bundle_id: str | None = None) -> Any:
        return self._implementation().create_macos_driver(settings, bundle_id=bundle_id)

    def create_ios_driver(self, settings: Any, *, bundle_id: str | None = None, udid: str | None = None) -> Any:
        return self._implementation().create_ios_driver(settings, bundle_id=bundle_id, udid=udid)


class HarnessFactory:
    def __init__(self, driver_factory: Any | None = None) -> None:
        self.driver_factory = driver_factory or DriverFactory()

    def create_harness(
        self,
        *,
        platform: HarnessPlatform,
        harness_settings: HarnessSettings,
        artifact_store: ArtifactStore | None = None,
        ai_assertion_evaluator: Any | None = None,
        visual_assertion_policy_resolver: Callable[[HarnessContext, int], AndroidVisualAssertionPolicy | None] | None = None,
        visual_ai_eligible: bool | None = None,
        runtime_secret_settings: RuntimeSecretSettings | None = None,
        app_id: str | None = None,
        serial: str | None = None,
        bundle_id: str | None = None,
        udid: str | None = None,
    ) -> Any:
        from fsq_agent.harnesses._factory import _HarnessFactoryImplementation

        return _HarnessFactoryImplementation(self.driver_factory).create_harness(
            platform=platform,
            harness_settings=harness_settings,
            artifact_store=artifact_store,
            ai_assertion_evaluator=ai_assertion_evaluator,
            visual_assertion_policy_resolver=visual_assertion_policy_resolver,
            visual_ai_eligible=visual_ai_eligible,
            runtime_secret_settings=runtime_secret_settings,
            app_id=app_id,
            serial=serial,
            bundle_id=bundle_id,
            udid=udid,
        )
