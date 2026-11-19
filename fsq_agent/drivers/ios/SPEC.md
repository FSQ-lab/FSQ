# Module: drivers.ios

## Purpose
Implement iOS mobile automation through the `appium-connect-agent` Python public library (`appium_connect_agent` backend), including lazy thread-affine managed loopback Appium server and XCUITest `Session` lifecycle, explicit application lifecycle, touch/keyboard/button/alert actions, compact semantic observations, visual inspection and assertions, and safe failure normalization.

## Dependencies
- `core.interfaces.IOSDriverInterface`, `capabilities`, and iOS parameter/result models.
- Optional `appium-connect-agent` (`appium_connect_agent.contracts`, `appium_connect_agent.runtime`, `appium_connect_agent.automation`), imported lazily at runtime without direct `Appium-Python-Client`, `selenium`, or private WebDriver access.

## Public Interface
Instances satisfy `IOSDriverInterface`; the concrete backend class is private outside Drivers and composition.

`ui_snapshot`/`uiSnapshot` returns the bounded compact current XCUITest control tree and `device_local_time` through `IOSUiSnapshotParams`. `inspect_element`/`inspectElement` returns unclipped structured element attributes while withholding `value` on `XCUIElementTypeSecureTextField`.

## Internal Structure
- Private `AppiumConnectIOSDriver` backend implementation (`_appium_connect.py`), decorated with `@_ios_driver_tool(...)` bound to `IOS_ACTION_DEFINITIONS`.
- Thread-affine execution worker (`ThreadPoolExecutor(max_workers=1)`) keeping `Runtime.start_managed(...)`, `create_xcuitest_session(...)`, `Session.ensure_alive(...)`, `Session`/`Element`/`Device` calls, and `close()` on one owner thread.
- Lazy managed Appium loopback server + XCUITest session lifecycle: construction is lazy; `ensure_session` resolves explicit or auto-selected single device UDID, honors Simulator `bundle_id` / `.app` or real-device `bundle_id` / `.ipa` with WDA code-signing parameters, and sets `new_command_timeout_seconds=900.0` by default.
- Structured locator adapter translating `IOSLocator` (`accessibility_id`, `id`, `name`, `label`, `value`, `element_type`, `predicate`, `class_chain`, `xpath`) into `Session.element(...)` with `Element.inspect(...)` uniqueness and ambiguity reporting.
- Compact `<XCUIElementType*>` snapshot builder with `node_count`, interactive-node preservation, depth bounding, text clipping, and safe unparsed fallback.
- Consumes package-internal `drivers._visual_inspection` for public-SDK visual provider adaptation (`inspectWithAI`), same-frame evidence handoff, and safe observation results without host coordinate mapping.

## Python Architecture
- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces, `capabilities`, and `models`; never imports Harnesses, sibling platform driver subpackages, or adapters.
- Rationale: one focused XCUITest backend implementation is sufficient.

## Error Handling
Normalized action/result and failure-category envelopes remain intact. Safe structured resolution reasons distinguish `not_found`, `ambiguous`, `invalid_locator`, `session_unavailable`, and `backend_error`. Syntax errors, session/transport failures, unsupported operations, and unexpected backend errors are not swallowed into ordinary target-not-found results. Sensitive runtime errors from `appium-connect-agent` preserve secret/UDID/signing redaction; driver-constructed configuration errors and bounded diagnostics never echo `xcode_org_id`, `xcode_signing_id`, `updated_wda_bundle_id`, `runtime_secret` values, raw backend stack traces, or unrestricted request/response bodies.

## Current Invariants
- Construction and registry discovery do not import `appium_connect_agent`, start Appium, query `simctl`, or launch an application.
- All `Runtime` and `Session` operations execute on one driver-owned single-worker thread to satisfy `appium-connect-agent` owner-thread affinity; idle liveness checks use public `Session.ensure_alive(...)` before action execution.
- `close()` settles the XCUITest `Session` before exiting the managed `Runtime` scope, runs `cleanup_managed(...)` if cleanup remains pending, and shuts down the worker executor last.
