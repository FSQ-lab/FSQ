# Android Harness Skill

Use when `harness.platform` is Android. This skill contains Android-specific stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start each Android case with `launch_app` before the main business path.
- Collect the final required verification while preserving the user's requested business terminal state, including the foreground page or application, running timers or activities, explicitly requested unsent or unsaved drafts, and system settings.
- Do not routinely call `kill_app` at the end of a task. Use it only when the user goal or explicit lifecycle instructions require closing or restarting the application.
- Automation-session and managed-service cleanup is owned by the runtime through `harness.close()`; killing the business application is not session cleanup.
- Do not report lifecycle actions as satisfying a business key action unless the case explicitly tests launch or kill behavior.

## Form Completion and Observed Sequences

- Stop with an unsaved, unsubmitted, or unsent form only when the goal explicitly requires it, such as "do not save", "without submitting", "leave as draft", or "unsent". Explicit restrictions override normal completion.
- Otherwise, when creating an entry, applying settings, or answering an interactive form/challenge needs a clear Submit / Save / Done / OK control to take effect, include that task-required action and verify the resulting state. Do not invent "without submitting" because the goal omits "click Submit". This does not authorize unrelated or destructive confirmations.
- Count requested clicks separately from observed sequence values. Include the initially displayed item when it belongs to the actual valid sequence, observe the subsequent values, and treat a final transition to the answer form as normal progression rather than proof of a missing extra value. Use the complete observed valid sequence, calculate, submit when required, and verify the result; do not guess genuinely missing or contradictory observations.

## Observation and Locator Rules

- Use `ui_snapshot` as the Android structural observation for locating current elements and resolving target ambiguity.
- Prefer locator fields confirmed in current output in this order: `resourceId`, `accessibilityId`, exact visible `text`, then `className` or `xpath` when simpler fields are absent or ambiguous.
- Prefer structured `locator` fields over a bare `target`. A bare target resolves exact text first, and exact content-description only if there are zero text matches. Duplicate text remains ambiguous; disabled/hidden text matches do not authorize description fallback. Use `locator.resourceId` for a short resource id; raw short-id XPath is exact and is not package-normalized.
- For a date-dependent task, take the first explicit `ui_snapshot` before date-dependent decisions and consume `output.device_local_time`: when available, use its `local_datetime`, `iso_weekday`, and `source`; explicit unavailable status leaves date uncertainty unless current calendar evidence establishes the complete date. Do not hunt today's date in Settings or repeated status-bar pulls, or guess from host time. Tree and clock are separate observations, not synchronized screenshot-time proof. Recover oversized output through `artifact.path`, `search_artifact`, and `read_artifact_slice`.
- Use coordinate taps only when current platform evidence or the user explicitly supplies the point and no reliable locator is available; prefer locator-based actions for normal UI elements.
- Re-evaluate stale or missing targets with a fresh `ui_snapshot` before retrying the same semantic action.
- After a single `target identity ambiguity` or `Input focus click failed`, do not repeat the unchanged ambiguous locator. Take a fresh `ui_snapshot` or permitted complete element inspection, then use a new uniquely identifying attribute combination or a bounds-based focus tap at the center of fresh, uniquely attributable physical element bounds for the intended editable control. Never guess from screenshot pixels or reuse stale bounds. Verify focus/current text before entry. Unknown text acceptance requires same-target readback before another write, not repeated input or blind Back presses.
- Do not invent abstract Android targets such as an outside blank area. If a menu or dialog must be dismissed and no concrete target is exposed, use the requested semantic key action such as `Back`, then verify the UI state.
- For Android key actions, use the requested semantic key string only. Do not mix key names with backend-native key codes.
- Supported media semantic keys include `MediaPlayPause`, `MediaPlay`, `MediaPause`, `MediaStop`, and `Space` (case-insensitive; underscore-separated media spellings are also supported). Key delivery alone does not prove playback state; observe the requested effect.
- Use `inspect_with_ai` in `mode: "extract"` to read genuinely pixel-only image, chart, map, or Canvas content; it is an observation, not a pass/fail assertion.
- Use `inspect_with_ai` in `mode: "locate"` only for inaccessible targets, or targets still unresolved after a fresh structural observation and locator refinement. Complex, clipped, or ambiguous accessibility output alone is not permission to skip that refinement.
- Inspection takes `prompt`, optional `mode` (default `extract`), optional direct image `region: {left, top, right, bottom}`, and `max_candidates` (default 5). Region bounds use half-open image pixels; candidates remain in full-image coordinates. Extract returns no executable candidates.
- Inspection never acts automatically. Only a current SDK-returned candidate's `pointer_center` may be passed to an explicit `tap_at` or explicit-point `swipe`; never use `image_bounds`, `pointer_bounds` corners, screenshot scaling, guessed offsets, or historical candidate coordinates as action points. Refresh observation after UI changes and verify the resulting action structurally where possible.
- Host privacy/upload permission is independent and denied by default for inspection as for visual assertion. A failure reason is an operational non-success, not an assertion verdict; do not retry around privacy, geometry, provider, or budget refusal.

## Verification and Assertion Rules

- Use `assert_state` for deterministic element state or text checks, such as verifying that `com.microsoft.emmx:id/url_bar` contains or equals a required URL or keyword. Its required `element` must contain a supported nonempty locator; text-only or unlocated state assertions are invalid.
- Use `assert_visible` or `assert_not_visible` for required presence or absence of visible UI elements.
- Use `assert_with_ai` only for an explicitly visual assertion: image content, color/appearance, or custom-drawn Canvas content without accessible semantic state. It is not a visual locator, grounding tool, or a way to drive the UI.
- When `ui_snapshot` or `inspect_element` exposes the exact state under test, including SeekBar numeric text, ordinary text, `checked`, or `selected`, use a deterministic assertion. Never substitute visual approximation or let an AI verdict override deterministic counterevidence.
- Complex, repeated, stale, or ambiguous locators are not permission to switch to AI. Refresh structural evidence and refine the locator; a failed, partial, or clipped observation does not prove that no accessibility node exists.
- Use `inspect_element` for complete safe text or `desc` when compact snapshot text is clipped, and request other schema-listed attributes explicitly when needed. Missing, unknown, or protected attributes stay unverified; do not infer False from an omitted compact attribute.
- A successful input/value verification is not invalidated by visual clipping. Use permitted same-target readback rather than rewriting the field; uncertain acceptance requires observation before replay, not an assumption that input failed.
- Collect final verification with an assertion-capable locator or text condition without disturbing the requested business terminal state. A visual assertion of a genuinely visual property remains valid even when unrelated accessible nodes exist elsewhere on screen.
- Visual assertion arguments are `assertion`, required `surface` (`custom_canvas`, `image_content`, `map_view`, or `appearance`), and optionally `region: {kind: image_rect, rect: {left, right, top, bottom}}`. Rectangles use half-open screenshot-image pixels, not pointer coordinates. Element regions and accessibility-bounds crops are unsupported. Omit `region` only with a nonblank `full_screen_reason` explaining why the whole image is necessary; that reason grants no privacy/upload permission.
- `unknown` is not False and `not_applicable` is not a negative assertion. Use genuine visual evidence only when the property is visual and structural semantics cannot decide it; otherwise report unverified. Host privacy/upload permission is independent and denied by default, not something the model may supply or override.
- Interpret `outcome=passed|failed|inconclusive`, not callback completion or the compatibility boolean alone. Inconclusive is incomplete/unverified, never passed or assertion failed; preserve its reason, including provider, privacy, capture, budget, or geometry refusal. An optional assertion does not make uncertainty pass, and historical recorded verdicts are not offline replay evidence.
- Exact color values/color-space semantics and claims of the same display/frame are unverified. Appearance such as a blue-looking icon may be visually evaluated, but does not establish an exact color value. Never derive UI-driving coordinates or actions from an assertion result; only the separate SDK-backed inspection can return current pointer centers.

## Argument Rules

- Do not use gestures, close buttons, or app lifecycle cleanup as proof that a required `pressKey` action succeeded.
- Use `wait_ms` for FSQ pauses or page-load delays so waiting does not change UI state.

## Sliders and Coordinate Gestures

- Read the exact current state first. If the requested state is already established, stop changing it.
- For a slider extreme, prefer an Element swipe using a current unique `target` or `locator`, the appropriate `direction`, and `percent: 1.0`. This covers the full usable Element span; a normal untargeted direction gesture covers only part of the display.
- Re-read the same node after the gesture and verify the exact requested state with a deterministic assertion. Gesture success or a thumb looking near an endpoint is not proof of an extreme. Do not invent a numeric minimum/maximum or assume one app's range applies to another.
- Explicit points use the current full-screen physical pointer space. Never supply `reference_screen_size` online, rescale from hierarchy content height, or add guessed status/navigation-bar offsets. Reference dimensions belong to internal Strict/Replay recording only.

## File Identity and Destructive Scope

- Distinguish a display title, basename, complete filename, format, extension, and destination. The explicitly requested full filename, including an explicit absence of extension, is invariant: a default `.md` suffix is not equivalent. Omit a suffix from the input field only when current evidence proves automatic suffixing still produces exactly the requested full name; otherwise adjust format or use an authorized rename.
- After an authorized save/rename, verify complete filename and destination in the actual file-list/file-management view. A matching editor/player title that may hide an extension is insufficient. Report missing full-identity evidence as unverified. Explicit draft/no-save restrictions still win and never authorize extra saving or renaming.
- If a document/note editor's top-bar menu has no rename action, return to its file list, identify the exact target file from fresh evidence, and use `long_press_on` to open its file-operation menu. Rename only when authorized, then read back the complete filename and destination; never long-press a guessed or stale row.
- Deleting all entries means deleting children inside the authorized collection, not the collection root, its parent, or `..`. Record the current path and selected item identities before confirming.
- Read the full confirmation target with `inspect_element` if snapshot text is clipped. Do not submit a destructive confirmation while the target path, selected objects, or scope is incomplete or ambiguous.
- Verify that the correct root/container still exists and that its complete contents are empty. An empty parent page or only the visible subset is not proof of the requested empty collection.

## Pre-Act Ledger and Pre-Submit Readback

- Before multi-item transcription, filtering, counting, or duration/subset selection, read the complete relevant source fields with the fewest permitted read-only observations. Prefer one accessibility snapshot for a list, using `inspect_element` to recover clipped text before exact transcription. Record source identity, complete actual content, presentation metadata, units, coverage, and computed totals. Distinguish labels/attribution from real content using observations, not blind suffix removal; for example, do not silently turn actual text `8 servings by` into `8 servings`.
- Calculate the exact selected entries and aggregate before writing. Retain all potentially contributing durations and solve the requested interval before adding tracks; do not add every candidate and delete down. Enter only the authorized selection, preserving exact text and avoiding duplicate/accidental entries.
- Before final delivery, while the Harness is still open, read back saved content, complete file identity and destination, selected items/total duration, and absence of accidental extras against the original request. Correct observed mismatches while authorized actions remain possible, then re-read the resulting state. Do not repair after cleanup or turn explicit drafts/no-save/no-send into permission to commit.
- Only a valid tool-root `write_budget` receipt activates write-throttling and goal-required completion reserves. When supplied, `tap_on`, `swipe`, `press_key`, `input_text`, and `launch_app` consume writes; budget the actual tool actions and reserve required save/rename/repair steps. Missing, malformed, or throwing optional hooks create no limits, synthetic counters, or budget-based stop. At valid `remaining <= 1` or near the turn boundary, deliver an already supported answer in the requested format rather than a disclaimer; uncertainty belongs in other structured fields.

## Dynamic Video Observation

- For time-varying image/video extraction, reveal the observed playback/progress controls, inspect their available time and seek semantics, and pause at observed temporal sample points distributed across the requested interval. Use exposed controls/seek values or uniquely attributable current control bounds; do not invent duration, timestamps, coordinates, or unsupported seek arguments. Read each paused sample, retaining its observed timestamp and content before moving on.
- Observe whether playback is actually paused and whether the seek reached its intended time. Avoid repeatedly restarting from the beginning or claiming unseen frames; if a sample/control remains unavailable, preserve that uncertainty rather than fabricating coverage. Use only navigation/playback effects authorized by the request.

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
