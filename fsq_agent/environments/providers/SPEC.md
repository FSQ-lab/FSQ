# Module: environments.providers

## Purpose

Own private Android, Web, Windows, macOS, and iOS host/runtime providers used by `PlatformRuntimeService`, including bounded installed-application lookup mechanics. These are not LLM providers and do not perform platform automation.

## Dependencies

Providers use standard-library host, import, and filesystem facilities, public Models contracts, and optional lazy imports of `appium_connect_agent.contracts.LocalSettings`, `appium_connect_agent.runtime.Runtime`, and `appium_connect_agent.automation.list_ios_devices` for static `Runtime.diagnose(target_driver="mac2" | "xcuitest")` inspection (acquiring only the upstream account coordination lock `runtime.lock` without mutating Workspace state or `~/.appium-connect-agent/` component and record directories) and read-only iOS device discovery. They must not invoke package managers or import adapters, Application, Config persistence, concrete drivers or harnesses, Execution, or top-level LLM `providers`.

## Public Interface

No public package API. Providers are selected internally by `environments`.

## Internal Structure

- Private Android, Web, Windows, macOS, and iOS provider modules.
- `_applications.py`: bounded native application inventory and explicit concrete target lookup, including the per-device iOS inventory cache; `_adb_transport.py` supplies fixed existing-server-only Android package queries.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: none outside `environments`.
- Internal modules: all platform provider modules.
- Domain boundaries: host/runtime mechanics only.
- Boundary models: shared values come from Models.
- Dependency direction: consumed only by the parent Environments service.
- Rationale: each provider is focused infrastructure.

## Error Handling

Providers perform read-only checks and return safe normalized outcomes. They never install or modify Python packages, system dependencies, services, targets, applications, devices, emulators, or virtual machines.

Android owns the existing-server-only ADB wire transport, response parsing, device inventory, and fixed package queries used by shared discovery and prerequisites. A missing server never triggers client auto-start, including disappearance between requests. Protocol failures, transport timeouts, missing requirements, device authorization/state, ambiguous selection and package-query errors remain distinguishable safe facts. Local host/port resolution, protocol/output limits and repair-command safety are governed by the parent Environments contract; no ADB logic is duplicated in adapters or Application.

Application inventory collection and explicit lookup use platform-local read-only metadata and canonical identity validation under the parent Environments limits. Android queries remain scoped to the selected online authorized device via the existing-server-only ADB transport; macOS inspects bundle `Info.plist` and localized `InfoPlist.strings` under standard application roots; Windows merges bundled preset paths, `App Paths` registry keys, and Start Menu `.lnk` targets into deduplicated `.exe` candidates. Providers report complete deduplicated candidate sets or explicit incomplete/error outcomes; they never certify semantic intent, rank candidates through an LLM, perform a full-disk scan, install applications, or invoke a shell from prompt text.

iOS Simulator inventory invokes fixed arguments `xcrun simctl listapps <udid>` without a shell, scoped to the exact validated booted UDID. Parsing accepts only recognized plist/JSON structures under elapsed-time, output-size, and candidate-count limits, using bundle IDs and available display/localized labels, with no arbitrary file references from command output. Malformed, truncated, timed-out, or failed queries are distinct from a valid empty inventory or unsupported physical-device enumeration. No fallback to device shells, Appium/WDA sessions, or device state changes is permitted.
