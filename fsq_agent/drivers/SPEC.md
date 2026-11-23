# Module: drivers

## Purpose

Own concrete platform automation backends for Android, Web, Windows, macOS, and iOS. Drivers implement public Core interfaces, perform backend actions and observations, normalize backend failures, and keep optional backend imports lazy. Drivers do not own complete execution, harness runtime context, transport behavior, workspace policy, or host runtime installation.

## Dependencies

- `core.interfaces`: public driver, observation, evaluator, and artifact-related protocols.
- `capabilities`: neutral declaration decorators and discovery contracts.
- `models`: platform parameters, capability metadata, normalized results, and errors.

Drivers must not import adapters, Application, Execution, concrete harnesses, Config persistence, environment providers, or provider session construction.

## Public Interface

The root package exports no concrete backend classes. Each platform package is a canonical implementation owner consumed through `core.interfaces` and composition factories. Driver capability metadata discovery is available through a narrow package-internal composition boundary; callers do not import concrete backend modules merely to inspect capability definitions.

The existing Android, macOS, and iOS factories receive typed initial-Session policies from `AndroidHarnessSettings` (`uiautomator2_wait_for_idle_timeout_ms`, `new_command_timeout_seconds`), `MacOSHarnessSettings` (`new_command_timeout_seconds`, `skip_app_kill`), and `IOSHarnessSettings` (`new_command_timeout_seconds`, `wda_launch_timeout_ms`, `xcuitest_wait_for_idle_timeout_seconds`, `xcuitest_snapshot_max_depth`, `xcuitest_accessibility_deadline_seconds`, `xcuitest_animation_cool_off_timeout_seconds`, `xcuitest_page_source_excluded_attributes`) plus optional runtime `app_id`/`serial` (Android), `bundle_id` (macOS), and `bundle_id`/`udid` (iOS) overrides, forwarding them to their private `appium_connect_agent` backends. Public DriverFactory/HarnessFactory signatures and dependency direction remain unchanged; callers do not import concrete backends or SDK internals to apply these policies.

Every factory-created Driver implements the public idempotent `close()` disposal contract inherited from `DriverObservationInterface`. Disposal releases only owned connections, sessions, and worker resources and never constructs an unopened backend or stops a shared host service. Web launch disposal closes the owned browser/Playwright session and executor; Web attach disposal closes the FSQ-owned page, disconnects Playwright, and leaves the borrowed browser context, unrelated pages, and operator-owned browser process running. Windows disposal runs owned-target cleanup (terminating a newly launched owned process or closing only a newly opened single-instance handoff window while leaving pre-existing or unlaunched desktop processes untouched) and shuts down its owned worker. Android, macOS, and iOS (`appium_connect_agent`) disposal runs on each driver's dedicated single-worker thread through the public SDK lifecycle contracts (`Session.quit`, explicit `Session.dispose` for uncertain/disposing sessions, managed `Runtime.start_managed(...)` scope exit and `Runtime.cleanup_managed(...)` when pending, and worker shutdown), retaining pending ownership until the owned Session's local resources, managed service, and worker are settled. Android, macOS, and iOS disposal never terminate the target application, uninstall packages, or stop/restart ADB or Simulator/device services. Disposal is distinct from recordable `close_browser`, `kill_app`, `stop_app`, or other lifecycle capabilities and creates no execution or replay record.

For Android, macOS, and iOS, an unconfirmed remote quit (`session_quit_unknown`) is distinct from pending owned-resource cleanup. When the public SDK terminal-disposal path (`Session.dispose`) completes with `SessionInfo.state == "disposed"` and the owned Session's local client, managed service resources, scope, and worker are all settled, `close()` returns normally while retaining safe remote-quit diagnostics. An unconfirmed DELETE is never represented as a successful remote Session deletion. Incomplete terminal disposal or any remaining owned resource still makes close fail; Web and Windows disposal semantics remain unchanged.

## Internal Structure

The sole external importer of `_factory._DriverFactoryImplementation` is `core.interfaces._factories`, implementing the stable lazy DriverFactory wrapper. Driver implementations consume Core protocols, not that wrapper.

Metadata-only composition has separate named importers: `core._default_capabilities` may use `_factory._driver_class_for_backend` and `_capabilities._discover_driver_capability_definitions`; private Android/Web/Windows/macOS/iOS Harness modules may use `_capability_matches`, `_discover_driver_capability_definitions`, `_schema_from_capability_definition`, and `_with_driver_metadata` against injected instances. Compatibility `core.harness._driver_tools` aliases the canonical metadata module without another implementation. These exceptions permit no arbitrary private access or backend construction/connection during metadata inspection.

- `android/`: `appium_connect_agent` backend and Android action/observation implementation.
- `web/`: Playwright backend and browser/page lifecycle implementation.
- `windows/`: pywinauto backend and Windows application/window implementation.
- `macos/`: `appium_connect_agent` Mac2 backend and session/control-tree implementation.
- `ios/`: `appium_connect_agent` XCUITest backend and mobile session/observation/action implementation.
- Private shared modules: driver capability declarations/discovery, semantic UI-tree compaction and XML serialization (`_tree_compaction.py`), provider-neutral tri-state AI assertion backend support (`_ai_assertion.py`), and read-only visual inspection support (`_visual_inspection.py`).

## Python Architecture

- Architecture level: Level 2 Simple Package with platform subpackages.
- Public API: public Core driver interfaces, not concrete backend classes.
- Internal modules: concrete backend classes, decorators, discovery helpers, and AI assertion mixins.
- Domain boundaries: platform automation and backend error normalization only.
- Boundary models: action params and normalized results come from `models`.
- Dependency direction: harnesses receive drivers through Core interfaces; Drivers never import Harnesses.
- Rationale: each platform backend is focused infrastructure and does not require an application or domain layer.

## Error Handling

Missing optional backend libraries and unsupported hosts produce normalized unavailable/unsupported runtime outcomes when the backend is constructed or invoked, not package import failures. Backend command output and exceptions are bounded and secret-safe.

## Current Invariants

- Registry bootstrap, strict parsing, and driver construction remain lazy and do not connect to devices, launch browsers/apps, or start sessions.
- Browser/app lifecycle remains explicit through declared capabilities.
- Concrete drivers satisfy `core.interfaces` protocols without being public cross-module types.
- Capability metadata, rather than action-name switches in Runner or Harnesses, remains the execution source of truth.
