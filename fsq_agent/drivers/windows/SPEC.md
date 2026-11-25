# Module: drivers.windows

## Purpose

Implement Windows desktop automation through optional pywinauto, including application/window lifecycle, actions, observations, assertions, and safe failure normalization.

## Dependencies

- `core.interfaces.WindowsDriverInterface`, `capabilities`, and Windows parameter/result models.
- Optional pywinauto, imported lazily at runtime.

## Public Interface

Instances satisfy `WindowsDriverInterface`; the concrete backend class is private outside Drivers and composition.

`ui_snapshot` (`uiSnapshot`) performs bounded raw UIA/Win32 control-tree collection (default raw scan depth `48`, raw node scan cap `2500`) followed by shared three-stage semantic compaction (`drivers._tree_compaction`): pruning offscreen or non-positive-geometry dead leaf nodes (`offscreen_nodes_omitted`), lifting children through signal-free layout containers such as unnamed `Pane`/`Group` wrappers (`layout_nodes_removed`), and clipping long text attributes to 160 characters (`clipped_attributes`) while withholding password control plaintext and omitting default boolean states (`enabled="true"`, `selected="false"`). It emits a compact single-string XML envelope `{"format": "xml", "xml": ..., "node_count": ..., "coverage": ..., "compaction": ...}` whose tags (`control_type`) and attributes (`automation_id`, `title`, `class_name`, `value`, `bounds`, non-default state) map directly to `WindowsLocator` fields.

`click_on` (`clickOn`), `double_click_on` (`doubleClickOn`), `right_click_on` (`rightClickOn`), `hover_on` (`hoverOn`), and `scroll_on` (`scrollOn`) accept mutually exclusive `locator` or explicit screen `point` targets (`type_text` and `assert_visible` remain `locator`-only). In `point` mode, the driver restores/activates the bound target window, verifies the foreground window is the bound `HWND` or an owned popup, and dispatches through `pywinauto.mouse` at `(point.x, point.y)` without UIA control lookup.

`inspect_with_ai` (`inspectWithAI`) is a read-only observation capability reusing `visual_assertion_policy_resolver(context, capture_sequence)` and `AndroidVisualAssertionPolicy`. `mode="extract"` validates single-frame PNG and `AssertionBudget`, persists the authorized window PNG once to `ArtifactStore`, and returns the model summary without UI actions. `mode="locate"` requires verified 1:1 physical pixel congruence on the bound `HWND` (Per-Monitor v2 DPI awareness on the capture worker and target window, identical pre- and post-capture `GetWindowRect(hwnd)` matching the captured PNG `width x height` and non-negative screen origin `(rect.left >= 0, rect.top >= 0)` within virtual-screen pointer bounds), mapping candidate `image_bounds` `(x, y, width, height)` to screen `pointer_bounds` `(rect.left + x, rect.top + y, width, height)` and `pointer_center`; any unverified window handle, minimized/moved window, negative multi-monitor origin, or DPI mismatch fails closed with `pointer_mapping_unavailable` without guessed coordinate math.

`sample_color` (`sampleColor`) is a deterministic local observation capability (`SampleColorParams`) that captures a single bound-window PNG frame and measures exact RGB, hex, opacity (`opaque`, `min_alpha`), `uniform`, `min_rgb`, `max_rgb`, `color_space`, `icc_profile_name`, `image_point`, `image_bounds`, `image_size`, `pointer_point`, `pointer_bounds`, `pointer_size`, and `png_sha256` locally without calling a Provider or `visual_assertion_policy_resolver`. `coordinate_space="image"` samples window-image coordinates directly (populating `pointer_*` when verified 1:1 window-to-screen mapping is available, else `None`), while `coordinate_space="pointer"` requires verified 1:1 window-to-screen mapping and target coordinates inside the bound window's screen rectangle (failing with `observation_error` and safe `backend_code="pointer_mapping_unavailable"` or `"invalid_argument"` otherwise).
`assert_with_ai` (`assertWithAI`) evaluates scoped `image_rect` or reasoned full-window visual assertions under the unified tri-state (`passed | failed | inconclusive`) contract, reusing the single privacy-approved prepare screenshot artifact when supplied by `WindowsHarness` (capturing once only on standalone invocation) and returning `inconclusive` (`provider_unavailable` / `privacy_blocked` / `capture_failed`) instead of raising configuration errors during offline Strict replay.

## Internal Structure

- Private pywinauto backend implementation (`_pywinauto.py`), shared tree compaction consumer, Win32 foreground/PrintWindow helpers, and Windows capability declarations.
## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Error Handling

Window resolution failures without waiting raise `RuntimeError`, and launch-window readiness waits raise `TimeoutError` after the readiness budget expires, both including configured `app_path`, `title_re`, `wait_for`, and `backend` context. Unresolved control queries raise `LookupError` for `WindowsHarness` classification as `target_resolution_error`. Process-module inspection failures such as `OSError` or `PermissionError` on unrelated desktop windows are ignored rather than aborting target enumeration.

## Current Invariants

- Construction and registry discovery do not import pywinauto or launch an application.
- Backend kind remains pywinauto configuration, not a second FSQ backend.
- When `app_path` is configured, main-window resolution never falls back to an unverified `connect(active_only=True)` or unconstrained global `title_re` lookup across unrelated executables. Before `launch_app`, window resolution requires a launched target session and fails safely without attaching to pre-existing desktop windows.
- `launch_app` snapshots pre-existing top-level window handles before starting `app_path`, then binds to a newly created visible top-level `Window` owned by the started process or by a process whose normalized executable module path matches `app_path` (supporting Chromium/single-instance launcher handoff across resident processes). When no direct `app_path` window appears and a newly created visible `ApplicationFrameHost.exe` top-level window (`hwnd` absent from the pre-launch snapshot) appears, resolution verifies host-frame ownership without requiring `window_title_re`: it prefers a host-frame candidate whose child `Windows.UI.Core.CoreWindow` belongs to a process matching the started PID, normalized `app_path`, or a newly started post-launch backing process (never a pre-existing host-frame window or an unrelated third-party executable). When explicit `window_title_re` is configured and child-window inspection cannot distinguish a stub-launched host window (such as `calc.exe` -> `CalculatorApp.exe` when no new backing PID is observable), a newly created title-matched host-frame window remains eligible; when `window_title_re` is omitted, an unverified host-frame window whose child ownership cannot be attributed to the launch is rejected rather than guessing across multiple candidates. Optional `window_title_re` filters candidate windows within the verified target scope.
- If a UIA candidate has a valid native window handle but no positive UIA process ID, the driver uses the Win32 owner PID for the same handle. An unresolved PID does not establish ownership. This fallback does not bypass the title, executable, or foreground checks.
- Subsequent observations and actions reuse the bound target window handle while it remains alive and visible, re-resolving within the same verified target identity (excluding pre-existing windows recorded before launch unless single-window reuse left no new handle) if a transient splash or intermediate window closes, so OS focus shifts never retarget another application.
- After `launch_app` binds a target window and immediately before pointer or keyboard actions (`click_on`, `double_click_on`, `right_click_on`, `type_text`, `press_key`, `hover_on`, `scroll_on`, `drag_to`), the driver restores the window when `IsIconic(hwnd)`, requests `set_focus()`, and verifies the foreground window is the bound `HWND` or a genuinely owned popup. Same process identity alone does not prove ownership. Unverified activation stops input dispatch.
- After requesting focus for a native target window, the driver allows a short, bounded interval for the foreground transition. It can proceed as soon as the existing target or owned-popup check passes; expiration fails without dispatching input. Immediate foreground checks at input dispatch remain non-waiting and do not reactivate the target or accept a stale successful observation.
- `_screenshot()` captures the bound `HWND` using Win32 `PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)` (`0x00000002`) regardless of foreground/occlusion. It never falls back to copying Win32 desktop pixels: focus and Z-order checks cannot exclude a transient unrelated window during the copy. Separate top-level popup pixels are not included; if the bound window cannot provide a usable render, capture fails.
- `kill_app` and `close()` enforce target ownership: they terminate the application process only when `launch_app` established an owned process with no pre-existing windows of that executable, close only the newly created bound window handle when `launch_app` handed off into an already-running multi-window executable, and perform no process/window termination when no owned launch occurred.
