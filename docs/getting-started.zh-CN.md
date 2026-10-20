# 快速开始

用自然语言描述目标，让 FSQ Quick Test 操作真实应用并验证结果，然后检查执行证据。**Coding Agent** 与 **FSQ UI** 是平等的替代入口。FSQ 是 Alpha 软件，请先阅读[支持与稳定性](support-and-stability.md)。

## 前置条件

- Python 3.11+ 和[对应平台与目标的前置条件](platform-prerequisites.md)。
- 已配置受支持的 Provider（OpenAI、DeepSeek、Google Gemini、Kimi、GitHub Copilot 或 Azure OpenAI），并明确选择模型。
- 使用 Coding Agent 时，打开一个准确的项目根目录以安装 Skill。

Quick Test 自动准备用户级 `~/.fsq/quick-test/` Workspace，无需注册或初始化项目 Workspace，也无需先运行项目 Doctor。它不继承项目的知识、私有环境或凭据；必要的非敏感项目事实应写入目标。它不保存正式 Case，也不自动重放。

## Coding Agent

在 Coding Agent 中打开项目，用以下提示完成一次性安装：

> FSQ 的官方项目地址是 https://github.com/FSQ-lab/FSQ。先确定准确打开的项目根目录。批量披露 Python、fsq 和 Skill 是否存在的只读检查，列出工作目录、命令、目的和影响。提出一次批准请求，只涵盖安装或升级 fsq-agent，以及在本项目运行一次 fsq init --agent skill；请求前列出准确命令及其对 Python 环境和项目文件的影响。只执行获准操作，验证 fsq，并报告 Skill 安装路径。此次设置不初始化 Workspace、不配置 Provider、不运行测试。最后让我重新加载 Coding Agent 或启动新 session，然后停止。

重新加载或开启新 session，让客户端发现 `.agents/skills/fsq/`。升级包后需重新运行 `fsq init --agent skill` 并重载，以更新复制的 Skill。

然后说：“使用 FSQ Quick Test 测试下方目标，选择 Web 和我确认的可用浏览器。遵循已安装的 FSQ Skill，只询问无法确定的决定。”附上下方完整目标。

Skill 批量披露只读发现，使用 `quick-test context` 检查平台、设备或浏览器及前置条件；它保留已确认的准确目标，不猜测或替换浏览器渠道、设备 ID 或路径。只询问影响执行的未解决决定。你的测试请求授权一次正常 Quick Test，无需另一层 FSQ 执行确认；host 与操作系统批准仍独立生效。

若 Provider 尚未配置，请自行打开 `fsq ui` 在 **Settings** 私下输入凭据并选模型，或使用私有交互式 Provider 配置。不要将 Key、token 或 secret 放进聊天、命令参数、目标或 Case。报告配置完成后，Skill 检查安全状态并刷新同一目标的 Quick Test context；ready 不等于连接测试或执行成功。

Skill 只提交一次 `quick-test run`，消费实际 JSONL 进度和终态结果，保留同一进程。失败、超时、中断、输出丢失或格式错误时不会自动重提。报告权威执行状态、整体验证结论、实际目标和已有证据；逐项标准的支持/未满足/未知是 Agent 的证据解读，不是多个独立 FSQ verdict。

## FSQ UI

```bash
python -m pip install --upgrade fsq-agent
fsq ui
```

1. 选择 **Quick Test**。
2. 在共享的 **Settings** 配置 Provider 并明确选模型。
3. 选择 Web 与可用浏览器，粘贴下方目标，点击 **Start Quick Test**。无需预先知道搜索发现的官网网址。
4. 使用 **Open report** 检查结果和证据，或选择 **New test**。意图不明、目标零匹配或多匹配、准备失败会终止请求，不补问。目标可运行不等于模型一定理解正确。

## 完整首次测试

Web 便于开始；[当前视频](https://youtu.be/f1sobvVHNg0) 展示 Android。两者设置和执行不同，搜索结果和测试结果可能变化。

```text
使用已选择的浏览器搜索 FSQ-lab/FSQ GitHub 仓库。打开该仓库，访问 About 区域中的官网链接，验证目标页面同时显示 “Describe a goal.” 和 “Test the real app.”，并结合捕获的页面证据报告结果。只浏览公开页面，不登录、不修改仓库内容、不提交表单。如果无法识别仓库或 About 官网链接，停止并报告阻碍。
```

## 检查结果，再选择进阶操作

先检查真实状态、页面证据、Run ID、Workspace 和报告路径。准备失败可能没有 Run；缺失证据保持未知。Quick Test 的候选记录只留在 Run 内，不发布或保存正式 Case。

需要可复用 Case 时，另行使用 **FSQ Case Manager** 或明确请求 Case 创建。这是单独的 UI 执行，使用注册 Workspace 与相应 Doctor 检查，并非保存已经完成的 Quick Test。仅当实际生成候选时才有可审查内容。

需要重放时，先原样审查准确返回的 Case，然后另行请求。Skill 执行静态检查与 Doctor，披露准确 Case、目标及重复副作用；你明确确认后才执行一次 Strict Replay。Quick Test 成功不证明重放成功，不自动修改 Case 或重试。

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

浏览器配置流程：运行 `fsq ui`，选择 **Quick Test** 或 **FSQ Case Manager**，打开两者共用的 **Settings**。

1. 点击 **Add configuration** 或 **Change provider**，选择 **OpenAI**、**DeepSeek**、**Google Gemini** 或 **Kimi**。
2. 使用 Kimi 时先显式选择 **China** 或 **Global**。输入 API Key，点击 **Load models**，选择模型，再点击 **Save changes**。
3. 点击 **Test connection**，使用已保存配置发送最小请求。
4. 在 **Quick Test** 中回到目标表单并显式开始测试。在 **FSQ Case Manager** 中回到 **Home** 查看 Provider 摘要，再到 **Test Runner** 选择 Workspace、平台和目标，显式启动 Explore。

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
fsq --output jsonl --non-interactive case create --platform web --goal "Open https://example.com and verify the Example Domain heading is visible."
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

选择 **Quick Test**，无需手动设置 Workspace 即可进行探索测试。先在 **Settings** 配置共享 Provider，选择平台及适用的设备或浏览器，再描述应用或搜索对象、操作和预期结果。Web 既支持提供网址，例如 `打开 https://example.com，确认页面标题为 Example Domain`，也支持不提供网址的明确搜索目标，例如 `搜索 FSQ github，并且帮我找到 FSQ 的官网地址`。

无网址的 Web 搜索复用现有空白页启动和 Agent 导航，不增加搜索引擎设置，也不沿用上一次测试的网站。Agent 按目标搜索并查看页面；用户提供的畸形或不支持的导航地址仍会报错，不会转成搜索来绕过校验。此类搜索必须有实际浏览器操作及可用的 HTTP(S) 页面观察证据，才能判定成功；仅凭模型回答或只启动空白浏览器不够。页面或链接证据仍需支持请求的结果，包括“所发现网址是官网”的判断。

**Start Quick Test** 会在 `~/.fsq/quick-test/` 准备专用注册 Workspace，只有输入、具体目标和运行环境检查全部通过才进入探索。所有 Quick Test 共用此 Workspace，每次运行冻结自己的目标。模型可根据应用名称推理 package ID、bundle ID 或可执行文件路径；也可在同一个 prompt 中直接写明这些标识。FSQ 通过所选平台验证解析出的标识，不要求额外取得本地化应用名称映射。它不会穷举排除所有同名应用，目标存在且可运行也不等于模型已正确理解你的意图。意图不足或不明确、具体目标查询零匹配或多匹配、发现或模型调用失败均直接终止，不补问、不自动重试。请自行修改 prompt 或修复环境后显式重新开始。

结果复用现有时间线和证据视图。**Open report** 打开持久报告，**New test** 回到目标输入。Quick Test 不提供保存 Case 或 Strict Replay。离开或刷新页面不会取消服务端活动任务；同一个本地服务仍在运行时，返回页面可恢复活动 Quick Test。重启服务不会续跑。

选择 **FSQ Case Manager**，使用本指南其他部分介绍的完整 Workspace、Case、历史和严格回放工作流。专用 Quick Test Workspace 也会显示在其中。现有 Run 报告链接仍直接打开对应报告。

## 下一步

- 阅读[平台前置条件](platform-prerequisites.md)。
- 学习 [Case 格式](case-format.md)。
- 查看 [CLI reference](cli-reference.md)。
- 实现级架构与行为契约请查阅根目录及各模块的 `SPEC.md` 文件。
