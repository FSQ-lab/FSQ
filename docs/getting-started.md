# Getting Started

This guide uses the package-owned Skill for a guided, platform-neutral AI
Explore, with optional review and user-initiated deterministic replay. FSQ is alpha
software; review [support and stability](support-and-stability.md) before
production adoption.

## Prerequisites

- Python 3.11 or newer.
- The prerequisites for the platform and target you want to test.
- A directory you can open as the exact local Coding Agent project root.

AI exploration and suggestion analysis also require OpenAI, DeepSeek, Google Gemini, Kimi, GitHub Copilot, or Azure OpenAI. Deterministic Case replay does not require a planning LLM unless the authored Case contains an AI assertion.

## Install the CLI and project-local Skill

Open the project in your Coding Agent and use this one-time setup:

> The official FSQ project is https://github.com/FSQ-lab/FSQ. Establish the
> exact opened project root. Batch and disclose read-only Python, `fsq`, and
> Skill-presence checks. Then request one approval covering only
> `python -m pip install --upgrade fsq-agent` and one
> `fsq init --agent skill` from that project root, with exact commands and
> effects. Perform only approved operations, verify `fsq`, and report the exact
> installed Skill path. This does not initialize a Workspace, configure a
> Provider, or run a test. Tell me to reload or start a new Coding Agent
> session, then stop.

Reload or start a new Coding Agent session so the client discovers
`.agents/skills/fsq/`. After a package upgrade, rerun
`fsq init --agent skill` and reload again to update the copied Skill.

Then state your actual goal concisely:

> Test this user goal with FSQ: **[describe the outcome]**. Follow the installed
> FSQ Skill and ask only for decisions it cannot establish.

Examples—not defaults—include: Android, “a signed-out user sees a clear error
for invalid sign-in”; Web, “a shopper can filter results and open one product”;
Windows, “a user can create and save a draft”; macOS, “a user can change a
preference and see it persist.” Do not guess a target ID or path.

## Guided AI Explore

The installed Skill leads the dialogue and uses the public FSQ CLI. It will:

1. Prefer the exact opened project root established by the host; if ambiguous,
   treat the current directory only as a candidate and ask you to resolve it.
   From that root it runs Doctor before asking for facts Doctor can provide,
   including the platform and exact current target.
2. Reuse a valid registered Workspace at the project root. Otherwise inspect
   only the exact `<project-root>/fsq_workspace`: reuse it if it is a valid
   registered Workspace; initialize an existing empty ordinary directory; or,
   after approval, create one empty absent directory with the host's native
   filesystem capability and initialize it.
3. Block on a symlink, non-directory, unreadable/indeterminate path, or non-empty
   invalid directory. It never searches broadly, creates a nested Workspace,
   follows or replaces a symlink, or overwrites, deletes, moves, or migrates
   content. It does not prescribe `mkdir` or assume a shell.
4. Before creation or initialization, show one cohesive recommendation with
   exact project and Workspace roots, platform, exact target, fact sources,
   candidates, exact existing `fsq init` argv, effects, and unauthorized
   actions. One approval covers the exact directory creation, when needed, and
   one initialization. It then verifies the actual returned root with Doctor.
5. Keep FSQ facts, host facts, user decisions, and unverified model candidates
   distinct. Candidate app IDs, browser targets, executable paths, bundle IDs,
   devices, or accounts never enter argv until their exact value is confirmed.
6. Ask for the smallest cohesive group of unresolved user-owned decisions.
   It may propose Goal criteria, actions, and restrictions, but automatic
   defaults only reduce authority or stop on ambiguity. It never silently
   selects a target, account, recipient, exact content, amount, device, or
   irreversible action, and shows exact literals and side effects in one
   complete Goal before a separate create confirmation. Initialization,
   creation, and replay permissions remain separate.

Related read-only checks use one compact disclosure of their exact working
directories, commands, purpose, and read-only effect; ordinary read-only
commands do not each require another approval. Ambiguous scope or an unmet
prerequisite blocks rather than being silently defaulted.

### Provider handoff

If AI Explore needs Provider setup, this is a user-controlled handoff. API
keys, tokens, and authentication secrets must not enter chat. The Coding Agent
asks you to open `fsq ui` yourself and enter credentials in **Settings**; the
private interactive `fsq providers configure "<provider>"` command is the
fallback. After you report completion, it checks Provider status and Doctor readiness
again, then resumes the same Workspace, target, and Goal draft without
repeating settled questions unless facts changed. Readiness is not a connection
test or a successful execution.

### Create, review, and optionally replay

After showing the complete confirmed Goal, the Skill may submit one approved
AI Explore with `fsq --output json --non-interactive case create ...`. It does
not retry an unknown, interrupted, failed, or contradictory result. Submission
consumes that confirmation. It reports submission once, may report no new
structured status once without being asked, and then waits silently rather than
inventing live action progress.

The Skill summarizes the authoritative execution result first and maps your
observable acceptance criteria to supported, unmet, or unknown evidence before
separately reporting recording, publication, and warnings. It selects only the
exact returned Case path: `published_case_path`, or otherwise
`candidate_case_path` marked as unpublished.

It reviews that exact returned Case, unchanged and read-only, only if you ask.
Replay is also initiated only by your request. After that request, it runs
read-only Doctor and static prechecks, including:

```bash
fsq case format "<exact-case-path>" --check --json
```

It then shows the exact path and target and strongly warns that replay is a new
target operation that may repeat side effects. Known failure/recovery or
repeated corrective mutation is included as risk, but does not automatically
rewrite or refuse replay. If you still want replay after that warning, the
Skill restates the residual risk and requires a second explicit confirmation.
Only after that confirmation may it submit the unchanged Case once with
`fsq --output json --non-interactive case test --platform "<platform>"
"<exact-case-path>"`, without suggestions. Creating a Case never pre-approves
replay, and Explore success does not prove deterministic replay success.

## Provider-free Web troubleshooting fallback

This bundled Strict Case is an explicit troubleshooting fallback, not the
primary experience or a prerequisite for your Goal. Ask the installed Skill to
use `assets/getting-started.web.fsq.yaml` only when you intentionally want to
check a configured Web target without a Provider. It verifies Doctor and
canonical formatting first, obtains explicit execution confirmation, executes
at most once, and uses only the exact returned Run ID. It does not configure a
Provider, use suggestions, retry, or start Control Plane.

## Advanced/historical TodoMVC example

The existing
[`examples/web/example-domain.fsq.yaml`](../examples/web/example-domain.fsq.yaml)
is the recorded TodoMVC example despite its historical filename. It is not the
recommended first run. To use it after setup:

```bash
mkdir -p cases/web
curl --fail --location --output cases/web/example-domain.fsq.yaml \
  https://raw.githubusercontent.com/FSQ-lab/FSQ/main/examples/web/example-domain.fsq.yaml
fsq case test --platform web cases/web/example-domain.fsq.yaml
fsq runs list --platform web
```

The Case starts the configured browser, opens TodoMVC, adds two tasks, completes the first task, filters to active tasks, verifies the expected visible state, and closes the browser. Evidence is stored below `.fsq/runs/web/<run-id>/`.

## Configure AI exploration

```bash
fsq providers configure github_copilot
fsq providers status
```

Alternatively run `fsq providers configure openai` for the official OpenAI API, `fsq providers configure deepseek` for the official DeepSeek Responses API, `fsq providers configure kimi` for the official China or Global Kimi API, or `fsq providers configure azure_openai` for an Azure deployment. Provider configuration is user-level, stored below `~/.fsq`, and shared with the local Control Plane. API-key Providers prompt privately for a key and offer eligible models; even a single model requires explicit selection.

For browser configuration, run `fsq ui` and open **Settings**:

1. Choose **Add configuration** or **Change provider**, then **OpenAI**, **DeepSeek**, **Google Gemini**, or **Kimi**.
2. For Kimi, explicitly choose **China** or **Global**. Enter an API key and select **Load models**. Select one offered model, then **Save changes**.
3. Select **Test connection** to send a minimal request using the saved configuration.
4. Return to **Home** to see the Provider summary, then open **Test Runner**, choose a Workspace/platform/target, and start an Explore task explicitly.

OpenAI always uses `https://api.openai.com/v1/` and the Responses API. Its picker offers general-purpose GPT major version 5 or later, excluding mini, nano, Codex, embedding, audio, realtime, image, search, transcription, and TTS variants. An empty list cannot be saved. Changing the key invalidates the list and selection; reload before saving. Azure instead requires a resource endpoint, **deployment name** (not necessarily an OpenAI model id), and API key.

DeepSeek always uses `https://api.deepseek.com/` and the Responses API. Its picker accepts `deepseek-flash`, exact versioned Flash ids from 4.1 onward, and exact versioned Pro ids from 4.0 onward, with no upper version bound. Other families and extra suffixes are excluded. Discovery is not a capability or image-support probe; unsupported input fails normally without automatic retry, model substitution, or Provider fallback.

For Gemini, obtain an API key from [Google AI Studio](https://aistudio.google.com/apikey). Use the browser flow above or run `fsq providers configure google_gemini`; the interactive command hides the key and requires an explicit model selection. The picker loads all bounded model-list pages and offers only stable Gemini 3-or-later general-purpose Flash/Pro ids with content-generation support. Preview, Latest, Experimental, Lite, image/audio/embedding and other specialized variants are excluded. An incomplete discovery is an error, not a partial selectable list. The fixed Developer API uses native Interactions with `store=false` and local conversation history; Vertex AI, ADC/service accounts, and custom endpoints are not supported.

Gemini metadata lives in the version-3 user configuration and its plaintext key in `~/.fsq/auth/google-gemini.json`. The same saved Provider serves pre-planning, execution, final verification, AI visual assertions, suggestions, and connection tests. **Test connection** proves a minimal saved-model request, not every tool/schema feature or Case. Start Explore explicitly, inspect its evidence, and save the generated Case using the normal flow; Strict Replay uses Gemini only when its Case requires AI assertions.

For Kimi, create a key in the [China platform](https://platform.kimi.com/console/api-keys) or [Global platform](https://platform.kimi.ai/console/api-keys). China keys use only `https://api.moonshot.cn/v1`; Global keys use only `https://api.moonshot.ai/v1`. The picker accepts stable `kimi-k3` or later versioned models and rejects preview, beta, alpha, experimental, release-candidate, latest, and dated snapshot ids without maintaining a fixed positive allowlist. A future model passing this filter is best-effort rather than guaranteed compatible. FSQ uses the Responses API only, maps neutral reasoning effort to Kimi `low`, `high`, or `max`, and does not fall back to Chat Completions, Messages, another model, or another region.

Kimi image capability is not a picker requirement. If the selected model rejects an image, schema, tool, or Responses request, FSQ returns the normal safe model/AI assertion error. Dynamic execution can choose another available tool on a later turn; an authored static AI assertion remains a failed step with its evidence. Kimi metadata lives in the version-3 user configuration and its plaintext key in `~/.fsq/auth/kimi.json`.

Saving rechecks model visibility but does not run inference. Readiness/status is a local configuration check, not a connection test. API keys are plaintext local credentials, masked in Settings by default but returned in full by the trusted-loopback Config API. Successful Provider replacement removes inactive credentials. Neither configuration nor execution falls back to environment variables or another Provider.

If the save response is lost, the page reports an unknown outcome and re-reads configuration. Use **Reload configuration** to retry recovery. Closing the page does not cancel a server save; the current snapshot is not proof that an earlier request finished. FSQ does not automatically resubmit or undo the save. After recovery, review the current Provider and any remaining unsaved draft before continuing.

## Explore and inspect

```bash
fsq --output json --non-interactive case create --platform web --goal "Open https://example.com and verify the Example Domain heading is visible."
fsq case format "<exact-returned-case-path>" --check --json
# Only after separate explicit confirmation:
fsq --output json --non-interactive case test --platform web "<exact-returned-case-path>"
fsq --output json --non-interactive runs show "<exact-returned-run-id>" --platform web
fsq --output json --non-interactive runs logs "<exact-returned-run-id>" --platform web --limit 50
```

Use only the Case path and persistent Run ID returned by the submitted
operation; do not construct either value or infer the latest Run. The static
check is read-only, and the unchanged Case is replayed only after the separate
confirmation described above.

## Analyze a deterministic run

```bash
fsq case test --platform web --suggest cases/web/example-domain.fsq.yaml
```

The Case is executed exactly once. AI analysis then consumes only the source Case, report, and persisted evidence. Suggestions and any candidate Case remain inside the corresponding Run.

## Open the Control Plane

```bash
fsq ui
```

The installed frontend is served locally on `127.0.0.1:8879` by default.

## Next steps

- Review [platform prerequisites](platform-prerequisites.md).
- Learn the [Case format](case-format.md).
- See the [CLI reference](cli-reference.md).
- Review the root and module `SPEC.md` files for implementation-level architecture and behavior contracts.
