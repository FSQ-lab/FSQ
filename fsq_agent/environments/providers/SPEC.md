# Module: environments.providers

## Purpose

Own private Android, Web, Windows, and macOS host/runtime providers used by `PlatformRuntimeService`, including bounded installed-application lookup mechanics. These are not LLM providers and do not perform platform automation.

## Dependencies

Providers use standard-library host, import, and filesystem facilities plus public Models contracts. They must not invoke package managers or import adapters, Application, Config persistence, concrete drivers or harnesses, Execution, or top-level LLM `providers`.

## Public Interface

No public package API. Providers are selected internally by `environments`.

## Internal Structure

- Private Android, Web, Windows, and macOS provider modules.
- `_applications.py`: bounded native application metadata lookup and exact-channel Web dispatch; `_adb_transport.py` also supplies the fixed existing-server-only package inventory query.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: none outside `environments`.
- Internal modules: all platform provider modules.
- Domain boundaries: host/runtime mechanics only.
- Boundary models: shared values come from Models.
- Dependency direction: consumed only by the parent Environments service.
- Rationale: each provider is focused infrastructure.

## Error Handling

Providers perform read-only checks and return safe normalized outcomes. They never install or modify Python packages, system dependencies, services, targets, applications, devices, emulators, or virtual machines.

Android owns the existing-server-only ADB wire transport, response parsing, device inventory, and fixed package queries used by shared discovery and prerequisites. A missing server never triggers client auto-start, including disappearance between requests. Protocol failures, transport timeouts, missing requirements, device authorization/state, ambiguous selection and package-query errors remain distinguishable safe facts. Local host/port resolution, protocol/output limits and repair-command safety are governed by the parent Environments contract; no ADB logic is duplicated in adapters or Application.

Application lookup uses platform-local read-only metadata and canonical identity validation under the parent Environments limits. Explicit and inferred concrete references define the same validation scope. Android package queries remain scoped to the selected device and resolved package without requiring a localized application label; macOS inspects application/bundle metadata; Windows validates concrete executable paths or bounded installed-application metadata. Providers report all distinct candidates within that reference's lookup scope or an explicit incomplete/error outcome. They do not certify the model's name-to-identity interpretation, exhaustively enumerate same-named applications, rank returned candidates through an LLM, perform a full-disk scan, or construct commands from prompt text.
