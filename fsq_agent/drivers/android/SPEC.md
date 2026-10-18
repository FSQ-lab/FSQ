# Module: drivers.android

## Purpose

Implement Android automation through the optional uiautomator2 backend, including declared actions, screenshot and compact UI hierarchy observation, device context, and backend failure normalization.

## Dependencies

- `core.interfaces.AndroidDriverInterface`, `capabilities`, and Android parameter/result models.
- Optional `uiautomator2`, imported lazily at runtime.

## Public Interface

Instances satisfy `AndroidDriverInterface`; the concrete backend class is private outside Drivers and composition.

`input_text` retains the existing target-or-locator parameter contract and replacement semantics. It establishes that the resolved target is an editable text control before clicking, clearing, or writing. Target existence, clickability, focusability, or focus alone is not sufficient; keyboard containers and other non-editable targets are rejected without text-entry side effects.

Successful text entry requires a bounded read-only postcondition check of the resolved target's text after replacement. The observed value must equal the requested string, including empty strings and Unicode text. A non-throwing backend call is not proof of acceptance. Rejected writes, mismatched values, unreadable or masked values, and targets that cannot be verified after entry do not produce success. The driver performs no implicit write retry, alternative-target selection, or focus-only keyboard fallback.

## Internal Structure

- Private uiautomator2 backend implementation and Android capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Error Handling

Text entry returns normalized non-success results with safe diagnostics that distinguish an invalid input target, rejected or mismatched entry, and unavailable verification. Unavailable verification does not assert that the text was not entered. Input values, observed values, target parameter payloads containing input, and raw backend exception messages are not included in text-entry results or diagnostics; comparisons remain in memory. Runtime-secret resolution and execution evidence sensitivity remain owned by Core.

## Current Invariants

- Construction does not connect to a device.
- Compact UI snapshots preserve the documented normalized XML payload and safe fallback.
- Built-in Android context reports session, display size, backend, and current-package metadata from the device-info observation. It leaves the optional `current_activity` context field unavailable and does not issue a separate foreground-activity probe.
