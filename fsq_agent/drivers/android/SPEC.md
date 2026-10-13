# Module: drivers.android

## Purpose

Implement Android automation through the optional uiautomator2 backend, including declared actions, screenshot and compact UI hierarchy observation, device context, and backend failure normalization.

## Dependencies

- `core.interfaces.AndroidDriverInterface`, `capabilities`, and Android parameter/result models.
- Optional `uiautomator2`, imported lazily at runtime.

## Public Interface

Instances satisfy `AndroidDriverInterface`; the concrete backend class is private outside Drivers and composition.

## Internal Structure

- Private uiautomator2 backend implementation and Android capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Current Invariants

- Construction does not connect to a device.
- Compact UI snapshots preserve the documented normalized XML payload and safe fallback.
- Built-in Android context reports session, display size, backend, and current-package metadata from the device-info observation. It leaves the optional `current_activity` context field unavailable and does not issue a separate foreground-activity probe.
