# Getting Started

Describe a goal, let FSQ Quick Test operate the real app and verify the outcome, then inspect the evidence. **Coding Agent** and **FSQ UI** are equal alternatives. FSQ is alpha software; read [support and stability](support-and-stability.md).

## Prerequisites

- Python 3.11+ and [prerequisites for the chosen platform and target](platform-prerequisites.md).
- A supported configured Provider (OpenAI, DeepSeek, Google Gemini, Kimi, GitHub Copilot, or Azure OpenAI) and explicitly selected model.
- For Coding Agent setup, an exact project root in which to install the Skill.

Quick Test prepares the user-level `~/.fsq/quick-test/` Workspace automatically. No registered project Workspace, manual project initialization, or project Doctor is required. It does not inherit the opened project's knowledge, private environment, or credentials; put necessary non-secret project facts in the goal. It does not save a formal Case or automatically replay one.

## Coding Agent

Open your project in the Coding Agent and use this one-time setup:

> The official FSQ project is https://github.com/FSQ-lab/FSQ. Establish the exact opened project root. Batch and disclose read-only Python, fsq, and Skill-presence checks with their working directories, commands, purpose, and effects. Request one approval covering only installation or upgrade of fsq-agent and one fsq init --agent skill in this project; show exact commands and Python-environment and project-file effects first. Perform only approved operations, verify fsq, and report the installed Skill path. This setup does not initialize a Workspace, configure a Provider, or run a test. Ask me to reload or start a new Coding Agent session, then stop.

Reload or start a new session so the client discovers `.agents/skills/fsq/`. After upgrading the package, rerun `fsq init --agent skill` and reload to update the copied Skill.

Then ask: “Use FSQ Quick Test for the goal below, with Web and my confirmed available browser. Follow the installed FSQ Skill and ask only for decisions it cannot establish.” Include the complete goal below.

The Skill discloses one batch of read-only discovery and uses `quick-test context` to inspect the platform, device or browser, and prerequisites. It preserves confirmed exact targets without guessing or substituting browser channels, device IDs, or paths. It asks only for unresolved decisions that affect execution. Your test request authorizes one normal Quick Test without another FSQ-layer run confirmation; host and operating-system approvals remain independent.

If Provider setup is needed, open `fsq ui` yourself and enter credentials privately in **Settings**, selecting a model, or use private interactive Provider configuration. Never put keys, tokens, or secrets in chat, visible command arguments, goals, or Cases. After you report completion, the Skill checks safe status and refreshes context for the same target. Readiness is not a connection test or successful execution.

The Skill submits one `quick-test run`, consumes actual JSONL progress and a terminal result, and retains the same process. It never automatically resubmits after failure, timeout, interruption, lost output, or malformed output. It reports authoritative execution status, whole-goal verification, actual target, and available evidence. Mapping individual criteria to supported, unmet, or unknown is the Coding Agent's interpretation, not multiple independent FSQ verdicts.

## FSQ UI

```bash
python -m pip install --upgrade fsq-agent
fsq ui
```

1. Choose **Quick Test**.
2. Configure a Provider and explicitly select a model in shared **Settings**.
3. Select Web and an available browser, paste the goal below, and choose **Start Quick Test**. You do not need to know the website URL the search will discover.
4. Use **Open report** to inspect the result and evidence, or choose **New test**. Unresolved intent, zero or multiple target matches, and preparation failures stop without follow-up dialogue. Target availability does not prove the model understood the intended app.

## Complete first test

Web is an accessible starting option; the [current video](https://youtu.be/f1sobvVHNg0) shows Android. Setup and execution differ, and search results and outcomes can vary.

```text
Use the selected browser to search for the FSQ-lab/FSQ GitHub repository. Open that repository, follow the website link in its About section, and verify that the destination page displays both “Describe a goal.” and “Test the real app.” Report the outcome with the captured page evidence. Only browse public pages. Do not sign in, change repository content, or submit forms. Stop and report a blocker if the repository or About website link cannot be identified.
```

## Review results before choosing advanced workflows

Inspect actual status, captured page evidence, Run ID, Workspace, and available report paths. Preparation failure may have no Run; missing evidence remains unknown. Any Quick Test recording remains Run-local and unpublished; Quick Test does not save a formal Case.

For reusable Cases, separately choose **FSQ Case Manager** or explicitly request Case creation. This is another UI execution with registered Workspace and Doctor checks, not saving the completed Quick Test. Reviewable content exists only when a candidate is actually generated.

For replay, first inspect the exact returned Case unchanged, then request replay separately. The Skill performs static and Doctor checks and discloses the exact Case, target, and repeated side effects. Only your explicit confirmation after that disclosure authorizes one Strict Replay. Quick Test success does not prove replay success; the Skill does not automatically rewrite Cases or retry.

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

For browser configuration, run `fsq ui`, choose **Quick Test** or **FSQ Case Manager**, and open their shared **Settings**:

1. Choose **Add configuration** or **Change provider**, then **OpenAI**, **DeepSeek**, **Google Gemini**, or **Kimi**.
2. For Kimi, explicitly choose **China** or **Global**. Enter an API key and select **Load models**. Select one offered model, then **Save changes**.
3. Select **Test connection** to send a minimal request using the saved configuration.
4. In **Quick Test**, return to the goal form and explicitly start a test. In **FSQ Case Manager**, return to **Home** to see the Provider summary, then open **Test Runner**, choose a Workspace/platform/target, and start an Explore task explicitly.

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
fsq --output jsonl --non-interactive case create --platform web --goal "Open https://example.com and verify the Example Domain heading is visible."
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

Choose **Quick Test** for exploration without Workspace setup. Configure the shared Provider in **Settings**, select a platform and the applicable device/browser, and describe the application or search subject, actions, and expected result. Web supports both a supplied URL, such as `Open https://example.com and verify that its heading is Example Domain`, and a clear address-free search goal, such as `Search FSQ on GitHub and find its official website`.

Address-free Web searches reuse the existing blank-page browser startup and Agent navigation. FSQ does not add a search-engine setting or assume the website found by a previous test. The Agent follows your goal to search and inspect pages; supplied malformed or unsupported navigation addresses are rejected rather than turned into searches. Before an address-free search can be reported as successful, it must have actual browser activity and usable HTTP(S) page observations. A model-only answer or blank-browser startup is insufficient. Page/link evidence still needs to support the requested result, including any claim that a discovered URL is official.

**Start Quick Test** prepares a dedicated registered Workspace at `~/.fsq/quick-test/` and starts exploration only after input, concrete target, and runtime checks pass. All Quick Tests share this Workspace; each run freezes its own target. The model can infer the named application's package ID, bundle ID, or executable path; explicit identifiers can also be written in the same prompt. FSQ validates that resolved identity through the selected platform without requiring a separate localized application-name mapping. It does not exhaustively rule out all same-named installed apps, and availability does not prove that the model understood your intended application correctly. Insufficient or ambiguous intent, zero/multiple results within the resolved target lookup, and discovery or model failures stop the request without clarification or automatic retry. Correct the prompt or environment and start again explicitly.

Results use the existing timeline and evidence views. **Open report** opens the persistent report, and **New test** returns to the prompt. Quick Test has no Save Case action or Strict Replay mode. Leaving or refreshing the page does not cancel an active server task, and returning recovers the active Quick Test while the same local service is running. Restarting the service does not resume execution.

Choose **FSQ Case Manager** for the complete Workspace, Case, history, and strict-replay workflows described elsewhere in this guide. The dedicated Quick Test Workspace is visible there. Existing Run report links still open the corresponding report directly.

## Next steps

- Review [platform prerequisites](platform-prerequisites.md).
- Learn the [Case format](case-format.md).
- See the [CLI reference](cli-reference.md).
- Review the root and module `SPEC.md` files for implementation-level architecture and behavior contracts.
