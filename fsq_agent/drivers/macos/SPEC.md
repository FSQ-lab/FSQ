# Module: drivers.macos

## Purpose

Implement macOS desktop automation through the `appium-connect-agent` public Python library (`appium_connect_agent` backend), including lazy thread-affine managed loopback Appium server and Mac2 `Session` lifecycle, explicit application lifecycle, desktop actions, compact semantic observations, assertions, and safe failure normalization.

## Dependencies

- `core.interfaces.MacOSDriverInterface`, `capabilities`, and macOS parameter/result models.
- Optional `appium-connect-agent` (`appium_connect_agent.contracts`, `appium_connect_agent.runtime`, `appium_connect_agent.automation`), imported lazily at runtime without direct `Appium-Python-Client`, `selenium`, or private WebDriver access.

## Public Interface

Instances satisfy `MacOSDriverInterface`; the concrete backend class is private outside Drivers and composition.

`ui_snapshot`/`uiSnapshot` returns the bounded compact current Mac2 control tree through `MacOSUiSnapshotParams`. It has no platform-specific element-query mode.

## Internal Structure

- Private `AppiumConnectMacOSDriver` backend implementation, bounded tree/candidate helpers, and macOS capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Element Resolution

- Structured locator fields constrain the same element conjunctively through `Session.element(...)` and `Session.observe_tree()`/`Element.inspect(...)` without raw WebDriver queries. `name` retains semantic-name compatibility across backend identity/label/value/title signals, while explicit `label`, `value`, `title`, and `accessibilityId` constrain their respective attributes; `controlType`, `className`, and `role` constrain element type/role signals. Explicit `xpath` and `predicate` selectors intersect with any accompanying semantic constraints rather than bypassing them. Failed combined lookup never degrades to type-only or another weaker target.
- Literal values are safely encoded for XPath and `-ios predicate string` query syntax, including apostrophes, quotes, and backslashes. Backend-owned selector syntax is not assembled by interpolating unescaped user text.
- Element-targeted actions and assertions require a unique live match. Multiple distinct matching elements produce an ambiguity `ConfigurationError` (`resolution_reason="ambiguous"`) with bounded candidate summaries, not a first-match action. Candidate descriptions include available type, text, state, geometry, and complete usable locator signals; uncertain uniqueness never permits an action.
- Explicit coordinate-only targeting (`Device.click_at`, `Device.hover_at`, `Device.swipe_between`) remains supported. Coordinates must not silently replace a failed semantic locator or bypass additional supplied element constraints. An element action uses `Element.click` / `Element.double_click` / `Element.right_click` / `Element.hover` / `Element.drag_and_drop` or the resolved element's live `bounds` center; screenshot-inferred coordinates are not element evidence.
- Display-clipped labels and values are never advertised as complete exact-match locators. Complete locator values remain separate from bounded display previews; values exceeding response limits are explicitly marked unavailable rather than silently shortened or converted to broad prefix matches. Returned locator candidates are revalidated against live state before actions and do not promise cross-session stability.
- `type_text` uses the resolved element input API (`Element.click`, optional `Element.clear`, `Element.send_keys`, and `Element.inspect(attributes=("value",))`) when targeted, and retries once through `Element.send_macos_keys` followed by another value check when the initial value does not reflect the requested text. A still-unsatisfied readable-element postcondition returns `action_error`; untargeted input without a semantic target or locator optionally clicks `point` via `Device.click_at` and delivers keys via `Device.send_keys` without claiming a value postcondition, while untargeted `clear=true` returns `action_error`.

## Structured Observation

- Compact tree output preserves interactive element identity and useful state when present in the source. Display text remains bounded, with explicit clipping metadata; default-state omission is documented. Requested depth limits identify truncated children instead of implying a complete subtree. Parsing and scanning enforce resource bounds and reject DTD/entity declarations.
- Snapshot diagnostics distinguish backend source facts and compact-tree coverage without embedding unrestricted raw source. A screenshot and a tree are separate observations and are not described as an atomic capture. A compact tree is observation context rather than proof that an omitted control is absent.

## Error Handling

Existing normalized action/result and failure-category envelopes remain intact. Safe structured resolution reasons distinguish `not_found`, `ambiguous`, `invalid_locator`, `session_unavailable`, and `backend_error`. A readable-element text-entry postcondition that remains unsatisfied after fallback is an `action_error`. Syntax errors, session/transport failures, unsupported operations, and unexpected backend errors are not swallowed into ordinary target-not-found results. Diagnostics remain bounded and secret-safe, without raw backend stack traces or unrestricted request/response bodies.

## Verification Scope

- Offline verification covers literal escaping, conjunctive matching, ambiguity without side effects, safe error classification, text-entry value verification and fallback, and display-preview versus exact-locator separation.
- Snapshot verification compares sanitized source and compact output for interactive nodes, duplicate labels, offscreen nodes, missing state, depth clipping, and malformed source. Real-browser success is not inferred from offline tests or screenshots alone.

## Current Invariants

- Construction and registry discovery do not import `appium_connect_agent`, start Appium, or launch an application.
- All `Runtime` and `Session` operations execute on one driver-owned single-worker thread to satisfy `appium-connect-agent` owner-thread affinity; `Session` creation remains explicit through `launch_app`.
