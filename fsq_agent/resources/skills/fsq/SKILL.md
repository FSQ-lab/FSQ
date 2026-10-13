---
name: fsq
description: Run the package-owned provider-free FSQ Web Strict getting-started Case and hand off its exact Run to the local Control Plane.
---

# FSQ Web Strict handoff

Use this Skill only when the user asks to run the bundled provider-free Web
getting-started Case and inspect its exact persistent Run in the local FSQ
Control Plane.

## Required input and scope

Require the exact registered FSQ Workspace root. Use it as the working directory
for every command. Do not search parent directories or substitute the Coding
Agent project. Require Doctor's returned Workspace root to equal that working
directory. The bundled Case is `assets/getting-started.web.fsq.yaml` relative to
this `SKILL.md`; resolve that installed file once and use the same absolute path
for static checking and execution.

This workflow is Web-only and Provider-independent. A configured browser is its
only external prerequisite; the bundled Case uses a package-owned inline HTML
document and makes no network request.

## Commands

Before each command, show the exact working directory, command, purpose, and
whether it mutates state.

1. From the exact Workspace root, run the read-only diagnosis:

   `fsq --output json --non-interactive doctor`

   Require the returned Workspace root to equal the command working directory,
   and require the Web platform and `commands.case_test.status` to be `ready`.
2. Run the read-only static check on the resolved bundled Case:

   `fsq case format "<absolute-case-path>" --check --json`

   Require `valid=true`. A valid but noncanonical result is a blocker; never
   use `--write`.
3. Submit at most one execution:

   `fsq --output json --non-interactive case test --platform web "<absolute-case-path>"`

   Never submit another Case command for this user request, regardless of
   interruption, timeout, malformed or contradictory output, missing terminal
   output, lost response, or failed/inconclusive/cancelled/error status.
4. If and only if the submitted operation returns a persistent Run ID, read that
   exact Run:

   `fsq --output json --non-interactive runs show "<run-id>" --platform web`

   Require that exact Run ID, platform `web`, and mode `strict` in the result.
   Optionally read at most 50 exact-Run log events:

   `fsq --output json --non-interactive runs logs "<run-id>" --platform web --limit 50`

Treat machine output as valid only when its schema and operation match the
command and its fields do not contradict the exit status. Without a verified
persistent Run ID, report an unknown handoff and do not execute again.

## Local Control Plane handoff

Build the relative route only from the exact Application-backed Run read:

`/#runs?workspace=<percent-encoded-workspace-name>&platform=web&run=<percent-encoded-run-id>`

Report the registered Workspace name, platform `web`, Run ID, execution mode and
status, and the route. Do not use request IDs, absolute paths, current UI
selection, directory order, or a latest-Run guess. Tell the user to start
`fsq ui` and open the route manually; this Skill never starts Control Plane or
calls its HTTP API.

Copied Control Plane text is convenience context, not execution truth. If the
user pastes UI text or asks to continue with this identity, re-run the exact
`runs show` read before stating current authoritative facts.

## Prohibitions

Never initialize or configure a Workspace, configure a Provider, install or
repair prerequisites, write or copy Case or configuration YAML, invoke Explore
or suggestions, use AI assertions or runtime secrets, run `runCase` or
`runShell`, operate browser UI directly, start Control Plane, call Control Plane
HTTP, export or rewrite evidence, mutate a completed Run, retry execution,
submit another Case, or delegate parallel execution.
