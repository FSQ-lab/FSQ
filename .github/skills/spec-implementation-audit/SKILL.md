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

Standard-mode completion cannot be claimed until implementation is audited against:

```text
relevant root SPEC.md sections + affected module SPEC.md files + exact git diff / base commit
```

Tests, lint, keyword scans, and implementation summaries are auxiliary evidence only. They do not replace diff-based SPEC audit.

## Audit Modes

- Round 1 uses `audit_mode=full`: run the complete change-scope procedure below with every applicable check, not a repository-wide historical audit.
- Round 2 and later use `audit_mode=repair-only`: audit only the repairs for the previous round's findings. Carry every unresolved earlier finding forward in that previous-round report so none is dropped.

A repair-only pass reads the previous independent report, its audited snapshot, the complete repair delta, and the SPEC clauses and implementation paths needed to judge those repairs. It does not rebuild the full applicable-item inventory, re-read unchanged parts of the original diff, or search for unrelated new issues. Retain unaffected passing verdicts only when their evidence remains unchanged.

Do not automatically restart a full audit. Changes outside the previous findings' repair scope require an explicit scope decision before they can be treated as covered.

## Independence Requirement

Use a fresh reviewer or independent context whenever the platform supports it. The reviewer must not inherit the implementation agent's conversation history or rely on its self-report.

Reviewer input is converged to high-signal authority and diff context:

- Affected module `SPEC.md` files and the relevant sections of root `SPEC.md` (do not force reading the entire root `SPEC.md` end-to-end when only specific architecture edges or Development Rules apply).
- Base commit / commit range or exact scoped `git diff` (plus explicit paths for any in-scope untracked project files).
- For repair-only passes, the previous independent report, its open finding IDs, and the repair delta from the previous reviewed state.
- SPEC delta mode: `confirmed-update` or `no-delta`, plus confirmation evidence and the authorized task scope/exclusions.
- Assigned `review_lane=combined|forward|reverse` and coverage boundaries.
- Already-passed local verification evidence (pinned Ruff lint/format results and affected-scope focused test commands, exit status, and tested paths); local checks must be green before Reviewer is invoked.
- For no-SPEC-delta work, the recorded SPEC references and neutral defect reproduction evidence as claims to verify, not accepted proof.

Do not provide persuasive summaries such as "this is complete" or "tests pass, so it should be fine".

## Audit Input Integrity

Review against the supplied base commit / commit range or exact scoped `git diff` and any declared in-scope untracked project files. Do not require temporary diff files with manual byte-size, `diff --git` entry-count, or pre/post double-SHA-256 regeneration ceremonies. If the diff is truncated, unreadable, or omits declared changed/untracked project paths, return `audit-blocked` rather than guessing from an implementation summary.

Read the affected module `SPEC.md` files and relevant root `SPEC.md` sections directly. Compare changed SPEC sections with what the user confirmed; code and unconfirmed SPEC edits changing together do not establish approval. Necessary source context outside the diff may be read directly from the frozen worktree or Git revision without copying the repository or reading unrelated module specs.

## Verification Evidence

Local Ruff and affected-scope focused tests are a prerequisite gate before Reviewer starts. Independently read the relevant tests and call paths, and consume the supplied verifiable local/CI results (exact commands, exit status, tested paths, and environment) rather than requesting reruns for independence. Identify any concrete coverage gap in the findings if focused tests miss a required boundary or negative path.

## Review Lanes

Default to one independent Reviewer with `review_lane=combined`. Under the root cost principle and `spec-driven` scheduling rules, Tech Lead may assign `forward` and `reverse` lanes concurrently against the same complete snapshot when concrete risk and substantial separate context justify splitting and agent capabilities permit it. Both lanes read the relevant SPEC and actual implementation; lane names describe coverage responsibility, not restrictions to reading only one kind of evidence.

- `forward`: owns the applicable SPEC inventory, implementation correctness, regressions, domain checks, and verification obligations.
- `reverse`: independently derives actual behavior changes and implementation boundaries from the diff and necessary paths, then checks SPEC grounding and authorized task scope.
- `combined`: owns both inventories and all checks.

A split-lane report declares only its own coverage and cannot pass the whole audit. Tech Lead joins both reports, resolves gaps or conflicting verdicts with the reviewers, and preserves all findings and source IDs. Neither inventory may be omitted because the other lane passed.

Local verification completes before Reviewer dispatch under `spec-driven`; Reviewer evaluates whether the executed focused checks actually cover the changed contracts and risk boundaries.

## First-Round Complete Scope

Before judging implementation in round 1, establish the complete scope:

- the affected module `SPEC.md` files and relevant root `SPEC.md` sections;
- the exact scoped `git diff` (plus any in-scope untracked files) or commit range;
- the SPEC delta mode;
- the complete set of applicable SPEC items;
- the complete set of in-scope added, changed, or removed behaviors and affected implementation boundaries, independently derived from code rather than filtered by known SPEC items;
- the file, symbol, interface, dependency, configuration, or behavior boundaries relevant to each item;

Determine this initial scope independently for the assigned lane. Account for every changed path, including untracked files, tests, configuration, and behavior documentation. Group related changes by behavior; explain non-behavioral changes rather than requiring one SPEC clause per line. This is change-scope coverage, not a claim to audit all historical behavior. Later repair-only passes take their scope from the previous findings and repair delta rather than repeating this inventory.

## First-Round Complete Procedure

1. Internally establish both the applicable SPEC requirements and the actual behavior changes from the diff and affected call paths, including independent validation of a `no-SPEC-delta` classification when applicable.
2. Perform both directions of inspection:
   - **Forward (`SPEC -> code`)**: completeness, behavioral correctness, boundary/error handling, architecture rules, and focused test adequacy against confirmed SPEC requirements.
   - **Reverse (`code -> SPEC`)**: trace every added, changed, or removed behavior (and test/doc promise) back to a confirmed SPEC basis and authorized task scope.
3. Apply the correctness/risk checks below and the Python, frontend, package, browser, and visual domain checks when affected.
4. Consolidate duplicate observations of each blocking root cause with precise SPEC/file/symbol references. When grounding is absent, record `SPEC basis: absent`.
5. Classify each finding as **Blocking** or **Non-blocking**, and record repair ownership (`implementation`, `SPEC/human decision`, or `verification environment`).
6. Emit an **exception-only** report: output only concrete findings (Blocking / Non-blocking), unverified or out-of-scope items, and a concise coverage declaration. Do NOT emit exhaustive per-item pass tables for items that passed both forward and reverse checks.

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

Keep internal bidirectional checking thorough while keeping output exception-only:

- Do not output row-by-row tables of passing SPEC clauses or passing `grounded` / `implementation-detail` behaviors.
- Every Blocking or Non-blocking finding records:
  - stable Finding ID (retained across `repair-only` passes);
  - severity (`Blocking` or `Non-blocking`) and verdict / reverse classification;
  - affected SPEC reference (or `SPEC basis: absent`) and concrete file/line/symbol evidence;
  - distinct root cause, trigger condition, impact, and repair owner (`implementation`, `SPEC/human decision`, or `verification environment`).

Duplicate observations of the same root cause are consolidated into one finding referencing all affected items. Distinct root causes remain distinct findings.

## Repair-Only Follow-Up

An audit pass must finish and return its exception-only report and coverage declaration before any implementation repair begins. Do not interleave audit and repair.

After an audit result:

1. Resolve any SPEC/human-decision or verification-environment blocker that can change or prevent implementation repair.
2. Repair every implementation-fixable blocking finding from that result in one batch.
3. Run local Ruff and affected focused tests on the repaired worktree until green, then freeze the repair delta.
4. Run independent `audit_mode=repair-only` review using the previous report, the repair delta, relevant SPEC sections, and green local verification results.
5. Judge every previous-round finding against the actual repair and reverse-trace any new behavior introduced by the repair delta. Record resolution status (`resolved` vs still open) for each prior Finding ID plus any newly introduced findings.
6. Repeat repair-only passes until no Blocking findings remain, subject to the two-round repair limit below.

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

Use **Exception-Only Output** for all audit passes (do not generate full forward/reverse tables of passing items):

1. **Audit Header & Concise Coverage Declaration**:
   - `audit_mode=full|repair-only`, `review_lane=combined|forward|reverse`, `spec_delta_mode=confirmed-update|no-delta`, `coverage_complete=true|false`.
   - Audited inputs: affected module `SPEC.md` paths, relevant root `SPEC.md` sections, base commit or scoped `git diff` target (plus any untracked files), and consumed local verification commands/status.
   - Concise bidirectional scope summary: 1–3 bullets or lines stating which modules/files/behaviors were checked forward (`SPEC -> code`) and reverse (`code -> SPEC`) and confirming all passing items satisfied grounding and correctness.
2. **Findings (`Blocking` then `Non-blocking`)** — omit table or write `None` when zero findings exist:
   ```text
   Finding ID | Severity | Verdict / Classification | SPEC ref (or absent) | File / Symbol Evidence | Root cause & Impact | Repair owner
   ```
3. **Repair-Only Delta Status** (only when `audit_mode=repair-only`):
   ```text
   Previous Finding ID | Status (resolved / open) | Repair & Verification Evidence | Remaining gap (if open)
   ```
4. **Unverified Or Out-Of-Scope Items**:
   - Explicitly list any unverified paths (for example CI-only platform matrix, real-device paths not run locally) or out-of-scope changes requiring human decision, or `None`.

## What Not To Accept

- "The tests pass" as proof that a SPEC item is implemented.
- "The implementation agent said it handled this" as proof.
- Keyword search as proof without reading the changed code path.
- A design document as final authority after `SPEC.md` exists.
- A `bugfix` or `no-spec-delta` label as proof that current SPEC needs no update.
- Code, tests, or unconfirmed SPEC edits as authorization for new behavior.
- One review direction's check, or a blanket `implementation-detail` claim without tracing actual behavior changes, as proof of bidirectional coverage.
- Agent workflow instructions presented as proof that project behavior satisfies SPEC.
- Public API declarations without backing behavior.
- Mocks, stubs, hardcoded success paths, or placeholder fallbacks as production implementation.
- Python layers or patterns that exist only as empty pass-through abstractions.

## Completion Gate

Before claiming completion, state:

- Which module `SPEC.md` files and root `SPEC.md` sections were covered, and which base commit or scoped `git diff` was audited.
- Which SPEC delta mode was audited and, for `no-delta` work, whether its conformance-only basis was independently validated.
- Whether both forward (`SPEC -> code`) and reverse (`code -> SPEC`) checks completed across all changed paths with zero unresolved Blocking findings (`ungrounded`, `conflicting`, `out-of-scope`, `incomplete`, `diverged`, etc.).
- Whether local Ruff and affected-scope focused tests passed on the reviewed state, and any residual CI-only or external verification boundaries.
- Any remaining `needs-human-decision` items explicitly accepted by the user.

Completion can be established by the full first pass plus the repair-only audit chain; it does not require another full audit. If a pass is incomplete, the evidence chain is broken, required verification is unavailable, or blocking gaps remain, do not claim completion. State the covered scope and residual limits; an audit is not a zero-bug guarantee, and a passed test suite alone is not Done.
