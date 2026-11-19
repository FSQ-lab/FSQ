---
name: spec-driven
description: "Required project modification workflow. Use when the user explicitly invokes /spec-driven with a confirmed design document path or direct project change request."
argument-hint: "Confirmed design path or direct project change request"
user-invocable: true
disable-model-invocation: true
---

# Spec-Driven Development

Turn an explicitly supplied confirmed project design document or direct project change request into SPEC-grounded implementation, verification, and independent audit. This user-invocable skill is the required `/spec-driven` project write entry point; `/requirements-to-design` is optional upstream input refinement.

## Invocation Gate

Run this skill only when the user explicitly invokes `/spec-driven` with either a confirmed project design document path or a direct project change request. Do not infer invocation or skill input from ordinary discussion, editor state, a natural-language project edit request outside the skill invocation, a skill-name mention, or prose approval. If the explicit invocation or a non-empty input is absent, stop without writing and ask the user to invoke `/spec-driven <confirmed-design-document-path | direct-project-change-request>`.

This skill does not handle workflow-control-only maintenance. If the supplied input changes only `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `.github/agents/**`, `.github/prompts/**`, or `.github/skills/**`, stop and explain that a clear ordinary edit request is sufficient.

## Core Rule

`SPEC.md` files are the source of truth for project implementation and must describe current project or module facts.

- Root `SPEC.md` owns current project-wide architecture, module navigation, dependency diagrams, project development constraints, and project implementation invariants.
- Module `SPEC.md` files own current module contracts, public interfaces, internal structure, dependencies, error handling, architecture level, and current invariants.
- Design documents, implementation notes, migration history, removed behavior, future roadmap, and detailed test matrices are not SPEC content unless they describe a currently supported compatibility behavior or a current verification obligation.

Project SPEC files must not define, duplicate, or override the SDD workflow. Agent workflow files may consume project SPEC as audit input, but they must not make SPEC the authority for how SDD is invoked or executed.

Project implementation may start only after required `SPEC.md` changes are reviewed and confirmed, or after a no-SPEC-delta decision is recorded with concrete existing-SPEC and defect evidence. If current SPEC does not ground the intended supported behavior, a SPEC delta is required.

## SPEC Hygiene Rule

Before writing or updating any `SPEC.md`, apply this filter:

- Keep: current behavior, current public API, current module ownership, current dependency direction, current configuration surface, current error semantics, current architecture level, and current invariants that constrain implementation.
- Keep as compatibility facts only when true in code: legacy input shapes, compatibility aliases, obsolete keys that are actively rejected, and supported migration shims. Write them as present-tense facts, not as history.
- Move out of SPEC: why a decision was made, discarded alternatives, design-process narrative, implementation plan, future platform ideas, planned signatures, target-state wording, removed behavior that code no longer accepts, and exhaustive test case lists.
- Avoid process markers such as `target`, `planned`, `future`, `after this change`, `this SPEC cycle`, `first batch`, `transitional`, `removed`, `previously`, and `during migration` unless the sentence is documenting a current compatibility fact or current rejection behavior.
- If a module SPEC starts to become a reference manual, keep only ownership and invariants in SPEC and move tables, examples, endpoint catalogs, or command catalogs to a reference document.

## Required Flow

```text
explicit /spec-driven <confirmed-design-document-path | direct-project-change-request>
  -> resolve and clarify the input
  -> read relevant module SPEC.md and relevant root SPEC.md sections + implementation evidence
  -> determine whether a SPEC delta is required
  -> if required: update root/module SPEC.md and get user confirmation
  -> if none: record existing-SPEC and defect evidence
  -> classify audit path: Fast-Path vs Standard independent audit
  -> implement against confirmed SPEC.md
  -> run local Ruff + affected-scope focused tests until green (mandatory pre-audit gate)
  -> if Fast-Path: main Agent inspects diff against SPEC/scope and completes
  -> if Standard: freeze pending-review snapshot -> run independent bidirectional audit (exception-only report) -> batch-fix findings -> repair-only re-verify + re-audit
  -> final report
```

Do not redirect a direct project change request to `/requirements-to-design`; that design phase is optional. Clarify ambiguous direct input within `/spec-driven` before deciding the SPEC delta or writing project files.

## Input Resolution

If the input resolves to a design document path, require the document to exist and be user-confirmed, then read it before deciding the SPEC delta. Design documents normally use:

```text
docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
```

If the input is not a design document path, treat the complete prompt argument as the direct project change request. Read enough current implementation, tests, and local documentation to remove material ambiguity. Resolve ordinary technical choices through evidence and Tech Lead coordination; ask focused user questions only for unresolved product, scope, risk, or authorization decisions. Do not require a separate design document or infer missing SPEC confirmation.

A design document or direct request is input to the SPEC-delta decision, not final project implementation authority. Project code must be implemented against current confirmed `SPEC.md` files.

When translating a design document into SPEC updates, copy only the resulting current contract. Do not copy the design process, rejected options, implementation sequence, temporary target state, or historical narrative into SPEC.

## SPEC Delta Decision

Before modifying non-SPEC project files, read the relevant module `SPEC.md` files and the relevant sections of root `SPEC.md` plus enough implementation and verification evidence to classify the request.

A no-SPEC-delta path is allowed only when all of these are true:

- Current SPEC already grounds the intended supported behavior and remains accurate after the repair.
- The request restores implementation conformance rather than adding or changing a supported contract.
- Public interfaces, configuration semantics, module ownership, dependency direction, architecture level, and the set of supported behaviors remain unchanged.
- The defect is demonstrated by concrete implementation evidence, a failing focused test, or a reproducible behavior mismatch.

Record the no-SPEC-delta decision before implementation with precise SPEC references, defect evidence, and the boundaries expected to remain unchanged. A user label such as `bugfix` or `no-spec-delta` is not proof and does not control this decision.

If any condition is unproven, current SPEC is missing or wrong, or the requested behavior conflicts with SPEC, a SPEC delta is required. Update the relevant current-fact specs and get user confirmation before modifying non-SPEC project files. Never add artificial SPEC text solely to manufacture a delta.

## Fast-Path Lightweight Channel

A change qualifies for **Fast-Path** only when it requires no SPEC delta (or updates only non-contract release/documentation metadata) and falls strictly within one or more of these bounded categories:

- Pure version bumps or package/release metadata preparation.
- User/developer documentation, `CHANGELOG`, or release notes updates.
- Pure test repairs, test isolation adjustments, or cross-platform skip/marker fixes that do not weaken contract assertions.
- Pure formatting or lint remediation preserving runtime behavior and public exports.
- Localized `no-SPEC-delta` bug fixes that do NOT touch public interfaces, security or privacy boundaries, session/process/device lifecycle, runtime dependencies, or publication/release permissions.

If any part of the diff touches an excluded boundary or requires a behavioral SPEC delta, Fast-Path is disallowed; use the Standard independent audit lifecycle.

When Fast-Path applies:

1. Complete implementation within the declared scope.
2. Pass the local pre-review verification gate (pinned Ruff lint/format checks where Python is touched, plus focused tests for affected modules and direct consumers, or equivalent frontend/build checks).
3. Exempt the independent `Reviewer` subagent: the main Agent (or Tech Lead) directly inspects the exact `git diff` against the recorded SPEC basis and task scope, confirms no excluded boundary was crossed, and proceeds to the final report.

## Python Architecture Integration

For Python projects or Python modules, apply the sibling `python-architecture` rules layer before:

- Choosing module ownership.
- Writing or updating module `SPEC.md` files.
- Implementing code.
- Running the consolidated project implementation audit.

When this bundle is installed under `.github/skills/`, the references are expected at:

```text
.github/skills/python-architecture/SKILL.md
.github/skills/python-architecture/references/architecture-levels.md
.github/skills/python-architecture/references/module-spec-template.md
.github/skills/python-architecture/references/implementation-rules.md
.github/skills/python-architecture/references/audit-checklist.md
```

Load only the reference needed for the current phase:

- SPEC design or module ownership: `architecture-levels.md` and `module-spec-template.md`.
- Implementation: `implementation-rules.md`.
- Consolidated project audit: `audit-checklist.md`.

If the sibling files are unavailable, apply the local Python rules below and continue.

### Python Rules Summary

- Default to the simplest architecture level that satisfies the SPEC.
- Do not introduce Repository, Unit of Work, Service Layer, Clean Architecture, or DDD patterns without a SPEC-recorded reason.
- Public APIs are exported through module entry points such as `__init__.py`.
- Internal modules use the project convention, usually `_name.py`, and must not be imported across module boundaries.
- Domain logic must not depend on FastAPI, Django, Flask, SQLAlchemy sessions, HTTP request objects, or CLI argument parsers unless the module SPEC explicitly permits that coupling.
- Pydantic schemas, serializers, ORM models, and DTOs are boundary models unless the SPEC explicitly chooses a simpler combined model.

## Frontend Architecture Integration

For frontend projects or modules, apply the sibling `frontend-architecture` rules layer before:

- Choosing frontend module ownership.
- Writing or updating frontend `SPEC.md` files.
- Implementing frontend source, build, or dependency changes.
- Running frontend verification and the consolidated project implementation audit.

The references are expected at:

```text
.github/skills/frontend-architecture/SKILL.md
.github/skills/frontend-architecture/references/architecture-levels.md
.github/skills/frontend-architecture/references/module-spec-template.md
.github/skills/frontend-architecture/references/design-rules.md
.github/skills/frontend-architecture/references/implementation-rules.md
.github/skills/frontend-architecture/references/verification-checklist.md
.github/skills/frontend-architecture/references/audit-checklist.md
```

Load only the reference needed for the current phase:

- Requirements or visible design: `architecture-levels.md` and, when applicable, `design-rules.md`.
- SPEC ownership and authoring: `architecture-levels.md` and `module-spec-template.md`.
- Implementation: `implementation-rules.md`.
- Verification: `verification-checklist.md`.
- Consolidated project audit: `audit-checklist.md`.

Frontend rules do not create a new user entry point and must not be fetched from remote sources at runtime. New frontend application modules default to the root Vite workspace with React and TypeScript/TSX. Existing modules keep the framework and language in their confirmed module SPEC until a separate SPEC update authorizes a migration.

## SPEC Update Procedure

### New module or feature

1. Read relevant module `SPEC.md` files and relevant sections of root `SPEC.md`.
2. Read the resolved design document or direct project change request.
3. Decide which module owns the feature, or whether a new module is needed.
4. For Python work, choose the Python architecture level and write the rationale into SPEC.
5. For frontend work, choose the frontend architecture level or accurately record a current legacy exception and write the ownership boundaries into SPEC.
6. Write or update relevant module `SPEC.md` files.
7. If adding a module or changing module relationships, update root `SPEC.md` module table and architecture diagram.
8. Ask the user to confirm SPEC changes before implementation.
9. Implement only after confirmation.
10. Run verification and the consolidated project implementation audit.

### Existing functionality change

1. Read current module specs for every touched module and relevant sections of root `SPEC.md`.
2. Read the resolved design document or direct project change request.
3. Determine impact: public interface, module contract, internal-only, or cross-module dependency change.
4. Apply the SPEC delta decision rules.
5. When a delta is required, update relevant specs and ask the user to confirm them before implementation.
6. When no delta is required, record the required evidence and proceed without editing SPEC.
7. Implement against current confirmed SPEC.
8. Run verification and the consolidated project implementation audit.

## Module SPEC Structure

Every module has exactly one `SPEC.md`. Use this structure unless root `SPEC.md` defines a compatible local convention:

```text
# Module: {name}
## Purpose
## Dependencies
## Public Interface
## Internal Structure
## Python Architecture        (for Python modules)
## Data And State Flow        (for stateful frontend modules)
## Frontend Architecture      (for frontend modules)
## Error Handling             (if applicable)
## Verification Scope         (optional; current externally visible verification obligations only)
## Current Invariants         (optional; present-tense constraints that keep implementation aligned)
```

Root `SPEC.md` should contain repository-wide sections such as:

```text
# {project} Project Specification
## Project Specification Ownership
## Module Table
## Architecture Diagram
## Development Rules
## Python Architecture Rules   (for Python repositories)
```

Do not add `Testing Contract` or `Design Decisions` as default sections for new module specs. Existing sections with those names should be narrowed during touched updates: test matrices move to tests/docs, while decision rationale becomes current invariants only when it still constrains code.

## Project Implementation Rules

After required project SPEC confirmation or a recorded no-SPEC-delta decision:

1. Re-read confirmed module specs and relevant root `SPEC.md` sections.
2. Implement only what the confirmed specs require.
3. If implementation reveals a missing or wrong SPEC decision, stop and update SPEC first.
4. Keep edits scoped to affected modules and tests.
5. For behavior changes, write or update tests before production code unless the user explicitly accepts a generated-code or throwaway exception.
6. For frontend work, follow the confirmed framework/language contract and the staged frontend implementation rules; do not mix a partial migration into an existing exception.
7. Pass local Ruff validation and affected-scope focused tests before freezing a snapshot or dispatching Reviewer. Do not launch an independent audit while local checks are still running or failing.

## Agent Team Orchestration

When the workspace agent team is available, Tech Lead is the coordinator for this workflow and uses the shared protocol in root `AGENTS.md`.

1. Tech Lead scopes and coordinates the task under the root team protocol; use PM only for unresolved goals, acceptance criteria, or product tradeoffs, not for every substantive change.
2. Use Designer only for actual interaction or design choices, not merely because existing UI, CLI, accessibility, or failure behavior is touched.
3. Tech Lead owns the SPEC-delta decision and all user confirmation gates. Read-only team output is advisory until persisted in an authorized authoritative file.
4. After SPEC confirmation or a recorded no-SPEC-delta decision, give Developer one bounded implementation task at a time with exclusive file ownership and required verification.
5. Before pending-review freeze, require local Ruff lint/format checks and affected-scope focused tests to be completely green. If the change qualifies for Fast-Path above, Tech Lead verifies the diff and targeted checks directly without spawning Reviewer.
6. For Standard changes, Tech Lead freezes the snapshot (base commit or exact scoped `git diff` plus any in-scope untracked files) after local checks are green, and gives Reviewer a lean, independent task package pointing to `.github/skills/spec-implementation-audit/SKILL.md`, the affected module `SPEC.md` files and relevant root `SPEC.md` sections, authorized task scope, assigned review lane, and passed local verification results. Do not run local tests in parallel with dispatching Reviewer, and do not require Reviewer to read unrelated root `SPEC.md` sections or perform manual byte-size/entry-count/double-hash diff ceremony.
7. Join audit results, resolve any authority/scope decisions, and return one implementation repair batch to the original Developer. After repairs pass local Ruff and affected focused tests, run repair-only Reviewer follow-up on the repair delta. Tech Lead owns integration validation and the final Done decision.

If custom-agent invocation is unavailable, state that limitation and follow the same role separation directly. Do not pretend delegation occurred, and do not weaken the independent audit requirement.

## Project Audit Lifecycle

Local verification is a strict prerequisite gate, never a parallel background track alongside Reviewer dispatch:

1. Run pinned Ruff lint and format checks (when Python files are touched) and focused tests for affected modules and direct consumers (plus affected frontend typecheck/test/build checks when frontend files are touched).
2. Do not freeze the audit snapshot or start `Reviewer` until local Ruff and affected-scope focused tests are green.
3. If the task qualifies for Fast-Path, main Agent/Tech Lead performs direct diff verification against the recorded SPEC basis and scope without spawning an independent Reviewer.
4. Otherwise, freeze the snapshot and run `audit_mode=full` with one independent combined Reviewer by default. Split into `forward` and `reverse` Reviewers only when substantial separate context addresses a concrete SPEC/acceptance-criterion risk that justifies the cost.

The first Standard audit independently checks both SPEC-to-code completeness/correctness and code-to-SPEC grounding/scope across the affected module `SPEC.md` files, relevant root `SPEC.md` sections, and exact diff, emitting an exception-only report (findings, unverified/out-of-scope items, and concise coverage declaration rather than exhaustive per-item pass tables). Finding the first blocker must not end the pass early.

From round 2 onward, use `audit_mode=repair-only`: after the repair batch passes local Ruff and affected focused tests, audit only the previous round's finding repairs and reverse grounding of behaviors introduced by the repair delta. Carry unresolved and repair-introduced findings forward without rebuilding full inventories or re-auditing unchanged files.

### Local Verification Gate

Local verification targets diff impact: pinned Ruff lint/format validation plus focused pytest checks for changed modules, directly affected consumers, and relevant boundary/negative cases (and corresponding frontend/package checks when touched). GitHub Actions CI owns the complete pytest suite and cross-platform matrix; do not routinely run the full pytest suite locally.

Agents may expand local checks only with a concrete evidence gap and a recorded reason the expansion closes it. Reuse verifiable raw results when inputs, environment, and coverage still match. Use check modes (`ruff check`, `ruff format --check`), not autofix or snapshot-update modes, once ready to freeze for review. Collect exact commands, exit status, and tested scope so Reviewer can consume the green verification summary and raw evidence without rerunning checks.

### Lean Snapshot And Reviewer Input

Keep Reviewer inputs minimal and high-signal:

1. Identify the snapshot by base commit / commit range or exact scoped `git diff` (for example `git diff <base> -- <allowed-paths>`) plus explicit paths of any in-scope untracked project files. Account for pre-existing user work without overwriting or silently absorbing unrelated changes.
2. Pass only the affected module `SPEC.md` files and the relevant sections of root `SPEC.md` (such as touched architecture edges or applicable Development Rules), confirmed SPEC delta evidence (or recorded `no-SPEC-delta` basis), authorized task scope, and the already-green local verification commands/results.
3. Do not force Reviewer to read the entire root `SPEC.md` end-to-end when only specific sections apply, and do not generate temporary diff files solely to manually cross-check byte sizes, `diff --git` entry counts, or pre/post double SHA-256 regeneration ceremonies.
4. Do not edit in-scope files while Reviewer is running. Before claiming completion, confirm via `git status` / `git diff` that the worktree or commit HEAD still matches the reviewed state and passed local checks.

After the audit result returns:

1. Group open findings by implementation repair, authority/human decision, or verification environment.
2. Resolve authority/human decisions before starting an implementation repair batch when they can change or prevent that repair.
3. Repair all in-scope implementation-fixable blocking findings in one batch, re-run local Ruff and affected focused tests until green, and then run `audit_mode=repair-only` on the repair delta.
4. Repeat until no unresolved blocking findings remain, up to at most two automatic repair rounds. If blocking findings remain after two repair rounds or a round makes no substantive progress, pause and present the open findings, evidence, and concrete human decision options.

The implementation agent may explain its repair but may not declare audit findings resolved; in Standard mode only the independent Reviewer closes findings.

## Consolidated Project Implementation Audit

For a project change, `spec-implementation-audit` owns both coverage inventories, risk/domain checks, SPEC/code synchronization, verdict semantics, evidence assessment, and coverage for each audit mode and review lane. Apply its complete change-scope checks in the first pass and its repair/affected-boundary checks in later passes; do not add a separate synchronization scan or duplicate checklist here.

Before claiming project completion, read and run:

```text
.github/skills/spec-implementation-audit/SKILL.md
```

Audit (via independent Reviewer in Standard mode, or main-Agent diff check in Fast-Path) against the applicable project authority inputs and actual diff:

```text
relevant root SPEC.md sections + affected module SPEC.md files + exact git diff / base commit
```

Tests, lint, and summaries are supporting evidence only. They do not replace diff-based SPEC audit.

If project SPEC itself needs correction, stop project implementation, update and confirm the applicable SPEC, reconcile the implementation and verification with it, and re-audit the relevant finding in repair-only mode. If the decision introduces changes outside that finding's repair scope, obtain an explicit scope decision rather than automatically restarting a full audit.

## Completion Gate

Do not claim completion while any blocking finding remains. Completion also requires:

- local Ruff validation and affected-scope focused tests passed before review/completion;
- for Fast-Path: qualifying boundary check, direct diff verification against SPEC/scope, and zero excluded-surface changes;
- for Standard mode: complete bidirectional independent audit (`full` first pass plus `repair-only` coverage of any prior findings) with concise scope declaration and zero unresolved blocking findings;
- worktree/commit state unchanged since the green local gate and final review;
- any accepted `needs-human-decision` recorded explicitly.

Pending required CI, external review, or release approval remains blocking for merge/release, without preventing prompt PR creation or milestone reporting when authorized.

## Required Final Report

End with:

- Input type (`confirmed design document` or `direct project change request`) and audit channel (`Fast-Path` or `Standard`).
- Project specs updated and confirmed, or the `no-SPEC-delta` decision with precise SPEC and defect evidence.
- Files implemented.
- Local verification commands run and results (Ruff + affected-scope focused tests).
- Audit coverage summary (Fast-Path diff check or Standard Reviewer lane/mode, SPEC sections/files checked, base commit or diff scope) and exception-only finding status (open/resolved findings, unverified or out-of-scope items, repair rounds performed, and any accepted human decisions).
