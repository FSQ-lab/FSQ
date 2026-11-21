# iOS Harness Skill

Use when `harness.platform` is iOS. This skill contains iOS XCUITest stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start each iOS case with `launch_app` before business interactions.
- Preserve the user's requested business terminal state, including the foreground page or application, running timers, explicitly requested unsent or unsaved drafts, and system settings.
- Do not routinely call `stop_app` at the end of a task unless the goal or explicit lifecycle instructions require terminating or restarting the application.
- Automation-session and managed Appium cleanup is owned by the runtime through `harness.close()`; terminating the business application is not session cleanup.
- When switching between apps inside one case, call `launch_app` with `bundle_id` and keep `new_session: false` so XCUITest activates the bundle without tearing down the session. Use `new_session: true` only when replacing `.app`/`.ipa` artifacts or resetting the session.
- Do not report lifecycle actions as satisfying a business key action unless the case explicitly tests launch or termination behavior.

## Form Completion and Observed Sequences

- Stop with an unsaved, unsubmitted, or unsent form only when the goal explicitly requires it, such as "do not save", "without submitting", "leave as draft", or "unsent". Explicit restrictions override normal completion.
- Otherwise, when creating an entry, applying settings, or answering an interactive form/challenge needs a clear Submit / Save / Done / OK / Return control to take effect, include that task-required action and verify the resulting state. Do not invent "without submitting" because the goal omits "tap Done". This does not authorize unrelated or destructive confirmations.
- Count requested taps separately from observed sequence values. Include the initially displayed item when it belongs to the actual valid sequence, observe subsequent values, and treat a final transition to the answer form as normal progression rather than proof of a missing extra value. Use the complete observed valid sequence, calculate, submit when required, and verify the result; do not guess genuinely missing or contradictory observations.

## Observation, Locator, and Text Entry Rules

- Call `ui_snapshot` after launch and after state-changing actions when the next target is not already unambiguous.
- `ui_snapshot` returns compact `<XCUIElementType*>` XML with `coverage`, `compaction`, logical `viewport` when available, and `device_local_time`. The XML omits empty attributes and default states (`enabled="true"`, `selected="false"`, `visible="true"`) and clips text attributes (`name`, `label`, `value`, `placeholderValue`) to 160 characters.
- By default `ui_snapshot` prunes off-screen-only `visible="false"` branches while retaining the application root and ancestors of retained controls. `compaction.offscreen_nodes_omitted` counts the pruned nodes; a positive count means `coverage.status` is `partial`, not that a missing target is absent. Scroll the relevant container and observe again for on-screen interaction, or use `include_offscreen: true` to inspect currently materialized off-screen nodes without scrolling. Layout, depth, and text compaction still apply.
- For date- or time-dependent tasks, take the first explicit `ui_snapshot` before date-dependent decisions and consume `output.device_local_time` (`local_datetime`, `iso_weekday`, `source`); explicit unavailable status leaves date uncertainty unless current calendar evidence establishes the complete date. Do not hunt today's date in Settings or pull Notification Center repeatedly, and never guess from host time. Tree and clock are separate observations, not synchronized screenshot-time proof.
- When the runtime externalizes a large snapshot, use `artifact.path`, `search_artifact`, and `read_artifact_slice` to inspect surrounding element types and attributes before choosing a locator.
- Prefer structured `locator` fields confirmed in current `ui_snapshot` output in this order: `accessibility_id` / `name`, exact `label`, `value` + `element_type`, then `predicate`, `class_chain`, or `xpath` only when simpler fields are absent or ambiguous.
- Never use `xpath` predicates on `@visible` or `@accessible`. WDA can evaluate them with slow per-element accessibility IPC across the entire tree. Prefer `accessibility_id` / `name`, exact `label`, `value` + `element_type`, or `predicate` (`-ios predicate string`) / `class_chain` (`-ios class chain`).
- In `-ios predicate string` and backtick predicates in `-ios class chain`, use the element's own attributes (`name`, `label`, `value`, `type`, `enabled`, `visible`, `selected`, `focused`, `hittable`, `rect`); never use `descendants`. Express hierarchy in separate class-chain segments such as `**/XCUIElementTypeCell/**/XCUIElementTypeButton`.
- All populated `locator` fields constrain the same element with logical AND. Use complete literal values; if snapshot attributes were clipped (`truncated_attributes`), call `inspect_element` to recover unclipped safe `label`, `name`, or `value` first.
- A bare `target` resolves exact `label` first, falling back to exact `name` or `value` only when no `label` matches. Duplicate matches fail as ambiguous; after a single target ambiguity or input failure, do not repeat the unchanged ambiguous locator. Refresh `ui_snapshot` or `inspect_element` and refine with a unique `locator` (`accessibility_id`, `element_type`, `predicate` with exact `rect`, or `class_chain`), or focus via `tap_at` at the center of fresh, uniquely attributable logical bounds for the intended editable control.
- `input_text` and `clear_text` require an enabled, visible editable XCUITest control (`TextField`, `SecureTextField`, `SearchField`, `TextView`, `ComboBox`). If `input_text` reports an unknown outcome (`request_state: "response_unknown"`), perform permitted same-target readback (`inspect_element` or `ui_snapshot`) before retrying; never blindly re-send text or tap Back.
- `inspect_element` withholds `value` on `XCUIElementTypeSecureTextField` (`state: "not_applicable"`). Never attempt to exfiltrate password values.
- Use `inspect_with_ai` in `mode: "extract"` to read genuinely pixel-only image, chart, map, or custom Canvas content; it is a read-only observation, not an assertion.
- Use `inspect_with_ai` in `mode: "locate"` only for targets without usable accessibility nodes after fresh structural observation and locator refinement. Complex, clipped, or ambiguous accessibility output alone is not permission to skip structural refinement. Only a current SDK-returned candidate's logical `pointer_center` may feed an explicit `tap_at` or explicit-point `swipe`; never use `image_bounds`, `pointer_bounds` corners, screenshot scaling, guessed offsets, or historical candidate coordinates.
- Host privacy/upload permission is independent and denied by default for both `inspect_with_ai` and `assert_with_ai`. Do not retry around privacy, geometry, provider, or budget refusals.

## Coordinates, Swipes, Sliders, Scrolling, and Alerts

- All iOS coordinates (`tap_at`, explicit-point `swipe`) use XCUITest logical points matching `Device.display_size()` / `viewport` (`width` x `height`), not physical Retina pixels. Never pass `reference_screen_size` online, rescale from content height, or add guessed status-bar/safe-area offsets; proportional scaling belongs strictly to offline Strict/Replay.
- `tap_at` takes `{"point": {"x": <int>, "y": <int>}}`; use logical points inside the current `viewport`.
- Read the exact current control value before adjusting sliders, switches, or pickers. If the requested state is already established, stop changing it.
- Prefer `scroll_to` on a resolved scrollable container (`XCUIElementTypeTable`, `XCUIElementTypeCollectionView`, `XCUIElementTypeScrollView`) with explicit `direction` and bounded `distance` in `(0, 1]` when scrolling content.
- Full-screen directional `swipe` (`direction` without `target`/`locator` or `start`/`end`) uses safe interior logical-display ranges (20%–80% vertically, 18%–82% horizontally) so iOS Notification Center, Control Center, Home-indicator, and edge-back gestures are not triggered accidentally.
- For sliders or element-scoped gestures, supply a unique `target` or `locator`, `direction`, and `percent` (`1.0` for full-span slider extremes): element-scoped directional swipes require positive logical `bounds` fully inside the current display and apply horizontal edge insets (`min(height // 2, width // 4)`) so tracks/sliders do not trigger system edge gestures. Re-read the same node afterward and verify the exact requested state with a deterministic assertion; gesture completion or a thumb looking near an endpoint is not proof of an extreme.
- Handle native iOS permission or application alerts explicitly with `handle_alert` (`action: "accept"` or `"dismiss"`, plus optional exact `button_label`).
- Use `hide_keyboard` or `press_key` (`return` / `enter`) when the software keyboard obscures lower controls; if `hide_keyboard` cannot dismiss a custom keyboard, tap a safe non-destructive header label or explicit Return/Done key confirmed in `ui_snapshot`.
- `press_key` supports physical buttons (`home`, `volume_up`, `volume_down`) and active-responder keys (`enter`, `return`, `backspace`, `delete`, `tab`, `space`, or a single printable character). iOS has no hardware Back key; use explicit navigation-bar back buttons from `ui_snapshot`, and never use lifecycle actions or gestures as proof that a required `press_key` succeeded.
- Use `wait_ms` for FSQ pauses or loading delays so waiting does not mutate UI state.

## Verification and Assertion Rules

- Finish every required verification step with `assert_state`, `assert_visible`, `assert_not_visible`, or `assert_with_ai` while preserving the requested terminal business state. Do not treat `ui_snapshot`, `inspect_element`, or screenshot paths alone as completed verification.
- Use `assert_state` for deterministic element existence, text (`contains` / `equals`), or boolean state (`enabled`, `selected`, `focused`, `accessible`); `element` must contain a non-empty structured locator. When `ui_snapshot` or `inspect_element` exposes the exact state under test (ordinary text, slider `value`, `selected`, `enabled`), use a deterministic assertion rather than visual approximation.
- Missing, `unknown`, or `not_applicable` attributes remain unverified; do not infer `false` from an omitted compact attribute. Visual clipping in screenshots does not invalidate a successful `inspect_element` value readback.
- Reserve `assert_with_ai` strictly for visual properties (`surface`: `custom_canvas`, `image_content`, `map_view`, or `appearance`) with either half-open screenshot-image pixel `region: {kind: "image_rect", rect: {left, top, right, bottom}}` or a nonblank `full_screen_reason`. Element regions and accessibility-bounds crops are unsupported.
- Interpret `outcome=passed|failed|inconclusive` directly: `inconclusive` is incomplete/unverified (never passed or assertion failed), and its reason must be preserved. Never let an AI verdict override deterministic counterevidence, claim exact color-space values or same-frame synchronization, or derive UI-driving coordinates from an assertion result.

## File Identity and Destructive Scope

- Distinguish a display title, basename, complete filename, format, extension, and destination folder. An explicitly requested full filename (including explicit absence of an extension) is invariant; after an authorized save or rename in Files, Notes, or document apps, verify the complete filename and destination in the actual file/folder list view rather than relying on an editor header that may hide extensions.
- Deleting all entries means deleting children inside the authorized folder/collection, never the collection root or parent. Inspect clipped confirmation text with `inspect_element` before confirming any destructive action, never confirm while target scope is ambiguous, and verify afterward that the intended container still exists and is empty.

## Pre-Act Ledger, Pre-Submit Readback, and Dynamic Media

- Before multi-item transcription, filtering, counting, or duration/subset selection, read complete relevant source fields with the fewest read-only observations (`ui_snapshot` plus `inspect_element` for clipped values), record exact actual text/units/totals without blind suffix stripping, and solve the exact selection before mutating state.
- Before final delivery while the Harness is open, read back saved content, complete file identity/destination, selected items/totals, and absence of accidental extras against the user's goal; repair mismatches only while authorized actions remain available and never violate explicit draft/no-save/no-send constraints.
- Only a valid tool-root `write_budget` receipt activates write-throttling and completion reserves (`tap_on`, `tap_at`, `double_tap_on`, `long_press_on`, `swipe`, `scroll_to`, `press_key`, `hide_keyboard`, `handle_alert`, `input_text`, `clear_text`, `launch_app`, `stop_app`). Missing or malformed optional hooks impose no synthetic cap; when valid `remaining <= 1` or near turn limits, return the supported answer directly rather than a disclaimer.
- For time-varying video/media observation, reveal playback/scrubber controls, pause at observed timestamps across the requested interval using exposed controls or uniquely attributable current bounds, and read each paused sample without inventing timestamps or unseen frames.

## Correct Key Examples

### `launchApp`
```json
{
  "bundle_id": "com.apple.Preferences"
}
```

### `tapOn` with structured locator
```json
{
  "locator": {
    "accessibility_id": "General",
    "element_type": "XCUIElementTypeCell"
  }
}
```

### `inputText` with a runtime secret
```json
{
  "locator": {
    "element_type": "XCUIElementTypeSecureTextField"
  },
  "text": "TEST_ACCOUNT_PASSWORD",
  "textType": "runtimeSecret",
  "clear": true
}
```

### `handleAlert`
```json
{
  "action": "accept",
  "button_label": "Allow While Using App"
}
```

### `assertState`
```json
{
  "element": {
    "accessibility_id": "About",
    "enabled": true
  },
  "text": {
    "equals": "About"
  }
}
```

## Tool Usage Error Recovery

- If an iOS tool validation fails, rebuild the payload from the active schema and requested semantic action.
- If a target is missing or stale after navigation or animation, take one fresh `ui_snapshot` before retrying with updated schema-valid locator fields.
- Before `assert_with_ai`, use `wait_ms` when animations are settling and rely on the returned tri-state verdict (`passed`, `failed`, `inconclusive`).
