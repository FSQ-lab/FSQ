<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img alt="FSQ — Fully Self Quality" src="docs/assets/logo-light.svg" width="320">
  </picture>
</p>

<h3 align="center">Describe what to test. Let AI help to test the user flow.</h3>

<p align="center">Describe the goal and let AI operate the real app. FSQ records evidence and turns the exploration into a test you can review and replay.</p>

<p align="center">An open-source project from <a href="https://github.com/FSQ-lab">FSQ Lab</a>.</p>

<p align="center">
  <a href="https://github.com/FSQ-lab/FSQ/actions/workflows/ci.yml"><img src="https://github.com/FSQ-lab/FSQ/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 or newer"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha status">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="README.zh-CN.md">中文</a> ·
  <a href="docs/getting-started.md">Guide</a> ·
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

## See FSQ in action

https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939

[Watch the full demo on YouTube](https://youtu.be/QqCahxGDdS0).

## How it works

1. **Describe:** You define the platform and target, goal, success criteria, and allowed and forbidden actions.
2. **Observe** as AI tests the real UI flow and FSQ captures screenshots, interface state, events, and may produce a reviewable test.
3. **Control** what happens next: inspect the evidence and, if FSQ returns a test, inspect it unchanged before separately requesting any deterministic replay.

You get an evidence-backed result and, when FSQ returns one, a portable, reusable test you can review, keep, and replay without surrendering control.

## Quick start

Start with the real outcome you want to test. You need Python 3.11+ and the [prerequisites for your platform and target](docs/platform-prerequisites.md). Choose one of the two paths below. Enter Provider credentials only in the FSQ UI—not in Coding Agent chat. Review the result first and, if FSQ returns a Case, review it unchanged before separately requesting any replay.

### 1. Coding Agent

**Initial setup.** Open your project in a Coding Agent and paste:
> The official FSQ project is https://github.com/FSQ-lab/FSQ. Establish the exact opened project root. Batch the read-only Python, `fsq`, and Skill-presence checks with their exact working directories, commands, purpose, and effects. Then request one approval for the listed changes, covering only installation or upgrade of `fsq-agent` and one `fsq init --agent skill` in this project; disclose the exact commands and Python-environment and project-file effects before asking. Perform only the approved operations, verify `fsq`, and report the exact installed Skill path. This one-time setup does not initialize a Workspace, configure a Provider, or run a test. Tell me to reload or start a new Coding Agent session, then stop.

Complete that reload before continuing.

**First test.** Copy this prompt into your Coding Agent:
> Test this user goal with FSQ: **[describe the outcome]**. Follow the installed FSQ Skill and ask only for decisions it cannot establish.

For example: **Android:** “A signed-out user sees a clear error for invalid sign-in.” **Web:** “A shopper can filter results and open one product.” **Windows:** “A user can create and save a draft.” **macOS:** “A user can change a preference and see it persist.” These are examples, not defaults; provide the exact target when the Skill asks rather than guessing an ID or path.

Review the result first. If FSQ returns a Case, review it unchanged before deciding whether to request replay separately.

### 2. FSQ UI

**Initial setup.**

```bash
python -m pip install --upgrade fsq-agent
fsq ui
```

In **Settings**, configure the FSQ LLM Provider and explicitly select a model.

**First test.**
1. Create or select a Workspace, and configure its platform and target as needed.
2. In **Test Runner**, select the exact target.
3. Describe a verifiable result, review the test setup, and start the test. Review the result first and, if FSQ returns a Case, review it unchanged before separately requesting any replay.

![FSQ Test Runner for describing a test goal](docs/media/01-describe-goal.png)

[Detailed first-test guide](docs/getting-started.md)

## Platforms

FSQ supports Web, Android, Windows, and macOS targets; see [platform prerequisites](docs/platform-prerequisites.md), and help broaden support through [contributions](CONTRIBUTING.md).

> [!IMPORTANT]
> FSQ is alpha software for evaluation and contribution. Evidence is stored locally and can contain visible application data or other private target data. Review it before sharing, and do not commit evidence or credentials. Public interfaces may evolve before 1.0; see [support and stability](docs/support-and-stability.md).

## Documentation

- [Getting started](docs/getting-started.md)
- [中文快速开始](docs/getting-started.zh-CN.md)
- [CLI reference](docs/cli-reference.md)
- [FSQ Case format](docs/case-format.md)
- [Advanced Codex compatibility](docs/codex-integration.md)
- [Platform prerequisites](docs/platform-prerequisites.md)
- [Support and stability](docs/support-and-stability.md)

## Built by FSQ Lab and the community

Built by [FSQ Lab](https://github.com/FSQ-lab) with thanks to [all contributors](https://github.com/FSQ-lab/FSQ/graphs/contributors). Want to help? Read [CONTRIBUTING.md](CONTRIBUTING.md) and join us.

## License

[MIT](LICENSE) — Copyright (c) Microsoft Corporation.
