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
  <a href="#install-fsq">Install</a> ·
  <a href="#try-one-test">Try one test</a> ·
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

## Install FSQ

Copy this prompt into your Coding Agent:

> The official FSQ project is https://github.com/FSQ-lab/FSQ. Follow that project's official installation instructions to install the FSQ CLI. Before changing my environment, explain the change and obtain my permission. Verify that the `fsq` command works, report the result, and then stop. Do not initialize a Workspace, configure a Provider, or run a test.

```bash
python -m pip install --upgrade fsq-agent
```

[Installation guide](docs/getting-started.md)

## Try one test

From the project directory, run `fsq init --agent skill`. Reload or start a new Coding Agent session before continuing.

Copy this prompt into your Coding Agent:

> Guide me through one FSQ test. Confirm the platform and target, goal and success criteria, and allowed and forbidden actions. Keep credentials out of chat. Execute only what I approve, summarize the result first, and show the returned test unchanged. Replay only after my separate explicit confirmation.

### Use the FSQ UI directly

```bash
fsq ui
```

In Test Runner, choose the platform and target, describe the result to verify, and start testing.

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
