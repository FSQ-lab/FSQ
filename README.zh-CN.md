<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img alt="FSQ - Fully Self Quality" src="docs/assets/logo-light.svg" width="320">
  </picture>
</p>

<h3 align="center">证据优先、可检查、可重放、可验证的 AI UI 自动化。</h3>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#推荐的首次运行">快速开始</a> ·
  <a href="docs/getting-started.zh-CN.md">详细指南</a> ·
  <a href="docs/cli-reference.md">CLI</a>
</p>

> [!IMPORTANT]
> FSQ 是用于评估和贡献的 Alpha 软件。公开 API 与 Case 编写细节在 1.0 前仍可能调整。请阅读[支持与稳定性说明](docs/support-and-stability.md)。

FSQ 把 UI 目标转换为可观察的 Run，并保存截图、UI Snapshot、事件和报告。经过审查的 YAML Case 可通过 Playwright、uiautomator2、pywinauto 或 Appium 确定性重放，本地 Control Plane 用于检查执行与证据。

## 推荐的首次运行

前置条件是 **Python 3.11+** 和已安装的受支持 Chromium 系浏览器（下列命令使用 Chrome）。安装或升级到 **fsq-agent 0.1.6**，创建并打开一个空目录，再用官方 CLI 一次初始化 Web 和包内置的主机中立 Skill：

```bash
python -m pip install --upgrade fsq-agent
mkdir fsq-web-demo
cd fsq-web-demo
fsq init --platform web --browser-channel chrome --agent skill
```

Workspace Root Strategy（Workspace root 选择策略）是精确规则：这个空目录就是准确注册的 Workspace root。如果从非空目录初始化，FSQ 会创建子 Workspace；后续请使用 `init` 报告的准确 root。Workspace 命令不会向父目录搜索。

让兼容的 Coding Agent 重新加载项目资源，或启动新的 agent session，然后粘贴下面这段**重载后的自检提示词**：

> 使用已安装的 FSQ Skill，从准确注册的 Workspace root `<ABSOLUTE_WORKSPACE_ROOT>` 运行它内置的、无需 Provider 的 Web Strict 自检，最多执行一次。返回权威状态、准确的持久化 Run ID 和相对 Control Plane 路由。不要初始化、配置、编辑 Case、重试或启动 Control Plane。

Agent 应依次运行 Doctor、静态检查内置 Case、执行一次、按准确 ID 读取该 Run，并返回类似 `/#runs?workspace=...&platform=web&run=...` 的路由。请自行启动 UI，再手动打开该相对路由：

```bash
fsq ui
```

Skill 不会启动 Control Plane。如果没有经过验证的 Run ID，结果就是**未知**：不得重试，也不得构造路由。

## 可复制给 Coding Agent 的安装提示词

下面只用于**安装**集成，不是上一节重载后使用的自检提示词。

> 检查 Python 3.11 或更高版本是否可用。运行 `fsq init --help`，确认支持 `--agent skill`。如果没有 `fsq`、版本低于 fsq-agent 0.1.5，或缺少该能力，必须先征得我的同意，才能运行 `python -m pip install --upgrade fsq-agent`；未经同意不要安装任何内容。在准确的项目目录中运行 `fsq init --platform web --browser-channel chrome --agent skill`。不要手工创建或修改任何 Skill 或 Case 文件，不要配置或修改 Provider 或 secret，也不要运行 Case。报告准确的 Workspace root 和已安装 Skill 路径，然后提醒我重新加载项目资源或启动新的 agent session，再使用单独的重载后自检提示词。

官方安装 CLI 可以初始化 Workspace 并安装 Skill；**安装后的 Skill 自身不能**执行初始化或配置。

## Skill 用途、边界与失败转换

单一 `fsq-agent` 分发包拥有 `.agents/skills/fsq/SKILL.md` 和内置的、无需 Provider 的 Web Strict Case。兼容的 GitHub Copilot 与 Codex 客户端共同发现 `.agents/skills/fsq/`；不存在单独的主机专用 Skill 分发。

Skill 仅执行：

- 只读 `fsq doctor`；
- 只读 `fsq case format ... --check --json`；
- 一次 `fsq case test`；
- 使用准确返回的 Run ID 执行 `fsq runs show`（以及可选的有界准确 Run 日志读取）；
- 交接准确状态、Run ID 和相对 Control Plane 路由。

它不会初始化或配置 FSQ，不处理 Provider 或 secret，不编辑/复制 Case，不重试，不操作浏览器 UI，不运行 Explore、suggestion 或 AI assertion，也不启动或调用 Control Plane。

| 状态转换 | 必须采取的结果 |
|---|---|
| 已安装 CLI 不支持 `--agent skill` | 停止；升级前先询问。 |
| 已安装 Skill 未被发现 | 重新加载项目资源或启动新 session；不要重建文件。 |
| Doctor 报告 Web 未就绪 | 报告浏览器/readiness blocker；不要执行。 |
| 内置 Case 无效或非 canonical | 报告 blocker；不要改写或执行。 |
| 执行没有经过验证的持久化 Run ID | 报告 `unknown`；不重试、不生成路由。 |

原有 Codex TOML/托管 `AGENTS.md` 集成仍可作为[高级兼容路径](docs/codex-integration.md)，通过 `fsq init --agent codex` 安装。首次自检不需要同时安装两套集成。

## 自检后的 Explore、审查与 Strict 工作流

Strict 重放默认不需要 Provider，除非编写的 Case 包含 AI assertion。AI Explore（`fsq case create`）、AI assertion 和运行后 suggestion 可能需要已配置的 Provider。

```bash
# AI Explore：执行目标并生成可审查的 Run-local Case。
fsq case create --platform web \
  --goal "Open https://example.com and verify the Example Domain heading is visible."

# 审查返回的 Case，检查格式，再进行确定性 Strict 重放。
fsq case format cases/web/YOUR_CASE.fsq.yaml --check
fsq case test --platform web cases/web/YOUR_CASE.fsq.yaml

# 可选 AI 分析：源 Case 执行一次，之后只分析已保存事实。
fsq case test --platform web --suggest cases/web/YOUR_CASE.fsq.yaml
fsq runs list --platform web
fsq runs show RUN_ID --open
```

现有 [`examples/web/example-domain.fsq.yaml`](examples/web/example-domain.fsq.yaml) 虽沿用历史文件名，实际是已记录的 TodoMVC 示例。它仍可用于高级/历史用途，但不再是推荐的首次运行。

支持的 Provider 是 OpenAI、Azure OpenAI、DeepSeek、Google Gemini、Kimi（中国区或国际区）和 GitHub Copilot。配置以用户级方式保存在 `~/.fsq`；模型、区域、凭据和回退边界详见[中文快速开始](docs/getting-started.zh-CN.md#配置-ai-探索)。

```bash
fsq providers configure github_copilot
fsq providers status
```

## 平台与本地证据

| 平台 | 后端 | 主机前置条件 |
|---|---|---|
| Web | Playwright | 已安装受支持的 Chromium 系浏览器 |
| Android | uiautomator2 | ADB 和在线授权设备 |
| Windows | pywinauto | Windows 和已有应用 |
| macOS | Appium Mac2 | macOS、已有应用和可访问的 Appium |

Run 证据位于 `<workspace>/.fsq/runs/<platform>/<run-id>/`。证据可能包含可见应用数据；分享前请审查。不要提交 `.fsq`、凭据、报告、截图或私有目标数据。

## 查看演示

https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939

[在 YouTube 观看完整演示](https://youtu.be/QqCahxGDdS0)。

## 文档

- [中文快速开始](docs/getting-started.zh-CN.md)
- [Getting started](docs/getting-started.md)
- [CLI reference](docs/cli-reference.md)
- [FSQ Case format](docs/case-format.md)
- [平台前置条件](docs/platform-prerequisites.md)
- [支持与稳定性](docs/support-and-stability.md)
- [Codex 高级兼容路径](docs/codex-integration.md)

## 许可证

[MIT](LICENSE) — Copyright (c) Microsoft Corporation.
