# Module: drivers.ios

## Purpose
Implement iOS mobile automation through the `appium-connect-agent` Python public library (`appium_connect_agent` backend), including lazy thread-affine managed loopback Appium server and XCUITest `Session` lifecycle, explicit application lifecycle, touch/keyboard/button/alert actions, compact semantic observations, visual inspection and assertions, and safe failure normalization.

## Dependencies
- `core.interfaces.IOSDriverInterface`, `capabilities`, and iOS parameter/result models.
- Optional `appium-connect-agent` (`appium_connect_agent.contracts`, `appium_connect_agent.runtime`, `appium_connect_agent.automation`), imported lazily at runtime without direct `Appium-Python-Client`, `selenium`, or private WebDriver access.

## Public Interface
Instances satisfy `IOSDriverInterface`; the concrete backend class is private outside Drivers and composition.

`ui_snapshot`/`uiSnapshot` returns the bounded compact current XCUITest control tree and `device_local_time` through `IOSUiSnapshotParams`. `inspect_element`/`inspectElement` returns unclipped structured element attributes while withholding `value` on `XCUIElementTypeSecureTextField`.

`input_text`/`inputText` and `clear_text`/`clearText` verify before any focus, clear, or keystroke side effect that the uniquely resolved target is an editable XCUITest control (`TextField`, `SecureTextField`, `SearchField`, `TextView`, or `ComboBox`) with explicit `enabled=True` and `displayed=True`. Before mutation, a real nonempty `id` (`name`) is bound and verified against the original `type`, `id`, and `bounds`; otherwise a text-independent locator is retained or a mutable `value`/`label`/bare-target locator is rebound through an exact `-ios predicate string` conjunction of `type` and `rect` (`x`, `y`, `width`, `height`). Primary `Element.send_keys` falls back at most once to `Element.send_macos_keys` only when `request_state` is `"not_sent"` or `"response_received"`; `response_unknown` fails safely without re-sending text. On non-secure controls, `input_text` and `clear_text` re-inspect the bound target and require preserved target identity plus exact post-write `value` (or empty/`placeholder_value` after clearing); `XCUIElementTypeSecureTextField` never reads back secret values and verifies only preserved structural target identity and enabled state.

`swipe` supports mutually exclusive full-screen direction, explicit logical points, and element-direction modes over `Device.display_size()` logical points. Untargeted directional swipes compute safe interior logical coordinates within 20%–80% vertically and 18%–82% horizontally via `Device.swipe_between(...)`. Element-scoped directional swipes inspect `bounds`, require positive logical bounds inside the current display, compute bounded center-symmetric span endpoints with horizontal edge insets, and execute `Device.swipe_between(...)` rather than unvalidated full-element backend swipes.

## Internal Structure
- Private `AppiumConnectIOSDriver` backend implementation (`_appium_connect.py`), decorated with `@_ios_driver_tool(...)` bound to `IOS_ACTION_DEFINITIONS`.
- Thread-affine execution worker (`ThreadPoolExecutor(max_workers=1)`) keeping `Runtime.start_managed(...)`, `create_xcuitest_session(...)`, `Session.ensure_alive(...)`, `Session`/`Element`/`Device` calls, and `close()` on one owner thread.
- Lazy managed Appium loopback server + XCUITest session lifecycle: construction is lazy and rejects non-`None` initial-Session WDA policy overrides (`xcuitest_wait_for_idle_timeout_seconds`, `xcuitest_snapshot_max_depth`, `xcuitest_accessibility_deadline_seconds`) when a borrowed `Session` is supplied; `ensure_session` resolves explicit or auto-selected single device UDID, honors Simulator `bundle_id` / `.app` or real-device `bundle_id` / `.ipa` with WDA code-signing parameters, forwards configured initial-Session WDA policies and `new_command_timeout_seconds` (default `900.0`) to `create_xcuitest_session(...)` (preserving them on explicit `launchApp(new_session=true)` replacement), and scales observation/locator operation timeouts safely with `action_timeout_seconds` and any configured `xcuitest_accessibility_deadline_seconds`.
- Structured locator adapter translating `IOSLocator` (`accessibility_id`, `id`, `name`, `label`, `value`, `element_type`, `predicate`, `class_chain`, `xpath`) into `Session.element(...)` with `Element.inspect(...)` uniqueness and ambiguity reporting.
- Compact `<XCUIElementType*>` snapshot builder with `node_count`, interactive-node preservation, depth bounding, text clipping, and safe unparsed fallback.
- Consumes package-internal `drivers._visual_inspection` for public-SDK visual provider adaptation (`inspectWithAI`), same-frame evidence handoff, and safe observation results without host coordinate mapping.

## Python Architecture
- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces, `capabilities`, and `models`; never imports Harnesses, sibling platform driver subpackages, or adapters.
- Rationale: one focused XCUITest backend implementation is sufficient.

## Error Handling
Normalized action/result and failure-category envelopes remain intact. Safe structured resolution reasons distinguish `not_found`, `ambiguous`, `invalid_locator`, `session_unavailable`, and `backend_error`; `TargetError` is mapped as definitive `not_found`/`ambiguous` only when `request_state != "response_unknown"` and the local `SessionInfo.state` is `"active"`. Backend diagnostics expose only allowlisted SDK `backend_code`, enumerated `request_state`, 32-lowercase-hex `operation_id`, allowlisted `completed_steps`, and local `session_state`; ambiguity diagnostics report match counts and structural types/bounds without echoing raw observed text values. Sensitive runtime errors from `appium-connect-agent` preserve secret/UDID/signing redaction; driver-constructed configuration errors and bounded diagnostics never echo `xcode_org_id`, `xcode_signing_id`, `updated_wda_bundle_id`, `udid`, submitted or observed text, `runtime_secret` values, raw backend stack traces, or unrestricted request/response bodies.

## Current Invariants
- Construction and registry discovery do not import `appium_connect_agent`, start Appium, query `simctl`, or launch an application.
- All `Runtime` and `Session` operations execute on one driver-owned single-worker thread to satisfy `appium-connect-agent` owner-thread affinity; idle liveness checks use public `Session.ensure_alive(...)` before action execution.
- Non-active owned sessions and drivers with `_cleanup_pending` or `_closed` state fail fast with safe `session_unavailable`/`cleanup_pending`/`closed` diagnostics and never implicitly close or reconstruct a session during ordinary tool, observation, or context calls; only explicit `launchApp(new_session=true)` on an active owned session replaces the session.
- Progressive startup ownership retains `_runtime`, `_managed_scope`, `_connection`, and any `exc.session` attached to a failed `create_xcuitest_session(...)` call (cleaning up `service_port_in_use` attempts via `Runtime.cleanup_managed(...)`) so a subsequent explicit `close()` can complete terminal `Session.dispose(...)` and managed-scope settlement on the owner thread while retaining safe `session_quit_unknown` settlement diagnostics.
