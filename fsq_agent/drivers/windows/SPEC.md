# Module: drivers.windows

## Purpose

Implement Windows desktop automation through optional pywinauto, including application/window lifecycle, actions, observations, assertions, and safe failure normalization.

## Dependencies

- `core.interfaces.WindowsDriverInterface`, `capabilities`, and Windows parameter/result models.
- Optional pywinauto, imported lazily at runtime.

## Public Interface

Instances satisfy `WindowsDriverInterface`; the concrete backend class is private outside Drivers and composition.

## Internal Structure

- Private pywinauto backend implementation and Windows capability declarations.

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
- `launch_app` snapshots pre-existing top-level window handles before starting `app_path`, then binds to a newly created visible top-level `Window` owned by the started process or by a process whose normalized executable module path matches `app_path` (supporting Chromium/single-instance launcher handoff across resident processes). When `window_title_re` is configured and no direct `app_path` window appears (such as `ApplicationFrameHost.exe`-hosted system apps), resolution may bind a newly created host-frame window matching `window_title_re`, never an unrelated third-party executable. Optional `window_title_re` filters candidate windows within the verified target scope.
- Subsequent observations and actions reuse the bound target window handle while it remains alive and visible, re-resolving within the same verified target identity (excluding pre-existing windows recorded before launch unless single-window reuse left no new handle) if a transient splash or intermediate window closes, so OS focus shifts never retarget another application.
- `kill_app` and `close()` enforce target ownership: they terminate the application process only when `launch_app` established an owned process with no pre-existing windows of that executable, close only the newly created bound window handle when `launch_app` handed off into an already-running multi-window executable, and perform no process/window termination when no owned launch occurred.
