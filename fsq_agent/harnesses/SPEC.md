# Module: harnesses

## Purpose

Own concrete runtime gateways that combine inherited CommonTools, one injected platform driver, artifact/evaluator/runtime context, and normalized capability invocation for Core Runner. Harnesses do not own backend automation implementations or transport orchestration.

## Dependencies

- `core.interfaces`: Harness, Driver, observation, evaluator, secret, and factory contracts.
- `core.evidence`: run-local artifact storage.
- `models`: canonical steps, contexts, schemas, artifacts, and results.
- `capabilities`: neutral declaration/catalog metadata for inherited CommonTools.
- `drivers`: only internal capability metadata composition and driver instances supplied through public interfaces; Harnesses must not import concrete backend modules.

## Public Interface

Concrete Android, Web, Windows, macOS, and iOS harness classes remain private. `core.interfaces.HarnessFactory` is the stable public construction boundary and returns `HarnessInterface`.

Factory-created Harnesses own their created Driver and receive ownership of the supplied run-local evaluator. `close()` attempts disposal of both through their public contracts, retains the first cleanup failure if more than one fails, and does not repeat successfully completed disposal. A construction failure releases resources created or transferred to the factory before propagating its primary failure. No cleanup call is exposed as a capability or recorded as an application lifecycle action.

The sole external importer of private `_factory._HarnessFactoryImplementation` is `core.interfaces._factories`; concrete Harnesses consume protocol definitions, never that wrapper. Private platform Harness modules use only the Drivers-approved metadata helpers against injected instances, not concrete backend classes or selectors.

## Internal Structure

- Private platform harness modules for Android, Web, Windows, macOS, and iOS.
- Private CommonTool routing and capability-schema adaptation shared by platform harnesses.
- Private composition implementation behind the public Core factory boundary.

## Python Architecture

- Architecture level: Level 3 Layered Application.
- Public API: `HarnessInterface` and `HarnessFactory` remain owned by `core.interfaces`.
- Internal modules: all concrete platform harnesses and selection/composition helpers.
- Domain boundaries: runtime gateway/context/evidence coordination only.
- Boundary models: shared execution values come from `models`.
- Dependency direction: Harnesses depend on Core Interfaces and receive Drivers through interfaces; Drivers never import Harnesses.
- Rationale: harnesses coordinate several runtime collaborators and side effects, requiring Level 3 without a new domain framework.

## Error Handling

Harnesses classify backend failures through normalized contracts (including converting uncaught driver `ConfigurationError` and `ToolExecutionError` on `MacOSHarness` and `IOSHarness` into structured `configuration_error` / `action_error` results), preserve cancellation, and keep secret values out of contexts, events, and artifacts. Raw byte screenshot observations (`takeScreenshot`) on `MacOSHarness` and `IOSHarness` persist the PNG through the injected `ArtifactStore` when present and attach the resulting `HarnessArtifactRef` alongside `{"byte_length": ...}`. Missing observations are explicit safe failures.

## Current Invariants

- CommonTools route to the inherited platform provider; PlatformTools delegate to the injected driver.
- Harnesses do not duplicate driver action bodies.
- Capture services preserve execution identity, phase/reason, independent screenshot/UI snapshot outcomes, and supplied snapshot coverage/compaction metadata. Unknown coverage remains unknown. Allocation and durable acknowledgement use injected Core evidence boundaries, not manually constructed paths or a second journal.
- Concrete harness selection is lazy and hidden behind `core.interfaces.HarnessFactory`.
- Android, iOS, macOS, Windows, and Web `action_space()` expose deterministic/structural tools, assertion-only `assert_with_ai`, and read-only `inspect_with_ai` (`inspectWithAI`). Inspection never invokes UI actions and does not change assertion tri-state or deterministic-counterevidence rules. Authored visual capabilities remain recognizable without a provider and return safe non-success/inconclusive semantics.
- Visual assertion invocation across Android, iOS, macOS, Windows, and Web binds trusted execution identity, Run-local artifact services, cancellation, upload policy, and current structural facts (where supported) to the driver. Their privacy-approved prepare screenshot is the step screenshot evidence (`prepared_artifacts`), avoiding duplicate invoke capture; generic pre-capture must not take or persist another screenshot before the visual privacy gate. When a target session/window/browser has not been started or is already closed (such as Web `browser_not_started`, Windows `windows_app_not_launched` before `launchApp` or after `killApp`/rollback, or unstarted Android/iOS/macOS sessions), automatic step screenshot/snapshot capture records `availability="not_applicable"` with an explicit `unavailable_reason` instead of raising a false `artifact_error`, while capture failures on an active session remain `availability="failed"`. No per-step frame cache survives invocation completion.
- `HarnessFactory.create_harness` accepts trusted Run-scoped `visual_ai_eligible` separately from the per-frame resolver. Eligibility does not grant screenshot upload permission.
- Android, iOS, and macOS share SDK-backed observation and assertion capture without a native screenshot fallback. Their `sample_color` uses local SDK observation and normal session, cancellation, dispatch-budget, and audit rules, without calling a Provider or upload resolver.
- Result normalization across all five harnesses preserves `incomplete`, `outcome`, reason, capture envelope, deterministic-conflict facts, and artifact references instead of treating an unknown/non-failed status as passed. Optional assertions do not promote inconclusive outcomes to success. Parameter validation failures omit raw caller input values (`include_input=False`) from structured validation error metadata.
- Visual inspection across Android, iOS, macOS, Windows, and Web binds the existing evaluator runtime and current invocation context to the driver, resolving the existing `(context, capture_sequence)` authorization callback for that capture. The inspection adapter persists the authorized single-frame PNG once and returns its artifact reference through normal action-result evidence; observation execution performs no generic prepare/finalize screenshot. No second resolver parameter, provider session, unchecked screenshot, or frame cache is introduced.
- Online schemas preserve model-owned exclusion of replay-only coordinate references. Internal Strict validation and coordinate recording retain those fields; online invocation cannot supply them through an undeclared argument.
- Closing releases owned backend/session/worker resources without starting a browser, device, or application, restarting ADB, or inventing an authored close/kill step. Shared externally owned resources are supplied through wrappers whose disposal does not release the borrowed resource.
- Old `core.harness` imports forward to canonical Drivers, Harnesses, or Core Interfaces without duplicate state.
