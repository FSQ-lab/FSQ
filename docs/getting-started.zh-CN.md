# 快速开始

这份指南使用包内置 Skill，引导你完成平台中立的 AI Explore，并可按需审查
或由用户发起确定性重放。FSQ 是 Alpha 软件；生产采用前请先阅读
[支持与稳定性说明](support-and-stability.md)。

## 前置条件

- Python 3.11 或更新版本。
- 满足要测试的平台和目标的前置条件。
- 一个可作为准确本地 Coding Agent 项目根目录打开的目录。

AI 探索和 suggestion 分析还需要 OpenAI、DeepSeek、Google Gemini、Kimi、GitHub Copilot 或 Azure OpenAI。确定性 Case 重放不需要规划 LLM，除非已编写的 Case 包含 AI assertion。

## 安装 CLI 和项目本地 Skill

```bash
python -m pip install --upgrade fsq-agent
cd /path/to/your/project
fsq init --agent skill
```

请从这个准确的项目根目录安装 Skill。该命令独立于 Workspace 初始化，把
Skill 安装到 `.agents/skills/fsq/`。重新加载或启动新的 Coding Agent session，
让客户端发现已安装的 Skill。升级包后，应再次运行 `fsq init --agent skill`
并再次重载，以更新已复制的 Skill。

然后粘贴：

> 引导我完成一个 FSQ 测试。先从准确的当前目录发现 readiness，再向我询问
> FSQ 无法提供的事实。然后逐步确认 Goal 和可观察的成功标准、允许和禁止的
> 操作以及相关限制。不要在聊天中提供凭据。只在我确认完整 Goal 后创建一次；
> 先总结结果，并等待我主动请求 Case 审查或重放。

## 引导式 AI Explore

安装后的 Skill 负责对话引导，并使用公开 FSQ CLI。它会：

1. 把准确的当前工作目录作为唯一初始 Workspace 候选，并先运行 Doctor，再
   询问 Doctor 已能提供的 Workspace、平台和准确的当前目标信息。
2. 如果该目录不是 Workspace，则要求准确注册的 Workspace 状态与 root，或
   准确的初始化目录；不会搜索、扫描、尝试猜测 root，也不会根据历史或
   “latest” 推断。
3. 如果需要初始化 Workspace，它会解释变更、征得权限，使用匹配的
   `fsq init --platform ...` 形式执行一次，并采用实际返回的 root。
4. 逐步确认 Goal 和可观察的成功标准、允许与禁止的操作、相关限制和当下所需
   权限。初始化、一次创建和重放分别确认；Explore 阶段不请求未来重放权限。

相关只读检查会一次紧凑披露准确工作目录、命令、目的和只读影响；普通只读
命令不需要逐条再次批准。范围不明确或前置条件未满足时会阻止流程，而不是
静默使用默认值。

### Provider 交接

如果 AI Explore 需要设置 Provider，这一步由用户控制。API Key、token 和认证
secret 不得进入聊天。Coding Agent 会请你自行打开 `fsq ui`，在 **Settings**
中输入凭据；私下交互式运行 `fsq providers configure "<provider>"` 是后备方式。
你报告完成后，它会再次检查 Provider status 和 Doctor readiness。readiness
不等于连接测试或成功执行。

### 创建、审查并选择是否重放

展示完整且已确认的 Goal 后，Skill 可以用
`fsq --output json --non-interactive case create ...` 提交一次获准的 AI
Explore。提交开始即消耗该确认。对于结果未知、中断、失败或矛盾的执行，它
不会重试。它只报告一次提交，最多主动报告一次没有新的结构化状态，之后静默
等待，而不会虚构实时操作进度。

Skill 会先总结结果，把每项可观察验收标准映射为有支持、未满足或未知，然后
分别说明记录、发布和 warning。随后只选择准确返回的 Case 路径：优先
`published_case_path`，否则使用 `candidate_case_path` 并标明尚未发布。

它仅在你提出请求时以只读方式原样审查该 Case。重放也只能由你主动发起。
收到请求后，它会执行只读 Doctor 和静态预检查，包括：

```bash
fsq case format "<exact-case-path>" --check --json
```

随后它会显示准确路径和目标，并强烈警告重放是新的目标操作，可能重复副作用。
已知的失败/恢复或重复纠正 mutation 会作为风险说明，但不会自动改写或拒绝
重放。如果你在警告后仍想重放，Skill 会重申剩余风险并要求第二次明确确认。
只有在该确认后，Skill 才能用
`fsq --output json --non-interactive case test --platform "<platform>"
"<exact-case-path>"` 原样提交一次，且不启用 suggestion。创建 Case 不会预先
授权重放，Explore 成功也不代表确定性重放会成功。

## 无需 Provider 的 Web 故障排查后备流程

这个内置 Strict Case 只用于明确选择的故障排查，不是主要体验，也不是用户
Goal 的前置步骤。只有在你有意检查一个不使用 Provider 的已配置 Web 目标时，
才让已安装的 Skill 使用 `assets/getting-started.web.fsq.yaml`。它会先检查
Doctor 和 canonical 格式，取得明确执行确认，最多执行一次，并且只使用准确
返回的 Run ID。它不会配置 Provider、启用 suggestion、重试或启动 Control Plane。

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
fsq --output json --non-interactive case create --platform web --goal "Open https://example.com and verify the Example Domain heading is visible."
fsq case format "<exact-returned-case-path>" --check --json
# 仅在另行明确确认后：
fsq --output json --non-interactive case test --platform web "<exact-returned-case-path>"
fsq --output json --non-interactive runs show "<exact-returned-run-id>" --platform web
fsq --output json --non-interactive runs logs "<exact-returned-run-id>" --platform web --limit 50
```

只使用已提交操作准确返回的 Case 路径和持久化 Run ID；不要自行构造这些值，
也不要推断“最新”Run。静态检查是只读的，并且只有在上述另行确认后才原样
重放 Case。

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
