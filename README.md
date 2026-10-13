<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img alt="FSQ — Fully Self Quality" src="docs/assets/logo-light.svg" width="320">
  </picture>
</p>

<h3 align="center">Evidence-first AI UI automation you can inspect, replay, and verify.</h3>

<p align="center">
  <a href="https://github.com/FSQ-lab/FSQ/actions/workflows/ci.yml"><img src="https://github.com/FSQ-lab/FSQ/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 or newer"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha status">
</p>

<p align="center">
  <a href="#recommended-first-run">Quickstart</a> ·
  <a href="README.zh-CN.md">中文</a> ·
  <a href="docs/getting-started.md">Detailed guide</a> ·
  <a href="docs/cli-reference.md">CLI</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

> [!IMPORTANT]
> FSQ is alpha software for evaluation and contribution. Public APIs and Case authoring details may evolve before 1.0. See [support and stability](docs/support-and-stability.md).

FSQ turns UI goals into observable Runs with screenshots, UI snapshots, events, and reports. Reviewed YAML Cases replay deterministically through Playwright, uiautomator2, pywinauto, or Appium while the local Control Plane keeps execution and evidence inspectable.

## Recommended first run

Prerequisites are **Python 3.11+** and an installed supported Chromium-family browser (the commands below use Chrome). Install or upgrade **fsq-agent 0.1.5**, create and open an empty directory, and initialize Web plus the package-owned, host-neutral Skill:

```bash
python -m pip install --upgrade fsq-agent
mkdir fsq-web-demo
cd fsq-web-demo
fsq init --platform web --browser-channel chrome --agent skill
```

This empty directory is the exact registered Workspace root. If you initialize from a non-empty directory instead, FSQ creates a child Workspace; use the root reported by `init`. Workspace commands never search parent directories.

Reload the compatible Coding Agent's project resources or start a new agent session, then paste this **post-reload self-check prompt**:

> Use the installed FSQ Skill to run its bundled provider-free Web Strict self-check from the exact registered Workspace root `<ABSOLUTE_WORKSPACE_ROOT>`. Run it at most once. Return the authoritative status, exact persistent Run ID, and relative Control Plane route. Do not initialize, configure, edit a Case, retry, or start the Control Plane.

The agent should run Doctor, statically check the bundled Case, execute it once, read that exact Run, and return a route shaped like `/#runs?workspace=...&platform=web&run=...`. Start the UI yourself and open that relative route:

```bash
fsq ui
```

The Skill never starts the Control Plane. If no verified Run ID is returned, the result is **unknown**: do not retry and do not construct a route.

## Copy-paste installer prompt for a Coding Agent

Use this only to **install** the integration. It is not the self-check prompt above.

> Check that Python 3.11 or newer is available. Run `fsq init --help` and verify that `--agent skill` is supported. If `fsq` is missing, older than fsq-agent 0.1.5, or lacks that capability, ask my permission before running `python -m pip install --upgrade fsq-agent`; do not install anything without approval. From the exact project directory, run `fsq init --platform web --browser-channel chrome --agent skill`. Do not hand-create or modify any Skill or Case file, do not configure or change a Provider or secret, and do not run a Case. Report the exact Workspace root and installed Skill path, then remind me to reload project resources or start a new agent session before using the separate post-reload self-check prompt.

The official CLI may initialize a Workspace and install the Skill. The **installed Skill itself may not** initialize or configure anything.

## Skill scope and failure transitions

One `fsq-agent` distribution owns `.agents/skills/fsq/SKILL.md` and its bundled provider-free Web Strict Case. Compatible GitHub Copilot and Codex clients discover that same `.agents/skills/fsq/` location; there is no separate host-specific Skill distribution.

The Skill is intentionally limited to:

- read-only `fsq doctor`;
- read-only `fsq case format ... --check --json`;
- one `fsq case test` execution;
- `fsq runs show` for the exact returned Run ID (and optionally bounded exact-Run logs);
- an exact status/Run/relative-Control-Plane handoff.

It does **not** initialize/configure FSQ, handle Providers or secrets, edit/copy Cases, retry, operate browser UI, run Explore/suggestions/AI assertions, or start/call the Control Plane.

| Transition | Required outcome |
|---|---|
| Installed CLI lacks `--agent skill` | Stop; ask before upgrading. |
| Installed Skill is not discovered | Reload project resources or start a new session; do not recreate files. |
| Doctor reports Web not ready | Report the browser/readiness blocker; do not execute. |
| Bundled Case is invalid or noncanonical | Report the blocker; do not rewrite or execute it. |
| Execution has no verified persistent Run ID | Report `unknown`; no retry and no route. |

The older Codex TOML/managed-`AGENTS.md` integration remains an [advanced compatibility path](docs/codex-integration.md) through `fsq init --agent codex`. Do not install both integrations for the first self-check.

## Continue after the self-check

Strict replay is provider-free unless an authored Case contains an AI assertion. AI Explore (`fsq case create`), AI assertions, and post-run suggestions may require a configured Provider.

```bash
# AI exploration: execute a goal and produce a reviewable Run-local Case.
fsq case create --platform web \
  --goal "Open https://example.com and verify the Example Domain heading is visible."

# Review and format-check the returned Case before deterministic replay.
fsq case format cases/web/YOUR_CASE.fsq.yaml --check
fsq case test --platform web cases/web/YOUR_CASE.fsq.yaml

# Optional AI analysis executes the source Case once, then analyzes saved facts.
fsq case test --platform web --suggest cases/web/YOUR_CASE.fsq.yaml
fsq runs list --platform web
fsq runs show RUN_ID --open
```

The existing [`examples/web/example-domain.fsq.yaml`](examples/web/example-domain.fsq.yaml) is the recorded TodoMVC example despite its historical filename. It remains available for advanced/historical use, but is no longer the recommended first run.

Supported Providers are OpenAI, Azure OpenAI, DeepSeek, Google Gemini, Kimi (China or Global), and GitHub Copilot. Configuration is user-level under `~/.fsq`; see [Getting started](docs/getting-started.md#configure-ai-exploration) for model, region, credential, and fallback details.

```bash
fsq providers configure github_copilot
fsq providers status
```

## Platforms and local evidence

| Platform | Backend | Host prerequisite |
|---|---|---|
| Web | Playwright | Installed supported Chromium-family browser |
| Android | uiautomator2 | ADB and an online authorized device |
| Windows | pywinauto | Windows and an existing application |
| macOS | Appium Mac2 | macOS, an existing application, and reachable Appium |

Run evidence lives under `<workspace>/.fsq/runs/<platform>/<run-id>/`. Review it before sharing; it can contain visible application data. Do not commit `.fsq`, credentials, reports, screenshots, or private target data.

## See FSQ in action

https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939

[Watch the full demo on YouTube](https://youtu.be/QqCahxGDdS0).

## Documentation

- [Getting started](docs/getting-started.md)
- [中文快速开始](docs/getting-started.zh-CN.md)
- [CLI reference](docs/cli-reference.md)
- [FSQ Case format](docs/case-format.md)
- [Platform prerequisites](docs/platform-prerequisites.md)
- [Support and stability](docs/support-and-stability.md)
- [Codex advanced compatibility](docs/codex-integration.md)

## License

[MIT](LICENSE) — Copyright (c) Microsoft Corporation.
