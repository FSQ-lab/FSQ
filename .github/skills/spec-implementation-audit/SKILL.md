---
name: spec-implementation-audit
description: "Internal diff-based project SPEC audit rules loaded only when an explicitly invoked repository prompt directs the agent to this file."
user-invocable: false
disable-model-invocation: true
---

# SPEC Implementation Audit

Determine whether project-code or project-logic implementation from an explicitly invoked SDD workflow satisfies confirmed project specifications. This is a bidirectional, diff-based audit: SPEC-to-code checks completeness and correctness; code-to-SPEC checks grounding and task scope. It is not a general test pass check or a restatement of the implementer's summary.

## Invocation Gate

Load this skill only when an explicitly invoked repository prompt directs the agent to this file. Ordinary discussion, explanation, review, planning, natural-language edit requests, skill-name mentions, and prose approvals must not trigger it.

## Core Rule

This skill audits only project-code or project-logic changes. Agent write authorization, SDD phase transitions, prompt routing, and workflow skills are not project SPEC items.

The audit covers both project paths: implementation after a confirmed SPEC update and implementation under a recorded no-SPEC-delta decision. In the latter path, the reviewer independently verifies that current SPEC remains the complete grounding truth for the changed behavior.

Completion cannot be claimed until implementation is audited against:

```text
root SPEC.md + relevant module SPEC.md files + actual diff
```

Tests, lint, keyword scans, and implementation summaries are auxiliary evidence only. They do not replace diff-based SPEC audit.

## Audit Modes

- Round 1 uses `audit_mode=full`: run the complete change-scope procedure below with every applicable check, not a repository-wide historical audit.
- Round 2 and later use `audit_mode=repair-only`: audit only the repairs for the previous round's findings. Carry every unresolved earlier finding forward in that previous-round report so none is dropped.

A repair-only pass reads the previous independent report, its audited snapshot, the complete repair delta, and the SPEC clauses and implementation paths needed to judge those repairs. It does not rebuild the full applicable-item inventory, re-read unchanged parts of the original diff, or search for unrelated new issues. Retain unaffected passing verdicts only when their evidence remains unchanged.

Do not automatically restart a full audit. Changes outside the previous findings' repair scope require an explicit scope decision before they can be treated as covered.

## Independence Requirement

Use a fresh reviewer or independent context whenever the platform supports it. The reviewer must not inherit the implementation agent's conversation history or rely on its self-report.

Reviewer input is limited to:

- Root `SPEC.md`.
- Relevant module `SPEC.md` files.
- Complete diff artifacts for the declared audit mode plus their identity manifest, or an exact commit range.
- For repair-only passes, the previous independent report, its finding IDs and audited snapshot, and the complete delta from that snapshot to the repaired snapshot.
- SPEC delta mode: `confirmed-update` or `no-delta`.
- Confirmed SPEC version and confirmation evidence, plus the authorized task scope and exclusions. These establish approval and scope, not a substitute for project SPEC.
- Assigned `review_lane=combined|forward|reverse`, coverage boundaries, and the verification plan/status for the same snapshot.
- Minimal navigation instructions required to locate modules and public APIs.
- Verifiable raw verification outputs or durable evidence references, with commands, exit status, tested inputs, relevant environment, and coverage; these support but never replace independent review.
- For no-SPEC-delta work, the recorded SPEC references and neutral defect reproduction evidence as claims to verify, not accepted proof.

Do not provide persuasive summaries such as "this is complete" or "tests pass, so it should be fine".

## Audit Input Integrity

For a worktree audit, validate the supplied artifact path, SHA-256, byte size, `diff --git` entry count, changed-path inventory, and any separate untracked-file artifacts for the declared audit mode. Read the complete in-scope artifacts, not terminal output or overflow wrappers. Use the single evidence package and compact manifest prepared by `spec-driven`; do not request a new repository copy at each handoff. A repair-only manifest links the previous snapshot and report to the complete repair delta and verifies that code and SPEC evidence outside that scope is unchanged; identity verification does not require re-auditing unchanged content. If required evidence is missing, unreadable, truncated, wrapped, or inconsistent with its identity manifest, return `audit-blocked` without substituting a live diff or implementation summary.

Record the validated artifact identities in the audit result. The calling `spec-driven` workflow owns the post-audit comparison between these identities and a freshly regenerated worktree snapshot; the reviewer must not claim that later worktree changes are covered. For a commit-range audit, validate and report the exact immutable base and head object ids.

Require the exact root and relevant module SPEC versions, even when unchanged by the diff, and independently read them for the initial scope. Compare changed SPEC inputs with their confirmed versions; matching code and unconfirmed SPEC edits do not establish approval. Necessary source context outside the diff must come from the declared frozen workspace or immutable snapshot. Immutable Git object/path references are sufficient for unchanged content; do not demand all repository files or unrelated SPECs. This reduces copying, not completeness or integrity.

## Verification Evidence

Independently read the relevant tests and necessary call paths, then default to consuming verifiable matching raw test/CI evidence instead of requesting reruns for independence. Check exact commands, exit status, tested input identity, relevant environment, and coverage against the affected requirements and review snapshot. A passing summary alone is insufficient.

Retain earlier results when those factors still match. Input, relevant environment, or coverage changes invalidate affected checks and verdicts, not unrelated proof; identify the precise gap for Tech Lead to assign the necessary verification under `spec-driven`. A new snapshot identifier alone does not invalidate checks whose actual inputs and coverage are unchanged. Missing or failed required evidence blocks its dependent conclusions; static review may continue. Do not require an empty download cache as a proxy for clean-install evidence.

## Review Lanes

Default to one independent Reviewer with `review_lane=combined`. Under the root cost principle and `spec-driven` scheduling rules, Tech Lead may assign `forward` and `reverse` lanes concurrently against the same complete snapshot when concrete risk and substantial separate context justify splitting and agent capabilities permit it. Both lanes read the relevant SPEC and actual implementation; lane names describe coverage responsibility, not restrictions to reading only one kind of evidence.

- `forward`: owns the applicable SPEC inventory, implementation correctness, regressions, domain checks, and verification obligations.
- `reverse`: independently derives actual behavior changes and implementation boundaries from the diff and necessary paths, then checks SPEC grounding and authorized task scope.
- `combined`: owns both inventories and all checks.

A split-lane report declares only its own coverage and cannot pass the whole audit. Tech Lead joins both reports, resolves gaps or conflicting verdicts with the reviewers, and preserves all findings and source IDs. Neither inventory may be omitted because the other lane passed.

Verification may run concurrently under `spec-driven`. Apply the evidence rules above while it is pending; the responsible Reviewer finalizes dependent conclusions only after consuming matching results. Verification is never inferred from another lane's success.

## First-Round Complete Scope

Before judging implementation in round 1, establish the complete scope:

- the root and relevant module SPEC inputs;
- the complete current worktree diff or commit range;
- the SPEC delta mode;
- the complete set of applicable SPEC items;
- the complete set of in-scope added, changed, or removed behaviors and affected implementation boundaries, independently derived from code rather than filtered by known SPEC items;
- the file, symbol, interface, dependency, configuration, or behavior boundaries relevant to each item;

Determine this initial scope independently for the assigned lane. Account for every changed path, including untracked files, tests, configuration, and behavior documentation. Group related changes by behavior; explain non-behavioral changes rather than requiring one SPEC clause per line. This is change-scope coverage, not a claim to audit all historical behavior. Later repair-only passes take their scope from the previous findings and repair delta rather than repeating this inventory.

## First-Round Complete Procedure

1. Establish both the applicable SPEC item inventory and the actual behavior inventory for the assigned lane before assigning final verdicts, including independent validation of a no-SPEC-delta decision when applicable.
2. Read the diff and relevant implementation paths for each item; independently trace actual behaviors back to SPEC and authorized scope.
3. Apply the correctness/risk checks below and the Python, frontend, package, browser, and visual domain checks when affected.
4. Record a verdict and concrete diff evidence for every item.
5. Consolidate duplicate observations of each blocking root cause and reference every affected SPEC or behavior item precisely. When grounding is absent, record `SPEC basis: absent`; do not drop the finding for lack of a SPEC reference.
6. Record repair ownership as implementation, SPEC/human decision, or verification environment.
7. Record non-blocking quality feedback separately from SPEC verdicts.
8. Return the assigned coverage tables and full finding set, with blocking gaps before quality/style feedback. The aggregate full audit requires both tables.

A finding alone must not cause an early return: complete the current pass before implementation repair or a finding-resolution decision begins. Missing required input, tools, or artifacts permit an early `audit-blocked` result; it is not an implementation verdict, does not consume a repair attempt, and cannot satisfy the completion gate. Root user-pause and safety/authorization stops still take precedence; interrupted coverage remains incomplete and cannot pass.

If blocking gaps exist, return the complete result to `spec-driven`. The implementation agent may repair findings but may not mark them closed.

## Correctness And Risk Coverage

For the change scope and directly affected consumers, independently inspect:

- implementation correctness and completeness against requirements, public interfaces, module ownership, dependency direction, and architecture constraints;
- regressions, boundary/negative cases, external inputs/responses/configuration, validation, platform differences, and error/partial/unknown outcomes;
- state transitions, concurrency, cancellation, cleanup, persistence, retries/fallbacks, and external side effects;
- security, privacy, authorization, sensitive data, execution/replay capabilities, and evidence-collection boundaries;
- whether tests assert required observable behavior rather than just implementation details or success paths, and whether local/CI evidence actually covers the relevant risk.

Judge applicability by behavior and impact, not diff size. A high-risk one-line change still needs its relevant call paths and boundary checks, but does not by itself justify a full local suite. Record concrete gaps rather than offering a zero-bug guarantee or treating passing tests as acceptance.

## Code-to-SPEC Grounding

Inspect observable behavior, public contracts, defaults, automatic target selection or execution, retries, fallbacks, error and partial-success semantics, state transitions, persistence, external side effects, configuration, ownership, and dependencies. Include removed behavior and behavior newly asserted by tests or promised by documentation. Passing tests and existing implementation are evidence of what happens, not authority for what should happen.

Classify every behavior item:

| Classification | Required evidence or action |
| --- | --- |
| `grounded` | Precise confirmed SPEC basis and authorized task scope. Grounding alone does not establish correct implementation. |
| `implementation-detail` | Explain how the change serves a grounded behavior without adding a product decision or changing observable behavior, contracts, side effects, error semantics, or architecture constraints; also establish task scope. |
| `ungrounded` | Record the absent basis and a blocking `spec-delta-required` finding owned by SPEC/human decision. |
| `conflicting` | Cite the contradiction and the applicable blocking verdict, such as `diverged` or `boundary-violation`. |
| `out-of-scope` | Record a blocking `needs-human-decision` finding even if the behavior is grounded in SPEC. |
| `unverified` | State the missing evidence and block coverage/completion; unavailable required audit inputs are `audit-blocked`. |

Only `grounded` and justified `implementation-detail` pass reverse grounding, and neither waives forward findings. A broad module purpose or "internal logic" label is not sufficient justification. Do not require SPEC text for every helper or local variable.

Code can reveal a requirement gap but cannot authorize its own requirement. For ungrounded behavior, Tech Lead obtains a decision to remove/correct the behavior within authorized scope or to update and confirm SPEC before reconciling implementation. Never automatically backfill SPEC to legitimize code already written.

## No-SPEC-Delta Audit

When the implementation used the no-SPEC-delta path, independently verify all of the following from current SPEC and the actual diff:

- Current SPEC already grounds the intended supported behavior repaired by the change.
- The diff restores conformance and does not add or change a supported contract.
- Public interfaces, configuration semantics, module ownership, dependency direction, architecture level, and supported-behavior scope remain accurately described.
- Concrete defect evidence demonstrates an implementation mismatch rather than an undocumented requirement.

The implementation agent's classification, a `bugfix` label, and passing tests are not sufficient proof. If any condition is unproven, return a blocking `spec-delta-required` finding owned by SPEC/human decision. Project implementation pauses until the relevant SPEC is updated and confirmed; after reconciliation and verification, re-audit that finding in repair-only mode. A decision that extends beyond the finding's repair scope requires explicit scope confirmation, not an automatic full re-audit.

## Python Architecture Audit

For Python modules, also verify the following within the declared audit mode's scope:

- Public Interface in SPEC matches `__init__.py` exports, endpoints, commands, events, or documented public symbols.
- Dependencies in SPEC match actual project imports.
- Internal Structure in SPEC matches actual files.
- No cross-module imports from `_private` implementation files.
- Dependency direction follows root `SPEC.md`.
- Domain/application/infrastructure/framework boundaries match SPEC.
- Boundary model choices match SPEC: Pydantic schemas, ORM models, DTOs, serializers, and domain objects do not silently swap roles.
- Tests cover public behavior and invariants promised by SPEC.

## Frontend Architecture Audit

For frontend-owned files, also verify the following within the declared audit mode's scope:

- Root module navigation and parent/child frontend SPEC links match actual ownership boundaries.
- Parent specs own workspace/build policy while child specs own application behavior, state flow, source structure, and browser integration.
- The implemented framework and source language match the module SPEC, including named legacy exceptions.
- npm manifest, lock file, Vite configuration, imports, and generated-output policy agree.
- Frontend code consumes documented backend transport contracts without importing backend implementation or duplicating backend security/filesystem policy.
- Server data, durable browser preferences, transient interaction state, derived values, streams, timers, and cleanup follow the documented owners.
- Loading, empty, error, disabled, cancellation, conflict, completion, responsive, keyboard, focus, and reduced-motion behavior required by SPEC is implemented.
- Available build, type, lint, unit/component, integration, package, browser, accessibility, and visual checks required by SPEC were run.

Use `.github/skills/frontend-architecture/references/audit-checklist.md` for the full checklist. Missing browser or screenshot evidence is blocking when the confirmed SPEC requires it.

## Package And Installation Audit

When packaging, dependencies, installation, or distribution behavior is affected, check the relevant manifests/lock files, declared package contents and entry points, compatibility/platform constraints, and install/runtime evidence against SPEC. Require a fresh installation environment and source-free execution where promised; isolated user configuration may reuse download caches unless the contract tests cache absence. Reused caches must not hide stale installed artifacts, missing packaged files, or unintended source-tree imports. Select evidence by affected contracts, not an automatic full test suite.

## Structured Result

Each applicable item records:

- SPEC source and requirement text or a precise requirement reference;
- represented implementation boundaries;
- concrete diff evidence;
- audit verdict;
- notes needed to reproduce or repair a gap.

Each finding records:

- a stable finding ID retained across repair-only passes;
- affected SPEC and behavior items or precise requirement references; explicitly absent grounding where applicable;
- the distinct root cause;
- blocking verdict and concrete evidence;
- repair owner: implementation, SPEC/human decision, or verification environment;

Duplicate observations of the same root cause are consolidated into one finding that may reference multiple SPEC items. Distinct root causes under one SPEC item remain distinct findings.

## Repair-Only Follow-Up

An audit pass must finish and return its assigned coverage tables and full finding set for the declared scope before any implementation repair begins. All review lanes and verification tasks join under `spec-driven` before a repair batch is issued. Do not interleave audit and repair.

After an audit result:

1. Resolve any SPEC/human-decision or verification-environment blocker that can change or prevent implementation repair.
2. Repair every implementation-fixable blocking finding from that result in one batch.
3. Freeze the complete repaired snapshot and select all verification affected by the repair batch.
4. Run affected verification and independent repair-only review concurrently where safe, using the previous aggregate report, its snapshot, the complete repair delta, relevant SPEC clauses, and neutral verification evidence. Join them before finalizing the pass.
5. Judge every previous-round finding against the actual repair and its controlling path. Keep an unfixed or partially fixed finding open. A defect in the repair that prevents the same requirement from being met also keeps that finding open. Reverse-trace all behavior changes introduced by the repair delta; record repair-introduced gaps with stable IDs linked to the originating finding. They cannot inherit earlier passing evidence.
6. Return a verdict for every previous-round finding and carry all remaining blockers forward. Repeat repair-only passes until none remain, subject to the repair limit below.

Do not reopen unrelated passing items or expand the pass into a new full audit. Unrelated discoveries are separate feedback, not automatic additions to this repair batch. If unrelated code or SPEC changes invalidate inherited evidence, pause for a scope decision rather than claiming coverage for them.

Run at most two automatic repair rounds. Switching from full to repair-only mode does not reset that limit. An `audit-blocked` result does not consume a repair round because no complete finding set is available to repair. If blocking findings remain after the second repair round, or one round makes no substantive progress, return the complete current finding status for human decision.

The implementation agent may not declare findings resolved. Only the independent reviewer may close a repaired finding with the applicable passing SPEC verdict or reverse classification; earlier unaffected passing verdicts remain backed by the initial audit and matching evidence.

## Consolidated Synchronization Category

SPEC/code synchronization is part of the applicable-item inventory, not a standalone scan. Cover applicable items within the complete change scope in the full first pass; in repair-only mode inspect the repairs and their affected boundaries:

- root module navigation/table, architecture diagram, and dependency direction against affected modules and actual dependencies;
- module public interfaces and exports;
- declared and actual dependencies;
- internal structure and ownership;
- Python architecture and boundary rules;
- frontend parent/child ownership, state, transport, dependency, build, and generated-output contracts;
- current-fact SPEC hygiene: exclude design-process/history/target-state narrative, future roadmap, and detailed test matrices that do not state current verification obligations.

Report synchronization verdicts within the current audit mode's result, not as another audit pass.

## Verdicts

- `implemented`: Diff contains concrete implementation satisfying the SPEC item.
- `incomplete`: Diff partially implements the SPEC item but leaves required behavior uncovered.
- `missing`: No meaningful implementation evidence exists in the diff.
- `diverged`: Implementation contradicts the SPEC item.
- `documentation-only`: Diff changes docs/specs but not required implementation.
- `interface-only`: Diff exposes signatures, exports, config, or declarations without required behavior.
- `mock-or-stub`: Diff uses placeholders, hardcoded responses, fake paths, or non-production behavior in place of required implementation.
- `boundary-violation`: Python imports, exports, layering, or model boundaries violate confirmed SPEC.
- `spec-delta-required`: Current SPEC does not ground the changed behavior or would become inaccurate without an update.
- `needs-human-decision`: SPEC and implementation cannot be reconciled without a product or design decision.

For SPEC-item verdicts, any verdict except `implemented` is blocking unless the user explicitly accepts `needs-human-decision` as out of scope for the current change. Reverse classifications use the grounding rules above. Deferring a finding does not authorize shipping an ungrounded or unauthorized change; reconcile the delivered scope and snapshot first.

## Required Output

For a full first pass, the forward or combined lane produces a complete item table:

```text
SPEC item | Boundaries | Diff evidence | Verdict | Notes
```

The reverse or combined lane also produces a complete behavior table:

```text
Behavior ID | Actual behavior/change | Code evidence | SPEC basis or absent | Task scope basis | Classification | Rationale/gap
```

Account for every changed path in these tables or an explicit non-behavioral classification. The aggregate result includes both inventories, not just the findings.

For every pass, produce the complete finding table for its scope:

```text
Finding ID | Affected SPEC/behavior items | Root cause | Evidence | Verdict | Repair owner
```

For repair-only passes, replace the full inventories with a table covering every previous-round finding and a scoped behavior table for the repair delta:

```text
Previous finding ID | Repair delta evidence | Verification evidence | Verdict | Remaining gap
```

State `audit_mode=full|repair-only`, `review_lane=combined|forward|reverse`, `coverage_complete=true|false` for that lane and mode's scope, `spec_delta_mode=confirmed-update|no-delta`, audited SPEC inputs and confirmation evidence, and validated diff artifact identities or exact commit range. State verification-dependent conclusions still pending. The aggregate result separately states forward and reverse coverage and verification status. A repair-only result also identifies the previous aggregate report and snapshot it extends. Do not describe scoped coverage as a new full audit. Each evidence entry must cite concrete files and, when possible, line numbers or changed symbols. If evidence is absent, say so directly.

## What Not To Accept

- "The tests pass" as proof that a SPEC item is implemented.
- "The implementation agent said it handled this" as proof.
- Keyword search as proof without reading the changed code path.
- A design document as final authority after `SPEC.md` exists.
- A `bugfix` or `no-spec-delta` label as proof that current SPEC needs no update.
- Code, tests, or unconfirmed SPEC edits as authorization for new behavior.
- A forward-only coverage table, a blanket implementation-detail exemption, or another lane's passing result as proof of reverse coverage.
- Agent workflow instructions presented as proof that project behavior satisfies SPEC.
- Public API declarations without backing behavior.
- Mocks, stubs, hardcoded success paths, or placeholder fallbacks as production implementation.
- Python layers or patterns that exist only as empty pass-through abstractions.

## Completion Gate

Before claiming completion, state:

- Which root/module `SPEC.md` files were covered by the full first pass and any repair-only follow-ups.
- Which validated snapshots or exact commit ranges connect the initial audit to the current implementation.
- Which SPEC delta mode was audited and, for no-delta work, whether its classification was independently validated.
- Whether the first full audit and every subsequent repair-only pass have complete coverage for their declared scopes.
- Whether both forward and reverse coverage are complete, every changed path is accounted for, and no ungrounded, conflicting, out-of-scope, or unverified behavior remains unresolved.
- Whether all review lanes and required verification have joined against the same snapshot, with pending evidence resolved by the responsible Reviewer.
- Whether every applicable SPEC item is supported by an initial passing verdict or an independent repair verdict, with unaffected evidence unchanged.
- Whether any unresolved blocking finding remains in the carried-forward finding set.
- Any remaining `needs-human-decision` items accepted by the user.

Completion can be established by the full first pass plus the repair-only audit chain; it does not require another full audit. If a pass is incomplete, the evidence chain is broken, required verification is unavailable, or blocking gaps remain, do not claim completion. State the covered scope and residual limits; an audit is not a zero-bug guarantee, and a passed test suite alone is not Done.
