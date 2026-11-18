# Module: capabilities

## Purpose

Own the neutral capability declaration layer for fsq-agent. This module provides the shared neutral decorator, the catalog-backed platform driver decorator factory, platform action catalog contracts, catalog-backed validation, and decorated-method discovery helpers that produce serializable `CapabilityDefinition` records for recordable CommonTool and PlatformTool capabilities.

This module does not execute capabilities, invoke CommonTool or PlatformTool providers, call harnesses or drivers, construct SDK tools, parse FSQ YAML, build registries, or generate reports. Execution ownership remains in `core`, `agent`, `cli`, and entry-layer bootstrap code. Dynamic-only AgentTools are outside this declaration layer.

## Dependencies

- Internal project dependencies: `models` only. Uses `CapabilityDefinition`, `CapabilityExecutorKind`, `ExecutableStepKind`, `HarnessPlatform`, `ReplayPolicy`, and `ConfigurationError`.
- External dependencies: standard library dataclasses, inspect, typing, and Pydantic `BaseModel` type references.
- Forbidden dependencies: `core`, `tools`, `agent`, `cli`, `fsq`, `config`, `providers`, `report`, `observation`, `knowledge`, `skills`, OpenAI Agents SDK types, concrete driver/runtime objects, and backend SDKs.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `CapabilityActionDefinition`: Lightweight catalog entry for authored platform actions. It describes authored action name, canonical capability name, executor kind, owner, parameter model, optional required method name, step kind, replay policy, default evidence policy, optional post-action delay override, and safe metadata defaults. The authored action name supplies `ReplayPolicy(kind="fsq_command").alias` for replayable commands; catalog entries do not carry separate capability aliases or SDK schema strictness flags.
- `CapabilityActionCatalog`: Mapping type alias from authored action name to `CapabilityActionDefinition`.
- `capability`: Neutral low-level decorator that attaches capability declaration metadata to a function or method. It can declare CommonTool or PlatformTool capabilities, but it does not register or execute them.
- `platform_driver_capability`: Factory that binds a platform/backend/catalog and returns a decorator for catalog-backed driver-backed PlatformTool declarations.
- `discover_capability_definitions(target: object, *, metadata: dict[str, object] | None = None) -> list[CapabilityDefinition]`: Inspect a decorated class or instance without invoking methods and return serializable capability definitions.

The neutral decorator API accepts canonical name, executor kind (`common` or `driver`), owner, parameter model, description, platform, backend, step kind, optional post-action delay override, sensitivity flag, replay policy, safe metadata, and optional catalog/action name inputs. It does not accept a duplicate alias list for primary authored replay command names and does not accept a per-capability SDK schema strictness flag. Default screenshot and UI snapshot capture is not declared through decorators or catalogs; it is a core runner policy derived from live `driver` executor metadata and step kind. `post_action_delay_seconds=None` means inherit the configured family default; `0` explicitly disables runner-owned post-action delay for that capability; positive values override the configured default. CommonTool declarations use `capability(...)` directly. Android, Web, Windows, and macOS platform driver actions must be declared through catalog-backed `platform_driver_capability` helpers rather than standalone compatibility helper decorators.

## Platform Declaration Blocks

Shared declaration rules:

- `capability`, `platform_driver_capability`, discovery, and `CapabilityDefinition` output stay platform-neutral.
- Platform-specific authored command names, parameter models, replay policy, backend metadata, step kind, and required driver method names belong in platform action catalogs. Default evidence capture timing and artifact kinds do not belong in platform action catalogs. Replayable authored command names are represented by `ReplayPolicy(kind="fsq_command").alias` in discovered capability metadata.
- Registry/bootstrap code, not this module, chooses which platform catalog definitions are active.

Android declaration block:

- Android `appium_connect_agent` driver methods use catalog-backed `platform_driver_capability` entries with Android replay aliases and parameter models.
- Android platform-level non-driver behavior must not introduce a live `harness` executor kind; new recordable platform behavior should be represented as a driver-backed PlatformTool or a CommonTool after SPEC review.

Web declaration block:

- Web Playwright driver methods use catalog-backed `platform_driver_capability` entries with Web replay aliases and parameter models, including explicit browser lifecycle actions `startBrowser`/`closeBrowser` alongside page actions such as `navigateTo` and `uiSnapshot`.
- Web platform-level non-driver behavior must not introduce a live `harness` executor kind; new recordable platform behavior should be represented as a driver-backed PlatformTool or a CommonTool after SPEC review.

macOS declaration block:

- macOS Appium Mac2 driver methods use catalog-backed `platform_driver_capability` entries with macOS desktop replay aliases and parameter models, including lifecycle actions `launchApp`/`killApp`, desktop interactions such as `clickOn`, `doubleClickOn`, `rightClickOn`, `typeText`, `pressKey`, `hoverOn`, and `dragTo`, observations such as `takeScreenshot` and `uiSnapshot`, and assertions such as `assertVisible`, `assertElementsOrder`, and `assertWithAI`.
- macOS reuses the existing neutral decorators, catalog validation, discovery, replay metadata, step-kind metadata, and backend metadata contracts. It must not introduce a macOS-only decorator, direct MCP schema importer, runtime Appium discovery path, default evidence metadata, or live `harness` executor kind in `capabilities`.
- macOS catalog entries are declaration-time validation inputs only. Registry/bootstrap code chooses whether macOS entries are active based on `harness.platform == "macos"`.

## Internal Structure

- `__init__.py`: Public exports only.
- `_decorators.py`: Neutral decorator, catalog-backed platform driver decorator factory, marker metadata, and legal-combination validation.
- `_catalog.py`: `CapabilityActionDefinition`, catalog mapping type, catalog lookup, and catalog-to-capability defaults.
- `_discovery.py`: Reflection helpers that discover decorated methods on classes or instances and convert metadata into `CapabilityDefinition` values.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: neutral declaration decorator, catalog-backed platform driver decorator factory, catalog entry types, and discovery helpers exported from `__init__.py`.
- Internal modules: `_decorators.py`, `_catalog.py`, and `_discovery.py` are private implementation modules.
- Domain boundaries: this module owns declaration metadata and validation only. CommonTool and PlatformTool safety/invocation live in `core`; AgentTool behavior and SDK helper adaptation live in `tools`; runner routing, harness/provider dispatch, and evidence live in `core`; strict FSQ parsing lives in `fsq` and entry modules.
- Boundary models: serializable capability contracts come from `models`. Decorator marker objects and catalog helper dataclasses are not persisted as runtime results.
- Dependency direction: imports public `models` only; may be imported by `core`; must not import `tools` or any execution or entry-layer module.
- Rationale: the module is a focused reusable declaration utility with validation and reflection only, so Level 2 is sufficient and a higher architecture level would add ceremony without isolating additional side effects.

## Error Handling

Declaration and discovery fail fast with `ConfigurationError` when a decorated capability is inconsistent or unsafe to expose:

- Missing or unresolvable parameter model.
- Unknown catalog action name.
- Catalog entry owner or executor kind incompatible with the selected helper.
- Decorated method name does not match a catalog-required method name.
- Method annotation conflicts with the catalog or explicit parameter model.
- Invalid executor kind, owner, platform, backend, or sensitivity combination.
- Negative post-action delay values in decorator arguments or catalog entries.
- Capability metadata attempts to store non-serializable runtime objects.

Duplicate capability names, replay alias conflicts, ambiguous replay aliases, and executable routing validation remain registry/bootstrap concerns owned by `core` and entry-layer code.

## Verification Scope

- Verification covers neutral decorator metadata, catalog-backed platform declarations, side-effect-free discovery, and the `CapabilityDefinition` shape consumed by registry/bootstrap code.
- Boundary verification ensures `capabilities` imports only `models` among project modules and never depends on execution modules, SDK objects, or concrete backend libraries.

## Current Invariants

- One declaration mechanism prevents CommonTool and platform-specific PlatformTool capabilities from growing separate decorator semantics.
- Thin compatibility helper decorators are not public API; declaration code follows the live paths: direct `capability(...)` for simple declarations and catalog-backed `platform_driver_capability(...)` for platform driver declarations.
- Platform differences belong in action catalogs, not in per-platform decorator implementations. Android, Web, Windows, and macOS catalogs reuse `platform_driver_capability`.
- `CapabilityDefinition` remains the runtime contract and registry input. Decorators attach declaration metadata to functions, including optional post-action delay overrides; discovery converts that metadata into serializable definitions.
- Discovery must be side-effect free. It may inspect method signatures and type hints, but it must not call methods, connect to devices, instantiate SDK tools, or build providers.
- Runtime routing is out of scope. `executor_kind` metadata is consumed by `core.StepRunner`; `capabilities` never invokes the selected provider or executor. Live declarations may use only `common` and `driver` executor kinds.
- `models` stays contract-only. Keeping decorator behavior out of `models` avoids turning the shared schema module into a reflection/behavior layer.
