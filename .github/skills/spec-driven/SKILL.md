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
  -> read current SPEC and implementation evidence
  -> determine whether a SPEC delta is required
  -> if required: update root/module SPEC.md and get user confirmation
  -> if none: record existing-SPEC and defect evidence
  -> implement against confirmed SPEC.md
  -> run fast self-checks and freeze the pending-review snapshot
  -> run verification and the first bidirectional spec-implementation-audit in parallel where safe
  -> join all results and resolve authority/environment blockers
  -> batch-fix complete finding set
  -> from round 2, freeze repairs and join repair-only audit with affected verification
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

Before modifying non-SPEC project files, read the root and relevant module specs plus enough implementation and verification evidence to classify the request.

A no-SPEC-delta path is allowed only when all of these are true:

- Current SPEC already grounds the intended supported behavior and remains accurate after the repair.
- The request restores implementation conformance rather than adding or changing a supported contract.
- Public interfaces, configuration semantics, module ownership, dependency direction, architecture level, and the set of supported behaviors remain unchanged.
- The defect is demonstrated by concrete implementation evidence, a failing focused test, or a reproducible behavior mismatch.

Record the no-SPEC-delta decision before implementation with precise SPEC references, defect evidence, and the boundaries expected to remain unchanged. A user label such as `bugfix` or `no-spec-delta` is not proof and does not control this decision.

If any condition is unproven, current SPEC is missing or wrong, or the requested behavior conflicts with SPEC, a SPEC delta is required. Update the relevant current-fact specs and get user confirmation before modifying non-SPEC project files. Never add artificial SPEC text solely to manufacture a delta.

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

1. Read root `SPEC.md` and relevant module `SPEC.md` files.
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

1. Read root `SPEC.md` and current module specs for every touched module.
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

1. Re-read confirmed root/module specs.
2. Implement only what the confirmed specs require.
3. If implementation reveals a missing or wrong SPEC decision, stop and update SPEC first.
4. Keep edits scoped to affected modules and tests.
5. For behavior changes, write or update tests before production code unless the user explicitly accepts a generated-code or throwaway exception.
6. For frontend work, follow the confirmed framework/language contract and the staged frontend implementation rules; do not mix a partial migration into an existing exception.
7. Run the agreed fast self-checks before handoff, preserving failures. Schedule remaining required verification with the independent audit under the parallel review lifecycle below; no required check is waived.

## Agent Team Orchestration

When the workspace agent team is available, Tech Lead is the coordinator for this workflow and uses the shared protocol in root `AGENTS.md`.

1. Tech Lead scopes and coordinates the task under the root team protocol; use PM only for unresolved goals, acceptance criteria, or product tradeoffs, not for every substantive change.
2. Use Designer only for actual interaction or design choices, not merely because existing UI, CLI, accessibility, or failure behavior is touched.
3. Tech Lead owns the SPEC-delta decision and all user confirmation gates. Read-only team output is advisory until persisted in an authorized authoritative file.
4. After SPEC confirmation or a recorded no-SPEC-delta decision, give Developer one bounded implementation task at a time with exclusive file ownership and required verification.
5. At pending-review, Tech Lead freezes and identifies the complete audit snapshot below. Give Reviewer a fresh, independent task package that directs it to `.github/skills/spec-implementation-audit/SKILL.md`, the confirmed SPEC versions, authorized task scope, assigned review lane, verification plan/status, and complete artifact identities.
6. Tech Lead runs verification directly or assigns one bounded verification-only task to Developer after its implementation handoff. That task may execute checks but not modify project inputs or start repairs. Review and verification can run concurrently; Developer still handles only one assigned task at a time.
7. Join all results, resolve decisions and conflicts, and return one implementation repair batch to the original Developer. Use Reviewer for repair-only follow-up in parallel with affected verification. Tech Lead owns integration validation and the final Done decision.

If custom-agent invocation is unavailable, state that limitation and follow the same role separation directly. Do not pretend delegation occurred, and do not weaken the independent audit requirement.

## Project Audit Lifecycle

After Developer's fast self-checks and pending-review handoff, freeze the snapshot and start required verification alongside `audit_mode=full` where safe. The first audit independently establishes both the applicable SPEC inventory and the actual behavior inventory from confirmed SPEC inputs and the complete diff. It checks SPEC-to-code correctness and code-to-SPEC grounding/scope. Finding the first blocker must not end the pass, and implementation repair must not begin while any review or verification task is still running.

Default scheduling is one combined independent Reviewer using valid existing evidence, with any outstanding verification in parallel where safe. Split into forward and reverse Reviewers only when substantial separate context addresses a concrete SPEC/acceptance-criterion risk that justifies the cost and available agent capabilities permit it. Assign both the same snapshot and explicit complementary coverage. If safe concurrency or agent capacity is unavailable, serialize without dropping a lane or verification obligation, and report the limitation.

From round 2 onward, use `audit_mode=repair-only`: audit the previous round's finding repairs, including reverse grounding of behaviors introduced by the repair delta. Carry unresolved and repair-introduced findings forward, retain unaffected passing verdicts only with unchanged evidence, and do not rebuild the full inventories or search for unrelated new issues. Freeze each repair batch and run its review alongside affected verification. The audit skill owns the detailed scope and verdict rules.

### Verification Scheduling And Join

Local verification targets diff impact: changed behavior, directly affected consumers, and relevant boundary/negative cases. Existing CI owns the full suite and platform matrix; do not routinely duplicate them locally. Map checks to affected SPEC/acceptance criteria and retain every explicitly required verification obligation. Risk follows behavior and boundaries, not line count: a high-risk one-line change can require substantial focused coverage.

Agents may autonomously expand local checks, including a full suite, only with a concrete evidence gap and a recorded reason the expansion closes it. For a local full run, explain why focused checks or CI evidence are insufficient and why the full run helps. "For insurance" or "a shared module changed" alone is not enough. Missing specialized CI coverage calls for the relevant targeted check, not automatically a full suite; changing CI configuration still requires task scope and authorization.

Before launching a needed check, record its unique owner, exact command, working directory, tested inputs/snapshot, relevant environment, coverage, and resource/output isolation. Reuse verifiable raw results when those inputs, environment, and coverage still match; link earlier evidence to the frozen snapshot. A change invalidates affected checks, not unrelated proof. Do not repeat a check because a different role is consuming it. The audit skill owns independent evidence assessment and verdicts.

Use existing runner concurrency only when tests are isolated. Bound workers to available resources. Checks sharing devices, databases, fixed ports, generated files, or output directories must be isolated or serialized. Use check modes, not autofix, source generation, or snapshot-update modes that alter audit inputs. Builds and tests may write declared isolated outputs, not mutate the frozen source/SPEC/configuration inputs. External or device operations still require their own authorization.

Isolate test user configuration without discarding reusable download caches by default. Clean-install contracts require a fresh installation environment and source-free execution where the contract demands it; an empty download cache is a separate requirement, not a synonym for a clean install. Reuse must not mask a stale installation or bypass network/organization policy.

Collect exact commands, exit status, raw output/evidence locations, tested inputs, and `passed|failed|blocked|cancelled` for each check, including matching CI run/commit identity where used. A timeout, missing result, or unavailable environment is not a pass. Reviewers may complete static analysis while checks run; verification-dependent verdicts remain pending until the responsible Reviewer consumes the results.

Wait for all review lanes and verification tasks to finish or explicitly stop them before changing inputs. A blocked/cancelled/incomplete lane prevents a complete aggregate result; restore the missing coverage or report the blocker, rather than issuing a partial repair batch. Completed checks that fail still contribute their full evidence and repairable findings to the aggregate.

Tech Lead then checks snapshot identity agreement, forward and reverse coverage, and all required verification results. Merge duplicate root causes while preserving lane/source finding IDs and affected items. Resolve contradictory reviewer conclusions with the reviewers, not by selecting the more permissive verdict. Classify findings as implementation, SPEC/human decision, or verification environment. Only after this join and prerequisite decisions may the original Developer receive one consolidated repair batch. No passing lane overrides a failed or incomplete one.

### Audit Artifact And Identity Gate

Freeze one review evidence package for the complete change scope: precise confirmed SPEC inputs, actual complete in-scope diff, necessary source context, and verification commands/raw results. Full means the complete change scope, not a repository-wide historical audit. Prefer a complete Git diff or immutable commit range plus a compact identity manifest, not repository copies or repeated semantic reads of unrelated SPEC files.

For the first worktree audit, create the complete in-scope tracked diff as a file directly through Git, such as `git diff HEAD --no-ext-diff --no-textconv --binary --output=<artifact> -- <allowed-paths>`. Include both staged and unstaged changes, declare the base, and account for pre-existing changes without overwriting or silently accepting unrelated user work. For a repair-only pass, create the complete repair delta through Git between the previous audited snapshot and the current snapshot, including new or deleted repair files. Never use terminal-captured stdout or a tool's overflow wrapper as the audit artifact.

Before starting the reviewer:

1. Record the artifact's absolute path, SHA-256, byte size, and `diff --git` entry count.
2. Record Git's complete changed-path inventory for the declared audit scope and verify its count and paths agree with the artifact.
3. Verify that no in-scope untracked project file is omitted. Include each such file as a separate complete artifact with its path and SHA-256, or stop as `audit-blocked`.
4. Give the reviewer the artifact identities and require it to validate them before auditing. A missing, truncated, wrapped, mismatched, or unreadable artifact is `audit-blocked`; do not substitute a live diff or an implementation summary.
5. For repair-only passes, include the previous independent report and snapshot, map the repair delta to its finding IDs, and verify that code and SPEC evidence outside the repair scope is unchanged. Do not require the reviewer to re-audit unchanged content.
6. Include exact root and relevant module SPEC content identities and confirmation evidence, the authorized scope, and the necessary frozen source context outside the diff. Use immutable Git object/path references for unchanged inputs; capture only necessary non-immutable inputs completely with their identities. All review lanes and verification tasks must name this same snapshot or explicitly link valid reused evidence to it. Independently check that SPEC edits match what the user confirmed; code and SPEC changing together is not confirmation.

Treat the verified artifact identities as the audit snapshot. Do not intentionally edit its project inputs while any review lane or verification task is running. Use an isolated snapshot only if needed to keep inputs stable, preserving all in-scope tracked/untracked inputs and necessary context; integrity does not require copying the entire repository. After the joined result and immediately before claiming completion, regenerate the worktree artifacts with the same commands and compare their SHA-256 values, path inventory, SPEC identities, and source-context identities with the audited/tested snapshot and inherited evidence. A mismatch blocks completion: re-verify and re-audit affected evidence, or ask for a scope decision if the changes are unrelated. Preserve unaffected coverage; do not automatically restart a full audit. For an audited commit range, record the exact immutable base and head object ids instead of a worktree artifact hash.

Each audit result must declare its mode and review lane and contain precise SPEC references (or explicitly absent grounding), concrete diff evidence, verdicts, repair ownership, complete coverage for its assigned scope, and the full finding status for that scope. The aggregate retains both coverage inventories and all verification statuses. A repair-only report accounts for every previous-round finding and repair-introduced findings, including unresolved findings carried from earlier rounds. Missing required authority inputs, tools, or artifacts permit an early `audit-blocked` result, not a passing verdict or a consumed repair round. User pause and safety/authorization stops under root `AGENTS.md` take precedence over completing a pass; report interrupted coverage as incomplete, never passed.

After the complete result returns:

1. Group all open findings by implementation repair, authority/human decision, or verification environment.
2. Resolve authority/human decisions and verification-environment blockers before starting an implementation repair batch when they can change or prevent that repair.
3. Repair all in-scope implementation-fixable blocking findings in one batch. Do not trigger another audit after only a partial repair.
4. Freeze the entire repair batch and run affected verification alongside independent repair-only review where safe.
5. Join both results using the previous aggregate report, repair delta, and relevant SPEC evidence. Reverse-check repair-introduced behaviors; do not reopen unrelated passing items or expand the pass into a new full audit.
6. Repeat repair-only audit, complete repair, and verification rounds until no unresolved blocking findings remain, subject to the repair limit below.

The implementation agent may explain its repair but may not declare audit findings resolved. Only the independent reviewer determines whether each repair satisfies the previous finding. Supply the previous independent report as the follow-up scope, not a persuasive implementation summary.

Run at most two automatic repair rounds; switching to repair-only mode does not reset this count. If the audit after the second repair round still has blocking findings, or one complete repair and re-audit round makes no substantive progress, pause automatic repair and present the complete current finding status, current evidence, attempted repair rounds, reviewer rationale, and concrete human decision options. The findings remain blocking until an allowed decision and any required project SPEC confirmation are complete.

## Consolidated Project Implementation Audit

For a project change, `spec-implementation-audit` owns both coverage inventories, risk/domain checks, SPEC/code synchronization, verdict semantics, evidence assessment, and coverage for each audit mode and review lane. Apply its complete change-scope checks in the first pass and its repair/affected-boundary checks in later passes; do not add a separate synchronization scan or duplicate checklist here.

Before claiming project completion, read and run:

```text
.github/skills/spec-implementation-audit/SKILL.md
```

Audit against the current project authority inputs and actual diff:

```text
root SPEC.md + relevant module SPEC.md files + actual diff
```

Tests, lint, and summaries are supporting evidence only. They do not replace diff-based SPEC audit.

If project SPEC itself needs correction, stop project implementation, update and confirm the applicable SPEC, reconcile the implementation and verification with it, and re-audit the relevant finding in repair-only mode. If the decision introduces changes outside that finding's repair scope, obtain an explicit scope decision rather than automatically restarting a full audit.

## Completion Gate

Do not claim completion while any blocking finding remains. Completion also requires:

- a complete first audit plus complete repair-only coverage of each previous round's findings;
- complete forward and reverse coverage, including repair-introduced behaviors, with no unresolved grounding or scope gaps;
- all review lanes and verification tasks joined against the same frozen snapshot, with no pending verification-dependent verdicts or unresolved reviewer conflicts;
- a matching post-audit artifact identity check linking the current worktree or immutable commit range to that audit chain;
- the first pass's complete applicable-item inventory, with unaffected passing evidence unchanged;
- concrete diff evidence and an initial or repair verdict for every item;
- no unresolved implementation-fixable blocking finding in the carried-forward finding set;
- required verification passed; failed, missing, cancelled, or unavailable verification remains blocking;
- any accepted `needs-human-decision` recorded explicitly.

These gates do not require a new full audit after repairs. Unrelated changes cannot inherit coverage and require a scope decision. They govern Done, not whether Tech Lead can promptly deliver an accurate partial milestone under its role instructions. Pending required CI, review, or external approval remains blocking for the affected completion stage; do not relabel it to ship.

## Required Final Report

End with:

- Input type: confirmed design document or direct project change request.
- Project specs updated and confirmed, or the no-SPEC-delta decision with precise SPEC and defect evidence.
- Files implemented.
- Verification commands run and results.
- Review-lane assignments, concurrency or serialization used, snapshot-matched verification evidence, and the joined forward/reverse coverage result.
- Audited SPEC inputs, full and repair-only audit identities or exact commit ranges, post-audit identity result, and cumulative finding status. Distinguish the full first pass from later repair-only passes.
- Complete current finding status, repair rounds performed, and any accepted human decisions.
