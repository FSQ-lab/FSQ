<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img alt="FSQ - Fully Self Quality" src="docs/assets/logo-light.svg" width="320">
  </picture>
</p>

<h3 align="center">描述你要测试什么，让 AI 帮你测试用户流程。</h3>

<p align="center">描述测试目标，AI 替你操作真实 App。FSQ 记录执行证据，并把探索过程转化为可审查、可重放的测试。</p>

<p align="center">来自 <a href="https://github.com/FSQ-lab">FSQ Lab</a> 的开源项目。</p>

<p align="center">
  <a href="https://github.com/FSQ-lab/FSQ/actions/workflows/ci.yml"><img src="https://github.com/FSQ-lab/FSQ/actions/workflows/ci.yml/badge.svg" alt="CI 状态"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 或更高版本"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="MIT 许可证"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha 状态">
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="README.md">English</a> ·
  <a href="docs/getting-started.zh-CN.md">指南</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a>
</p>

## 查看 FSQ 演示

https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939

[在 YouTube 观看完整演示](https://youtu.be/QqCahxGDdS0)。

## 工作原理

1. **描述：**你定义平台和目标、测试目的、成功标准，以及允许和禁止的操作。
2. **观察** AI 测试真实 UI 流程，FSQ 记录截图、界面状态和事件，并可能生成可审查的测试。
3. **掌控**下一步：检查证据；如果 FSQ 返回了测试，再原样检查它，然后单独请求任何确定性重放。

最终会得到带证据的结果；如果 FSQ 返回了测试，你还会得到可移植、可复用的测试，可供审查、保留和重放，控制权始终在你手中。

## 快速开始

从你真正想测试的用户结果开始。你需要 Python 3.11 或更高版本，并满足[平台和目标的前置条件](docs/platform-prerequisites.md)。请从以下两条路径中选择一条。Provider 凭据只能由你在 FSQ UI 中输入，不要发送到 Coding Agent 聊天中。先审查结果；如果 FSQ 返回了 Case，再原样审查它，然后另行决定是否请求重放。

### 1. Coding Agent

**初始设置。** 在 Coding Agent 中打开你的项目，然后粘贴：
> FSQ 的官方项目地址是 https://github.com/FSQ-lab/FSQ。先确定准确打开的项目根目录。把 Python、`fsq` 和 Skill 是否存在的只读检查批量披露，列出准确工作目录、命令、目的和影响。然后提出一次修改批准请求，且只涵盖安装或升级 `fsq-agent`，以及在本项目运行一次 `fsq init --agent skill`；请求前列出准确命令及其对 Python 环境和项目文件的影响。只执行获准操作，验证 `fsq`，并报告准确的 Skill 安装路径。此次一次性设置不会初始化 Workspace、配置 Provider 或运行测试。最后让我重新加载 Coding Agent 或启动新 session，然后停止。

完成重新加载后再继续。

**首次测试。** 将这段提示词复制给你的 Coding Agent：
> 使用 FSQ 测试这个用户目标：启动 Microsoft Edge Stable，使用 Bing 搜索 `FSQ GitHub`，然后检查自然搜索结果，并报告 FSQ GitHub 仓库当前所在的位置/排名。选择 Web 平台和 Microsoft Edge Stable。遵循已安装的 FSQ Skill，只询问它无法确定的决定。

平台和准确目标是事实或由用户决定的事项：当 Skill 询问时请明确提供，不要让 Agent 猜测目标 ID 或路径，也不要替换为其他 Edge 渠道。

先审查结果。如果 FSQ 返回了 Case，再原样审查它，然后另行决定是否请求重放。

### 2. FSQ UI

**初始设置。**

```bash
python -m pip install --upgrade fsq-agent
fsq ui
```

在 **Settings** 中配置 FSQ LLM Provider，并显式选择模型。

**首次测试。**
1. 创建或选择 Workspace，并按需配置其平台和目标。
2. 在 **Test Runner** 中选择准确目标。
3. 描述可验证的结果，审查测试设置，然后开始测试。先审查结果；如果 FSQ 返回了 Case，再原样审查它，然后另行决定是否请求重放。

![用于描述测试目标的 FSQ Test Runner](docs/media/01-describe-goal.png)

[详细首次测试指南](docs/getting-started.zh-CN.md)

## 平台

FSQ 支持 Web、Android、Windows 和 macOS 目标；请查看[平台前置条件](docs/platform-prerequisites.md)，也欢迎通过[贡献](CONTRIBUTING.md)帮助扩展支持。

> [!IMPORTANT]
> FSQ 是用于评估和贡献的 Alpha 软件。证据保存在本地，可能包含界面中可见的数据或其他私有目标数据；分享前请审查，不要提交证据或凭据。公开接口在 1.0 前仍可能调整，请阅读[支持与稳定性说明](docs/support-and-stability.md)。

## 文档

- [中文快速开始](docs/getting-started.zh-CN.md)
- [Getting started](docs/getting-started.md)
- [CLI reference](docs/cli-reference.md)
- [FSQ Case format](docs/case-format.md)
- [Codex 高级兼容性](docs/codex-integration.md)
- [平台前置条件](docs/platform-prerequisites.md)
- [支持与稳定性](docs/support-and-stability.md)

## 由 FSQ Lab 与社区共同打造

由 [FSQ Lab](https://github.com/FSQ-lab) 与社区共同打造，感谢[所有贡献者](https://github.com/FSQ-lab/FSQ/graphs/contributors)。想加入我们？请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 并参与贡献。

## 许可证

[MIT](LICENSE) — Copyright (c) Microsoft Corporation.
