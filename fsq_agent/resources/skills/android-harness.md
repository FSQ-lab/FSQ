# Android Harness Skill

Use when `harness.platform` is Android. This skill contains Android-specific stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start each Android case with `launch_app` before the main business path.
- Collect the final required verification while preserving the user's requested business terminal state, including the foreground page or application, running timers or activities, unsent or unsaved drafts, and system settings.
- Do not routinely call `kill_app` at the end of a task. Use it only when the user goal or explicit lifecycle instructions require closing or restarting the application.
- Automation-session and managed-service cleanup is owned by the runtime through `harness.close()`; killing the business application is not session cleanup.
- Do not report lifecycle actions as satisfying a business key action unless the case explicitly tests launch or kill behavior.

## Observation and Locator Rules

- Use `ui_snapshot` as the Android structural observation for locating current elements and resolving target ambiguity.
- Prefer locator fields confirmed in current output in this order: `resourceId`, `accessibilityId`, exact visible `text`, then `className` or `xpath` when simpler fields are absent or ambiguous.
- Use coordinate taps only when current platform evidence or the user explicitly supplies the point and no reliable locator is available; prefer locator-based actions for normal UI elements.
- Re-evaluate stale or missing targets with a fresh `ui_snapshot` before retrying the same semantic action.
- Do not invent abstract Android targets such as an outside blank area. If a menu or dialog must be dismissed and no concrete target is exposed, use the requested semantic key action such as `Back`, then verify the UI state.
- For Android key actions, use the requested semantic key string only. Do not mix key names with backend-native key codes.

## Verification and Assertion Rules

- Use `assert_state` for deterministic element state or text checks, such as verifying that `com.microsoft.emmx:id/url_bar` contains or equals a required URL or keyword.
- Use `assert_visible` or `assert_not_visible` for required presence or absence of visible UI elements.
- Use `assert_with_ai` only for an explicitly visual assertion: image content, color/appearance, or custom-drawn Canvas content without accessible semantic state. It is not a visual locator, grounding tool, or a way to drive the UI.
- When `ui_snapshot` or `inspect_element` exposes the exact state under test, including SeekBar numeric text, ordinary text, `checked`, or `selected`, use a deterministic assertion. Never substitute visual approximation or let an AI verdict override deterministic counterevidence.
- Complex, repeated, stale, or ambiguous locators are not permission to switch to AI. Refresh structural evidence and refine the locator; a failed, partial, or clipped observation does not prove that no accessibility node exists.
- Use `inspect_element` for complete safe text or `desc` when compact snapshot text is clipped, and request other schema-listed attributes explicitly when needed. Missing, unknown, or protected attributes stay unverified; do not infer False from an omitted compact attribute.
- Collect final verification with an assertion-capable locator or text condition without disturbing the requested business terminal state. A visual assertion of a genuinely visual property remains valid even when unrelated accessible nodes exist elsewhere on screen.
- Visual assertion arguments are `assertion`, required `surface` (`custom_canvas`, `image_content`, `map_view`, or `appearance`), and optionally `region: {kind: image_rect, rect: {left, right, top, bottom}}`. Rectangles use half-open screenshot-image pixels, not pointer coordinates. Element regions and accessibility-bounds crops are unsupported. Omit `region` only with a nonblank `full_screen_reason` explaining why the whole image is necessary; that reason grants no privacy/upload permission.
- `unknown` is not False and `not_applicable` is not a negative assertion. Use genuine visual evidence only when the property is visual and structural semantics cannot decide it; otherwise report unverified. Host privacy/upload permission is independent and denied by default, not something the model may supply or override.
- Interpret `outcome=passed|failed|inconclusive`, not callback completion or the compatibility boolean alone. Inconclusive is incomplete/unverified, never passed or assertion failed; preserve its reason, including provider, privacy, capture, budget, or geometry refusal. An optional assertion does not make uncertainty pass, and historical recorded verdicts are not offline replay evidence.
- Exact color values/color-space semantics and claims of the same display/frame are unverified. Appearance such as a blue-looking icon may be visually evaluated, but does not establish an exact color value. Never derive UI-driving coordinates or actions from a visual result.

## Argument Rules

- Do not use gestures, close buttons, or app lifecycle cleanup as proof that a required `pressKey` action succeeded.
- Use `wait_ms` for FSQ pauses or page-load delays so waiting does not change UI state.

## Sliders and Coordinate Gestures

- Read the exact current state first. If the requested state is already established, stop changing it.
- For a slider extreme, prefer an Element swipe using a current unique `target` or `locator`, the appropriate `direction`, and `percent: 1.0`. This covers the full usable Element span; a normal untargeted direction gesture covers only part of the display.
- Re-read the same node after the gesture and verify the exact requested state with a deterministic assertion. Gesture success or a thumb looking near an endpoint is not proof of an extreme. Do not invent a numeric minimum/maximum or assume one app's range applies to another.
- Explicit points use the current full-screen physical pointer space. Never supply `reference_screen_size` online, rescale from hierarchy content height, or add guessed status/navigation-bar offsets. Reference dimensions belong to internal Strict/Replay recording only.

## File Identity and Destructive Scope

- Distinguish a display title, basename, complete filename, format, extension, and destination. If current app evidence proves that the selected format appends an extension automatically, enter the basename without duplicating that extension. Otherwise do not strip a requested suffix by assumption.
- After saving, verify the complete filename and location through current app details or file-management UI. A matching player/list title does not prove the on-disk filename. Report missing complete-file-identity evidence as unverified.
- Deleting all entries means deleting children inside the authorized collection, not the collection root, its parent, or `..`. Record the current path and selected item identities before confirming.
- Read the full confirmation target with `inspect_element` if snapshot text is clipped. Do not submit a destructive confirmation while the target path, selected objects, or scope is incomplete or ambiguous.
- Verify that the correct root/container still exists and that its complete contents are empty. An empty parent page or only the visible subset is not proof of the requested empty collection.

## Correct Key Examples

Use one payload from the matching semantic action. Do not combine semantic keys with backend-native key codes.

### `pressKey: {key: Back}`

```json
{
  "key": "Back"
}
```

### `pressKey: {key: Enter}`

```json
{
  "key": "Enter"
}
```

### `inputText` with a runtime secret

```json
{
  "target": "Password field",
  "text": "TEST_ACCOUNT_PASSWORD",
  "textType": "runtimeSecret"
}
```

### Invalid mixed key call

Do not call `press_key` with conflicting identities:

```json
{
  "key": "BACK",
  "keyCode": 66
}
```

## Tool Usage Error Recovery

- If `press_key` validation fails, rebuild the payload from the active schema and requested semantic key.
- If a `pressKey` action returns the wrong key result, do not count it. Retry the requested key with the schema-valid payload.
- After retrying a key action, verify UI state with fresh page source, visible text, or screenshot evidence.
- Before `assertWithAI`, use `wait_ms` for required pauses and keep the page at the intended visual state.
- For `assertWithAI`, do not decide from a screenshot path alone; call `assert_with_ai` and use its verdict.
