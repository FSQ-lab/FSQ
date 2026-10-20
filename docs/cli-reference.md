# CLI Reference

FSQ installs `fsq` and the compatibility alias `fsq-agent`. Both invoke the same CLI. Run `fsq COMMAND --help` for the authoritative options of the installed version.

## Global options

| Option | Meaning |
|---|---|
| `--output human|json|jsonl` | Select human or machine-readable output. |
| `--non-interactive` | Reject flows requiring terminal interaction. |

## Public commands

| Command | Purpose | Workspace required |
|---|---|---|
| `fsq init` | Initialize a Workspace platform, a project-local Coding Agent integration, or both. | No |
| `fsq doctor` | Diagnose configured platforms without mutation. | Yes |
| `fsq providers configure/status` | Configure or inspect the user-level Provider. | No |
| `fsq quick-test context` | Discover platform readiness and device/browser choices without execution. | No project Workspace |
| `fsq quick-test run` | Execute one standalone Explore goal in the dedicated Quick Test Workspace. | No project Workspace |
| `fsq case create` | Execute one AI-driven goal. | Yes |
| `fsq case test` | Execute an existing Case exactly once. | Yes |
| `fsq case test --suggest` | Execute once, then analyze persisted facts. | Yes |
| `fsq runs list/show/logs` | Query Workspace Run history without execution readiness. | Yes |
| `fsq runs export RUN_ID` | Export JSON, JUnit, standalone HTML, or an evidence bundle. | Yes |
| `fsq ui` | Start the local browser entry for Quick Test or FSQ Case Manager. | No |

Except when creating an unregistered Workspace, Workspace commands use the exact current directory. They do not search ancestors or accept an alternate Workspace flag.

`fsq ui` can start from any directory. Choose **Quick Test** in the browser for exploration using the automatically prepared `~/.fsq/quick-test/` Workspace, or **FSQ Case Manager** for the complete existing workflows. Both use the same **Settings**. Entry selection does not itself start a test. Standalone CLI Quick Test uses the same preparation/execution contracts without starting the HTTP service. See [the browser workflow](getting-started.md#open-the-control-plane).

Human output is for terminals. JSON produces one structured result. JSONL emits documented events and a terminal result where streaming applies. Invalid command requests use exit code 2; consume stable machine fields rather than human wording.

Standalone Quick Test forms:

```bash
fsq quick-test context --platform PLATFORM [--target-id ID]
fsq quick-test run --platform PLATFORM --goal TEXT [--target-id ID] [--url URL]
fsq --output jsonl --non-interactive quick-test run --platform web --target-id chrome --url 'https://example.com/search?q=FSQ#results' --goal "Verify the results contain FSQ"
```

`PLATFORM` is `android`, `web`, `windows`, or `macos`, subject to existing host/runtime prerequisites. Context is read-only: it does not send inference, create a Workspace, or launch an application. A run requires an exact Android device serial or supported Web browser channel in `--target-id`; Windows/macOS reject that run option and resolve application identity from the goal. Choose an available target reported by context.

Web-only `--url` supplies one complete HTTP(S) starting address separately from the required goal. It preserves the exact address, including case, escapes, port, path, query, fragment, and punctuation. Invalid or unsafe addresses fail before inference or Workspace mutation, without correction or search fallback. Keep test actions, expected outcomes, and limits in `--goal`. Without `--url`, existing goal-based address parsing and address-free search remain available. Browser Quick Test retains its single goal field.

Quick Test needs no registered project Workspace and does not inherit the current project's knowledge, private environment, or settings. Runs use the dedicated `~/.fsq/quick-test/` Workspace and shared user-level Provider settings. It is always Explore; recordings remain Run-local, with no save/publication option. Explicit Case creation and strict replay retain their existing commands.

Machine operations are `quick-test.context` and `quick-test.run`. JSON returns one terminal envelope; JSONL streams actual progress followed by one terminal result/error when output remains writable. Context exits `0` when startable and `4` when unavailable. Run exits are `0` success, `1` failed/inconclusive, `2` validation, `3` Workspace/configuration, `4` Provider/runtime unavailable, `5` infrastructure, and `130` cancellation/interruption. Interrupting requests finalization of the same Run; missing terminal facts leave the outcome unknown and do not authorize a retry.

Run export works without browser interaction or a Provider:

```bash
fsq --output json --non-interactive runs export RUN_ID --format html --output report.html
fsq runs export RUN_ID --format bundle --baseline BASELINE_RUN_ID --related-run EXPLORE_RUN_ID
```

Choose one `--format json|junit|html|bundle`. Omitted `--output` creates a unique Run-local export directory; an explicit path names an absent output file. Existing files are not overwritten. `--baseline` requires comparable source identity; `--related-run` requires persisted lineage. `--share-profile PATH` selects bounded, local artifact/text/mask transformations on copies, without publishing. The global `--output json` controls CLI output, while the subcommand's `--output` controls the exported file. A successful export exits zero even for a failed Run; the report retains its non-passing gate. See [CI evidence](ci-evidence.md) and the [public demo source](../examples/evidence-demo/README.md).

`fsq init --agent skill` installs the host-neutral FSQ Agent Skill manifest
byte-for-byte at `.agents/skills/fsq/` in the current project. The manifest
contains exactly `SKILL.md` and
`assets/getting-started.web.fsq.yaml`. The bundled Case is a provider-free Web
Strict check of the `FSQ Web Strict Replay` heading in a package-owned inline
HTML document; a configured browser is its only external prerequisite and the
Skill presents it only as an explicit troubleshooting fallback.

The Skill defaults to standalone Quick Test. The Coding Agent prepares the
application using authorized project tools, gathers platform/device/browser
facts with `quick-test context`, and resolves only unsettled user choices. It
supplies the full goal, observable acceptance conditions, and action/data
limits; a known Web starting address goes in `--url`. FSQ owns target validation,
UI execution, evidence, and whole-goal verification. The default journey does
not require project Workspace discovery or initialization.

Provider credentials are never a Coding Agent chat input. If Quick Test is
blocked, the preferred handoff is for the user to run `fsq ui` and enter
credentials directly in Settings; private interactive `fsq providers configure`
is the fallback. The Skill does not start or call the UI, receive credentials,
or treat `providers status` as a connection test.

The request to verify a goal permits one `quick-test run` submission without a
second FSQ execution confirmation. The Skill uses `--output jsonl` for actual
progress and one terminal result/error, retains the same process for
cancellation, and never automatically resubmits after failure, cancellation,
timeout, or lost output. It leads with FSQ's whole-goal verdict and separately
maps acceptance conditions to returned evidence as supported, unmet, or unknown.
Optional Run reads use only exact returned Workspace/platform/Run identities
with `runs show` or bounded `runs logs`; there is no latest-Run guessing or
Quick Test Case publication.

Explicit Case creation remains a separately requested execution with its
existing publication behavior. Separately confirmed strict replay uses the
exact unchanged, statically valid Case in its registered Workspace at most once
without `--suggest`; Quick Test success does not prove replay success. The Skill
does not author or rewrite Case YAML. The separate Codex compatibility agent
retains terminal `--output json` for Goal-based Case creation.

Review the installed instructions and use the compatible host's discovery/reload
flow. Because installation copies package resources, upgrading the package or
reloading the host alone does not update an existing project copy. After a
package upgrade, run `fsq init --agent skill` from the user project directory,
review its backup/update result at `.agents/skills/fsq/`, then reload discovery
or start a new session. `--no-overwrite` deliberately
preserves an older existing copy.

`fsq init --agent codex` retains the compatibility integration: it installs the
two FSQ Codex TOML definitions byte-for-byte in the current project and uses the
packaged `AGENTS.md` workflow content without path rendering. When `AGENTS.md`
is absent, installation creates it with one FSQ managed block. Any existing
entry at that path is always skipped without reading or modification.

Compatible GitHub Copilot and Codex clients discover the same
`.agents/skills/fsq/` installation; there is no separate Codex Skill
distribution. `--agent codex` is a different, advanced compatibility path for
the older TOML/managed-`AGENTS.md` workflow. Installing one integration does not
create or remove the other's files, and users should not install both for the
first self-check. For either integration, `--no-overwrite` skips each existing
managed destination independently. At least one of `--platform` or `--agent` is
required.

`init` never installs Driver/Runtime packages or system prerequisites. The public CLI has no `environments` command, `providers list`, or `--install-driver` option.

## Provider configuration

```bash
fsq providers configure openai [--model MODEL] [--api-key KEY]
fsq providers configure azure_openai [--base-url URL] [--model DEPLOYMENT] [--api-key KEY]
fsq providers configure deepseek [--model MODEL] [--api-key KEY]
fsq providers configure google_gemini [--model MODEL] [--api-key KEY]
fsq providers configure kimi [--region cn|global] [--model MODEL] [--api-key KEY]
fsq providers configure github_copilot [--model MODEL]
fsq providers status
```

OpenAI uses the official API only and rejects `--base-url`. Human interactive mode hides omitted API-key input and requires explicit selection from authenticated, filtered GPT-5-or-later general models. An explicit `--model` must also be offered to that key. Saving re-fetches model metadata before activation and never sends inference. An empty eligible list cannot be saved.

OpenAI non-interactive/JSON/JSONL modes require both `--model` and `--api-key`. Avoid putting real keys in shared command history; use the hidden interactive prompt for local setup. Missing/inapplicable options exit 2; rejected candidates/models or confirmed local storage failures exit 3; supplier unavailability exits 4; unexpected/unconfirmed failures exit 5. No output includes the key. GitHub retains its Human-only device authorization flow; Azure retains endpoint/deployment/key configuration.

DeepSeek follows the same hidden-key prompt, explicit discovered-model selection, machine-mode requirements, save-time revalidation, and exit categories as OpenAI. It rejects `--base-url` and uses only `https://api.deepseek.com/`. Eligible ids are `deepseek-flash`, exact versioned Flash ids from 4.1 onward, and exact versioned Pro ids from 4.0 onward; matching later versions have no upper bound. Keys are stored only in `~/.fsq/auth/deepseek.json`.

Google Gemini uses the same hidden-key prompt, explicit discovered-model selection, machine-mode option requirements, and exit categories as OpenAI. It rejects `--base-url`. The complete paginated model list is filtered to stable Gemini 3-or-later general-purpose Flash/Pro; even an explicit `--model` must be offered to the candidate key at save time. Vertex AI and Preview/specialized models are not supported. Keys are stored only in `~/.fsq/auth/google-gemini.json`, not Workspace configuration or environment fallbacks.

Kimi requires an explicit region: `cn` uses `https://api.moonshot.cn/v1`, while `global` uses `https://api.moonshot.ai/v1`. Keys are region-specific and are never retried against the other endpoint. Human mode prompts for region with no default, hides the key, loads stable K3-or-later models, and requires explicit selection even when only one model is returned. JSON, JSONL, and `--non-interactive` require `--region`, `--model`, and `--api-key`. Kimi rejects `--base-url`, uses Responses only, and never falls back to Chat Completions, Messages, another model, or another region. The selected region/model are stored in version-3 user configuration and the key in `~/.fsq/auth/kimi.json`.

All Provider commands are user-level and share the one active configuration under `~/.fsq` with Settings. Status does not discover models or send inference. Use Settings **Test connection** for an explicit saved-model request. A disconnected save is not a guaranteed cancellation and is never automatically retried. See the [setup guide](getting-started.md#configure-ai-exploration).

## Static Case formatting

```bash
fsq case format PATH [--check | --diff | --write] [--json]
fsq case create --platform web --goal "Verify product search" --name product-search
```

`format` defaults to `--check`, works without an initialized Workspace, and uses the same validator and serializer as internal Case generation. `--diff` previews changes; `--write` validates before writing. Modes are exclusive. JSON diagnostics distinguish invalid input from formatting differences. See [Case format](case-format.md#canonical-formatting-and-static-checks) for exit codes, machine fields, and the static validation boundary. Case creation accepts an optional stable suffix-free name; publication never replaces a different existing Case.
