# 快速开始

这份指南先完成包内置、无需 Provider 的 Web Strict 自检，再进入 Case
创建、审查、重放和 suggestion。FSQ 是 Alpha 软件；生产采用前请先阅读
[支持与稳定性说明](support-and-stability.md)。

## 前置条件

- Python 3.11 或更新版本。
- 已安装受支持的 Chromium 系浏览器。示例使用稳定版 Chrome。
- 一个可打开为本地 FSQ 项目和 Workspace 的目录。

AI 探索和 suggestion 分析还需要 OpenAI、DeepSeek、Google Gemini、Kimi、GitHub Copilot 或 Azure OpenAI。确定性 Case 重放不需要规划 LLM，除非已编写的 Case 包含 AI assertion。

## 推荐的首次路径

```bash
python -m pip install --upgrade fsq-agent
mkdir fsq-web-demo
cd fsq-web-demo
fsq init --platform web --browser-channel chrome --agent skill
```

请使用 fsq-agent 0.1.6。空的 `fsq-web-demo` 目录会成为准确注册的
Workspace root，并在 `.agents/skills/fsq/` 安装主机中立 Skill。在非空
目录中执行 `init` 时会改为创建子 Workspace；请使用 `init` 报告的准确
root。Workspace 命令不会向父目录搜索。

让兼容的 GitHub Copilot 或 Codex 客户端重新加载项目资源，或启动新的
agent session。然后粘贴下面这段**重载后的自检提示词**：

> 使用已安装的 FSQ Skill，从准确注册的 Workspace root
> `<ABSOLUTE_WORKSPACE_ROOT>` 运行它内置的、无需 Provider 的 Web Strict
> 自检，最多执行一次。返回权威状态、准确的持久化 Run ID 和相对
> Control Plane 路由。不要初始化、配置、编辑 Case、重试或启动 Control Plane。

Skill 会运行只读 Doctor 和静态 Case 检查，只执行一次，并只读取准确返回的
Run。请自行启动 Control Plane，再打开返回的
`/#runs?workspace=...&platform=web&run=...` 路由：

```bash
fsq ui
```

没有经过验证的持久化 Run ID 时，结果为 `unknown`：不得重试，也不得生成
路由。Web readiness 未就绪会在执行前阻止流程；内置 Case 无效或非 canonical
也会阻止执行，且不得改写。

## 可复制的安装提示词

下面的安装提示词用于现有 Coding Agent session。它不同于上一节只能在重载
之后使用的自检提示词。

> 检查 Python 3.11 或更高版本是否可用。运行 `fsq init --help`，确认支持
> `--agent skill`。如果没有 `fsq`、版本低于 fsq-agent 0.1.5，或缺少该能力，
> 必须先征得我的同意，才能运行
> `python -m pip install --upgrade fsq-agent`；未经同意不要安装。在准确的项目
> 目录中运行 `fsq init --platform web --browser-channel chrome --agent skill`。
> 不要手工创建或修改任何 Skill 或 Case 文件，不要配置或修改 Provider 或
> secret，也不要运行 Case。报告准确的 Workspace root 和 Skill 路径，然后提醒
> 我重新加载资源或启动新的 agent session，再使用单独的重载后自检提示词。

安装 CLI 可以初始化 Workspace；安装后的 Skill 不可以。Skill 也不能配置
Provider/secret、编辑 Case、重试、操作 UI、运行 Explore/suggestion、启动
`fsq ui` 或调用 Control Plane。GitHub Copilot 与兼容 Codex 客户端共享
`.agents/skills/fsq/`，没有单独 Skill 包。Codex TOML/托管 `AGENTS.md` 集成是
单独的[高级兼容路径](codex-integration.md)，首次检查无需同时安装。

## 高级/历史 TodoMVC 示例

现有 [`examples/web/example-domain.fsq.yaml`](../examples/web/example-domain.fsq.yaml)
虽然保留历史文件名，实际是已记录的 TodoMVC 示例；它不再是推荐的首次运行。
完成上述设置后可这样运行：

```bash
mkdir -p cases/web
curl --fail --location --output cases/web/example-domain.fsq.yaml \
  https://raw.githubusercontent.com/FSQ-lab/FSQ/main/examples/web/example-domain.fsq.yaml
fsq case test --platform web cases/web/example-domain.fsq.yaml
fsq runs list --platform web
```

这个 Case 会启动已配置的浏览器，打开 TodoMVC，添加两个任务、完成第一个任务、筛选未完成任务、验证预期可见状态，然后关闭浏览器。证据保存在 `.fsq/runs/web/<run-id>/` 下。

## 配置 AI 探索

```bash
fsq providers configure github_copilot
fsq providers status
```

也可以运行 `fsq providers configure openai` 连接 OpenAI 官方 API，运行 `fsq providers configure deepseek` 连接 DeepSeek 官方 Responses API，运行 `fsq providers configure kimi` 连接中国区或国际区 Kimi 官方 API，或运行 `fsq providers configure azure_openai` 连接 Azure 部署。配置保存在用户级 `~/.fsq` 下，由 CLI 和 Control Plane 共享。API Key Provider 会隐藏 Key 输入并提供可选模型；即使只有一个模型也需要显式选择。

浏览器配置流程：运行 `fsq ui`，打开 **Settings**。

1. 点击 **Add configuration** 或 **Change provider**，选择 **OpenAI**、**DeepSeek**、**Google Gemini** 或 **Kimi**。
2. 使用 Kimi 时先显式选择 **China** 或 **Global**。输入 API Key，点击 **Load models**，选择模型，再点击 **Save changes**。
3. 点击 **Test connection**，使用已保存配置发送最小请求。
4. 回到 **Home** 查看 Provider 摘要，再到 **Test Runner** 选择 Workspace、平台和目标，显式启动 Explore。

OpenAI 固定使用 `https://api.openai.com/v1/` 和 Responses API，不支持自定义端点。模型列表仅展示 GPT 主版本 5 或更高的通用模型，排除 mini、nano、Codex、embedding、audio、realtime、image、search、transcription、TTS 等变体。空列表不能保存；修改 Key 后必须重新加载并选择模型。Azure 则需要资源端点、**deployment name（部署名，不一定是 OpenAI model id）** 和 API Key。

DeepSeek 固定使用 `https://api.deepseek.com/` 和 Responses API。模型列表接受 `deepseek-flash`、4.1 及以上的精确版本化 Flash ID，以及 4.0 及以上的精确版本化 Pro ID，不设置最高版本；其他模型族和额外后缀会被排除。发现过程不探测能力或图片支持；输入不兼容时正常失败，不自动重试、替换模型或回退 Provider。

Gemini 的 API Key 从 [Google AI Studio](https://aistudio.google.com/apikey) 获取。可使用上述页面流程，或运行 `fsq providers configure google_gemini`，在隐藏提示中输入 Key 后显式选模型。列表读完所有有界分页，只提供支持内容生成的稳定 Gemini 3 或更高版本通用 Flash/Pro；排除 Preview、Latest、Experimental、Lite、图像/音频/Embedding 等专用变体。分页不完整会报错，不会提供部分候选列表。固定 Developer API 使用原生 Interactions、`store=false` 和本地会话历史，不支持 Vertex AI、ADC/服务账号或自定义端点。

Gemini 元数据保存在 version 3 用户配置中，明文 Key 位于 `~/.fsq/auth/google-gemini.json`。预规划、主执行、最终验证、AI 视觉断言、Case 建议和连接测试均使用同一已保存 Provider。**Test connection** 只验证最小推理请求，不保证所有工具/Schema 或 Case 都可执行。显式启动 Explore 后按正常流程检查证据并保存 Case；Strict Replay 只在 Case 需要 AI 断言时使用 Gemini。

Kimi API Key 可在[中国区平台](https://platform.kimi.com/console/api-keys)或[国际区平台](https://platform.kimi.ai/console/api-keys)创建。中国区 Key 只发送到 `https://api.moonshot.cn/v1`，国际区 Key 只发送到 `https://api.moonshot.ai/v1`，两区不会自动重试。模型列表接受稳定的 `kimi-k3` 或更高版本，并排除 preview、beta、alpha、experimental、release candidate、latest 和日期快照；它不维护固定正向白名单。未来模型即使通过筛选也只提供尽力兼容，不代表能力保证。FSQ 只使用 Responses API，并把中立推理强度映射为 Kimi 的 `low`、`high`、`max`，不会回退到 Chat Completions、Messages、其他模型或其他区域。

图片能力不作为 Kimi 模型筛选条件。若所选模型拒绝图片、Schema、工具或 Responses 请求，FSQ 会返回普通安全模型/AI assertion 错误。动态执行可在后续轮次自行选择其他可用工具；已编写的静态 AI assertion 仍是带证据的失败步骤。Kimi 元数据保存在 version 3 用户配置中，明文 Key 位于 `~/.fsq/auth/kimi.json`。

保存只复核模型可见性，不发送推理请求；status/readiness 也不等于连接测试。API Key 以明文保存在本地，Settings 默认掩码显示，但受 loopback 限制的 Config API 会完整返回。只有一个活动 Provider，替换成功后清理其他 Provider 凭据；不从环境变量或其他 Provider 回退。

保存响应丢失时，页面会提示结果未知并重新读取配置。可以用 **Reload configuration** 重试恢复。关闭页面不等于取消服务端保存，读到当前配置也不能证明旧请求已经完成。FSQ 不会自动重提或回滚；恢复后应核对当前 Provider 和剩余草稿再继续。

## 探索与检查

```bash
fsq case create --platform web --goal "Open https://example.com and verify the Example Domain heading is visible."
fsq case format cases/web/YOUR_CASE.fsq.yaml --check
fsq case test --platform web cases/web/YOUR_CASE.fsq.yaml
fsq runs list
fsq runs show RUN_ID
fsq runs logs RUN_ID
fsq runs show RUN_ID --open
```

最后一个命令基于已保存的 Run 事实创建离线报告。它不会操作目标 UI，也不会调用 Provider。

## 分析确定性 Run

```bash
fsq case test --platform web --suggest cases/web/example-domain.fsq.yaml
```

Case 只执行一次。随后 AI 分析只消费源 Case、报告和已保存证据。建议和任何候选 Case 都只保留在对应 Run 内。

## 打开 Control Plane

```bash
fsq ui
```

安装后的前端默认在本地 `127.0.0.1:8879` 提供服务。

## 下一步

- 阅读[平台前置条件](platform-prerequisites.md)。
- 学习 [Case 格式](case-format.md)。
- 查看 [CLI reference](cli-reference.md)。
- 实现级架构与行为契约请查阅根目录及各模块的 `SPEC.md` 文件。
