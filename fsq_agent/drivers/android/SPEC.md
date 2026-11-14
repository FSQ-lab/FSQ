# Module: drivers.android

## Purpose

Implement Android automation through the `appium-connect-agent` `Runtime` and `Automation` public contracts (`appium_connect_agent` backend), including lazy thread-affine managed Appium server and Automation session lifecycle, declared actions, screenshot and compact `<hierarchy>` XML UI observation, device context, and backend failure normalization.

## Dependencies

- `core.interfaces.AndroidDriverInterface`, `capabilities`, and Android parameter/result models.
- Optional `appium-connect-agent` (`appium_connect_agent.contracts`, `appium_connect_agent.runtime`, `appium_connect_agent.automation`), imported lazily at runtime without private WebDriver access.

## Public Interface

Instances satisfy `AndroidDriverInterface`; the concrete backend class is private outside Drivers and composition.

`input_text` retains the existing target-or-locator parameter contract and replacement semantics. It establishes that the resolved target is an editable text control before clicking, clearing, or writing. Target existence, clickability, focusability, or focus alone is not sufficient; keyboard containers and other non-editable targets are rejected without text-entry side effects.

Successful text entry requires a bounded read-only postcondition check of the resolved target's text after replacement. The observed value must equal the requested string, including empty strings and Unicode text. A non-throwing backend call is not proof of acceptance. Rejected writes, mismatched values, unreadable or masked values, and targets that cannot be verified after entry do not produce success. The driver performs no implicit write retry, alternative-target selection, or focus-only keyboard fallback.

`ui_snapshot` (`uiTree`) consumes public `Session.observe_tree()` `TreeObservation` and deterministically synthesizes compact Android `<hierarchy>` XML in `{"xml": ..., "coverage": ..., "compaction": ...}`. It preserves `resource-id`, `content-desc`, `text`, `class`, `package`, `bounds`, `window-id`, and non-default boolean states (`enabled="false"`, `displayed="false"`, `focused="true"`, `checked="true"`, `selected="true"`), folds meaningless layout-only wrapper nodes, clips `text` and `content-desc` to the first 50 characters, and falls back safely to complete unfolded `TreeObservation` XML serialization on unexpected compaction errors without accessing any private `_driver` handle.

On first session-requiring operation, the driver lazily binds on its dedicated single-worker thread (`ThreadPoolExecutor(max_workers=1)`), allocates an ephemeral loopback TCP port (`127.0.0.1`), enters `Runtime.start_managed(...)`, and creates an Automation `Session` with `auto_launch=False`, `force_app_launch=False`, `no_reset=True`, `full_reset=False`, and `should_terminate_app=False`.

`close()` performs explicit terminal cleanup on that same owner thread. An active owned Session uses public `Session.quit()` followed by managed-scope completion. An uncertain or disposing owned Session uses public `Session.dispose(*, control: OperationControl | None = None) -> None`; if quit leaves the Session uncertain, the same close operation may explicitly dispose it. `SessionInfo.state == "disposed"` proves local terminal disposal and settlement of the owned managed service, not an Appium DELETE or confirmed remote Session close; `appium_session_id` remains historical evidence. A public disposal success supplies the managed-service settlement proof without accessing or separately releasing its usage token. Borrowed Sessions are not disposed by this driver.

The driver retains Session, Runtime, connection, managed-scope, and owner-thread resources needed to continue pending cleanup until their corresponding settlement succeeds. Public disposal preflight cancellation or timeout can leave the Session uncertain; disposal that has begun can remain disposing. Both retain ownership and permit a later explicit close to continue the same cleanup through public SDK APIs. Successfully completed stages are not repeated, and the worker is shut down only when pending cleanup no longer requires its owner thread. A successful close means every owned resource is settled; an unsuccessful close preserves pending ownership and reports failure rather than becoming an empty successful close.

Ordinary actions, observations, and context acquisition never dispose, reconnect, or create a new managed generation when a Session is non-active or owned cleanup is pending. They report the unavailable state without changing the SDK's quarantine or sending cleanup signals. Explicit close does not retry business operations, rebuild a Session, restore device state, kill the target app, or stop ADB. SDK terminal disposal is restricted to the exact owned managed connection; foreign or external ownership never grants cleanup authority. The driver consumes public lifecycle states, results, and errors only: it does not inspect private lease/process fields, infer ownership from a port or disk PID, or send process signals itself.

## Internal Structure

- Private `AppiumConnectAndroidDriver` backend implementation and Android capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Error Handling

Text entry returns normalized non-success results with safe diagnostics that distinguish an invalid input target, rejected or mismatched entry, and unavailable verification. Unavailable verification does not assert that the text was not entered. Input values, observed values, target parameter payloads containing input, and raw backend exception messages are not included in text-entry results or diagnostics; comparisons remain in memory. Runtime-secret resolution and execution evidence sensitivity remain owned by Core.

Primary action/session failures and secondary cleanup failures remain independently inspectable through public exception or normalized runtime-result diagnostics. Cleanup failures are not reduced to warnings or hidden by a prior failed operation. Session invalidity is not proof of disposal, and cancellation retains precedence without erasing pending-cleanup diagnostics. Failed cleanup does not clear ownership, permit implicit reconstruction, or report successful close.

## Verification Scope

Isolated device-free lifecycle verification consumes the real pinned SDK's public terminal-disposal contract and exercises pending ownership, explicit continuation, owned-resource settlement, failure propagation, and cancellation without mocking the lifecycle or usage authority under proof. It makes no device, application, or model calls and does not use or mutate shared SDK records, locks, frozen installations, or historical Run evidence.

## Current Invariants

- Construction does not connect to a device.
- Compact UI snapshots preserve the documented normalized XML payload and safe fallback.
- Built-in Android context reports session, display size, backend (`appium_connect_agent`), and current-package metadata from public `Session.session_info` and `Session.observe_tree()` observations without private WebDriver probes. It leaves the optional `current_activity` context field unavailable and does not issue a separate foreground-activity probe.
