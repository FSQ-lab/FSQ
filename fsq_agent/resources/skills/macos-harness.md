# macOS Harness Skill

Use when `harness.platform` is macOS. This skill contains macOS-specific stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start each macOS case with `launch_app` and set `new_session` to true so the case opens a fresh Mac2 session before the main business path.
- Collect final required verification while preserving the user's requested business terminal state, including open windows, active views, running activities, explicitly requested unsaved drafts, and system or app settings.
- Do not routinely call `kill_app` at the end of a task. Use `kill_app` only when the user goal or explicit lifecycle instructions require closing, terminating, or restarting the application.
- Automation-session and managed Mac2 service cleanup is owned by the runtime through `harness.close()`; killing the business application is not session cleanup.
- When the case temporarily switches to another application and must switch back, call `launch_app` with the desired `bundle_id` and keep `new_session` false. This activates the application in the current Mac2 session without replacing it.
- Do not report `launch_app` or `kill_app` as satisfying a business key action unless the case explicitly tests app lifecycle.

## Form Completion, Pre-Act Ledger, and Write Budgets

- Stop with an unsaved, unsubmitted, or unsent form only when the goal explicitly asks to leave it unsaved, unsubmitted, or as a draft.
- Otherwise, when a requested creation, setting, or challenge answer requires a clear Submit / Save / Done / OK control to take effect, complete that control and verify the resulting state while the Harness is still open.
- Read complete source items (using `inspect_element` when `ui_snapshot` clips at 50 characters) and compute target selections in your ledger before writing.
- Read back saved state and complete filenames/extensions in actual file-management views while the Harness is still open, rather than relying on window titles that may hide extensions.
- Honor optional valid `write_budget` receipts when present (`click_on`, `double_click_on`, `right_click_on`, `scroll_on`, `drag_to`, `type_text`, `press_key`, and `launch_app` count as write actions; observations, assertions, and `wait_ms` are non-writes). Never invent synthetic limits when `write_budget` is absent.

## Snapshot-First, Structural Inspection, and Scrolling Rules

- Start app-owned workflows with `launch_app`; do not assume a Mac2 session exists before lifecycle setup.
- Call `ui_snapshot` after launch and after state-changing actions when the next target is not already unambiguous.
- Use `inspect_element` (`inspectElement`) on a uniquely resolved `target` or `locator` to read unclipped `label`, `value`, `title`, `text`, `placeholder_value`, `bounds`, or exact boolean states (`enabled`, `focused`, `selected`, `hittable`, `displayed`) when `ui_snapshot` clips strings at 50 characters or omits default booleans. Note that `SecureTextField` `text` and `value` are intentionally withheld as `unavailable` (`protected_text`).
- Use `scroll_on` (`scrollOn`) on a resolved scroll container/view or explicit `point` with finite `delta_y` and/or `delta_x` (under macOS CGEvent wheel semantics, negative `delta_y` scrolls content down toward lower items; positive `delta_y` scrolls content up) for long lists, tables, settings panes, and documents instead of misusing `drag_to`.
- Use `inspect_with_ai` (`inspectWithAI`) with `mode: "extract"` only to read genuinely pixel-only Canvas, chart, map, image, or video-frame content. It is a read-only observation, never a pass/fail assertion. Note that `mode: "locate"` is currently unavailable on macOS (`pointer_mapping_unavailable`).
- Prefer stable macOS accessibility identifiers and names over coordinates. Use coordinate `point` values only as an explicit fallback when the target cannot be represented by accessibility metadata.
- Do not infer that the window changed from a screenshot path alone. Use a fresh `ui_snapshot`, `inspect_element`, or assertion after the action.

## Locator Rules

- Inspect the current `ui_snapshot` tree directly when it is returned inline. When the runtime externalizes a large snapshot, use `search_artifact` to find relevant text and `read_artifact_slice` to recover enough surrounding structure before choosing a locator.
- Artifact searches inspect one immutable snapshot and may run concurrently. Search matches are discovery evidence only; use the surrounding element type and attributes to build a complete locator rather than treating matched text alone as an element identity.
- All populated element locator fields constrain the same element. `label`, `value`, and `title` match their own fields exactly; `name` is a compatibility semantic-name signal. A type constraint never replaces a failed text constraint.
- A shallow or clipped tree is not proof that a control is absent. Refresh the snapshot, scroll if needed, or call `inspect_element` before reporting a missing backend control. Default tree output omits `enabled=true`, `visible=true`, and `selected=false`.
- A macOS locator may use `accessibilityId`, `name`, `label`, `value`, `title`, `role`, `controlType`, `className`, `xpath`, `predicate`, or `point` (coordinate-only `point` is rejected on structural inspection and element-only assertions).
- Prefer `accessibilityId` when available because it is the most stable Appium Mac2 lookup.
- Use `role`, `controlType`, or `className` only when the current `ui_snapshot` confirms those fields, and use `xpath` or `predicate` only when simpler accessibility fields are absent or ambiguous.

## Verification and Assertion Decision Tree

- Honor explicit element-only requests: never call `assert_with_ai` or use screenshot-inferred coordinates. Actions on a uniquely resolved element's current geometry remain element-based. Screenshot capture is evidence only.
- Preserve user-required filters and order. Confirm required filter state before dependent price comparison or add-to-cart actions. Detail-page variant selection does not substitute for result-list filtering. If bounded permitted recovery cannot complete a required filter, stop dependent actions and report the blocking step as unmet.
- Existing cart contents are initial state, not proof of a new add. Link product identity, size, price, actual add action and cart change with element evidence. Never clear or modify existing items unless requested.
- Preserve explicit user method constraints in planning and outcome claims. Do not hide a non-equivalent fallback as an adjusted success. Generated key actions do not introduce extra final-verifier requirements.
- Use `search_artifact` and `read_artifact_slice` only to inspect evidence and choose the next action; never treat helper reads, `inspect_element`, `inspect_with_ai`, screenshot paths, or `ui_snapshot` narration as completed verification.
- Finish every required verification step with the matching assertion tool:
  - Prefer deterministic `assert` (`assert_state`) for exact or substring text (`equals` / `contains` over `attribute`) and boolean states (`enabled`, `focused`, `selected`, `hittable`, `displayed`), `assert_visible` for required element visibility, `assert_not_visible` for required element absence/hiding, and `assert_elements_order` for vertical or horizontal visual order.
  - Reserve `assert_with_ai` strictly for pure visual appearance, custom-canvas, map, or image-content checks that accessible attributes do not expose, supplying `assertion`, `surface`, and either `region: {"kind": "image_rect", "rect": ...}` or `full_screen_reason`. Interpret tri-state `outcome` truthfully (`passed`, `failed`, or `inconclusive` / `incomplete` when unverified; never claim `inconclusive` passed or failed the assertion).

## Argument Rules

- Use `new_session=true` on the case-opening `launch_app`; this closes any stale active session and creates the case session from the provided or configured `bundle_id` or `app_path`.
- During the case, omit `new_session` or set it to false when activating another application or switching back by `bundle_id`. Do not pass `app_path` or `arguments` when reusing the session.
- Reserve `kill_app` for cases that explicitly test terminating an app (`close_session=false` or omitted) or explicitly closing a session (`close_session=true`).

## Correct Key Examples

### Case-opening `launchApp` with a fresh session

```json
{
  "bundle_id": "com.example.TargetApp",
  "new_session": true
}
```

### In-case `launchApp` when switching back

```json
{
  "bundle_id": "com.example.TargetApp",
  "new_session": false
}
```

### `scrollOn` inside a scroll view

```json
{
  "locator": {
    "accessibilityId": "ResultsScrollView"
  },
  "delta_y": -300
}
```

### `inspectElement` for unclipped text and boolean state

```json
{
  "locator": {
    "accessibilityId": "DocumentTitle"
  },
  "attributes": ["label", "value", "enabled", "selected"]
}
```

### Deterministic `assert` (`assert_state`)

```json
{
  "element": {
    "accessibilityId": "DarkModeToggle",
    "selected": true
  }
}
```

### Scoped `assertWithAI`

```json
{
  "assertion": "The preview canvas renders a blue bar chart with three bars.",
  "surface": "custom_canvas",
  "region": {
    "kind": "image_rect",
    "rect": {
      "left": 100,
      "top": 120,
      "right": 640,
      "bottom": 480
    }
  }
}
```

### `typeText` with a runtime secret

```json
{
  "target": "Password field",
  "text": "TEST_ACCOUNT_PASSWORD",
  "textType": "runtimeSecret"
}
```

## Tool Usage Error Recovery

- If a macOS tool validation fails, rebuild the payload from the active schema and the requested semantic action.
- If an action executes but the expected state is not present, take a fresh `ui_snapshot` or `inspect_element`, then decide whether retrying the same semantic action is justified.
- If `type_text` reports `verification_unavailable` after sending text, inspect current UI state before re-entering so text is not duplicated.
- Before `assert_with_ai`, keep the window at the intended visual state and use the returned tri-state verdict (`passed`, `failed`, or `inconclusive`).
