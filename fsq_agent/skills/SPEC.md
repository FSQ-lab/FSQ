# Module: skills

## Purpose

Load configured automation skills from local Markdown files, directories, or inline descriptive bundles. Provide only complete skill instructions and file metadata to the SDK-neutral agent runtime without coupling skill loading to private knowledge storage or granting command execution authority.

## Dependencies

- `models`: Uses `SkillConfig`, `SkillBundle`, and shared exception types when required skill files are missing or invalid.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `SkillLoader`: Loads configured automation skills from Markdown files, directories, or inline descriptive bundles. Required broken skills fail fast; optional broken skills are skipped with operator-visible diagnostics and are not returned as model-facing bundles.
- `SkillBundle`: Re-exported shared model from `models` for callers that work through the skills module.

## Internal Structure

- `__init__.py`: Public exports only.
- `_loader.py`: Skill discovery, Markdown rendering, and inline bundle loading.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: `SkillLoader` and `SkillBundle` exported from `__init__.py`.
- Internal modules: `_loader.py` owns private discovery and loading mechanics.
- Domain boundaries: complete advisory instruction loading and operational diagnostics, not execution or user-file migration.
- Boundary models: `SkillConfig` and `SkillBundle` from `models`.
- Dependency direction: depends on `models`, never agent implementations or SDK packages.
- Rationale: bounded instruction loading needs a simple package, not orchestration or persistence layers.

## Error Handling

Invalid or missing required skill files raise `FsqAgentError` subclasses from `models` with the failing path and skill name. Optional skill entries with missing paths, missing files, unreadable files, invalid directories, or no inline content are skipped and logged as diagnostics. Skipped optional skills must not be returned as warning-only `SkillBundle` values or sent to LLM prompts. Inline skill bundles are accepted as descriptive instructions and do not imply shell execution authority.

## Platform Skill Blocks

Shared skill guidance belongs in `automation-basics.md`.

Android runs should configure `android-harness.md` alongside shared automation guidance. The Android harness skill describes Android action selection, locators, assertions, UI-tree usage, waits, and recovery. It preserves the user's requested business terminal state, including the foreground page/application, running timers or activities, explicitly requested unsent/unsaved drafts, and system settings. An unsubmitted/unsaved/unsent final state requires an explicit goal restriction or draft request; otherwise clear submit/save/confirm controls required to complete the requested entry, setting, or form/challenge answer belong in the completion path and their resulting state is verified. Shared and Android guidance never infer a draft merely because the goal omits "click Submit"; explicit no-save/no-submit/no-send restrictions override normal completion. This does not authorize unrelated or destructive confirmations. Routine end-of-task `kill_app` remains prohibited unless the goal or explicit lifecycle instructions require closure/restart; session/service disposal remains owned by `harness.close()`.

Shared and Android guidance treats an initially visible sequence item, later valid click-revealed values, and the transition to an answer/submission form as observed state-machine progression. It uses the complete actually observed valid sequence and verifies required submission results, without inventing an additional item or abandoning submission solely because the final click opens the form. Missing or contradictory observations are not guessed or declared successful.

Shared/main execution guidance treats supplied write-budget facts as authoritative planning limits, not permission to increase/reset/refund them. Allowed observations resolve uncertainty before scarce writes; completion reserves include only goal-required commits, never an explicitly prohibited save/submit. Cross-screen collection work maintains observed identities, relevant fields, unresolved facts, and coverage without losing multiplicity or double-counting overlapping pages. A visible difference permits early exclusion only when it logically disproves the goal-defined equality/qualification predicate; clipping is not a mismatch, and unequal durations remain valid inputs to sum/subset tasks. Unfinished requested coverage with remaining authorized feasible work is not grounds for early inconclusive termination. Paging, scrolling, or deletion requires current re-anchoring rather than reuse of old viewport indices.

Android locator guidance prefers structured locators, explains exact text-first/content-description fallback for bare targets, and directs short resource-id lookup to `locator.resourceId` instead of guessing prefixes inside raw XPath. Assertion guidance requires a located `element` for `assert_state`, and key guidance includes the supported media semantic names. Time-dependent tasks obtain device-local time from `ui_snapshot` or sufficiently complete current calendar-date evidence; they do not guess host dates or open Settings/repeatedly pull status bars merely to discover today. Missing date evidence remains explicit uncertainty. A successful input/value check is not invalidated by narrow-field visual clipping; use allowed same-target readback rather than repeat a possibly accepted mutation.

Map/list search starts from the core requested name while preserving country/region as disambiguation constraints. If the current general search mode exposes radius expansion, advance through its observed ranges until a candidate, an exhausted/unchanged/blocked range, or a supplied limit; do not prematurely switch to narrow categories or cycle unchanged searches. No hidden coordinates, guessed geography, unbounded search, or possibly accepted action replay is authorized.

Android/shared guidance reserves `assert_with_ai` for pure visual assertions, never locating/driving; exact accessible semantic assertions remain deterministic and visual verdicts cannot override counterevidence. Separate read-only `inspect_with_ai` is accessibility-first: extract reads pixel-only text/colors/state; locate supports targets without usable independent accessibility nodes or unresolved structural lookup after fresh inspection/refinement. Only returned SDK physical `pointer_center` may be passed to a subsequent explicit `tap_at` or `swipe`; inspection itself never acts. Missing observations or geometry never authorize guessed coordinates. Existing slider and business-terminal-state guidance remains unchanged.

Android, iOS, and macOS visual assertions retain required assertion/surface, image-rectangle or reasoned full-image selection, deterministic-first verification, privacy gates, and passed/failed/inconclusive semantics. Inspection accepts a prompt, extract/locate mode (with macOS locate unavailable under SDK `pointer_mapping_unavailable`), optional half-open image rectangle, and candidate limit, with separate host authorization model arguments cannot grant. Image and physical or logical pointer coordinates are not interchangeable; only the SDK performs mapping where supported. Screenshot content is untrusted evidence, not instructions. Observation summaries are not assertion verdicts, exact-color/display-identity guarantees, or proof of a later action. Web and Windows visual rules and filename/deletion-scope rules remain unchanged.

Android file-creation guidance distinguishes display titles, basenames, full filenames, formats, and extensions. An extension is omitted from a name field only when current application evidence proves automatic suffixing; saved full filename and location require their own verification. Collection-deletion guidance preserves the authorized root/container and deletes only its children, excluding `..`, the scope itself, and ancestors. Truncated confirmation targets require `inspect_element` complete safe attributes before confirmation; unreadable or still ambiguous scope prevents destructive submission. Empty-state verification establishes the correct retained container and complete content scope, not merely an empty parent page. These are execution guidance, not a claim that generic `tap_on` can enforce arbitrary filesystem intent.

An explicitly requested full filename, including an explicitly extensionless name, is an exact invariant. Account for observed default format suffixes; adjust the format or perform an authorized rename when needed. After an authorized save/rename, verify complete name and destination in the actual file list/file-management view; a hidden-extension editor title is insufficient. Neither `.md` equivalence nor extra save/rename actions may override an explicit draft/no-save restriction.

Web runs should configure `web-harness.md` alongside shared automation guidance. The Web harness skill states that `ui_snapshot` returns ref-free role/name hierarchy, element actions use only schema-valid role/name locators, and target selection prefers a unique role/name, then one unique `within` scope, then an order-sensitive final index. It tells the model to refresh the snapshot after relevant state changes or a stale or missing target, never retry an ambiguous locator unchanged, use locatorless `assert_text` for literal page-body text rather than interaction, use visible option labels for `select_option`, use `wait_for` for URL or semantic-element conditions, use `wait_ms` only for deliberate elapsed time, and use explicit browser lifecycle capabilities rather than key presses.

macOS runs should configure `macos-harness.md` alongside shared automation guidance. The macOS harness skill describes the complete Mac2 action/observation/assertion surface (`launch_app`, `kill_app`, `click_on`, `double_click_on`, `right_click_on`, `type_text`, `press_key`, `hover_on`, `scroll_on`, `drag_to`, `take_screenshot`, `ui_snapshot`, `inspect_element`, `inspect_with_ai`, `assert_visible`, `assert_not_visible`, `assert` / `assert_state`, `assert_elements_order`, `assert_with_ai`), initial frontmost attachment (`bundle_id="auto"`), avoiding redundant `new_session=true` restarts on an already-open target app or document (`new_session=false` foreground activation for the bound app, and cross-bundle rebinding with default `skip_app_kill` session-exit app preservation when Mac2 must inspect another app's accessibility tree), stable accessibility locators, `scroll_on` wheel navigation, unclipped `inspect_element` reads with `SecureTextField` withholding, read-only `inspect_with_ai` (`extract` supported, `locate` unavailable under SDK `pointer_mapping_unavailable`), deterministic-first verification before scoped tri-state `assert_with_ai`, credential text entry via `textType: runtimeSecret`, Pre-Act ledger and pre-submit readback, optional `write_budget` discipline, writing requested script/text/code directly into the target GUI editor control via `type_text` and verifying its `value` even when instructed not to run/execute it (rather than only printing it in the final summary), using `Command+Shift+G` (`Go to Folder`) in macOS Save/Open sheets to navigate directly to target directory paths, preserving native document/script extensions (such as `.pages`, `.numbers`, `.key`, `.scpt`) when given a bare title/basename without later stripping the extension in Finder while still honoring explicit extensionless filenames, preferring standard extensionless Obsidian WikiLinks (`[[NoteName]]`) unless `.md` is explicitly requested, running newly saved Automator `.workflow` workflows once when the goal requires executing the archive/processing result, form submission when implied by completion, and preserving requested business terminal state without routine end-of-case `kill_app`.

iOS runs should configure `ios-harness.md` alongside shared automation guidance. The iOS harness skill describes XCUITest mobile action aliases, form completion and observed-sequence progression, structured `XCUIElementType*` locators (`accessibilityId`, `name`, `label`, `value`, `elementType`, `predicate`, `classChain`, `xpath`), logical-point coordinate semantics, safe interior and element-inset swipe rules, `ui_snapshot` compact XML and `device_local_time` usage, `inspect_element` unclipped readback, input-uncertainty/ambiguity recovery without blind replay, file identity and destructive-scope verification, pre-act ledger, pre-submit readback, and `write_budget` reserve rules, dynamic media sampling, `inspect_with_ai` locate/extract usage, `handle_alert` system alert handling, deterministic `assert_state`/`assert_visible`/`assert_not_visible` assertions, visual assertions through `assert_with_ai` (including privacy/reason/inconclusive handling), and credential text entry through `textType: runtimeSecret`.

The iOS harness skill instructs agents never to use `xpath` predicates on `@visible` or `@accessible`: WDA can perform slow per-element accessibility IPC across the tree to evaluate them. Agents prefer `accessibility_id`/`name`, exact `label`, `value` with `element_type`, or XCUITest predicate or class-chain locators. This guidance does not change the supported locator schema.

The iOS harness skill describes `ui_snapshot` XML as the sole hierarchy, the 160-character text limit, and how to interpret `compaction.offscreen_nodes_omitted` as partial coverage rather than proof of absence. It distinguishes scrolling and observing again from an explicit `include_offscreen: true` inspection of the currently materialized tree.

Additional platform skills must be separate platform-specific Markdown bundles instead of expanding existing platform skill files with unrelated platform rules.

## Current Invariants

- Skills are advisory context, not executable authority.
- Local Markdown skills are rendered into instructions/context only when successfully loaded.
- Dynamic pre-plan receives configured skills only as complete loaded `SkillBundle` values. Required broken skills fail before pre-plan, and optional broken skills are skipped without warning-only model-facing skill content.
- Platform-specific skills should describe scope, AgentTool/CommonTool/PlatformTool selection, argument rules, tool usage error recovery, semantic fidelity rules, and evidence rules. They guide the generic agent for the configured runtime without turning the agent runtime into a platform-specific implementation.
- Skills do not attach command execution tools. Command execution capabilities require their own SPEC update outside the skills module.
- Skill models remain centralized in `models` so runtime, tools, and configuration share the same serializable contracts.
- Skill loader diagnostics are operational metadata for logs or runtime events, not prompt content. The LLM should see complete skill instructions or no skill block.
