# macOS Harness Skill

Use when `harness.platform` is macOS. This skill contains macOS-specific stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start app-owned work by establishing a session with `launch_app` if none is bound. For initial attachment, use `new_session=true` once; when the Workspace target is unset or `"auto"`, omitting `bundle_id` or passing `"auto"` intentionally discovers the current frontmost desktop application.
- Reuse a healthy session already bound to the intended application. If the intended document or view is already open and foreground, continue in it; use `launch_app` with `new_session=false` only when it needs foreground activation. Don't recreate the session just to access a different document in the same application.
- When authorized work switches to a different logical application, call `launch_app` with `new_session=true` and the new `bundle_id` or `.app` `app_path`. Owned Mac2 sessions default `skip_app_kill` to true, so closing or switching sessions preserves previously open desktop apps and unsaved windows.
- Collect final required verification while preserving the user's requested business terminal state, including open windows, active views, running activities, explicitly requested unsaved drafts, and system or app settings.
- Do not routinely call `kill_app` at the end of a task. Use `kill_app` only when the user goal or explicit lifecycle instructions require closing, terminating, or restarting the application.
- Automation-session and managed Mac2 service cleanup is owned by the runtime through `harness.close()`; killing the business application is not session cleanup.
- Do not report `launch_app` or `kill_app` as satisfying a business key action unless the case explicitly tests app lifecycle.

## Reusable Desktop Workflows

- Preserve the exact identity of explicitly named targets without treating a general description as an exact UI label. If the user leaves the variant open, use live category evidence to choose a standard variant that meets all explicit constraints; disclose the choice. If a required named variant is missing, report that rather than substituting another.
- When the user wants to correct a record and the displayed result is derived, edit the underlying source record, not its derived field. Trace the references to identify the correct source from current evidence; do not invent a contributing record or overwrite the computation. Only change the formula itself when the goal calls for changing that logic. Explicitly commit authorized record edits and read back the resulting state: use the active editor's commit action (such as Return or Tab for a cell), then verify the source value and the recalculated result. Explicit draft and no-save constraints still win.
- In a healthy bound session, use the application's open, import, or composition controls to compose the requested result from already verified sources instead of retrying with startup arguments. Verify that the intended sources were incorporated and preserve user-required order or identity.

## Form Completion, Pre-Act Ledger, and Write Budgets

- Stop with an unsaved, unsubmitted, or unsent form only when the goal explicitly asks to leave it unsaved, unsubmitted, or as a draft.
- Otherwise, when a requested creation, setting, or challenge answer requires a clear Submit / Save / Done / OK control to take effect, complete that control and verify the resulting state while the Harness is still open.
- When the goal asks you to write, draft, or prepare script/code/text inside an editor, terminal composer, or form field (even with "do not run", "do not execute", or "leave unsent"), actually enter the text into the target GUI control with `type_text` and verify its `value` via `inspect_element` or `assert`—never merely output the text in your final assistant summary without writing it to the UI.
- In macOS Save / Open / Export sheets, press `Command+Shift+G` (`press_key` with `key: "g"`, `modifiers: ["COMMAND", "SHIFT"]`) to open the `Go to Folder` path bar and jump directly to a requested directory path rather than clicking blindly through sidebar folders. Where the `Save As` text field hides the file extension when "Hide Extension" is active, inspect the `Save As` field's current `value` with `inspect_element` before overwriting it so you preserve or supply the exact requested file extension, and after saving verify the complete saved filename including extension in the actual file-management view or directory listing rather than relying on a window title that hides extensions.
- When the user specifies a bare document/script title or basename (for example, `"with file name 'Template'"`), keep the application's native format extension (such as `.pages`, `.numbers`, `.key`, or `.scpt`)—never go to Finder afterward to manually rename the saved file and strip its native extension unless the goal explicitly demands an extensionless filename.
- In Obsidian notes, prefer standard extensionless WikiLinks (`[[NoteName]]`) when linking notes unless the goal explicitly asks for `.md` inside the link target.
- When creating an Automator `.workflow` and the goal requires executing the archive, file processing, or automation output, run the saved workflow once so its target side effects actually materialize and verify the produced output before finishing.
- Read complete source items (using `inspect_element` when `ui_snapshot` clips at 50 characters) and compute target selections in your ledger before writing.
- Honor optional valid `write_budget` receipts when present (`click_on`, `double_click_on`, `right_click_on`, `scroll_on`, `drag_to`, `type_text`, `press_key`, and `launch_app` count as write actions; observations, assertions, and `wait_ms` are non-writes). Never invent synthetic limits when `write_budget` is absent.
## Snapshot-First, Structural Inspection, and Scrolling Rules

- Call `ui_snapshot` after launch, navigation, modal/sheet changes, dynamic search results, or locator ambiguity/failure. Reuse unambiguous locators for consecutive deterministic controls on an unchanged view; keep required checkpoint and final assertions.
- Use `inspect_element` (`inspectElement`) on a uniquely resolved `target` or `locator` to read unclipped `label`, `value`, `title`, `text`, `placeholder_value`, `bounds`, or exact boolean states (`enabled`, `focused`, `selected`, `hittable`, `displayed`) when `ui_snapshot` clips strings at 50 characters or omits default booleans. Note that `SecureTextField` `text` and `value` are intentionally withheld as `unavailable` (`protected_text`).
- Use `scroll_on` (`scrollOn`) on a resolved scroll container/view or explicit `point` with finite `delta_y` and/or `delta_x` (under macOS CGEvent wheel semantics, negative `delta_y` scrolls content down toward lower items; positive `delta_y` scrolls content up) for long lists, tables, settings panes, and documents instead of misusing `drag_to`.
- Use `inspect_with_ai` (`inspectWithAI`) only for pixel-only content or targets unresolved after structural refinement. `extract` reads content; `locate` returns current SDK-validated coordinates on supported single-main-display topology. `pointer_mapping_unavailable` supplies no usable coordinates. Neither mode asserts or acts.
- Do not probe AI visual tools without explicit Run eligibility. Per-frame authorization still applies; on `privacy_blocked` or `provider_unavailable`, stop AI visual calls and report unverified checks.
- Measure exact RGB with authoritative application readback or local `sample_color`; prefer a small uniform interior region away from text and edges. Never use a model estimate as a color value.
- Prefer stable macOS accessibility identifiers and names over coordinates. Use explicit `point` values only when the task permits coordinate actions and the intended target is not exposed through accessibility metadata. In native input, a live hit must belong to the logical target. A failed or ambiguous semantic lookup is not permission to bypass it with an arbitrary point, host application input, or another backend.
- In Keynote, Pages, Numbers, and LibreOffice document or slide canvases, use accessibility locators for exposed controls. If interior text boxes, shapes, or cells are not exposed and the task is not element-only, use a fresh screenshot to place `click_on` or `double_click_on` on the intended canvas region, then edit with `type_text` or `press_key`. Verify the resulting content or state before continuing.
- Do not infer a view change from a screenshot path alone. Refresh with `ui_snapshot` after a view change; otherwise inspect or assert the resulting state when the goal requires it.

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

- Use `new_session=true` on the case-opening `launch_app` or when rebinding Mac2 accessibility observation to a different `bundle_id` / `.app` `app_path` (preserving running desktop apps when `skip_app_kill` is enabled); never pass `new_session=true` repeatedly on the same already-open application unless explicit startup `arguments` are required.
- Omit `new_session` or set `new_session=false` when foregrounding the already-bound desktop application without tearing down the session. Do not pass non-empty `arguments` when reusing the session (`arguments` require `new_session=true`).
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
- For `target_not_foreground`, `foreground_mismatch`, or `foreground_unobservable`, reactivate the explicit logical `bundle_id` with `launch_app(new_session=false)` and take a fresh `ui_snapshot` before resuming. Once a logical target is known, never recover using `auto` or the session's host bundle. A rejected snapshot supplies no live target evidence; never substitute the host application's tree or screenshot. If the target still cannot be verified, report the blockage; do not act into unknown focus. Leave a reported `permission_modal` untouched and report the blocker.
- A successful native input dispatch does not prove UI receipt, a state change, or persistence. Check the resulting state with fresh `ui_snapshot` or `inspect_element` readback before treating a required effect as complete or deciding on recovery. For readable text controls, value readback proves current control content, not a saved document; verify any authorized commit against the resulting persisted state. Never try to read secure plaintext; use permitted evidence of the resulting state instead.
- `not_sent` means the rejected dispatch did not post; correct the cause and re-establish live target evidence before retrying the same semantic action. An input may already have taken effect: on `response_unknown`, do not replay the mutation; inspect the logical target and recover only from observed state. Also inspect before taking another write action if `type_text` reports `verification_unavailable` after sending text. If the result remains unverified, report it and stop actions that depend on it.
- Before `assert_with_ai`, keep the window at the intended visual state and use the returned tri-state verdict (`passed`, `failed`, or `inconclusive`).
