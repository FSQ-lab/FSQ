# Changelog

All notable changes are documented here. FSQ follows Semantic Versioning after publication; during the 0.x alpha period, minor releases may change public behavior with release-note guidance.

## [Unreleased]

## [0.3.7] - 2026-11-21

Release preparation; this entry does not establish a published package, tag, or GitHub release. See the [release notes](docs/releases/v0.3.7.md) for upgrade steps and validation limits.

### Fixed

- `AppiumConnectMacOSDriver.launch_app` short-circuits idempotently when an active session is already bound to the resolved target `bundle_id` without non-empty `arguments` (regardless of `new_session=True` or `new_session=False`), probing liveness via `Session.ensure_alive` on owned sessions and foregrounding via `Session.device.activate_app` without tearing down or recreating WebDriverAgentMac. When creating or switching a Mac2 session for an already-running macOS GUI application (`lsappinfo info -only pid <bundle_id>`) without non-empty `arguments`, the driver pre-activates the running bundle via bounded `/usr/bin/open -b` before `create_mac2_session(..., no_reset=True, skip_app_kill=...)` while preserving `skip_app_kill=True` by default across session switches and disposal.
- `macos-harness.md` and `MacOSLaunchAppParams` clarify single-initialization vs cross-bundle Mac2 rebinding vs `new_session=false` foregrounding, native document/script extension preservation (`.pages`, `.numbers`, `.key`, `.scpt`) without Finder extension stripping, standard extensionless Obsidian WikiLinks (`[[NoteName]]`), and running saved Automator `.workflow` workflows when execution output is required.

## [0.3.6] - 2026-11-20

Release preparation; this entry does not establish a published package, tag, or GitHub release. See the [release notes](docs/releases/v0.3.6.md) for upgrade steps and validation limits.

### Added

- Windows Quick Test bundles a common application catalogue (`fsq_agent/config/applications.windows.yaml`) with stable presets for `edge`, `chrome`, `edge-beta`, `edge-canary`, `notepad`, and `calculator`, resolving concrete installed executable paths and optional `window_title_re` filters through existing intent analysis while preserving explicit-path precedence.

### Fixed

- `PywinautoWindowsDriver` binds main-window resolution and cleanup to the launched `app_path` identity instead of falling back to arbitrary active desktop windows or unconstrained global title regexes. Launch snapshots pre-existing top-level window handles (including minimized/offscreen windows) and running process IDs, prefers newly opened target windows across resident multi-PID single-instance handoff (plus `ApplicationFrameHost.exe` host frames when `window_title_re` is configured), keeps observations and actions anchored across OS focus changes, and restricts `killApp` / `close()` to terminating owned processes or closing only newly opened handoff windows while surfacing cleanup errors explicitly.

## [0.3.5] - 2026-11-19

Release preparation; this entry does not establish a published package, tag, or GitHub release. See the [release notes](docs/releases/v0.3.5.md) for upgrade steps and validation limits.

### Added

- Android `assert_with_ai` / `assertWithAI` accepts a structured visual assertion with an explicit image-pixel rectangle or nonblank full-screen reason. It remains assertion-only: no element-region mapping, grounding, driving, or coordinate output. External upload permission and current non-sensitive-frame classification are resolved anew by a trusted, non-persistent host callback for each invocation; previous frame decisions and static settings do not grant access.
- Android visual evidence carries a Run-local `HostCaptureEnvelopeV1`, single-frame PNG identity, monotonic capture window, bounded image/response budgets, and safe failure reason codes. Assertions share one current capture across Runner and driver; APNG, stale captures, and escaped artifact paths are refused.
- Android harness policy accepts optional `uiautomator2_wait_for_idle_timeout_ms` for owned Session creation: `None` leaves the SDK keyword omitted, and explicit built-in integers in `0..9007199254740991` retain their exact value, including zero. Borrowed Sessions reject an initial override. Ordinary defaults and Workspace v2 YAML remain unchanged.
- Android `inspect_element` / `inspectElement` reads complete safe attributes from one uniquely resolved element without compact-snapshot clipping, preserving explicit unavailable states and protecting password text.
- Android swipes support mutually exclusive display-direction, explicit-point, and Element-direction modes. Element gestures use current bounds and a finite distance fraction with a full-span default.
- Android UI snapshots include one fresh public SDK `Device.device_time` read with device-local datetime, numeric UTC offset, and ISO weekday. Missing clock facts remain explicitly unavailable without host-time, cached-time, or UI-navigation fallbacks; tree and time are separate non-atomic observations.
- Coding Agent tools accept an optional native `write_budget_status` hook. Validated gate receipts remain top-level model-visible JSON even when normal output limits externalize or omit the business body; unsupported harnesses retain their existing contract, and hook failures do not replace the primary result.
- Android owned Sessions default to a 900-second command-idle timeout with explicit-value preservation. Reuse after 45 seconds idle performs an owner-thread public SDK liveness check; deterministic dead-session recovery remains SDK-owned and non-destructive, without FSQ business-action replay or quarantine bypass.
- Neutral Agent requests may opt into `final_turn_instructions`: the shared engine reserves the last available logical turn for tool-free structured delivery from actual retained evidence, within the original turn limit. FSQ main execution enables this across Responses/Kimi and Gemini while preserving cleanup and hard-exhaustion behavior.
- macOS desktop automation migrates to `appium-connect-agent` (`appium_connect_agent` backend) via `AppiumConnectMacOSDriver` with lazy thread-affine managed loopback Appium server and Mac2 `Session` lifecycle, deterministic `TreeObservation` compact UI snapshot synthesis, and verified `Element`/`Device` actions. Direct `Appium-Python-Client` and `selenium` runtime dependencies, `MacOSHarnessSettings.appium_server_url`, and the `FSQ_MACOS_APPIUM_SERVER_URL` override are retired; macOS Doctor and Control Plane Preflight check `appium_connect_runtime` through static zero-subprocess `Runtime.diagnose(target_driver="mac2")` inspection.
- iOS Phase-1 CLI and SDK automation (`ios` platform) integrates through `appium-connect-agent` (`AppiumConnectIOSDriver` and `IOSHarness`) with lazy thread-affine managed loopback Appium server and XCUITest `Session` lifecycle, 22 dual-named action schemas, compact `<XCUIElementType*>` UI snapshots with `SecureTextField` redaction and fresh `Device.device_time` observations, proportional logical-point replay coordinate scaling, shared mobile `assertWithAI`/`inspectWithAI` visual capabilities, `IOSDeviceService` discovery, `Runtime.diagnose(target_driver="xcuitest")` Doctor checks, and bundled `ios-harness.md` guidance while keeping Control Plane scoped to Phase-1 supported surfaces.
- Human GitHub Copilot sign-in displays valid native UTC expiration and polling interval, with allowlisted stage, timing, polling, and HTTP diagnostics on failure without credentials or raw exception output.

### Fixed

- Legitimate browsing and exploration requests use evidence-grounded flow-delivery planning and verification, including Quick Test intent analysis. Valid blank planning goals fall back to the original request's constraints; malformed output and unusable scope still fail before UI actions.
- AgentTool file outputs redact literal and JSON-escaped runtime-secret values before model presentation and artifact persistence.
- Bounded artifact search and slice recovery preserve original source references and structured continuation when output envelopes exceed inline limits, without rewriting stored evidence or Unicode offsets. Historical references use safe Run-relative paths when private parent directories cannot be disclosed, and keep evidence stored without exposing an unsafe reference when no safe path exists.
- Direct Task planning without an explicit reference preserves the full description alongside a distinct name or legacy goal label instead of dropping subgoals.
- Main summaries, Case/Quick Test results, live Explore delivery, Markdown reports, and portable HTML preserve evidence-supported answers in the requested format and units. Verification rationale, incomplete coverage, failures, and status remain separate rather than replacing an available answer with a disclaimer or claiming a pass.
- Public main-answer interpretations reuse the existing report contract, retain resolvable source artifacts, and honor sharing replacements and summary/content omission. Case and Quick Test summaries share credential, authentication, and encoded-URL redaction.
- Cross-platform CI separates real SDK host/type prerequisites from portable FSQ behavior without reducing the matrix or assertion strength. Windows visual evidence uses native Run-relative, no-reparse handle reads with unchanged containment, integrity, and budget gates; controlled SDK wire peers avoid reverse DNS and retain real lifecycle/fencing proofs. Portable checks consume structured artifact paths rather than encoded text, deadline tests use a controlled clock, save-reconciliation tests await completion signals, and formatting follows the pinned Ruff policy.
- Main execution plans complete source fields and subset arithmetic before writes, then reads back and corrects authorized saved state before closing the Harness. Only valid external write-budget receipts guide throttling/reserves; absent or failed hooks add no write limits. Guidance also avoids unchanged ambiguous focus retries, uses file-list long-press rename when needed, and samples paused video content through observed controls.
- Android numeric text entry can recover once from an explicitly received SDK replacement-command rejection by clearing the same bound target, proving its identity and focus, and sending exact digit/minus/decimal keycodes consecutively without per-character inspections. Unknown outcomes, unsupported characters, unsafe targets, and unproven focus never trigger blind key entry; partial operations are not replayed. Only completed recovery accepts the exact cleared-text placeholder suffix used by Chromium accessibility; ordinary replacement retains exact readback.
- Android bare targets prefer exact text and use exact content-description only when no text element matches; ambiguity and explicit structured-locator precedence remain intact. Short-id schema/skill guidance prefers `locator.resourceId` without rewriting valid raw XPath.
- Android media-key aliases dispatch one corresponding public key event. `assert_state` now requires an explicit nonempty element locator in validation and published schemas instead of accepting unlocated text/state requests.
- Android safe failure diagnostics retain the actual SDK `condition_timeout` and `session_invalid` codes while keeping allowlisted completion facts, canonical operation IDs, and input-stage separation without backend-message leakage.
- Main execution receives Pre-Planner notes intact, in order, and without authority to weaken the task. Planning/execution/verification guidance preserves every requested subgoal and restriction, exact filename/extensions and object identity, and protected draft/terminal states; it calls for cross-screen ledgers, early blocked-candidate pruning, bounded search, and explicit write-budget discipline.
- Android text entry treats blank and `"null"` resource-id observations as absent. Unique text-only no-id targets bind exact class/package/bounds before replacement and verify through that text-independent anchor; post-entry re-anchoring failures explicitly warn that input was sent and UI state must be inspected before re-entering.
- Android UI snapshots preserve SDK-observed ids without inventing package prefixes for WebView or nonstandard controls. Trimmed short `resourceId` locators match bare ids or each candidate node's own package-qualified id in one unique-resolution XPath, including SystemUI and other application windows; fully qualified native ids remain exact and collisions fail closed.
- Android visual uncertainty remains `inconclusive` / Runner `incomplete` through nested execution, verification, reports, and recorded drafts. Exact same-subject contradictory evidence cannot be overridden by an AI pass; independent failures and cancellation retain precedence. Owned and borrowed Session captures deliver cooperative cancellation without accepting late pixels. Interrupted visual invocations retain observed provider request/response provenance, including an unknown upload disposition. Android Strict can start without a Provider and records the unresolved assertion instead of claiming verification; Explore and other platforms keep their Provider gates.
- Horizontal Android Element swipes inset edge-adjacent starts without collapsing valid spans; full rightward swipes reach the element's right boundary when it remains a valid display pixel. Vertical Element and explicit-point gestures retain their geometry. Untargeted direction gestures use vertical 20%–80% and horizontal 18%–82% physical-display interiors.
- Android text-entry pre-inspection, stable-id binding, and post-verification each use the default 25-second read-only operation budget. Exact readback, mutation counts, cancellation, timeout failures, and the separate assertion/observation budgets are unchanged.
- Android assertions inspect only the attributes needed by the active text, state, or existence branch, preserving compound locator constraints and the 10-second inspection budget. Input-text post-verification omits the unused visibility read while retaining target-identity, enabled-state, password/readability, bounds, and exact-text checks.
- Android retains pending Session, managed-runtime, and owner-thread resources until cleanup is proven complete, blocks implicit reconstruction after session failure, and consumes the SDK's public uncertain terminal-disposal contract instead of private lease state. Terminal disposal is local/owned-resource settlement, not confirmed remote Session closure.
- Coding Agent runtime cleanup failures remain independently visible alongside execution failures and cancellation instead of being reduced to warnings or masked by a completed final answer.
- Android close returns normally after an unconfirmed remote quit is taken over by fully completed public terminal disposal and all owned resources settle. Safe diagnostics preserve the unconfirmed DELETE; pending disposal, managed scope, or worker cleanup still fails visibly.
- Main-execution turn-budget exhaustion returns a complete inconclusive task result with a validated, explicitly runtime-generated terminal summary and no additional verifier request. Configured turn limits, real execution evidence, and independent cleanup/error precedence remain unchanged.
- Artifact search reports bounded Run-local repeated-search diagnostics across interleaved artifacts, provides character-offset slice/refinement guidance, and preserves saved-text offsets across Unicode lowercase expansion without implying a fresh UI observation.
- GitHub Copilot task preparation reuses a valid cached bearer token outside its 60-second expiration safety window without authentication requests or configuration writes. Non-interactive remote refresh shares at most three additional retries for transient transport, 429, or 5xx failures; deterministic authentication, format, and storage failures remain immediate errors. Task and inference budgets are unchanged.
- Interrupted Agent runs retain allowlisted domain error codes, reasons, HTTP statuses, and operating-system errno in `run_failed` events without copying raw exception text, credentials, headers, response bodies, or complete exception context.
- The bundled Android harness skill preserves requested foreground, timer/activity, draft, and settings terminal states instead of routinely killing the application after final verification. Explicit user/lifecycle closure or restart remains supported; runtime session disposal is not a business application kill action.
- Responses streaming Agent turns retry bounded pre-commit transport, timeout, rate-limit, service, and incomplete-stream failures without multiplying SDK retries or replaying accepted tool effects. Old streams settle before retry, cancellation remains effective, and deterministic authentication/schema/refusal/output failures are not repaired by additional model requests.
- Exhausted streamed turns retain safe neutral failure categories, reasons, HTTP status, and sanitized causal diagnostics; failed runner records and `run_failed` events preserve allowlisted engine fields without changing infrastructure failures into budget-generated inconclusive results.
- Android coordinate context, display gestures, and replay scaling use the SDK's public physical `Device.display_size()` without viewport, screenshot, offset, or cached-size fallbacks; coordinate recordings preserve the physical reference actually used by the invocation. Replay-only `reference_screen_size` is omitted from online schemas and rejected before online execution, while Strict recording and replay retain it.
- Workspace-relative `read_file` paths resolve once against an explicit Workspace base, independently of read permissions, rather than searching roots or inventing a `cases/android` prefix. Artifact current-Run containment remains unchanged.
- Android End/MoveEnd and MoveHome key aliases retain text-navigation semantics; `Ctrl+End` sends one control-modified event. Text-entry verification binds a stable observed resource identity before writing and rechecks that identity after replacement without replaying input.
- Android input failures preserve safe operation stage, SDK request/completion facts, and local lifecycle state without leaking input or backend messages. Closed and pending-cleanup failures remain distinct, with quarantine and primary-versus-evidence failure precedence preserved.
- Android and shared guidance reserve `assert_with_ai` for pure visual assertions, not locating/driving or approximating exact accessible state. Slider, filename-extension, and collection-deletion guidance requires precise readback, complete object identity, and preservation of the authorized root container.
- iOS `IOSHarnessSettings` and `AppiumConnectIOSDriver` forward optional typed initial-Session WDA policies (`xcuitest_wait_for_idle_timeout_seconds`, `xcuitest_snapshot_max_depth`, `xcuitest_accessibility_deadline_seconds`; defaulting to `None`) to `create_xcuitest_session(...)` while rejecting non-`None` overrides on borrowed Sessions. `input_text` and `clear_text` require an editable, enabled, visible XCUITest control, rebind mutable text-dependent locators to a stable `name` or `type`+`rect` predicate anchor, restrict `send_macos_keys` fallback to confirmed non-acceptance (`not_sent` or `response_received`), and verify post-write readback (with `SecureTextField` withholding); element-scoped `swipe` validates logical display bounds with horizontal edge insets, non-active or pending-cleanup sessions fail fast without implicit reconstruction, progressive startup retains creation-failure resources for owner-thread `Session.dispose` settlement, and `ios-harness.md` adds form-completion and observed-sequence guidance.

### Changed

- Published the AndroidWorld benchmark presentation (`95.69%`, `111/116`, `pass@1`), public-code leaderboard comparison, methodology/evidence links, and refined README/Website hierarchy (#53, #54, #55).
- Updated `pillow` to `13.0.0`, `vite` to `8.3.4`, and `vitest` / `@vitest/mocker` (#44, #45, #46).
- Python distribution metadata is prepared for version 0.3.5. The private npm package remains independently versioned at 0.1.0; this metadata preparation adds no runtime or dependency change.

### Integration status

- Android, macOS, and iOS integration depends on the published PyPI package `appium-connect-agent==0.1.0`, retiring the direct Git URL reference and `allow-direct-references` build metadata so `fsq-agent` wheel metadata satisfies `release.yml` PyPI publication checks.

## [0.3.2] - 2026-10-21

Release preparation; this entry does not establish a published package, tag, or GitHub release. See the [release notes](docs/releases/v0.3.2.md) for upgrade steps and validation limits.

### Fixed

- Android text-entry verification tolerates geometry changes only when a unique locator remains bound to the original resource, while retaining exact readback and target-identity checks. Ambiguous, unreadable, disabled, or mismatched targets remain non-success results without implicit write retries (#39).
- Android's initial device-context read retries once for the specifically identified `ApplicationSharedMemory not initialized` backend error; it does not repeat the application action or retry unrelated failures (#39).

### Changed

- Refreshed the bilingual README, Landing Page, current Android Quick Test demonstration, captions, screenshots, and media source records. The Website retains its headline and white/green visual identity, activates inline native video only on user action, and preserves independent viewing links (#41).
- Both public entry paths now lead to Quick Test through Coding Agent or FSQ UI as equal alternatives, with one complete repository-search → About website → homepage-verification example, evidence review, separate advanced Case/replay workflows, and Star/feedback destinations. Documentation contract tests follow the current onboarding (#41).
- Removed the duplicate YouTube cover-image links from both READMEs while retaining the GitHub inline video, text links, and three explanatory images (#42).
- Python distribution metadata is prepared for version 0.3.2. The private npm package remains independently versioned at 0.1.0; this metadata preparation adds no runtime or dependency change.

## [0.3.1] - 2026-10-20

Release preparation; this entry does not establish a published package, tag, or GitHub release. See the [release notes](docs/releases/v0.3.1.md) for upgrade steps and validation limits.

### Added

- Standalone `fsq quick-test context` and `fsq quick-test run` for Android, Web, Windows, and macOS under existing host/runtime restrictions, without project Workspace setup or an HTTP service.
- Optional Web-only `--url` supplies an exact HTTP(S) starting address separately from the required goal. Existing goal-only execution and the browser Quick Test input remain supported.

### Changed

- The package-owned, host-neutral FSQ Agent Skill guides a platform-neutral AI testing workflow and defaults to standalone Quick Test with actual streamed progress, cancellation of the same process, and evidence-based results. Candidates remain Run-local without Case publication. Explicit Case creation/replay and the provider-free Web Strict troubleshooting fallback remain available; the Codex compatibility integration is unchanged.
- The bilingual README now leads with user value, supports Coding Agent or `fsq ui` entry paths, and keeps detailed onboarding in the user guides.
- Python distribution metadata is prepared for version 0.3.1. The private npm package keeps its independent version.

### Fixed

- Web goal address parsing recognizes Chinese punctuation, paired wrappers, adjacent address labels, and Markdown boundaries while preserving exact address data and rejecting malformed or ambiguous references.
- Dynamic Web navigation receives the frozen starting address as authoritative task context, including its path, query, fragment, case, and escapes.

## [0.1.6] - 2026-10-13

### Added

- Saved-session GitHub Copilot model status and model switching in the Control Plane (#7).

### Fixed

- Workspace Doctor can inspect the registry in read-only Workspaces without attempting to create a writable lock, while preserving safe diagnostic causes for genuine failures (#5).
- Android context collection no longer issues the unused foreground-activity probe, eliminating the repeated approximately 10-second probe observed on the affected device without making a universal latency guarantee (#8).

### Changed

- Updated the indirect frontend dependency `source-map-js` from 1.2.1 to 1.2.2 (#1).

## [0.1.5] - 2026-10-13

### Added

- Initial open-source release preparation.
- DeepSeek Provider configuration through the CLI and Control Plane, with official Responses API inference and forward-compatible Flash/Pro model discovery.
- Package-owned, host-neutral FSQ Agent Skill onboarding for a provider-free Web Strict self-check and exact Run handoff to the local Control Plane.

### Changed

- FSQ business runtime and settings use SDK-neutral names: `DefaultCodingAgentRuntime`, `AgentRuntimeSettings`, `AgentPromptConfig`, and `agent_runtime`. Custom Python integrations and low-level runtime YAML must use the canonical names; the old SDK-prefixed exports and configuration key are not aliases.
- New dynamic Run provenance uses `agent_runtime.runner` and `agent_runtime.verifier`, with `agent_runtime_error` for generic runtime failures. External scripts matching old output strings need to adopt the new values. The actual OpenAI SDK backend and supplier identifiers are unchanged.
- Existing local data from v0.1.0 and later retains its original formats, labels, contents, and artifact paths. This naming update requires no Workspace reinitialization, configuration edits, or reauthentication; historical queries do not rewrite authoritative data.
- Documentation now recommends Skill-first onboarding, retains the TodoMVC Case as an advanced historical example, and describes the separate Codex compatibility integration.
- Package and repository metadata identify version 0.1.5 and the canonical `FSQ-lab/FSQ` repository.

### Removed

- Removed the legacy Playground browser application, standalone HTTP server, Python APIs, and packaged frontend assets. Use `fsq ui` for the supported Control Plane browser workflows; raw YAML dynamic execution, lifecycle-hook editing, completed-Run loading, and automatic Goal Case publication are not carried forward.

## [0.1.0] - Unreleased

### Added

- Evidence-first AI exploration and deterministic Case replay across Web, Android, Windows, and macOS.
- Local Workspace configuration, readiness diagnostics, user-level Provider configuration, Run queries, redacted logs, and offline HTML reports.
- Local Control Plane for Workspace, Provider, Case, Run, and evidence workflows.
- GitHub Copilot and Azure OpenAI Provider configuration.
- Wheel and source distributions containing all supported Python platform dependencies and compiled frontend assets.

### Stability

- This is an alpha release. Public APIs and Case authoring details may evolve before 1.0.

[Unreleased]: https://github.com/FSQ-lab/FSQ/compare/v0.3.7...HEAD
[0.3.7]: https://github.com/FSQ-lab/FSQ/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/FSQ-lab/FSQ/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/FSQ-lab/FSQ/compare/v0.3.2...v0.3.5
[0.3.2]: https://github.com/FSQ-lab/FSQ/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/FSQ-lab/FSQ/compare/v0.3.0...v0.3.1
[0.1.6]: https://github.com/FSQ-lab/FSQ/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/FSQ-lab/FSQ/compare/v0.1.0...v0.1.5
[0.1.0]: https://github.com/FSQ-lab/FSQ/releases/tag/v0.1.0

The 0.3.7 and 0.3.6 comparison references anticipate release tags; they do not establish that a tag or publication exists.
