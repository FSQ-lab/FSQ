# iOS Harness Skill

Use when `harness.platform` is iOS. This skill contains iOS XCUITest stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start each iOS case with `launch_app` before business interactions.
- Preserve the user's requested business terminal state, including the foreground page or application, running timers, explicitly requested unsent or unsaved drafts, and system settings.
- Do not routinely call `stop_app` at the end of a task unless the goal or explicit lifecycle instructions require terminating or restarting the application.
- Automation-session and managed Appium cleanup is owned by the runtime through `harness.close()`; terminating the business application is not session cleanup.
- When switching between apps inside one case, call `launch_app` with `bundle_id` and keep `new_session: false` so XCUITest activates the bundle without tearing down the session. Use `new_session: true` only when replacing `.app`/`.ipa` artifacts or resetting the session.
- Do not report lifecycle actions as satisfying a business key action unless the case explicitly tests launch or termination behavior.

## Observation and Locator Rules

- Call `ui_snapshot` after launch and after state-changing actions when the next target is not already unambiguous.
- `ui_snapshot` returns compact `<XCUIElementType*>` XML, structured `root`, logical `viewport`, and `device_local_time`. Default snapshot output removes empty attributes and default states (`enabled="true"`, `selected="false"`, `visible="true"`) and clips text attributes (`name`, `label`, `value`, `placeholderValue`) to 50 characters.
- For date- or time-dependent tasks, read `device_local_time` (`local_datetime`, `iso_weekday`, `source`) from `ui_snapshot` rather than guessing from host time or opening Settings merely to inspect today's date.
- When the runtime externalizes a large snapshot, use `search_artifact` and `read_artifact_slice` to inspect surrounding element types and attributes before choosing a locator.
- Prefer structured `locator` fields confirmed in current `ui_snapshot` output in this order: `accessibility_id` / `name`, exact `label`, `value` + `element_type`, then `predicate`, `class_chain`, or `xpath` only when simpler fields are absent or ambiguous.
- All populated `locator` fields constrain the same element with logical AND. Use complete literal values; if snapshot attributes were clipped (`truncated_attributes`), call `inspect_element` to recover unclipped safe `label`, `name`, or `value` first.
- A bare `target` resolves exact `label` first, falling back to exact `name` or `value` only when no `label` matches. Duplicate matches fail as ambiguous; refine with a structured `locator` rather than repeating the ambiguous call unchanged.
- `inspect_element` withholds `value` on `XCUIElementTypeSecureTextField` (`state: "not_applicable"`). Never attempt to exfiltrate password values.

## Coordinates, Swipes, Scrolling, and Alerts

- All iOS coordinates (`tap_at`, explicit-point `swipe`) use XCUITest logical points matching `viewport` (`width` x `height`), not physical Retina pixels. Never pass `reference_screen_size` online; proportional scaling belongs strictly to offline Strict/Replay.
- Prefer `scroll_to` on a resolved scrollable container (`XCUIElementTypeTable`, `XCUIElementTypeCollectionView`, `XCUIElementTypeScrollView`) with explicit `direction` and bounded `distance` in `(0, 1]` when scrolling content.
- Full-screen directional `swipe` (`direction` without `target`/`locator` or `start`/`end`) uses safe interior display ranges so iOS Notification Center, Control Center, and Home-indicator gestures are not triggered accidentally.
- For sliders or element-scoped gestures, supply `target` or `locator`, `direction`, and `percent` (for example `1.0` for full-span slider adjustment), then re-read the element value deterministically.
- Handle native iOS permission or application alerts explicitly with `handle_alert` (`action: "accept"` or `"dismiss"`, plus optional exact `button_label`).
- Use `hide_keyboard` or `press_key` (`return` / `enter`) when the software keyboard obscures lower controls; if `hide_keyboard` cannot dismiss a custom keyboard, tap a safe non-destructive header label or explicit Return/Done key confirmed in `ui_snapshot`.
- `press_key` supports physical buttons (`home`, `volume_up`, `volume_down`) and active-responder keys (`enter`, `return`, `backspace`, `delete`, `tab`, `space`, or a single printable character). iOS has no hardware Back key; use explicit navigation-bar back buttons from `ui_snapshot`.

## Visual Inspection and Assertions

- Use `inspect_with_ai` in `mode: "extract"` to read genuinely pixel-only image, chart, map, or custom Canvas content; it is a read-only observation, not an assertion.
- Use `inspect_with_ai` in `mode: "locate"` only for targets without usable accessibility nodes after fresh structural observation and locator refinement. Only a returned candidate's logical `pointer_center` may feed a subsequent explicit `tap_at` or `swipe`.
- Finish every required verification step with `assert_state`, `assert_visible`, `assert_not_visible`, or `assert_with_ai`. Do not treat `ui_snapshot`, `inspect_element`, or screenshot paths alone as completed verification.
- Use `assert_state` for deterministic element existence, text (`contains` / `equals`), or boolean state (`enabled`, `selected`, `focused`, `accessible`); `element` must contain a non-empty structured locator.
- Reserve `assert_with_ai` strictly for visual appearance, image content, map views, or custom Canvas surfaces (`surface`: `custom_canvas`, `image_content`, `map_view`, or `appearance`) with either half-open pixel `region` or a nonblank `full_screen_reason`. Never override deterministic accessibility counterevidence with an AI verdict, and preserve `inconclusive` outcomes honestly.

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
