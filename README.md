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
2. **Observe** as AI tests the real UI flow and FSQ captures screenshots, interface state, events, and a reviewable test.
3. **Control** what happens next: inspect the evidence and unchanged generated test, then separately approve any deterministic replay.

You get an evidence-backed result and a portable, reusable test you can review, keep, and replay without surrendering control.

## Quick start

You need Python 3.11+ and the [prerequisites for your platform and target](docs/platform-prerequisites.md). Choose one of the two paths below. Enter Provider credentials only in the FSQ UI—not in Coding Agent chat—and review the result and generated Case before separately approving any replay.

### 1. Coding Agent

**Initial setup.** Open your project in a Coding Agent and paste:
> The official FSQ project is https://github.com/FSQ-lab/FSQ. Confirm the current project root with me. Following the official instructions, explain the Python-environment and project-file changes and get my approval, then install or upgrade the FSQ CLI and run `fsq init --agent skill` in this project. Verify that `fsq` works and report where the Skill was installed. Next, ask me to open `fsq ui` and configure the FSQ LLM Provider and model in **Settings**; never ask me to put credentials in chat. Do not initialize a Workspace or run a test. When setup is complete, tell me to reload or start a new Coding Agent session, then stop.

Complete that reload before continuing.

**First test.** Copy this prompt into your Coding Agent:
> Guide me through one FSQ test. Configure or select the Workspace, platform, and exact target with me. Confirm the Goal, observable success criteria, and allowed and forbidden actions. Keep credentials out of chat. Execute only after I approve. Report the result first, then show the exact returned Case unchanged. Do not replay it until I separately and explicitly approve replay.

Review the returned result and unchanged Case before deciding whether to approve replay separately.

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
3. Describe a verifiable result, review the test setup, and start the test. Review the result and Case before separately approving any replay.

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
