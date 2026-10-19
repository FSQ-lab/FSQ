# Changelog

All notable changes are documented here. FSQ follows Semantic Versioning after publication; during the 0.x alpha period, minor releases may change public behavior with release-note guidance.

## [Unreleased]

### Changed

- The package-owned, host-neutral FSQ Agent Skill now guides a platform-neutral AI testing workflow, with the provider-free Web Strict self-check retained as a fallback.
- The bilingual README now leads with user value, supports Coding Agent or `fsq ui` entry paths, and keeps detailed onboarding in the user guides.
- Python distribution metadata and current-version guidance are prepared for version 0.3.0. This does not claim publication, merge, or release completion.

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

[Unreleased]: https://github.com/FSQ-lab/FSQ/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/FSQ-lab/FSQ/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/FSQ-lab/FSQ/compare/v0.1.0...v0.1.5
[0.1.0]: https://github.com/FSQ-lab/FSQ/releases/tag/v0.1.0
