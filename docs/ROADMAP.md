# Nahida Bot ROADMAP

> 本路线图仅基于 Python 方案，不包含 Rust 方案。

## 1. 目标与范围

Nahida Bot 的目标不是做一个普通聊天机器人，而是做一个以 Agent 为核心、以工作空间为中心、可通过插件扩展、可分布式连接 Node 的 Python Bot 框架。

当前仓库还处于早期骨架阶段，主要可见内容是项目元信息、测试与风格约束、基础文档，以及一个最小化的 Python 包结构。因此，这份 ROADMAP 以“从骨架走向可用 MVP，再走向可扩展平台”为主线。

## 2. 技术路线原则

- 语言与运行时：Python 3.12 + `asyncio`
- Web 与 Gateway：FastAPI + WebSocket
- 类型与配置：Pydantic v2 + `pydantic-settings`
- 数据存储：SQLite + `aiosqlite`
- 包管理：`uv`
- CLI：`typer` + `rich`
- 日志与可观测性：`structlog`
- 测试：`pytest` + `pytest-asyncio`
- 类型检查：`pyright`，必要时补充 `mypy`

## 3. 模块参考映射（可复用思路）

> 参考原则：学习架构思路与工程组织，不直接复制实现代码；尤其对非公开来源，仅做行为模式和产品交互层面的抽象借鉴。

| 模块 | 主参考 | 可借鉴点 | 落地提醒 |
|------|--------|----------|----------|
| `core`（应用容器、生命周期） | OpenClaw, AstrBot | 统一启动入口、模块化初始化、优雅退出 | 先保证依赖方向干净，再做功能堆叠 |
| `core.config`（配置系统） | AstrBot, pydantic-settings 官方示例 | 分层配置、环境变量覆盖、默认值策略 | 配置模型必须强类型，避免魔法字符串 |
| `agent.loop`（推理回路） | OpenClaw, claude-code（模式层） | 消息拼装、工具调用回填、流式输出链路 | 不复制具体 prompt 或私有实现细节 |
| `agent.context`（上下文管理） | claude-code（模式层）, LangGraph | 上下文裁剪、历史管理、状态拼接 | 先可用再优化，优先滑窗策略 |
| `agent.providers`（模型抽象） | OpenClaw, LiteLLM, OpenAI SDK 生态 | Provider 统一接口、错误归一化、重试策略 | 首先打通一个 provider，再扩展 |
| `agent.providers.adapters`（响应适配） | LiteLLM, 各厂商 API 文档 | 多后端响应归一化、推理链提取、流式解析 | ⚠️ 必须处理 DeepSeek-R1 和 Claude thinking |
| `workspace`（文件即上下文） | OpenClaw, AstrBot | 指令文件注入、工作区隔离、状态持久化 | 路径安全必须先于易用性 |
| `workspace.sandbox`（文件沙盒） | AstrBot, claude-code（模式层） | 符号链接防护、TOCTOU 防护、多层防御 | ⚠️ 当前实现不安全，Phase 2.7 必须加固 |
| `plugins`（声明式扩展） | OpenClaw, nonebot2 插件生态 | 插件发现、生命周期、能力注册 | 不允许绕过权限模型直连核心 |
| `plugins.permissions`（权限系统） | OpenClaw, Android Manifest 思路 | 声明式权限、运行时拦截、审计日志 | 权限粒度从小开始，逐步放开 |
| `channels`（平台接入层） | AstrBot, nonebot2, aiogram | 多平台适配、消息标准化、事件分发 | 平台差异收敛在 adapter 层 |
| `gateway/node`（分布式执行） | OpenClaw | 节点注册、心跳重连、远程执行协议 | 协议一旦对外发布必须版本化 |
| `cli`（运维入口） | OpenClaw, typer 官方最佳实践 | 命令分组、可读输出、诊断命令 | CLI 输出要面向运维，不只面向开发 |
| `webui`（可视化运维） | AstrBot Dashboard, OpenClaw 控制面思路 | 状态面板、配置可视化、节点管理 | 只消费公开 API，不读内部状态 |
| 测试与质量闸门 | nahida-bot 现有规范, OpenClaw CI 思路 | 分层测试、类型检查、覆盖率门禁 | 新模块必须同时交付测试 |

补充可参考项目：

- nonebot2：插件生态与消息适配设计。
- aiogram：Telegram 领域建模和异步处理。
- LangGraph：状态图化 agent 流程组织。
- LiteLLM：多 provider 抽象和错误兼容。
- FastAPI 官方项目模板：服务分层、依赖注入和测试组织。

关于 claude-code：

- 建议仅参考交互流程、任务分解方式、工具调用行为模式。
- 不复制任何疑似私有实现细节、特定 prompt 文本或内部协议定义。

## 4. 目标架构

Python 方案的核心结构可以概括为五层：

1. `core`：应用容器、配置、事件、日志、异常
2. `agent`：上下文管理、记忆、工具调用、LLM Provider 抽象
3. `workspace`：工作空间、文件系统沙盒、模板
4. `plugins`：插件加载、权限、注册表、Hook、内置工具
5. `gateway` / `node` / `channels`：消息入口、远程节点、平台适配

这五层之间要保持稳定的依赖方向：上层依赖下层，内层不反向依赖外层；`core` 保持中立，不直接绑定具体渠道或插件实现。

## 5. 阶段规划（细节与勾选合并）

> 说明：每个阶段都使用可勾选任务清单，既是执行计划也是验收项。

### Phase 0 - 项目地基

目标：把仓库从“文档驱动的设想”变成“可持续开发的 Python 工程”。

任务清单：

- [x] 统一包名、模块命名、目录边界，明确 `nahida_bot/core`、`nahida_bot/agent`、`nahida_bot/plugins` 等目标目录。
- [x] 补齐 `pyproject.toml` 的运行依赖与开发依赖，固定 `ruff`、`pyright`、`pytest` 基线。
- [x] 增加最小 CLI（如 `nahida-bot start`），先实现空壳启动与优雅退出。
- [x] 在 `scripts/` 添加常用命令包装（lint、typecheck、test、run）。
- [x] 将 README、开发文档、架构文档对齐到同一目录与模块命名。
- [x] 验证 `uv sync`、`ruff check .`、`ruff format .`、`pyright`、`pytest` 全部可跑通。

前置依赖：无。

风险控制：避免先写业务逻辑再补工具链；先把质量闸门立起来。

参考来源：AstrBot（项目初始化）、OpenClaw（模块拆分）、FastAPI 官方模板。

### Phase 1 - 核心运行时

目标：建立应用容器、配置系统、日志系统和事件系统。

任务清单：

- [x] 实现 `Application` 主类，统一管理生命周期（init/start/stop）。
- [x] 使用 `pydantic-settings` 建立分层配置（默认值、配置文件、环境变量）。
- [x] 接入 `structlog`，区分开发态可读日志和生产态 JSON 日志。
- [x] 建立基础异常树（配置错误、启动错误、插件错误、通信错误等）。
- [x] 实现轻量事件总线（内部事件与订阅机制，基础版）。
- [x] 验证应用可独立启动/优雅退出（基础版）。
- [x] 关键错误可结构化记录。

前置依赖：Phase 0。

风险控制：不要在本阶段引入具体平台逻辑，保证核心层中立。

参考来源：OpenClaw（生命周期）、AstrBot（配置日志）、pydantic-settings。

事件系统的结构设计、类型约束、依赖注入集成和参考方案，统一维护在 [docs/architecture/event-system.md](architecture/event-system.md)，ROADMAP 仅保留交付目标与勾选状态。

### Phase 2 - Agent 与 Workspace 联合阶段

目标：同步打通 Agent 核心回路与 Workspace 上下文能力，形成最小可用智能闭环。

#### Phase 2.1 - Workspace 基线与安全边界

- [x] 实现 workspace 初始化、模板复制、默认空间和多空间切换。
- [x] 文件读写统一走安全 API，完成路径归一化与路径穿越防护。
- [x] 明确 workspace 元数据结构（空间标识、创建时间、最近活跃时间、默认空间标记）。

#### Phase 2.2 - 指令注入与上下文预算

- [x] 定义 `AGENTS.md`、`SOUL.md`、`USER.md` 的上下文注入优先级与拼接规则。
- [x] 建立上下文构建流程（系统指令、工作区指令、会话历史、工具回填）。
- [x] 接入上下文预算与最小可用截断策略（先滑窗，后摘要）。

#### Phase 2.3 - Agent Loop 与 Provider 打通

- [x] 实现 Agent Loop（消息组装、模型调用、工具调用、结果回填、终止条件）。
- [x] 定义 Provider 抽象接口，先接入一个 OpenAI 兼容 Provider。
- [x] 固定统一错误模型（超时、限流、认证失败、响应格式错误）。

#### Phase 2.4 - Tool Calling 协议闭环

- [x] 实现 tool calling 协议、参数校验和执行结果协议。
- [x] 定义工具执行生命周期（准备、执行、回填、失败重试/中止）。
- [x] 明确工具结果注入策略（结构化结果、可裁剪日志、错误可解释）。

#### Phase 2.5 - 记忆模型与持久化

- [x] 实现会话记忆（短期）和长期记忆（可检索）模型。
- [x] 先用 SQLite 打通记忆持久化，并预留存储抽象接口。
- [x] 建立最小检索策略（按会话、时间窗、关键词）并定义淘汰规则。

> ⚠️ **待优化（技术债）**：当前记忆层存在三层间接（`MemoryStore ABC → SQLiteMemoryStore → SQLiteMemoryRepository → DatabaseEngine`），对于单一 SQLite 后端而言抽象层数偏多。后续应考虑：
>
> - 评估是否引入轻量 ORM（如 SQLModel）统一 Repository 与模型层，减少手写 SQL 和序列化代码。
> - 若确认只使用 SQLite，可考虑合并 `SQLiteMemoryStore` 与 `SQLiteMemoryRepository` 为一层。
> - 关键词检索当前为 jieba 分词+精确匹配，后续可接入向量检索或 BM25 排序提升召回质量。

#### Phase 2.5b - LLM 增强记忆（规划中）

> 当前关键词方案（jieba 分词+精确匹配）可解决基础检索，但存在语义理解缺失（近义词无法召回）和长文本摘要丢失问题。本阶段规划 LLM 辅助的记忆管理方案。

- [ ] 实现 LLM keyphrase extraction：存储时调用 LLM 提取语义关键词，替代/补充 jieba 分词。
- [ ] 实现 LLM 摘要压缩：对长期记忆做阶段性摘要，减少存储量并保留语义。
- [ ] 实现语义向量检索：将记忆 embedding 化，支持近义词和语义相似度召回。
- [ ] 定义记忆分层策略：短期（最近 N 轮）→ 中期（摘要）→ 长期（向量检索）。
- [ ] 评估 embedding 模型选型（本地 vs API），平衡精度与延迟。

#### Phase 2.6 - 稳定性增强与阶段验收

- [x] 增加重试、超时、回退提示，避免单次错误中断对话。
- [x] 建立最小可观测性埋点（调用耗时、失败率、工具调用成功率）。
- [x] 验证至少一条完整闭环：workspace 指令加载 -> provider 调用 -> tool 调用 -> 最终回复。

> ⚠️ **待优化（技术债）**：
>
> - `MetricsCollector` 当前为纯内存聚合，缺少持久化和导出能力。后续需增加 flush/export 机制（如 log sink、Prometheus exporter），否则进程重启后指标丢失。
> - 错误回退文案通过 `AgentLoopConfig.provider_error_template` 配置，但尚未接入 i18n 系统。

#### Phase 2.7 - Workspace Sandbox 安全增强

> ⚠️ **重要性**：当前沙盒实现仅使用简单路径检查，存在符号链接攻击、TOCTOU 等安全风险。本阶段必须完成安全加固。

- [ ] 实现符号链接检测与拒绝（包括指向沙盒内和沙盒外的符号链接）。
- [ ] 实现 TOCTOU 防护（操作时二次验证路径有效性）。
- [ ] 实现文件大小限制（默认 10MB，可配置）。
- [ ] 实现可选的文件扩展名白名单机制。
- [ ] 处理特殊文件系统对象（设备文件、FIFO、socket）的拒绝逻辑。
- [ ] 添加 Unicode/编码绕过防护。
- [ ] 编写完整的安全测试套件（符号链接、硬链接、路径穿越、编码绕过等）。
- [ ] 更新 architecture 文档中的沙盒安全文档。

**参考实现**：见 [docs/architecture/sandbox-security.md](architecture/sandbox-security.md)。

#### Phase 2.8 - Provider 响应健壮性与多后端适配

> ✅ **已完成**：Phase 2.8 和 2.8b 全部完成。Provider 现在支持 OpenAI 兼容族和 Anthropic 族的推理链、Extended Thinking、签名回传等高级特性。

- [x] 扩展 `ProviderResponse` 数据结构，添加 `reasoning_content` 和 `reasoning_tokens` 字段。
- [x] 扩展 `ContextMessage` 数据结构，添加 `reasoning` 字段。
- [x] 实现集成式 Provider 架构（`_ReasoningMixin` + 类继承），替代独立 `ResponseAdapter` 协议。
- [x] 实现标准 OpenAI 响应适配（`OpenAICompatibleProvider` 演进）。
- [x] 实现 DeepSeek-R1 响应适配（`DeepSeekProvider` 子类，处理 `reasoning_content` 字段）。
- [x] 实现 Anthropic/Claude 响应适配器（处理 `thinking` 块和 `redacted_thinking` 块）。
- [x] 定义推理链上下文策略（`ReasoningPolicy`：strip/append/budget）。
- [x] 在 Agent Loop 中实现推理内容的传播逻辑（`_build_assistant_message` → `ContextMessage`）。
- [x] 编写适配器和上下文策略的完整测试套件。
- [x] 实现 Provider 注册表（`@register_provider` + 工厂方法）和子类（GLM、Groq、Minimax）。
- [x] 编写多后端集成测试（OpenAI/DeepSeek/Anthropic）。
- [x] 更新 architecture 文档中的 Provider 文档（当前实现部分）。

**支持的响应格式**：

| 后端 | 特殊字段 | Provider 类 |
|-----|---------|----------|
| OpenAI 标准 | `content` | `OpenAICompatibleProvider` |
| DeepSeek-R1 | `reasoning_content` | `DeepSeekProvider` |
| GLM/智谱 | （无特殊字段） | `GLMProvider` |
| Groq | `reasoning` | `GroqProvider` |
| Minimax | （无特殊字段） | `MinimaxProvider` |
| Claude | `thinking` 块 | `AnthropicProvider` |

**参考实现**：见 [docs/architecture/provider-architecture.md](architecture/provider-architecture.md)。

前置依赖：Phase 1。

风险控制：

- 工具协议要尽早固定，后续插件系统将强依赖该协议。
- 先保证安全边界，再做便捷 API。

参考来源：OpenClaw（Agent + Workspace 模式）、claude-code（流程模式层）、AstrBot（运行时文件组织）、LiteLLM/OpenAI SDK。

### Phase 3 - 插件系统与 Channel 接口定义

目标：建立声明式、可治理的插件系统，并定义 Channel 作为标准插件接口。

> 本阶段的关键设计决策：Channel 不是独立层，而是通过插件系统接入。这样可以复用权限模型、生命周期管理和能力注册机制。参考 OneBot/NapCat 等协议，定义统一的 Channel 接口，但支持多种底层通信方式（HTTP、WebSocket、SSE）。

任务清单：

#### Phase 3.1 — Manifest 与 Loader

- [x] 定义 `plugin.yaml` 字段模型（`PluginManifest` Pydantic 模型：id、name、version、entrypoint、type、permissions、capabilities、config schema、depends_on）。
- [x] 实现 YAML 解析与校验（`parse_manifest`，缺失字段报错、格式校验）。
- [x] 定义入口点格式约束（`module:Class`，强制一对一模块绑定）。
- [x] 实现插件发现（扫描指定目录下的 `plugin.yaml`，支持子目录结构）。
- [x] 实现动态加载（`importlib` 导入入口类，校验 `Plugin` 子类）。
- [x] 实现 `sys.modules` 清理与模块绑定追踪（支持热重载前提）。

#### Phase 3.2 — 事件系统增强

- [x] 新增插件生命周期事件类型（`PluginLoaded`、`PluginEnabled`、`PluginDisabled`、`PluginUnloaded`、`PluginErrorOccurred`）。
- [x] 改进 `EventBus.publish()` 为两阶段执行模型（同步核心 phase 1 + 异步插件 phase 2）。
- [x] 实现 handler 优先级（`priority` 参数，值越小越先执行）。
- [x] 实现 per-handler 超时保护（`timeout` 参数，异步阶段 handler 超时后记录失败但不阻塞其他）。

#### Phase 3.3 — APIBridge 与权限

- [x] 实现 `BotAPI` 协议定义（send_message、on_event、subscribe、register_tool、get_session、memory_search/store、workspace_read/write、logger）。
- [x] 实现 `RealBotAPI` 桥接层（将 `BotAPI` 协议连接到 EventBus、WorkspaceManager、MemoryStore）。
- [x] 实现声明式权限校验（`PermissionChecker`：network outbound/inbound、filesystem read/write zone、memory read/write、subprocess、env_vars 前缀匹配）。
- [x] 实现审计日志（权限拒绝时通过 structlog 记录 plugin_id、resource、action、target）。

#### Phase 3.4 — 异常隔离与生命周期管理

- [x] 实现 `PluginManager` 完整生命周期（discover → load → enable → disable → unload，含状态机校验）。
- [x] 实现生命周期事件发布（每次状态转换触发对应 Event）。
- [x] 实现首启与重启用区分（`LOADED → ENABLED` 调用 `on_load` + `on_enable`；`DISABLED → ENABLED` 仅调用 `on_enable`）。
- [x] 实现逆序关闭（`shutdown_all` 按反向加载顺序关闭插件）。
- [x] 实现插件异常隔离（`_safe_invoke` 超时 + 异常捕获，崩溃插件进入 `ERROR` 状态不影响其他插件）。
- [x] 实现 `ToolRegistry` 和 `HandlerRegistry`（按 plugin_id 注册/注销，支持批量清理）。
- [ ] 实现降级策略（ERROR 状态插件的可配置自动重试，含最大次数与冷却时间）。

#### Phase 3.5 — ChannelPlugin 接口

- [ ] 定义 `ChannelPlugin` 基类（`handle_inbound_event`、`send_message`、`get_user_info`、`get_group_info`）。
- [ ] 定义通信方式声明（`SUPPORT_HTTP_SERVER/CLIENT`、`SUPPORT_WEBSOCKET_SERVER/CLIENT`、`SUPPORT_SSE`）。
- [ ] 实现 HTTP Server 模式的 webhook 端点自动注册（声明了 `http_server` 协议的 ChannelPlugin 自动挂载路由）。
- [ ] 实现消息标准化流程（平台原生事件 → `InboundMessage` → Agent → `OutboundMessage` → 平台回复）。
- [ ] 实现消息事件类型（`MessageReceived`、`MessageSending`、`MessageSent`）。

#### Phase 3.6 — 内置插件与验证

- [ ] 提供基础内置插件（读文件、命令执行、网页读取、记忆检索）。
- [ ] 实现插件配置解析（环境变量 `NAHIDA_PLUGIN_{ID}_{KEY}` + `config/plugins/{id}.yaml`，JSON Schema 校验）。
- [ ] 验证不改核心代码可新增并加载外部插件。
- [ ] 验证越权行为可拦截、可追踪（权限拒绝触发 `PermissionDenied` + 审计日志）。
- [ ] 验证插件崩溃不影响核心和其他插件（异常隔离测试通过）。

#### Phase 3.7 — SDK 分离（可选前置）

- [ ] 抽取 `nahida-bot-sdk` 独立包（Plugin 基类、BotAPI 协议、Manifest 模型、消息类型）。
- [ ] 实现 `MockBotAPI` 和测试 fixture（插件开发者无需启动 bot 即可单元测试）。
- [ ] 发布到 PyPI 或本地可安装（`uv` 可安装）。
- [ ] 验证：一个不依赖 `nahida-bot` 的插件可以安装 SDK 并完成编译 + 单元测试。

**ChannelPlugin 接口设计（关键产出物）**：

参考 OneBot/NapCat，Channel 应该支持多种通信协议组合：

```python
class ChannelPlugin(Plugin):
    """所有 Channel 插件必须实现的基类。"""

    async def handle_inbound_event(self, event: dict) -> None:
        """来自外部平台的事件回调（由 webhook/push 机制触发）。"""
        ...

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """向外部平台发送消息，返回消息 ID。"""
        ...

    async def get_user_info(self, user_id: str) -> dict:
        """获取用户信息（可选）。"""
        ...

    # 支持的通信方式标记（插件可声明一个或多个）
    SUPPORT_HTTP_SERVER: bool = False      # 本 Bot 提供 HTTP 端点
    SUPPORT_HTTP_CLIENT: bool = False      # 本 Bot 主动 HTTP 请求外部
    SUPPORT_WEBSOCKET_SERVER: bool = False # 本 Bot 提供 WebSocket 端点
    SUPPORT_WEBSOCKET_CLIENT: bool = False # 本 Bot 连接到外部 WebSocket
    SUPPORT_SSE: bool = False              # 本 Bot 通过 SSE 推送
```

**通信方式详解**（第三方 Channel 可选择一个或多个组合）：

1. **HTTP Server 模式**（外部推送）
   - 外部系统向 Bot 的 HTTP 端点发送 webhook 事件
   - Bot 处理后可通过 HTTP Client 或其他方式回复
   - 例：`POST /channels/<channel_id>/webhook` 接收平台消息

2. **HTTP Client 模式**（Bot 主动）
   - Bot 主动轮询或通过心跳获取消息
   - Bot 通过 HTTP 请求向外部系统发送消息
   - 例：Telegram Polling 模式、NapCat 的 HTTP API 调用

3. **WebSocket Server 模式**（双向长连接）
   - 外部系统连接到 Bot 的 WebSocket 端点
   - 双向推送消息和事件
   - 例：Matrix、自定义 WebSocket 网关

4. **WebSocket Client 模式**（Bot 连接外部）
   - Bot 主动连接到外部系统的 WebSocket
   - 接收事件，发送消息
   - 例：连接到中心网关或云服务

5. **SSE 模式**（单向服务端推送）
   - 外部系统通过 HTTP SSE 接收 Bot 的消息
   - Event 向 Bot 的 HTTP 端点提交
   - 例：Web 浏览器直接与 Bot 互联

前置依赖：Phase 2。

风险控制：

- 不要为了"插件方便"绕过权限检查，ChannelPlugin 仍需声明所需权限。
- ChannelPlugin 的 webhook 端点需要鉴权与频率限制，防止欺骗。
- 多 Channel 并存时需要隔离会话上下文，避免消息混淆。

参考来源：OpenClaw（插件 contract）、nonebot2（扩展生态）、OneBot 协议、NapCat 设计、Android Manifest 思路。

### Phase 4 - 基于插件系统的 Channel 实现

目标：实现一个或多个 ChannelPlugin，让系统真正接入外部平台消息。

> 本阶段的核心是：创建 ChannelPlugin 的具体实现（作为插件形式加载），而不是核心硬编码的 adapter 层。这样保证了 Channel 的可扩展性和权限隔离。

任务清单：

- [ ] 落地统一 `InboundMessage` 与 `OutboundMessage`，与 Phase 3 中 ChannelPlugin 接口对齐。
- [ ] 选择一个平台实现第一个 ChannelPlugin（建议 Telegram，参考 aiogram 异步设计；或 QQ/NapCat，参考 OneBot Webhook 模式）。
- [ ] 在插件中实现选定的通信方式（HTTP Server + HTTP Client，或 WebSocket 双向，等）。
- [ ] 打通平台消息 -> InboundMessage -> Plugin 事件处理 -> Agent -> OutboundMessage -> 平台回复链路。
- [ ] 建立平台消息 ID、聊天 ID、用户 ID 到内部会话 ID 的映射策略（在 Plugin 内完成）。
- [ ] 实现流式分片发送与速率控制。
- [ ] 保留关键消息追踪字段用于调试与审计。
- [ ] 验证外部平台可稳定完成"发消息 -> 得回复"。
- [ ] 将 Plugin 打包（plugin.yaml + 代码），验证可通过 plugin load 命令加载。

前置依赖：Phase 3（ChannelPlugin 接口）。

风险控制：

- 平台差异统一收敛在 ChannelPlugin 实现内部，不渗透进 Agent 核心或其他插件。
- 第一个 ChannelPlugin 的稳定性直接影响用户体验，务必包含充分的测试和监控。
- 多 Channel 并存时，核心层应该透明地支持（会话隔离、上下文管理）。

参考来源：AstrBot、nonebot2、aiogram、OneBot 协议、NapCat、Telegram Bot API 生态。

### Phase 5 - Gateway 与 Node

目标：实现 Python 版远程节点控制能力。

任务清单：

- [ ] 完成 Gateway 服务（FastAPI 控制 API + 节点连接端点）。
- [ ] 固定 WebSocket 协议（消息类型、版本字段、错误码、兼容策略）。
- [ ] 实现节点配对、令牌签名、会话续期。
- [ ] 实现心跳、超时判定、断线重连、节点状态机。
- [ ] 打通命令下发、结果回传、超时取消、输出截断。
- [ ] 增加命令白名单、执行审计、敏感字段脱敏。
- [ ] 验证多节点长期连接稳定性与协议回归测试。

前置依赖：Phase 4。

风险控制：协议一旦对外开放，默认只做向后兼容变更。

参考来源：OpenClaw（Gateway-Node 模式）、FastAPI WebSocket 实践。

### Phase 6 - WebUI 与运维工具

目标：让系统可视化、可配置、可诊断。

任务清单：

- [ ] 完成 WebUI 基础页面（登录、配置编辑、状态仪表盘）。
- [ ] 实现节点、插件、workspace 状态可视化。
- [ ] 完善 CLI 命令组（init/start/stop/plugin/node/workspace/config/doctor）。
- [ ] 实现 doctor 诊断（配置、连接、权限、日志导出）。
- [ ] 暴露关键指标与健康检查。
- [ ] 验证非开发者可通过 UI 或 CLI 完成基础部署与定位常见故障。

前置依赖：Phase 5。

风险控制：WebUI 只消费公开 API，不直接耦合内部模块。

参考来源：AstrBot Dashboard、OpenClaw 控制面思路、typer + rich。

### Phase 7 - 稳定性、发布与生态

目标：把项目从“能跑”推进到“能发版、能增长”。

任务清单：

- [ ] 建立完整 CI/CD（lint、format、typecheck、test、coverage、构建）。
- [ ] 定义版本规范、发布清单和回滚预案。
- [ ] 同步维护架构、插件开发、部署与 API 文档。
- [ ] 提供示例插件、示例配置、示例 workspace 模板。
- [ ] 完成 Issue/PR 模板、贡献指南、破坏性变更公告机制。
- [ ] 验证发布过程可自动化、可追溯，且文档与代码同步。

前置依赖：Phase 6。

风险控制：发布阶段不再做核心架构改造，优先稳定与补文档。

参考来源：OpenClaw（发布治理）、AstrBot（文档维护）、PyPA 官方指南。

## 6. 建议的里程碑顺序

如果按风险和依赖关系排序，建议遵循下面的顺序：

1. 项目地基（Phase 0）
2. 核心运行时（Phase 1）
3. Agent 与 Workspace 联合阶段（Phase 2.1-2.6）
4. **Workspace Sandbox 安全加固（Phase 2.7）** ⚠️ 阻断项
5. **Provider 响应健壮性增强（Phase 2.8）** ⚠️ 推荐在 Phase 3 前完成
6. 插件系统与 Channel 接口定义（Phase 3）
7. 基于插件系统的 Channel 实现（Phase 4）
8. Gateway 与 Node（Phase 5）
9. WebUI 与运维工具（Phase 6）
10. 稳定性、发布与生态（Phase 7）

这个顺序的核心原因是：

- **Phase 0-2.6** 建立最小智能闭环（应用容器 -> 核心运行时 -> Agent + Workspace）
- **Phase 2.7-2.8** 安全与健壮性加固（**必须在 Phase 3 前完成，避免在不可靠基础上构建插件生态**）
- **Phase 3-4** 打通插件和 Channel（先定义接口，允许多种通信协议；再实现具体 Channel 作为插件）
- **Phase 5-6** 扩展分布式与运维（Gateway-Node + WebUI）
- **Phase 7** 稳定化与商业化（发版、CI/CD、生态）

关键设计点：

- **Phase 2.7 是阻断项**：不安全的沙盒会威胁整个系统安全，必须在插件系统落地前修复。
- **Phase 2.8 推荐优先**：Provider 响应格式差异会直接影响 Agent 能力，尽早适配可减少后续返工。
- **Phase 3 中的 Channel 接口设计直接服务于 Phase 4**，避免核心层改造。

建议实践方式：

- 主线阶段：按 Phase 0 -> Phase 7 推进。
- 并行事项：测试基建、文档维护、示例维护可全程并行。
- 冻结策略：每个 Phase 结束时冻结接口一次，避免跨阶段大范围返工。

## 7. MVP 定义

第一版可接受的 MVP 不要求完整生态，但必须包含以下能力：

- 一个可启动的 Python 应用容器
- 一个能工作的 Agent Loop
- 一个可用的 Workspace 目录
- 一个插件加载机制
- 一个 Channel 接入实现
- 一个最小可用的 Gateway 或 Node 通信闭环
- 基础测试与类型检查

如果上述能力都没有完成，项目仍然停留在“设计文档阶段”；如果都完成了，项目就进入“可扩展平台阶段”。

MVP 建议额外约束：

- 必须包含至少一个真实平台消息回路。
- 必须包含至少一个真实 Provider 回路。
- 必须包含最小权限控制，而不是“先全放开”。

## 8. 风险与约束

- Python 方案的性能上限主要依赖异步 I/O 设计和插件隔离质量，而不是单纯依赖语言性能。
- 插件系统一旦失控，会直接影响安全性和稳定性，因此权限模型必须先于生态扩张落地。
- Workspace 机制是项目的核心资产，任何能破坏文件安全边界的实现都应视为阻断项。
- Gateway-Node 协议一旦发布，就属于稳定契约，后续只能做兼容性演进。

**⚠️ 关键安全风险（Phase 2 必须解决）**：

### 8.1 Workspace Sandbox 安全风险

当前 `workspace/sandbox.py` 实现存在以下已知漏洞：

| 风险类型 | 严重程度 | 状态 |
|---------|---------|------|
| 符号链接攻击 | 🔴 高 | 待修复（Phase 2.7） |
| TOCTOU 竞态条件 | 🔴 高 | 待修复（Phase 2.7） |
| 硬链接攻击 | 🟡 中 | 待修复（Phase 2.7） |
| Unicode/编码绕过 | 🟡 中 | 待修复（Phase 2.7） |
| 特殊文件系统对象 | 🟡 中 | 待修复（Phase 2.7） |
| 无文件大小限制 | 🟡 中 | 待修复（Phase 2.7） |

**缓解措施**：在 Phase 2.7 中实现多层防御机制，详见 [docs/architecture/sandbox-security.md](architecture/sandbox-security.md)。

### 8.2 Provider 响应兼容性风险

当前 `agent/providers/openai_compatible.py` 实现的局限性：

| 风险类型 | 严重程度 | 状态 |
|---------|---------|------|
| DeepSeek-R1 推理链丢失 | 🟡 中 | 待修复（Phase 2.8） |
| Claude thinking 块丢失 | 🟡 中 | 待修复（Phase 2.8） |
| 流式响应不支持 | 🟡 中 | 待规划（Phase 3+） |
| 拒绝标记未处理 | 🟢 低 | 待规划 |

**缓解措施**：在 Phase 2.8 中实现响应适配器模式和推理链支持，详见 [docs/architecture/provider-architecture.md](architecture/provider-architecture.md)。

额外风险清单：

- 过早引入多 Provider、多 Channel，可能导致核心抽象失稳。
- 没有回归测试的接口调整会快速积累技术债。
- 插件热加载如果没有隔离与回滚机制，会成为线上稳定性风险点。

建议的质量闸门：

- 每个 Phase 至少新增一组单元测试和一组集成测试。
- 关键协议（消息模型、插件 manifest、Gateway-Node 报文）要有固定示例和回归测试。
- 任何跨模块重构都必须伴随文档更新。

## 9. 结语

Python 方案的价值不只是“更快写出来”，而是用 Python 的 AI 生态、类型校验和开发体验，把一个 Agent Bot 框架做成真正可持续演进的平台。只要围绕 Agent、Workspace、插件系统和 Gateway-Node 四个核心支柱推进，这个项目的技术方向就不会跑偏。
