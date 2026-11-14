# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from fsq_agent._capability_bootstrap import build_capability_registry, provider_required_capability_names, steps_require_provider
from fsq_agent.capabilities import (
    CapabilityActionDefinition,
    capability,
    discover_capability_definitions,
    platform_driver_capability,
)
from fsq_agent.core import CapabilityRegistry, CommonPlatformTools
from fsq_agent.core.harness._appium_connect_android_driver import AppiumConnectAndroidDriver
from fsq_agent.core.harness._appium_mac2_driver import AppiumMac2Driver
from fsq_agent.core.harness._driver_tools import _discover_driver_capability_definitions
from fsq_agent.core.harness._playwright_driver import PlaywrightWebDriver
from fsq_agent.models import CapabilityDefinition, ConfigurationError, ReplayPolicy


class ExampleParams(BaseModel):
    value: str


class OtherParams(BaseModel):
    value: str


def test_capability_discovery_returns_serializable_definition() -> None:
    class Provider:
        @capability(
            name="example_tool",
            executor_kind="common",
            owner="common",
            description="Example tool.",
            params_model=ExampleParams,
            replay=ReplayPolicy(kind="fsq_command", alias="exampleTool"),
            post_action_delay_seconds=0.5,
            metadata={"origin": "test"},
        )
        async def _example_tool(self, arguments: dict[str, object]) -> object:
            raise AssertionError("discovery must not invoke the method")

    definitions = discover_capability_definitions(Provider)

    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.name == "example_tool"
    assert definition.executor_kind == "common"
    assert definition.owner == "common"
    assert definition.post_action_delay_seconds == 0.5
    assert definition.params_model is ExampleParams
    assert definition.replay == ReplayPolicy(kind="fsq_command", alias="exampleTool")
    assert definition.safe_metadata()["origin"] == "test"


def test_common_platform_tools_use_shared_declaration_layer() -> None:
    definitions = {definition.name: definition for definition in CommonPlatformTools.capability_definitions()}

    assert definitions["wait_ms"].executor_kind == "common"
    assert definitions["wait_ms"].owner == "common"
    assert definitions["wait_ms"].replay == ReplayPolicy(kind="fsq_command", alias="waitMs")
    assert set(definitions) == {"wait_ms"}


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
def test_provider_requirement_is_derived_from_platform_registry_metadata(platform: str) -> None:
    snapshot = build_capability_registry(platform=platform).snapshot()
    required_names = provider_required_capability_names(platform)

    assert required_names == frozenset({"assert_with_ai"})
    assert steps_require_provider([SimpleNamespace(action_name="assertWithAI")], snapshot, required_names)
    assert steps_require_provider([SimpleNamespace(action_name="assert_with_ai")], snapshot, required_names)
    assert not steps_require_provider([SimpleNamespace(action_name="waitMs")], snapshot, required_names)
    assert not steps_require_provider([SimpleNamespace(action_name="unknown")], snapshot, required_names)


def test_capability_registry_resolves_replay_alias_and_allows_self_alias() -> None:
    registry = CapabilityRegistry.from_definitions(
        [
            CapabilityDefinition(
                name="swipe",
                executor_kind="driver",
                params_model=ExampleParams,
                replay=ReplayPolicy(kind="fsq_command", alias="swipe"),
            ),
            CapabilityDefinition(
                name="tap_on",
                executor_kind="driver",
                params_model=ExampleParams,
                replay=ReplayPolicy(kind="fsq_command", alias="tapOn"),
            ),
        ]
    )

    assert registry.resolve("swipe") is registry.get("swipe")
    assert registry.resolve("tapOn") is registry.get("tap_on")


def test_capability_registry_rejects_cross_capability_replay_alias_conflict() -> None:
    with pytest.raises(ConfigurationError, match="replay alias"):
        CapabilityRegistry.from_definitions(
            [
                CapabilityDefinition(
                    name="tap_on",
                    executor_kind="driver",
                    params_model=ExampleParams,
                    replay=ReplayPolicy(kind="fsq_command", alias="clickOn"),
                ),
                CapabilityDefinition(
                    name="click_on",
                    executor_kind="driver",
                    params_model=ExampleParams,
                    replay=ReplayPolicy(kind="fsq_command", alias="clickOn"),
                ),
            ]
        )


def test_platform_driver_capability_validates_catalog_method_name() -> None:
    driver_action = platform_driver_capability(
        platform="android",
        backend="fake",
        catalog={
            "tapOn": CapabilityActionDefinition(
                action_name="tapOn",
                canonical_name="tap_on",
                executor_kind="driver",
                owner="driver",
                params_model=ExampleParams,
                method_name="tap_on",
                post_action_delay_seconds=0.75,
                replay=ReplayPolicy(kind="fsq_command", alias="tapOn"),
            )
        },
    )

    with pytest.raises(ConfigurationError, match="method"):

        class BadDriver:
            @driver_action("tapOn", description="Tap.")
            def wrong_name(self, params: ExampleParams) -> dict[str, object]:
                return {}


def test_platform_driver_capability_inherits_and_overrides_catalog_delay() -> None:
    driver_action = platform_driver_capability(
        platform="android",
        backend="fake",
        catalog={
            "tapOn": CapabilityActionDefinition(
                action_name="tapOn",
                canonical_name="tap_on",
                executor_kind="driver",
                owner="driver",
                params_model=ExampleParams,
                method_name="tap_on",
                post_action_delay_seconds=0.75,
                replay=ReplayPolicy(kind="fsq_command", alias="tapOn"),
            ),
            "inputText": CapabilityActionDefinition(
                action_name="inputText",
                canonical_name="input_text",
                executor_kind="driver",
                owner="driver",
                params_model=ExampleParams,
                method_name="input_text",
                post_action_delay_seconds=0.5,
                replay=ReplayPolicy(kind="fsq_command", alias="inputText"),
            ),
        },
    )

    class Driver:
        @driver_action("tapOn", description="Tap.")
        def tap_on(self, params: ExampleParams) -> dict[str, object]:
            return {}

        @driver_action("inputText", description="Input.", post_action_delay_seconds=0)
        def input_text(self, params: ExampleParams) -> dict[str, object]:
            return {}

    definitions = {definition.name: definition for definition in discover_capability_definitions(Driver)}

    assert definitions["tap_on"].post_action_delay_seconds == 0.75
    assert definitions["input_text"].post_action_delay_seconds == 0


def test_capability_rejects_negative_post_action_delay() -> None:
    with pytest.raises(ConfigurationError, match="post_action_delay_seconds"):

        class BadProvider:
            @capability(
                name="bad_delay",
                executor_kind="common",
                owner="common",
                description="Bad.",
                params_model=ExampleParams,
                post_action_delay_seconds=-0.1,
            )
            async def bad_delay(self, arguments: dict[str, object]) -> object:
                return {}


def test_platform_driver_capability_validates_catalog_params_model() -> None:
    driver_action = platform_driver_capability(
        platform="android",
        backend="fake",
        catalog={
            "tapOn": CapabilityActionDefinition(
                action_name="tapOn",
                canonical_name="tap_on",
                executor_kind="driver",
                owner="driver",
                params_model=ExampleParams,
                method_name="tap_on",
                replay=ReplayPolicy(kind="fsq_command", alias="tapOn"),
            )
        },
    )

    with pytest.raises(ConfigurationError, match="parameter model"):

        class BadDriver:
            @driver_action("tapOn", description="Tap.")
            def tap_on(self, params: OtherParams) -> dict[str, object]:
                return {}


def test_android_driver_declarations_keep_catalog_backed_metadata() -> None:
    definitions = {
        definition.name: definition
        for definition in _discover_driver_capability_definitions(
            AppiumConnectAndroidDriver,
            platform="android",
            metadata={"driver_class": "AppiumConnectAndroidDriver", "backend": "appium_connect_agent"},
        )
    }

    tap = definitions["tap_on"]
    assert tap.executor_kind == "driver"
    assert tap.owner == "driver"
    assert tap.platform == "android"
    assert tap.backend == "appium_connect_agent"
    assert tap.metadata["driver_method"] == "tap_on"
    assert tap.metadata["fsq_action_name"] == "tapOn"
    assert tap.replay == ReplayPolicy(kind="fsq_command", alias="tapOn")
    assert definitions["ui_snapshot"].step_kind == "observation"
    assert "perform_actions" not in definitions


def test_playwright_web_driver_declarations_keep_catalog_backed_metadata() -> None:
    definitions = {
        definition.name: definition
        for definition in _discover_driver_capability_definitions(
            PlaywrightWebDriver,
            platform="web",
            metadata={"driver_class": "PlaywrightWebDriver", "backend": "playwright"},
        )
    }

    click = definitions["click_on"]
    assert click.executor_kind == "driver"
    assert click.owner == "driver"
    assert click.platform == "web"
    assert click.backend == "playwright"
    assert click.metadata["driver_method"] == "click_on"
    assert click.metadata["fsq_action_name"] == "clickOn"
    assert click.replay == ReplayPolicy(kind="fsq_command", alias="clickOn")
    assert definitions["ui_snapshot"].replay == ReplayPolicy(kind="fsq_command", alias="uiSnapshot")
    assert definitions["ui_snapshot"].step_kind == "observation"
    assert definitions["assert_visible"].step_kind == "assertion"
    assert definitions["assert_with_ai"].replay == ReplayPolicy(kind="fsq_command", alias="assertWithAI")
    assert definitions["assert_with_ai"].executor_kind == "driver"
    assert definitions["assert_with_ai"].owner == "driver"
    assert definitions["assert_with_ai"].metadata["driver_method"] == "assert_with_ai"


def test_appium_mac2_driver_declarations_keep_catalog_backed_metadata() -> None:
    definitions = {
        definition.name: definition
        for definition in _discover_driver_capability_definitions(
            AppiumMac2Driver,
            platform="macos",
            metadata={"driver_class": "AppiumMac2Driver", "backend": "appium_mac2"},
        )
    }

    click = definitions["click_on"]
    assert click.executor_kind == "driver"
    assert click.owner == "driver"
    assert click.platform == "macos"
    assert click.backend == "appium_mac2"
    assert click.metadata["driver_method"] == "click_on"
    assert click.metadata["fsq_action_name"] == "clickOn"
    assert click.replay == ReplayPolicy(kind="fsq_command", alias="clickOn")
    assert definitions["ui_snapshot"].replay == ReplayPolicy(kind="fsq_command", alias="uiSnapshot")
    assert definitions["ui_snapshot"].step_kind == "observation"
    assert definitions["assert_elements_order"].replay == ReplayPolicy(kind="fsq_command", alias="assertElementsOrder")
    assert definitions["assert_elements_order"].step_kind == "assertion"
    assert definitions["assert_with_ai"].replay == ReplayPolicy(kind="fsq_command", alias="assertWithAI")
    assert definitions["assert_with_ai"].executor_kind == "driver"
    assert definitions["assert_with_ai"].owner == "driver"
    assert "tap_on" not in definitions


def test_web_registry_uses_web_only_platform_capabilities_without_playwright_import() -> None:
    snapshot = build_capability_registry(platform="web").snapshot()
    definitions = {definition.name: definition for definition in snapshot.capabilities}

    assert "click_on" in definitions
    assert "ui_snapshot" in definitions
    assert "tap_on" not in definitions
    assert definitions["click_on"].executor_kind == "driver"
    assert definitions["click_on"].platform == "web"
    assert definitions["click_on"].backend == "playwright"
    assert definitions["click_on"].metadata["driver_method"] == "click_on"
    assert definitions["ui_snapshot"].replay == ReplayPolicy(kind="fsq_command", alias="uiSnapshot")
    assert definitions["ui_snapshot"].step_kind == "observation"
    assert definitions["assert_with_ai"].executor_kind == "driver"
    assert definitions["assert_with_ai"].owner == "driver"
    assert snapshot.resolve("clickOn") is definitions["click_on"]
    assert snapshot.resolve("uiSnapshot") is definitions["ui_snapshot"]


def test_macos_registry_uses_macos_only_platform_capabilities_without_appium_import() -> None:
    snapshot = build_capability_registry(platform="macos").snapshot()
    definitions = {definition.name: definition for definition in snapshot.capabilities}

    assert "click_on" in definitions
    assert "ui_snapshot" in definitions
    assert "assert_elements_order" in definitions
    assert "tap_on" not in definitions
    assert definitions["click_on"].executor_kind == "driver"
    assert definitions["click_on"].platform == "macos"
    assert definitions["click_on"].backend == "appium_mac2"
    assert definitions["click_on"].metadata["driver_method"] == "click_on"
    assert definitions["ui_snapshot"].replay == ReplayPolicy(kind="fsq_command", alias="uiSnapshot")
    assert definitions["ui_snapshot"].step_kind == "observation"
    assert definitions["assert_elements_order"].executor_kind == "driver"
    assert definitions["assert_elements_order"].owner == "driver"
    assert snapshot.resolve("assertElementsOrder") is definitions["assert_elements_order"]


def test_capabilities_imports_only_models_across_project_modules() -> None:
    package_root = Path(__file__).resolve().parents[1] / "fsq_agent" / "capabilities"
    forbidden = {
        "fsq_agent.agent",
        "fsq_agent.cli",
        "fsq_agent.config",
        "fsq_agent.core",
        "fsq_agent.fsq",
        "fsq_agent.knowledge",
        "fsq_agent.observation",
        "fsq_agent.providers",
        "fsq_agent.report",
        "fsq_agent.skills",
        "fsq_agent.tools",
    }

    imports: set[str] = set()
    for source_path in package_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

    assert not {module for module in imports for forbidden_module in forbidden if module == forbidden_module or module.startswith(f"{forbidden_module}.")}
