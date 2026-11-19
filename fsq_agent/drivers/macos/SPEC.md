# Module: drivers.macos

## Purpose

Implement macOS desktop automation through the `appium-connect-agent` public Python library (`appium_connect_agent` backend), including lazy thread-affine managed loopback Appium server and Mac2 `Session` lifecycle, explicit application lifecycle, desktop actions, compact semantic observations, assertions, and safe failure normalization.

## Dependencies

- `core.interfaces.MacOSDriverInterface`, `capabilities`, and macOS parameter/result models.
- Optional `appium-connect-agent` (`appium_connect_agent.contracts`, `appium_connect_agent.runtime`, `appium_connect_agent.automation`), imported lazily at runtime without direct `Appium-Python-Client`, `selenium`, or private WebDriver access.

## Public Interface

Instances satisfy `MacOSDriverInterface`; the concrete backend class is private outside Drivers and composition.

`ui_snapshot`/`uiSnapshot` returns the bounded compact current Mac2 control tree through `MacOSUiSnapshotParams`, without a platform-specific element-query mode or a device-clock probe.

`take_screenshot`/`takeScreenshot` captures a current Mac2 PNG frame through `MacOSTakeScreenshotParams`, returning raw PNG bytes for Harness persistence (`ArtifactStore` screenshot artifact plus `byte_length`) or a normalized `observation_error` envelope with allowlisted capture diagnostics on failure.

`inspect_element` (`inspectElement`) inspects one uniquely resolved element via `Element.inspect` for a non-empty subset of the 14 Mac2 attributes (`id`, `text`, `type`, `label`, `title`, `value`, `placeholder_value`, `element_type`, `enabled`, `focused`, `selected`, `hittable`, `displayed`, `bounds`), returning unclipped strings, explicit observation states, and integer `{x, y, width, height}` bounds. Whenever `text` or `value` is requested, the driver inspects `type` first and withholds `text`/`value` (`state="unavailable"`, `reason="protected_text"`) without querying them if `type` is `SecureTextField` / `XCUIElementTypeSecureTextField` or unknown; glyph-only mask strings are likewise marked unavailable.

`inspect_with_ai` (`inspectWithAI`) is a read-only observation backed by `Session.device.inspect_with_ai` on the owner worker, reusing `visual_assertion_policy_resolver(context, capture_sequence)` and `AndroidVisualAssertionPolicy`. Denied/missing upload policy, non-`"non_sensitive"` frames, or missing model evaluator fail closed before screenshot persistence or upload. `mode="extract"` validates single-frame PNG and `AssertionBudget`, writes the callback PNG once to `ArtifactStore`, and returns the summary without candidates. `mode="locate"` hits the pinned SDK's macOS guard (`pointer_mapping_unavailable`) and returns a safe non-success observation result without host-guessed Retina or window coordinate math.

`assert_with_ai` (`assertWithAI`) evaluates scoped image-space or reasoned full-screen visual assertions through the shared tri-state (`passed | failed | inconclusive`) contract, sharing one privacy-approved single-frame PNG with step evidence and adjudicating structured same-target Mac2 deterministic facts from `inspect_element` so contradictory facts force `failed` (`deterministic_conflict=True`).

`assert_not_visible` (`assertNotVisible`) polls `Element.inspect(attributes=("displayed",))` on semantic or element-locator targets (rejecting coordinate-only points), passing on `displayed=False` or definitive `target_not_found`, reporting ambiguity when multiple matches persist, failing with `assertion_error` if still visible at timeout, and never treating `response_unknown` or inactive sessions as absence.

`assert_state` (`assert`) requires a non-empty element locator on `params.element` and performs branch-minimal inspection: text assertions take precedence (checking `type` first before reading `value` or `text` so `SecureTextField` fails safely without plaintext reads; `attribute="auto"` selects the first available value in `value`, `label`, `title`, `text`); state-only assertions request only explicitly supplied booleans in canonical order `("enabled", "focused", "selected", "hittable", "displayed")` with strict boolean equality; locator-only existence assertions request only `("enabled",)` to prove unique resolution.

`scroll_on` (`scrollOn`) uniquely resolves a semantic/locator target's live `bounds` center (without falling back to coordinates on miss or ambiguity) or consumes an explicit coordinate-only `point`, then invokes `Session.device.scroll_at(x, y, delta_x=..., delta_y=..., key_modifier_flags=..., control=...)`.

## Internal Structure

- Private `AppiumConnectMacOSDriver` backend implementation, bounded tree/candidate helpers (`_elements.py`), public-SDK visual inspection adapter (`_visual_inspection.py`), and macOS capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Element Resolution

- Structured locator fields constrain the same element conjunctively through `Session.element(...)` and `Session.observe_tree()`/`Element.inspect(...)` without raw WebDriver queries. `name` retains semantic-name compatibility across backend identity/label/value/title signals, while explicit `label`, `value`, `title`, and `accessibilityId` constrain their respective attributes; `controlType`, `className`, and `role` constrain element type/role signals. Explicit `xpath` and `predicate` selectors intersect with any accompanying semantic constraints rather than bypassing them. Failed combined lookup never degrades to type-only or another weaker target.
- Literal values are safely encoded for XPath and `-ios predicate string` query syntax, including apostrophes, quotes, and backslashes. Backend-owned selector syntax is not assembled by interpolating unescaped user text.
- Element-targeted actions, observations, and assertions require a unique live match. Multiple distinct matching elements produce a `target_resolution_error` (`resolution_reason="ambiguous"`) with bounded candidate summaries, not a first-match action. Candidate descriptions include available type, text, state, geometry, and complete usable locator signals; uncertain uniqueness never permits an action.
- Explicit coordinate-only targeting (`Device.click_at`, `Device.hover_at`, `Device.scroll_at`, `Device.swipe_between`) remains supported on coordinate-capable actions. Coordinates must not silently replace a failed semantic locator or bypass additional supplied element constraints. An element action uses `Element.click` / `Element.double_click` / `Element.right_click` / `Element.hover` / `Element.drag_and_drop` or the resolved element's live `bounds` center; screenshot-inferred coordinates are not element evidence.
- Display-clipped labels and values are never advertised as complete exact-match locators. Complete locator values remain separate from bounded display previews; values exceeding response limits are explicitly marked unavailable rather than silently shortened or converted to broad prefix matches. Returned locator candidates are revalidated against live state before actions and do not promise cross-session stability.
- `type_text` inspects `("type", "id", "enabled", "displayed", "bounds")` before clicking or typing when an element is targeted, rejecting disabled or hidden targets without UI side effects. When the caller's target or locator depends on mutable text, the driver binds a proven unique `accessibility id` (falling back to an exact `-ios predicate string` over `identifier`, `amType`, and `amRect` if needed, or `amType` + `amRect` when `id` is absent) before mutation so post-write readback does not re-query stale pre-edit text. Non-`SecureTextField` controls read initial `value`, execute `click`, optional `clear`, and `send_keys` on the bound element, verify post-write identity and `value` (retrying once via `Element.send_macos_keys` when needed), and return `action_error` (`reason="verification_unavailable"`) if geometry shifts on a no-id binding or `value` remains unsatisfied. `SecureTextField` controls execute bound `click`, optional `clear`, and `send_keys` and verify post-entry target identity without reading, comparing, or echoing plaintext `value`. Untargeted input without a semantic target or locator optionally clicks `point` via `Device.click_at` and delivers keys via `Device.send_keys` without claiming a value postcondition, while untargeted `clear=true` returns `action_error`.

## Structured Observation

- Compact tree output preserves interactive element identity and useful state when present in the source. Display text remains bounded, with explicit clipping metadata; default-state omission is documented. Requested depth limits identify truncated children instead of implying a complete subtree. Parsing and scanning enforce resource bounds and reject DTD/entity declarations.
- Snapshot diagnostics distinguish backend source facts and compact-tree coverage without embedding unrestricted raw source. A screenshot and a tree are separate observations and are not described as an atomic capture. A compact tree is observation context rather than proof that an omitted control is absent.

## Error Handling

Existing normalized action/result and failure-category envelopes remain intact. Selector/argument validation errors map to `configuration_error` (`resolution_reason="invalid_locator"`); definitive `target_not_found` and `target_ambiguous` on an active session map to `target_resolution_error`; non-definitive `TargetError` (including `request_state="response_unknown"`), `CommandError`, `OperationTimeoutError`, unavailable/quarantined sessions, and unsatisfied text-entry postconditions map to `action_error` (or `observation_error` for observations) with allowlisted `_safe_diagnostics` (`backend_code`, `request_state`, 32-hex `operation_id`, allowlisted `completed_steps`, `session_state`, and `stage`). Literal inputs, secrets, observed input text, and raw backend exception text are excluded from diagnostics.

## Verification Scope

- Offline verification covers literal escaping, conjunctive matching, ambiguity without side effects, safe error classification, text-entry value verification and fallback, and display-preview versus exact-locator separation.
- Snapshot verification compares sanitized source and compact output for interactive nodes, duplicate labels, offscreen nodes, missing state, depth clipping, and malformed source. Real-browser success is not inferred from offline tests or screenshots alone.

## Current Invariants

- Construction and registry discovery do not import `appium_connect_agent`, start Appium, or launch an application.
- All `Runtime` and `Session` operations execute on one driver-owned single-worker thread to satisfy `appium-connect-agent` owner-thread affinity, propagating cooperative cross-thread cancellation via `OperationControl` on a 25ms poll loop.
- Owned Mac2 Sessions default `new_command_timeout_seconds` to `900.0` seconds (`0..86_400`, mapping `None` to `0.0` to disable Appium command-idle expiry in `create_mac2_session`) and invoke owner-thread `Session.ensure_alive` (`macos: listDisplays`) before reusing a session after at least 45 seconds of inactivity; under the pinned SDK, failed Mac2 liveness probes quarantine the owned session (`_cleanup_pending`) so callers explicitly recover via `launch_app(new_session=True)` or `close()`.
- `Session` creation remains explicit through `launch_app`. Non-active sessions and pending cleanup quarantine the driver (`session_unavailable`) without implicit session teardown or recreation during ordinary actions or observations. `close()` settles `session_quit_unknown` cleanly when subsequent `Session.dispose()`, managed scope exit, and worker shutdown all succeed, retains the exact worker executor and primary quit error (`BaseExceptionGroup`) if worker shutdown fails so a retry can complete terminal disposal, and preserves `_terminal_disposal_diagnostic` across idempotent post-settlement `close()` calls.
