# Changelog

All notable changes are documented here. FSQ follows Semantic Versioning after publication; during the 0.x alpha period, minor releases may change public behavior with release-note guidance.

## [Unreleased]

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

[Unreleased]: https://github.com/FSQ-lab/FSQ/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/FSQ-lab/FSQ/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/FSQ-lab/FSQ/compare/v0.3.0...v0.3.1
[0.1.6]: https://github.com/FSQ-lab/FSQ/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/FSQ-lab/FSQ/compare/v0.1.0...v0.1.5
[0.1.0]: https://github.com/FSQ-lab/FSQ/releases/tag/v0.1.0

The 0.3.2 comparison references anticipate release tags; they do not establish that a tag or publication exists.
