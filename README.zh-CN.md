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
  <a href="#安装-fsq">安装</a> ·
  <a href="#尝试一个测试">尝试一个测试</a> ·
  <a href="README.md">English</a> ·
  <a href="docs/getting-started.zh-CN.md">指南</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a>
</p>

## 查看 FSQ 演示

https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939

[在 YouTube 观看完整演示](https://youtu.be/QqCahxGDdS0)。

## 工作原理

1. **描述：**你定义平台和目标、测试目的、成功标准，以及允许和禁止的操作。
2. **观察** AI 测试真实 UI 流程，FSQ 记录截图、界面状态、事件和可审查的测试。
3. **掌控**下一步：检查证据和未经改动的生成测试，再单独批准任何确定性重放。

最终得到带证据的结果和可移植、可复用的测试；你可以审查、保留和重放，控制权始终在你手中。

## 安装 FSQ

将这段提示词复制给你的 Coding Agent：

> FSQ 的官方项目地址是 https://github.com/FSQ-lab/FSQ。按照该项目的官方安装说明安装 FSQ CLI。修改我的环境前，先说明变更并征得我的同意。验证 `fsq` 命令可以工作，报告结果，然后停止。不要初始化 Workspace、配置 Provider 或运行测试。

```bash
python -m pip install --upgrade fsq-agent
```

[安装指南](docs/getting-started.zh-CN.md)

## 尝试一个测试

在项目目录运行 `fsq init --agent skill`。重新加载或启动新的 Coding Agent session 后再继续。

将这段提示词复制给你的 Coding Agent：

> 引导我完成一个 FSQ 测试。确认平台和目标、测试目的和成功标准，以及允许和禁止的操作。不要在聊天中提供凭据。只执行我批准的内容；先总结结果，再原样展示返回的测试。仅在我另行明确确认后重放。

### 直接使用 FSQ UI

```bash
fsq ui
```

在 Test Runner 中选择平台和目标，描述要验证的结果，然后开始测试。

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
