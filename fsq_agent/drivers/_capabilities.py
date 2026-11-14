# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from fsq_agent.capabilities import CapabilityActionDefinition, discover_capability_definitions, platform_driver_capability
from fsq_agent.models import (
    ANDROID_ACTION_DEFINITIONS,
    MACOS_ACTION_DEFINITIONS,
    WEB_ACTION_DEFINITIONS,
    CapabilityDefinition,
    ExecutableStepKind,
    HarnessFunctionSchema,
    HarnessPlatform,
    ReplayPolicy,
    WindowsAssertVisibleParams,
    WindowsAssertWithAIParams,
    WindowsClickOnParams,
    WindowsDoubleClickOnParams,
    WindowsDragToParams,
    WindowsHoverOnParams,
    WindowsKillAppParams,
    WindowsLaunchAppParams,
    WindowsPressKeyParams,
    WindowsRightClickOnParams,
    WindowsScrollOnParams,
    WindowsTypeTextParams,
    WindowsUiSnapshotParams,
)

F = TypeVar("F", bound=Callable[..., Any])


ANDROID_DRIVER_ACTION_CATALOG = {
    definition.fsq_action_name: CapabilityActionDefinition(
        action_name=definition.fsq_action_name,
        canonical_name=definition.driver_method,
        executor_kind="driver",
        owner=definition.owner,
        params_model=definition.params_model,
        step_kind=definition.step_kind,
        method_name=definition.driver_method,
        replay=ReplayPolicy(kind="fsq_command", alias=definition.fsq_action_name),
    )
    for definition in ANDROID_ACTION_DEFINITIONS
    if definition.owner == "driver"
}
_android_driver_capability = platform_driver_capability(
    platform="android",
    backend=None,
    catalog=ANDROID_DRIVER_ACTION_CATALOG,
)

WEB_DRIVER_ACTION_CATALOG = {
    definition.fsq_action_name: CapabilityActionDefinition(
        action_name=definition.fsq_action_name,
        canonical_name=definition.driver_method,
        executor_kind="driver",
        owner=definition.owner,
        params_model=definition.params_model,
        step_kind=definition.step_kind,
        method_name=definition.driver_method,
        replay=ReplayPolicy(kind="fsq_command", alias=definition.fsq_action_name),
    )
    for definition in WEB_ACTION_DEFINITIONS
    if definition.owner == "driver"
}
_web_driver_capability = platform_driver_capability(
    platform="web",
    backend=None,
    catalog=WEB_DRIVER_ACTION_CATALOG,
)


def _windows_action(
    action_name: str,
    canonical_name: str,
    params_model: type[BaseModel],
    *,
    step_kind: ExecutableStepKind = "action",
) -> CapabilityActionDefinition:
    return CapabilityActionDefinition(
        action_name=action_name,
        canonical_name=canonical_name,
        executor_kind="driver",
        owner="driver",
        params_model=params_model,
        step_kind=step_kind,
        method_name=canonical_name,
        replay=ReplayPolicy(kind="fsq_command", alias=action_name),
    )


WINDOWS_DRIVER_ACTION_CATALOG = {
    definition.action_name: definition
    for definition in (
        _windows_action("launchApp", "launch_app", WindowsLaunchAppParams, step_kind="setup"),
        _windows_action("killApp", "kill_app", WindowsKillAppParams, step_kind="teardown"),
        _windows_action("clickOn", "click_on", WindowsClickOnParams),
        _windows_action("doubleClickOn", "double_click_on", WindowsDoubleClickOnParams),
        _windows_action("rightClickOn", "right_click_on", WindowsRightClickOnParams),
        _windows_action("typeText", "type_text", WindowsTypeTextParams),
        _windows_action("pressKey", "press_key", WindowsPressKeyParams),
        _windows_action("hoverOn", "hover_on", WindowsHoverOnParams),
        _windows_action("scrollOn", "scroll_on", WindowsScrollOnParams),
        _windows_action("dragTo", "drag_to", WindowsDragToParams),
        _windows_action("assertVisible", "assert_visible", WindowsAssertVisibleParams, step_kind="assertion"),
        _windows_action("uiSnapshot", "ui_snapshot", WindowsUiSnapshotParams, step_kind="observation"),
        _windows_action("assertWithAI", "assert_with_ai", WindowsAssertWithAIParams, step_kind="assertion"),
    )
}
_windows_driver_capability = platform_driver_capability(
    platform="windows",
    backend=None,
    catalog=WINDOWS_DRIVER_ACTION_CATALOG,
)

MACOS_DRIVER_ACTION_CATALOG = {
    definition.fsq_action_name: CapabilityActionDefinition(
        action_name=definition.fsq_action_name,
        canonical_name=definition.driver_method,
        executor_kind="driver",
        owner=definition.owner,
        params_model=definition.params_model,
        step_kind=definition.step_kind,
        method_name=definition.driver_method,
        replay=ReplayPolicy(kind="fsq_command", alias=definition.fsq_action_name),
    )
    for definition in MACOS_ACTION_DEFINITIONS
    if definition.owner == "driver"
}
_macos_driver_capability = platform_driver_capability(
    platform="macos",
    backend=None,
    catalog=MACOS_DRIVER_ACTION_CATALOG,
)


def _android_driver_tool(
    fsq_action_name: str,
    *,
    description: str,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _android_driver_capability(
        fsq_action_name,
        description=description,
        metadata=metadata,
    )


def _web_driver_tool(
    fsq_action_name: str,
    *,
    description: str,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _web_driver_capability(
        fsq_action_name,
        description=description,
        metadata=metadata,
    )


def _windows_driver_tool(
    fsq_action_name: str,
    *,
    description: str,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _windows_driver_capability(
        fsq_action_name,
        description=description,
        metadata=metadata,
    )


def _macos_driver_tool(
    fsq_action_name: str,
    *,
    description: str,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _macos_driver_capability(
        fsq_action_name,
        description=description,
        metadata=metadata,
    )


def _discover_driver_function_schemas(
    driver: object,
    *,
    platform: HarnessPlatform,
    metadata: dict[str, object] | None = None,
) -> list[HarnessFunctionSchema]:
    return [_schema_from_capability_definition(definition, platform=platform) for definition in _discover_driver_capability_definitions(driver, platform=platform, metadata=metadata)]


def _discover_driver_capability_definitions(
    driver: object,
    *,
    platform: HarnessPlatform,
    metadata: dict[str, object] | None = None,
) -> list[CapabilityDefinition]:
    definitions: list[CapabilityDefinition] = []
    for definition in discover_capability_definitions(driver, metadata=metadata):
        if definition.executor_kind != "driver":
            continue
        updates: dict[str, object] = {}
        if definition.platform is None:
            updates["platform"] = platform
        if definition.backend is None:
            backend = _metadata_str(definition.metadata, "backend")
            if backend is not None:
                updates["backend"] = backend
        definitions.append(definition.model_copy(update=updates) if updates else definition)
    return definitions


def _default_driver_capability_definitions(*, platform: HarnessPlatform) -> list[CapabilityDefinition]:
    if platform == "android":
        from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver

        driver_class = AppiumConnectAndroidDriver
    elif platform == "web":
        from fsq_agent.drivers.web._playwright import PlaywrightWebDriver

        driver_class = PlaywrightWebDriver
    elif platform == "windows":
        from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver

        driver_class = PywinautoWindowsDriver
    elif platform == "macos":
        from fsq_agent.drivers.macos._appium_mac2 import AppiumMac2Driver

        driver_class = AppiumMac2Driver
    else:
        return []
    return _discover_driver_capability_definitions(driver_class, platform=platform)


def _metadata_str(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def _capability_matches(definition: CapabilityDefinition, name_or_alias: str) -> bool:
    return definition.name == name_or_alias or definition.fsq_command_alias == name_or_alias


def _with_driver_metadata(definition: CapabilityDefinition, updates: dict[str, object]) -> CapabilityDefinition:
    metadata = dict(definition.metadata)
    metadata.update(updates)
    model_updates: dict[str, object] = {"metadata": metadata}
    backend = updates.get("backend")
    if isinstance(backend, str):
        model_updates["backend"] = backend
    return definition.model_copy(update=model_updates)


def _schema_from_capability_definition(
    definition: CapabilityDefinition,
    *,
    platform: HarnessPlatform,
) -> HarnessFunctionSchema:
    driver_method = _metadata_str(definition.metadata, "driver_method") or definition.name
    fsq_action_name = _metadata_str(definition.metadata, "fsq_action_name")
    schema_metadata = dict(definition.metadata)
    schema_metadata.update(
        {
            "capability_name": definition.name,
            "executor_kind": definition.executor_kind,
            "driver_method": driver_method,
            "owner": definition.owner,
            "step_kind": definition.step_kind,
            "replay": definition.replay.model_dump(mode="json") if definition.replay else None,
        }
    )
    if fsq_action_name is not None:
        schema_metadata["fsq_action_name"] = fsq_action_name
    return HarnessFunctionSchema(
        name=definition.name,
        description=definition.description,
        params_json_schema=definition.params_json_schema,
        platform=definition.platform or platform,
        driver_method=driver_method,
        fsq_action_name=fsq_action_name,
        metadata=schema_metadata,
    )
