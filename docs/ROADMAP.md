# Nahida Bot ROADMAP

> 本路线图仅基于 Python 方案，不包含 Rust 方案。

## 1. 目标与范围

Nahida Bot 的目标不是做一个普通聊天机器人，而是做一个以 Agent 为核心、以工作空间为中心、可通过插件扩展、可分布式连接 Node 的 Python Bot 框架。

当前仓库已经越过早期骨架阶段，完成了 Core、Agent、Workspace、Plugin、Telegram Channel、多 Provider 与内置命令的主体闭环。因此，这份 ROADMAP 以“稳定当前 MVP，再走向安全加固、分布式执行与可扩展生态”为主线。

## 2. 技术路线原则

- 语言与运行时：Python 3.12 + `asyncio`
- Web 与 Gateway：FastAPI + WebSocket
- 类型与配置：Pydantic v2 + 手工分层配置加载（保留显式优先级控制）
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
| `core.config`（配置系统） | AstrBot, Pydantic 官方示例 | 分层配置、环境变量覆盖、显式优先级策略 | 配置模型必须强类型，避免魔法字符串；当前采用手工 merge 保持优先级可控 |
| `agent.loop`（推理回路） | OpenClaw, claude-code（模式层） | 消息拼装、工具调用回填、流式输出链路 | 不复制具体 prompt 或私有实现细节 |
| `agent.context`（上下文管理） | claude-code（模式层）, LangGraph | 上下文裁剪、历史管理、状态拼接 | 先可用再优化，优先滑窗策略 |
| `agent.providers`（模型抽象） | OpenClaw, LiteLLM, OpenAI SDK 生态 | Provider 统一接口、错误归一化、重试策略 | 首先打通一个 provider，再扩展 |
| `agent.providers.adapters`（响应适配） | LiteLLM, 各厂商 API 文档 | 多后端响应归一化、推理链提取、流式解析 | ⚠️ 必须处理 DeepSeek-R1 和 Claude thinking |
| `workspace`（文件即上下文） | OpenClaw, AstrBot | 指令文件注入、工作区隔离、状态持久化 | 路径安全必须先于易用性 |
| `workspace.sandbox`（文件沙盒） | AstrBot, claude-code（模式层） | 符号链接防护、TOCTOU 防护、多层防御 | ⚠️ 当前实现仅适合可信本地 MVP；开放不可信插件/远程执行前必须加固 |
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
- [x] 使用 Pydantic v2 建立分层配置（默认值、配置文件、环境变量/`.env`、调用参数），通过手工 merge 保持优先级可控。
- [x] 接入 `structlog`，区分开发态可读日志和生产态 JSON 日志。
- [x] 建立基础异常树（配置错误、启动错误、插件错误、通信错误等）。
- [x] 实现轻量事件总线（内部事件与订阅机制，基础版）。
- [x] 验证应用可独立启动/优雅退出（基础版）。
- [x] 关键错误可结构化记录。

前置依赖：Phase 0。

风险控制：不要在本阶段引入具体平台逻辑，保证核心层中立。

参考来源：OpenClaw（生命周期）、AstrBot（配置日志）、Pydantic。

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

> ⚠️ **重要性**：当前沙盒实现仅使用简单路径检查，存在符号链接攻击、TOCTOU 等安全风险。为尽快打通可运行 MVP，本阶段未阻塞 Phase 3/4 的可信本地插件与 Telegram 接入；但在开放不可信第三方插件、远程节点执行或更高权限文件工具前，必须完成安全加固。

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
- [x] 实现 DeepSeek V4 thinking 模式支持（`thinking` 开关 + 工具调用轮次 `reasoning_content` 回传）。
- [x] 实现 Anthropic/Claude 响应适配器（处理 `thinking` 块和 `redacted_thinking` 块）。
- [x] 定义推理链上下文策略（`ReasoningPolicy`：strip/append/budget）。
- [x] 在 Agent Loop 中实现推理内容的传播逻辑（`_build_assistant_message` → `ContextMessage`）。
- [x] 编写适配器和上下文策略的完整测试套件。
- [x] 实现 Provider 注册表（`@register_provider` + 工厂方法）和子类（GLM、Groq、Minimax）。
- [x] 编写多后端集成测试（OpenAI/DeepSeek/Anthropic）。
- [x] 更新 architecture 文档中的 Provider 文档（当前实现部分）。
- [ ] 实现跨 Provider 的 `reasoning_effort` 聊天指令（DeepSeek/OpenAI/Claude 等均支持，接口已预留）。

**支持的响应格式**：

| 后端 | 特殊字段 | Provider 类 |
|-----|---------|----------|
| OpenAI 标准 | `content` | `OpenAICompatibleProvider` |
| DeepSeek-R1/V4 | `reasoning_content` + thinking 模式 | `DeepSeekProvider` |
| GLM/智谱 | （无特殊字段） | `GLMProvider` |
| Groq | `reasoning` | `GroqProvider` |
| Minimax | Anthropic Messages API，`thinking` 块 | `MinimaxProvider(AnthropicProvider)` |
| Claude | `thinking` 块 | `AnthropicProvider` |

**参考实现**：见 [docs/architecture/provider-architecture.md](architecture/provider-architecture.md)。

前置依赖：Phase 1。

风险控制：

- 工具协议要尽早固定，后续插件系统将强依赖该协议。
- 先保证安全边界，再做便捷 API。

参考来源：OpenClaw（Agent + Workspace 模式）、claude-code（流程模式层）、AstrBot（运行时文件组织）、LiteLLM/OpenAI SDK。

#### Phase 2.9 - 图像理解与多模态上下文

> 当前进度：**Phase 2.9 主链路已完成，安全与平台兼容性已加固**。MediaResolver/MediaCache/MediaPolicy 已实现；双 Channel 入站 attachment 已完成；Provider 能力配置可注入 ProviderSlot；vision 主模型路径已覆盖；非 vision fallback 三模式（auto/tool/off）已实现；`image_understand` 工具已能读取当前回合和 session 历史图片；多轮上下文 cache-aware 策略已实现基础版；Memory 持久化已保存 attachment 元数据、缓存路径/描述和 assistant reasoning，并避免持久化平台临时 URL；缓存观测指标已记录；Anthropic `cache_control` 注入已支持；本地单元测试已通过。

设计原则：

- 主模型支持图片输入时，优先原生传图，不先压成文字描述。
- 主模型不支持图片输入时，提供 `image_understand` 能力，并支持自动 fallback 描述，保证“这张图是什么？”这类消息可直接工作。
- 能力判断以显式配置为准，Provider/模型名启发式只作为保守默认，未知模型默认不支持图片输入。
- 多轮上下文采用 cache-aware 策略：短期内可保留稳定的原生图片内容块来争取 Provider prompt/KV cache 命中；长期不持久化 base64 或过期 URL，只保存媒体引用、缓存路径、hash、描述、Provider cache id 和可用性状态。

任务清单：

- [x] 定义首版 `ModelCapabilities`，支持按 provider slot 和具体 model 解析 `image_input`、`tool_calling`、`max_image_count`、`max_image_bytes`、支持 MIME 等能力。（`MediaCapabilities` 可后续按需要拆分。）
- [x] 扩展配置：新增 `multimodal.image_fallback_mode`（auto/tool/off）、`media_context_policy`（cache_aware/description_only/native_recent）、`image_fallback_provider`、`image_fallback_model`、图片数量/大小限制；Provider 配置改为 `models` 列表，模型对象通过 `capabilities` 声明能力。
- [x] 扩展 `ProviderSlot` / `ProviderManager`：`resolve_model()` 后可返回本轮实际模型能力；`Application` 初始化时消费 Provider 能力配置。
- [x] 扩展 `InboundMessage`：新增 `attachments`，用标准 `InboundAttachment` 表示图片、语音、视频和文件；Milky 和 Telegram converter 均已填充 `attachments`，同时保留文本降级和 `raw_event`。
- [x] 扩展 `ContextMessage`：在兼容 `content: str` 的前提下新增 `parts`，支持 `text`、`image_url`、`image_base64`、`image_description` 等内容块。
- [x] 实现 `MediaResolver` / `MediaCache` / `MediaPolicy`：统一处理平台资源解析、下载缓存、TTL、MIME/大小限制、URL 脱敏和清理；下载路径已加入 scheme/host/private IP 防护和 streaming 大小限制，平台生成的可信临时 URL 可显式放行本机 Channel 服务。
- [x] Provider 序列化支持图片内容块：OpenAI 兼容 vision 和 Anthropic image blocks 已支持；非 vision 路由当前不生成原生图片 part。
- [x] 建模 Provider prompt/context cache 能力：记录 `prompt_cache`、`prompt_cache_images`、`explicit_context_cache`、最小 token 阈值和 TTL；支持 Anthropic `cache_control`、Gemini cached content、OpenAI prompt cache usage/retention 等 Provider 差异。
- [x] 在 `SessionRunner` / Agent 前置阶段实现首版路由：vision 主模型走原生图片；非 vision 主模型保留文本降级。
- [x] 非 vision 主模型按 `image_fallback_mode` 自动描述或仅注入 `image_understand` 工具。
- [x] 实现内置 `image_understand` 工具：读取当前回合或当前 session 历史中的 `media_id`，调用 fallback vision Provider，返回描述/OCR/安全备注；工具注册已去重。
- [x] 多轮上下文策略：最近图片可按能力和缓存收益继续附图，旧图片转为缓存描述；用户追问”刚才那张图”时能通过 `media_id` 找到描述或可用本地缓存。Provider 显式 cache id 仍保留为后续增强项。
- [x] 更新 Memory 持久化：保存用户入站 attachment metadata、缓存路径、描述和可用性状态；不持久化平台临时 URL 或 base64。
- [x] 增加缓存观测指标：记录 cached tokens/cache read tokens、显式 cache id 命中、图片 part 保留/降级原因、fallback vision 调用次数。
- [x] 补齐首批测试：能力解析、配置校验、Milky/Telegram attachment 转换、`_build_user_parts` 边界条件、`_persist_turns` 附件元数据、Agent parts 传递、Provider 多模态序列化。
- [x] 补齐后续测试：fallback 工具、自动 fallback、多轮追问、prompt cache 稳定序列化、资源过期、安全限制、Telegram opaque file_id 下载、缓存元数据损坏和混合附件顺序。

**参考实现**：见 [docs/architecture/provider-architecture.md](architecture/provider-architecture.md#f-图像理解与多模态上下文规划)。

实现核对/已发现偏差：

- `docs/architecture/provider-architecture.md` 曾描述 reasoning 上下文策略已完整实现；实际代码已有字段传播，但 `ContextBuilder` 和 `SessionRunner._load_history()` 尚未真正应用/恢复 reasoning、signature 和 metadata。这属于架构承诺尚未落地，不是优化性偏移。
- Milky 配置已有 `cache_media_on_receive`，ROADMAP 也提到“收到消息立刻缓存媒体”，但当前实现只注册了 `milky_get_resource_temp_url`，还没有入站缓存下载。这属于 planned 行为未落地。
- ~~Milky converter 当前把图片降级为文本并保留 raw event，避免了 Agent 层感知平台结构。这是合理的 MVP 优化，但需要在 Phase 2.9 中升级为第一类 attachment，否则会限制原生多模态能力。~~ 已完成：Milky 和 Telegram converter 均已填充 `InboundMessage.attachments`，同时保留文本降级。
- 2026-05-05 修复核对：`image_understand` 曾只注册但无法读取当前回合图片，属于架构实现缺口；现已通过 request context 保存当前 attachments，并从 Memory 恢复历史 attachments。Telegram 图片仅有 `file_id`、无 URL，属于平台能力差异；现通过 channel `download_media()` 钩子落成本地文件后再交给 `MediaResolver`。Milky 文本渲染曾暴露 `temp_url`，属于安全偏移；现已移除文本侧临时 URL，并在持久化层默认不保存 attachment URL。

前置依赖：

- Phase 2.8 Provider 抽象、模型切换和上下文构建链路。
- Phase 3.6 内置工具注册与执行闭环。
- Phase 4 Channel 媒体 segment 解析与资源 URL/缓存能力。

风险控制：

- 不要用模型名硬编码替代显式能力配置；模型能力变化必须可由配置覆盖。
- 不把带 token 的临时 URL、base64 图片或本地敏感路径写入日志/长期记忆。
- 不要为了“省历史”无条件删除最近图片。对支持图片缓存的 Provider，删除原生图片可能降低后续追问的 cache 命中率；应按 `media_context_policy`、预算、TTL 和缓存指标决策。
- fallback 描述是模型生成的二手信息，必须保留 `media_id` 和可用性状态，避免后续多轮把描述误当作原图。

参考来源：OpenAI/Anthropic/Gemini 多模态消息格式和 prompt/context cache 文档、LiteLLM 能力声明思路、现有 Milky/Telegram 媒体处理经验。

### Phase 3 - 插件系统与 Channel 接口定义

目标：建立声明式、可治理的插件系统，并定义 Channel 作为标准插件接口。

> 本阶段的关键设计决策：Channel 不是独立层，而是通过插件系统接入。这样可以复用权限模型、生命周期管理和能力注册机制。参考 OneBot/NapCat 等协议，定义统一的 Channel 接口，但支持多种底层通信方式（HTTP、WebSocket、SSE）。

任务清单：

#### Phase 3.1 — Manifest 与 Loader

- [x] 定义 `plugin.yaml` 字段模型（`PluginManifest` Pydantic 模型：id、name、version、entrypoint、load_phase、permissions、capabilities、config schema、depends_on）。
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

- [x] 实现 `BotAPI` 协议定义（send_message、on_event、subscribe、register_tool、register_command、session/model 管理、memory_search/store、workspace_read/write、logger）。
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

#### Phase 3.5 — Channel Service 接口与指令系统

- [x] 定义 `ChannelService` 运行时协议（`channel_id`、`handle_inbound_event`、`send_message`）。
- [x] Channel 注册通过 `isinstance(channel, ChannelService)` 运行时协议校验。
- [x] 实现消息事件类型（`MessageReceived`、`MessageSending`、`MessageSent`）。
- [x] 实现统一指令系统（`CommandRegistry` + `CommandMatcher`，前缀匹配、别名支持、@mention 剥离）。
- [x] `InboundMessage` 增加 `command_prefix` 字段，支持各平台自定义前缀。
- [x] 实现命令返回协议（`str | OutboundMessage | CommandResult | None`）和 router 级命令超时保护。
- [ ] 实现宿主 Web 扩展点（供插件挂载 webhook / route，而不是通过静态协议标签推导宿主行为）。
- [x] 实现消息标准化流程（平台原生事件 → `InboundMessage` → Agent → `OutboundMessage` → 平台回复）。

#### Phase 3.6 — 内置工具插件与验证

> 当前进度：**部分完成**。内置插件已落地 `workspace_read`、`workspace_write`、`exec`、`web_fetch`、`plan`、`cron_*`；`edit/apply_patch`、记忆工具、消息发送工具和插件配置体系仍待补齐。

> 以下工具清单参考 OpenClaw 的 31 个内置工具（`src/agents/tool-catalog.ts`），按依赖程度分为两类。
> 仅收录**不需要 Gateway-Node 分布式架构**即可独立实现的工具。
> 需要 Gateway-Node 的工具（`nodes`、`gateway`、`canvas`、`browser`）纳入 Phase 5 范围。
> Subagent 编排和跨会话管理可在本地单进程内实现，纳入 Phase 3.8。

**第一类 — 已有基础设施，可直接包装为工具插件：**

这些功能已在 Phase 2 中实现，只需通过插件系统的 `register_tool` 注册为可调用工具。

| 工具 ID | 功能 | 依赖现状 | 备注 |
|---------|------|---------|------|
| `read` | 读取工作空间文件 | Workspace sandbox 已实现 | 包装 `workspace.read_file()` |
| `write` | 创建/覆写工作空间文件 | Workspace sandbox 已实现 | 包装 `workspace.write_file()` |
| `edit` | 精确编辑文件（行级替换） | Workspace sandbox 已实现 | 需实现行级 diff 编辑逻辑 |
| `apply_patch` | 多 hunk 文件补丁 | Workspace sandbox 已实现 | 需实现 unified diff 解析与应用 |
| `memory_search` | 记忆语义检索 | MemoryStore 已实现 | 包装 `memory.search()`，已有 jieba 分词 |
| `memory_get` | 读取记忆文件 | MemoryStore 已实现 | 包装 `memory.get()` |

**第二类 — 无需 Gateway-Node，需新增实现：**

这些工具需要新的实现，但不依赖分布式架构，可在本地独立运行。

| 工具 ID | 功能 | 实现要点 | 优先级 | 备注 |
|---------|------|---------|--------|------|
| `exec` | 执行 shell 命令 | `asyncio.create_subprocess_exec` + 超时 + 输出截断 | P0 | ⚠️ 需权限控制（命令白名单/黑名单） |
| `web_search` | 网页搜索 | 调用搜索 API（SerpAPI / Bing / DuckDuckGo） | P0 | 可用 `duckduckgo-search` Python 包零成本起步 |
| `web_fetch` | 获取网页内容 | HTTP GET + HTML→Markdown（`readability-lxml` + `markdownify`） | P0 | 需 SSRF 防护（拒绝私有 IP 段） |
| `message` | 跨 Channel 发消息 | 调用已注册 channel service 的 `send_message` | P1 | 需路由层：target → channel + chat_id |
| `cron` | 定时任务调度 | 本地调度器（`APScheduler` / `asyncio` 定时器） | P1 | 本地模式不需要 Gateway |
| `tts` | 文本转语音 | API 调用（edge-tts 免费 / OpenAI TTS） | P2 | 可用 `edge-tts` 零成本起步 |
| `image_understand` | 图片理解 | 对非 vision 主模型调用 fallback vision Provider 生成描述；vision 主模型优先原生传图 | P2 | 详见 Phase 2.9 |
| `image_generate` | 图片生成 | 调用图片生成 API（DALL-E / Stable Diffusion / Flux） | P2 | 需配置图片 Provider |
| `code_execution` | 沙箱 Python 执行 | `subprocess` + 资源限制 + 输出截断 | P2 | 可参考 OpenClaw 的远程沙箱模式 |
| `x_search` | 搜索 X/Twitter | 调用 X API v2 | P3 | 需要 X API 凭证 |
| `music_generate` | 音乐生成 | 调用音乐生成 API | P3 | 需要 Provider 支持 |
| `video_generate` | 视频生成 | 调用视频生成 API | P3 | 需要 Provider 支持 |
| `update_plan` | 更新任务计划 | 本地状态管理 | P3 | Agent 内部计划维护 |

**需要 Gateway-Node（不在本阶段范围，纳入 Phase 5）：**

| 工具 ID | 功能 | 依赖原因 |
|---------|------|---------|
| `nodes` | 发现与操控配对设备 | 需要 Node 注册、心跳、远程执行协议 |
| `gateway` | 网关管理与配置 | 需要 Gateway 服务运行 |
| `canvas` | 驱动 Node Canvas 画布 | 需要 Node 端 Canvas 运行时 |
| `browser` | 浏览器自动化控制 | 需要 Playwright/Chromium 运行时，适合 Node 端；另有官方 Playwright MCP server 可直接对接 |

**实施建议：**

- P0 工具（`exec`、`web_search`、`web_fetch`）应优先实现，它们是 Agent 实用性的关键飞跃。
- 第一类工具（文件 I/O、记忆）可快速交付，复用已有基础设施。
- `exec` 必须配合严格的权限声明（`subprocess` 权限 + 命令审计），不可跳过权限校验。
- `web_fetch` 必须实现 SSRF 防护，拒绝 `127.0.0.0/8`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16` 等私有网段。
- `message` 工具可作为 channel service 跨消息路由的基础，先支持同 Channel 回复，再扩展跨 Channel。

任务清单：

- [ ] 第一类：将 workspace 文件操作（read/write/edit/apply_patch）注册为内置工具插件。
- [x] 第一类（已落地子集）：`workspace_read` / `workspace_write` 已注册为内置工具插件。
- [ ] 第一类：将 memory 操作（memory_search/memory_get）注册为内置工具插件。
- [x] 实现 `exec` 工具（shell 命令执行 + 超时 + 输出截断）。
- [ ] 实现 `web_search` 工具（DuckDuckGo 搜索 API 集成）。
- [x] 实现 `web_fetch` 工具（URL 抓取 + HTML→Markdown + SSRF 防护）。
- [ ] 实现 `message` 工具（通过 channel service 路由发送消息）。
- [x] 实现 `cron` 工具（本地定时任务调度）。
- [x] 实现 `plan` 工具（工作区内任务计划创建、查询与更新）。
- [ ] 实现插件配置解析（环境变量 `NAHIDA_PLUGIN_{ID}_{KEY}` + `config/plugins/{id}.yaml`，JSON Schema 校验）。
- [x] 验证不改核心代码可新增并加载外部插件。
- [ ] 验证越权行为可拦截、可追踪（权限拒绝触发 `PermissionDenied` + 审计日志）。
- [x] 验证插件崩溃不影响核心和其他插件（异常隔离测试通过）。

#### Phase 3.7 — SDK 分离（可选前置）

- [ ] 抽取 `nahida-bot-sdk` 独立包（Plugin 基类、BotAPI 协议、Manifest 模型、消息类型）。
- [ ] 实现 `MockBotAPI` 和测试 fixture（插件开发者无需启动 bot 即可单元测试）。
- [ ] 发布到 PyPI 或本地可安装（`uv` 可安装）。
- [ ] 验证：一个不依赖 `nahida-bot` 的插件可以安装 SDK 并完成编译 + 单元测试。

#### Phase 3.8 — Subagent 编排与跨会话管理

> Subagent 编排和跨会话操作不需要分布式架构。nahida-bot 是单进程 asyncio 模型，子 Agent 就是同一进程内独立的 `AgentLoop` 实例，会话数据全在本地 SQLite。因此这些能力可在本地完整实现，无需等待 Gateway-Node。
>
> 远程执行场景（如 GPU 节点上跑重模型）才需要 Phase 5 的 Gateway-Node。本地编排先落地，远程扩展作为 Phase 5 的增强。

**Subagent 编排（本地实现）：**

| 工具 ID | 功能 | 实现要点 |
| ------- | ---- | -------- |
| `agent_spawn` | 派生子 Agent | 创建新 `AgentLoop` 实例 + 独立 context，用 `asyncio.create_task` 并行运行 |
| `agent_yield` | 等待并获取子 Agent 结果 | 通过 `asyncio.Queue` / `asyncio.Future` 在父子间传递结果 |
| `agent_list` | 列出所有活跃 Agent | 维护 `dict[agent_id, Task]` 本地注册表 |
| `agent_stop` | 终止子 Agent | 取消对应 `asyncio.Task`，清理资源 |

**跨会话管理（本地实现）：**

| 工具 ID | 功能 | 实现要点 |
| ------- | ---- | -------- |
| `sessions_list` | 列出所有会话 | 查询本地 `MemoryStore` / SQLite |
| `sessions_history` | 读取任意会话历史 | 查询 `MemoryStore.get_turns()` |
| `sessions_send` | 向指定会话注入消息 | 在目标会话上下文中追加消息，触发其 AgentLoop |
| `session_status` | 查询会话运行状态 | 查本地注册表（AgentLoop 是否活跃、当前步骤等） |

任务清单：

- [ ] 实现 `AgentRegistry`（`dict[agent_id, AgentTask]` 注册表，跟踪父子关系、状态、结果）。
- [ ] 实现 `agent_spawn` 工具（创建子 AgentLoop + 独立 context + system prompt，返回 agent_id）。
- [ ] 实现 `agent_yield` 工具（等待指定子 Agent 完成，返回其最终回复）。
- [ ] 实现 `agent_list` 工具（列出所有活跃子 Agent 及其状态）。
- [ ] 实现 `agent_stop` 工具（取消指定子 Agent 的 asyncio.Task）。
- [ ] 实现 `sessions_list` 工具（列出所有会话及其元数据）。
- [ ] 实现 `sessions_history` 工具（读取指定会话的对话历史）。
- [ ] 实现 `sessions_send` 工具（向指定会话注入消息并触发响应）。
- [ ] 实现 `session_status` 工具（查询会话是否活跃、当前进度等）。
- [ ] 实现子 Agent 资源限制（最大并发数、单 Agent 超时、总 token 预算）。
- [ ] 验证父子 Agent 并行运行、结果正确传递。
- [ ] 验证子 Agent 异常不影响父 Agent 和其他子 Agent（异常隔离）。

**ChannelService 接口设计（关键产出物）**：

Channel 应该以普通 Plugin 的形式暴露运行时服务：

```python
class ChannelService(Protocol):
    @property
    def channel_id(self) -> str: ...

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """来自外部平台的事件回调（触发方式由插件自己决定）。"""
        ...

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """向外部平台发送消息，返回消息 ID。"""
        ...
```

普通 `Plugin` 在 `on_load()` 中通过 `api.register_channel(self)` 显式注册自己为
channel service；注册时通过 `isinstance(channel, ChannelService)` 校验协议满足。

**设计收敛说明**：

- 宿主不再试图用一组固定的“通信协议标签”描述 Channel 插件的内部实现。
- Channel 插件可以直接使用第三方 SDK、自带 HTTP client、长轮询、webhook、WebSocket 或其它机制；这些属于插件内部实现细节。
- 宿主真正关心的是两个问题：
  1. 这个插件是否显式注册了一个 `ChannelService`
  2. 这个插件是否需要宿主额外提供某种扩展点或共享基础设施

**长期规划：宿主扩展点与共享基础设施**：

当插件确实需要复用宿主能力时，不再通过 `channel_protocols` 之类的静态标签声明，而是通过显式的 host service / extension point 暴露：

1. **Web Host 扩展点**
   - 目标：允许插件挂载 webhook、辅助 route、或少量诊断端点。
   - 建议形态：`api.mount_router(...)`、`api.register_webhook_endpoint(...)` 或更抽象的 `WebHostService`。
   - 设计原则：插件依赖的是“宿主提供可挂载的 Web 入口”，而不是直接依赖“宿主当前用 FastAPI”这一实现细节。

2. **共享 HTTP Client 服务**
   - 目标：让插件在需要时复用连接池、代理、审计、超时和统一出站策略。
   - 建议形态：`api.get_http_client()` 或 `HttpClientService`。
   - 设计原则：插件既可以完全自带 SDK/client，也可以选择使用宿主提供的共享客户端；两者都应被允许。

3. **其它可复用宿主服务**
   - 候选范围：scheduler、secrets/config、对象存储、审计日志、后台任务执行。
   - 原则：只有当宿主提供这些能力能显著降低插件重复实现和运维成本时，才上升为正式扩展点。

**约束**：

- 插件的“角色”不由 manifest 分类字段决定，而由运行时注册动作决定。
- Channel / Provider / Tool / Command 不是互斥类别；一个普通 Plugin 可以同时注册多种能力。
- 若未来需要展示层分类，优先使用文档或 tags，而不是重新引入驱动运行时语义的 `type` 字段。

前置依赖：Phase 2。

风险控制：

- 不要为了"插件方便"绕过权限检查，channel service plugin 仍需声明所需权限。
- channel service plugin 的 webhook 端点需要鉴权与频率限制，防止欺骗。
- 多 Channel 并存时需要隔离会话上下文，避免消息混淆。

参考来源：OpenClaw（插件 contract）、nonebot2（扩展生态）、OneBot 协议、NapCat 设计、Android Manifest 思路。

#### Phase 3.9 — Provider Plugin（模型 Provider 插件化）

> 当前进度：**部分完成**。运行时 Provider 注册、阶段化插件加载、Provider Registry 扩展和生命周期清理都已落地；后续重点应放在 provider 配置/校验与宿主扩展点，而不是再引入专用 `ProviderPlugin` 基类。

**当前状态**：

1. **Manifest 已支持 provider 加载时序** — `PluginManifest.load_phase` 字段驱动 pre-agent/post-agent 分阶段加载。
2. **BotAPI/RealBotAPI 已支持 Provider 注册接口** — 插件可通过 `register_provider_type()` 注册运行时 Provider。
3. **初始化顺序已调整** — `Application.initialize()` 先发现插件、加载 `pre-agent` 插件，再创建 ProviderManager。
4. **Provider Registry 已可运行时扩展** — 除静态 `_REGISTRY` 外，已有可卸载的 `_RUNTIME_REGISTRY`。

**设计方案**：

**1. Manifest 扩展（可选，偏配置与展示）**

```yaml
# plugin.yaml 示例
id: "provider-ollama"
name: "Ollama Provider"
load_phase: "pre-agent"    # Provider 插件必须在 Agent 初始化前加载
version: "0.1.0"
entrypoint: "plugin:OllamaPlugin"

# 可选 provider metadata：仅用于 host 侧配置描述/展示，
# 不作为运行时“这是 provider 插件”的判定条件
provider:
  type_key: "ollama"      # 注册到 create_provider() 的 type 名称
  config_schema:          # JSON Schema：声明此 Provider 接受哪些配置项
    type: object
    required: ["base_url", "models"]
    properties:
      base_url: { type: string, default: "http://localhost:11434/v1" }
      models:
        type: array
        items: { type: string }
        default: ["llama3"]
      timeout: { type: number, default: 60 }
```

**2. 普通 Plugin + register_provider_type()**

```python
class OllamaPlugin(Plugin):
    def create_provider(self, config: dict[str, Any]) -> ChatProvider:
        return OllamaProvider(config)

    async def on_load(self) -> None:
        self.api.register_provider_type(
            type_key="ollama",
            factory=self.create_provider,
            config_schema={
                "type": "object",
                "required": ["base_url", "model"],
                "properties": {
                    "base_url": {"type": "string"},
                    "model": {"type": "string"},
                },
            },
        )
```

**3. BotAPI 扩展**

```python
# BotAPI 协议新增
def register_provider_type(
    self,
    type_key: str,
    factory: Callable[[dict], ChatProvider],
    config_schema: dict | None = None,
) -> None:
    """注册一个 Provider 类型，使其可在 YAML 配置中使用。"""
    ...
```

`RealBotAPI` 实现将调用委托给 `ProviderRegistry.register_runtime()`（新增方法，区别于 `@register_provider` 的 static registration）。

**4. 初始化顺序调整（两阶段）**

```text
Application.initialize():
  1. _init_database() + _init_memory()
  2. _init_plugin_manager()           # 创建 PluginManager
  3. _load_provider_plugins()         # ← 新增：发现并加载 load_phase=pre-agent 的插件
  4. _init_agent_subsystem()          # 现在可用插件注册的 Provider 类型
  5. _init_workspace_subsystem()
  6. _load_remaining_plugins()        # 加载 tool/channel/hook 等插件
  7. _init_scheduler()
```

关键变更：将 `_init_agent_subsystem()` 从当前位置（plugin loading 之前）移到 provider plugin 加载之后。这需要把 `_init_plugin_manager()` 拆分为两个阶段。

**5. Provider Registry 运行时扩展**

```python
class ProviderRegistry:
    _static: dict[str, ProviderDescriptor] = {}     # @register_provider 装饰器填充
    _runtime: dict[str, RuntimeProviderDescriptor] = {}  # 插件 register_provider_type() 填充

    def create_provider(self, type_key: str, **kwargs) -> ChatProvider:
        # 优先查 static（内置），fallback 到 runtime（插件）
        desc = self._static.get(type_key) or self._runtime.get(type_key)
        if desc is None:
            raise ValueError(f"Unknown provider type: {type_key}")
        return desc.cls(**kwargs) if desc.cls else desc.factory(kwargs)
```

**6. 配置集成**

插件注册的 Provider 与内置 Provider 在配置中完全等价：

```yaml
providers:
  deepseek-main:
    type: deepseek           # 内置
    api_key: "${DEEPSEEK_API_KEY}"
    ...
  ollama-local:
    type: ollama             # ← 由 provider-ollama 插件注册
    base_url: "http://localhost:11434/v1"
    model: "llama3"
```

**任务清单**：

- [ ] 评估是否为 `PluginManifest` 增加可选 `provider` metadata（如 `type_key` + `config_schema`，仅用于 host 侧配置描述/展示）。
- [x] `BotAPI` 协议新增 `register_provider_type()` 方法。
- [x] `RealBotAPI` 实现 `register_provider_type()`，委托给 Provider Registry。
- [x] Provider Registry 支持运行时注册（`_RUNTIME_REGISTRY` + `create_provider` 合并查找）。
- [x] `Application.initialize()` 拆分初始化顺序：先加载 `pre-agent` 插件，再创建 ProviderManager。
- [x] 插件清理：provider 插件 disable/unload 时从 Registry 移除 `type_key`。
- [ ] 编写 Provider 注册集成测试（插件注册 → 配置解析 → Provider 创建 → chat 调用）。
- [ ] 编写示例 provider 插件（如 `provider-ollama`）作为开发者参考。
- [ ] 更新 architecture 文档中的 Provider 和 Plugin 文档。

**风险控制**：

- Provider 插件注册的 `type_key` 不可与内置类型冲突（`deepseek`、`anthropic`、`openai-compatible` 等）。
- Provider 插件的生命周期必须与 AgentLoop 解耦：插件卸载不应中断正在进行的请求。
- `config_schema` 校验应在 Provider 创建前执行，错误配置不应导致启动崩溃（应 skip 并记录警告）。
- 安全考虑：Provider 插件处理 API key 等敏感信息，权限系统需增加 `provider` 能力声明。

> **架构反思：加载时序与插件类型泛化**
>
> Provider Plugin 的核心难题是**加载时序**——ProviderManager 必须在插件注册 Provider 类型**之后**创建，但常规插件的加载又在 ProviderManager 创建之后。这个"先有鸡还是先有蛋"的问题在插件系统中很常见，典型解法有四种：
>
> **1. 阶段化加载（Phase-based loading）**
>
> 为插件 manifest 引入 `load_phase` 字段，定义显式加载阶段（如 `pre-agent` / `post-agent`）。Loader 按 phase 顺序依次处理。Provider 插件声明 `load_phase: pre-agent`，普通插件默认 `post-agent`。这是最实用的方案——语义清晰，实现简单，且对现有流程改动最小。`Application.initialize()` 只需把 `_load_plugins()` 拆成 `_load_plugins(phase="pre-agent")` 和 `_load_plugins(phase="post-agent")` 两步。
>
> **2. 依赖声明（Dependency declaration）**
>
> 插件声明 `provides: ["provider:ollama"]` 和 `requires: ["subsystem:agent"]`，Loader 做拓扑排序决定加载顺序。更灵活但更复杂——需要定义服务命名空间、循环依赖检测、缺失依赖处理等。对只有两三个阶段需求的系统来说过度设计。
>
> **3. 惰性初始化（Lazy initialization）**
>
> ProviderManager 不在启动时创建，而是在第一次 `SessionRunner.run()` 被调用时按需构建。这样所有插件都可以在常规阶段加载，`register_provider_type()` 注册的类型在第一次请求时被消费。优点是不需要修改初始化流程；缺点是 Provider 配置错误不会在启动时暴露，而是在第一次对话时才失败，增加了运维排查成本。
>
> **4. 两遍扫描（Two-pass discovery）**
>
> 第一遍扫描所有插件目录，收集 manifest 但不加载代码，只提取类型和能力声明。第二遍根据收集到的信息决定加载顺序。优点是可以在不执行插件代码的情况下做全局规划；缺点是实现复杂，且 manifest 必须包含足够的信息来驱动决策（当前的 manifest 设计不完全满足这个需求）。
>
> **推荐方案：阶段化加载（方案 1）**。理由：实现成本最低、语义最直观、对现有架构改动最小。一个 `load_phase` 字段 + `_load_plugins()` 接受 phase 参数即可。如果未来出现更多阶段需求（如 `pre-memory`、`pre-scheduler`），phase 枚举自然扩展。
>
> **关于插件类型的泛化**：Channel 和 Provider 的特殊性不应体现在“独立插件基类”或“静态协议标签”上，而应体现在“普通 Plugin 在生命周期里显式注册服务”。普通插件注册回调、核心调用（Tool、Command、Event）；channel/provider 插件则额外通过 `api.register_channel()` / `api.register_provider_type()` 暴露运行时服务。Provider 的时序需求由 `load_phase` 建模，Channel 通过运行时协议校验确保注册对象合法，二者都不再要求专门基类。

> **长期规划：Host 扩展点优先于协议分类**。后续如果需要支持 webhook 挂载、共享 HTTP client、后台任务、统一 secrets/config 等能力，优先新增明确的 host service / extension point，而不是重新引入 `type`、`channel_protocols` 这类静态分类字段。宿主应该表达“我能提供什么能力”，而不是试图推断“插件内部用了什么传输协议或 SDK”。

### Phase 4 - 基于 Channel Service Plugin 的 Telegram 接入 + Multi-Provider + 内置命令

目标：实现 Telegram Bot 接入、多 Provider 支持、以及核心命令插件。

> Phase 4 已完成。系统现在支持：Telegram 长轮询消息接收、多 LLM Provider 动态切换、
> 内置命令（/reset, /new, /status, /model, /help）、会话管理、以及完整的资源清理。

任务清单：

#### Phase 4.1 — Telegram Bot API 基础

- [x] 封装 Telegram Bot HTTP API 客户端（aiogram v3 Bot 封装）。
- [x] 实现 Long Polling 模式（`getUpdates` 轮询 + offset 追踪 + 超时处理）。
- [x] 实现 Telegram Update → 内部事件分发（text message 处理）。
- [x] 处理 Telegram Rate Limiting（429 响应 + Retry-After + 指数退避）。

#### Phase 4.2 — 消息标准化与转换

- [x] 实现 Telegram Message → `InboundMessage` 转换（文本、群聊/私聊、回复关系、@mention 提取）。
- [x] 实现 `OutboundMessage` → Telegram 发送（纯文本、回复、HTML 解析模式）。
- [x] 实现会话映射策略（Telegram chat_id → 内部 session_id）。
- [x] 处理 Telegram 特殊消息类型（sticker、photo、document、video、audio、voice、animation 等降级为文本描述）。

#### Phase 4.3 — Channel Service 集成

- [x] 实现 `TelegramPlugin(Plugin + ChannelService)` 类。
- [x] 实现 `plugin.yaml` manifest（声明 network 出站权限至 api.telegram.org）。
- [x] 实现 `on_load` / `on_enable` / `on_disable` 生命周期（启动/停止轮询）。
- [x] 实现 `handle_inbound_event` 将 Telegram Update 分发到消息处理流程。
- [x] 实现 `send_message` 通过 Bot API 发送回复。

#### Phase 4.4 — 端到端闭环验证

- [x] 打通完整链路：Telegram Update → InboundMessage → Agent Loop → OutboundMessage → Telegram 回复。
- [x] 验证群聊 @mention 触发与私聊自动回复。
- [x] 验证指令系统（/help 等命令通过 CommandMatcher 正确匹配）。
- [x] 验证插件生命周期（热重载、异常隔离、优雅关闭）。
- [x] 编写 Telegram API 客户端和消息转换的单元测试。

#### Phase 4.5 — Multi-Provider 支持与内置命令

- [x] 实现 `ProviderManager`：多 LLM Provider 注册、按 id 或 model 名称解析。
- [x] 实现 `ProviderEntryConfig`：dict 格式的多 provider 配置（key 即 provider id），`models[0]` 为 provider 默认模型；旧单 provider `provider` 配置、provider 级 `model/capabilities/model_capabilities` 和运行时 `ProviderSlot.capabilities` 已废弃移除，能力只按模型解析。
- [x] 实现 per-request provider 切换：session metadata 存储模型偏好，MessageRouter 解析。
- [x] 实现 `BuiltinCommandsPlugin` 内置命令插件：
  - `/reset` 清空当前会话历史
  - `/new` 开始新会话
  - `/status` 查看当前会话和模型信息
  - `/model` 列出/切换模型
  - `/help` 列出所有命令
- [x] 实现 session 管理原语：`clear_session`、`list_sessions`、`get/update_session_meta`。
- [x] 添加 DB migration 002（sessions.metadata_json 列）。
- [x] 修复 Windows Ctrl+C 挂起问题（DatabaseEngine 和 Provider 资源清理）。
- [x] 实现 per-request model override：`ChatProvider.chat()` 接受 `model` 参数覆盖默认模型；`SessionRunner` 从会话 metadata 中解析选中的模型名称并传递给 `AgentLoop.run()`，确保同一 Provider 下多模型切换真正生效。

#### Phase 4.6 — Milky QQ Channel Plugin（临时新增计划项）

目标：基于 Milky 协议和 Lagrange.Milky 实现一个 QQ Channel 插件，复用现有 `Plugin + ChannelService` 运行时模型，打通 QQ 私聊/群聊消息到 Agent 的完整闭环。

> 设计决策：不复用 `milky-python-sdk` 的 Client 或 Bot 框架。Nahida Bot 侧直接实现 Milky HTTP API client 和 WebSocket `/event` 事件流；仅参考 `milky-python-sdk` 的消息结构、segment 命名和类型建模方式，避免把第三方生命周期、事件分发和命令系统引入 Nahida。

> 参考事实：Milky 协议端通过 `/api/:api` 接收 HTTP POST API 调用，通过 `/event` 推送事件，事件传输支持 SSE、WebSocket、WebHook；Lagrange.Milky 当前实现 WebSocket 和 WebHook，SSE 标记为 wontimpl。因此 Nahida Bot 侧优先实现 WebSocket client 模式，WebHook 等待 Host Web 扩展点成熟后再接入。Milky 与 OneBot 不同，当前没有定义可在同一条 WebSocket 上同时承载 API 请求和事件响应的 `{action, params, echo}` RPC envelope。

##### Phase 4.6.1 — 目录与依赖基线

- [x] 新增 `nahida_bot/channels/milky/` 目录。
- [x] 新增 `__init__.py`，导出 `MilkyPlugin`。
- [x] 新增 `plugin.yaml`，`id` 使用 `milky`，`entrypoint` 指向 `nahida_bot.channels.milky.plugin:MilkyPlugin`。
- [x] 评估并加入最小运行依赖：HTTP 使用现有可用异步 HTTP 客户端（优先复用项目已有依赖），WebSocket 使用项目现有依赖或补充 `websockets`。（当前 `pyproject.toml` 已包含 `httpx` 与 `websockets`，无需新增依赖。）
- [x] 明确首版不引入 `milky-python-sdk` 作为运行时依赖。

##### Phase 4.6.2 — 配置模型

- [x] 定义 `MilkyPluginConfig`：`base_url`、`access_token`、`api_prefix`、`event_path`、`command_prefix`、`allowed_friends`、`allowed_groups`。
- [x] 定义 WebSocket 配置：`ws_url` 可选覆盖、`connect_timeout`、`heartbeat_timeout`、`reconnect_initial_delay`、`reconnect_max_delay`。
- [x] 定义群聊触发策略：`group_trigger_mode` 支持 `mention`、`command`、`always`，默认 `mention`。
- [x] 定义发送策略：`send_retry_attempts`、`send_retry_backoff`、`max_text_length`。
- [x] 定义媒体策略：`media_download_dir`、`enable_media_download_tool`、`resource_url_ttl_hint`。

##### Phase 4.6.3 — Milky HTTP API Client

- [x] 新增 `client.py`，实现轻量 `MilkyClient`，不依赖 `milky-python-sdk`。
- [x] 封装 `post_api(api_name: str, payload: dict[str, Any]) -> dict[str, Any]`，请求路径为 `{base_url}/{api_prefix}/{api_name}` 或 Milky 标准 `/api/:api`。
- [x] 支持 `access_token` 鉴权，按 Milky/Lagrange.Milky 文档实现 `Authorization: Bearer {access_token}`。
- [x] 统一处理 HTTP 状态码、Milky API 错误响应、JSON 解析错误和超时。
- [x] 定义 `MilkyClientError`、`MilkyAuthError`、`MilkyAPIError`、`MilkyNetworkError`、`MilkyHTTPStatusError`、`MilkyResponseError`。
- [x] 实现基础 API 方法：`get_login_info()`、`get_impl_info()`、`send_private_message()`、`send_group_message()`、`get_resource_temp_url()`、`get_forwarded_messages()`。
- [x] 实现文件 API 基础方法：`upload_private_file()`、`upload_group_file()`、`get_private_file_download_url()`、`get_group_file_download_url()`。
- [x] 为发送消息实现有限重试，避免对非幂等 API 进行不可控重放；首版仅对明确的连接类网络错误和 HTTP 429 重试。
- [x] 支持 `close()`，确保插件禁用时释放连接池。

##### Phase 4.6.4 — 消息结构与 Segment 建模

- [x] 新增 `segments.py`，用 dataclass 建模 Milky incoming/outgoing segment。
- [x] 参考 `milky-python-sdk` 的结构命名和字段组织，但实现本项目自己的类型，避免依赖其 Client。
- [x] 覆盖首版 incoming segment：`text`、`mention`、`mention_all`、`reply`、`image`、`record`、`video`、`file`、`forward`、`market_face`、`light_app`、`xml`、未知 segment。
- [x] 覆盖首版 outgoing segment：`text`、`mention`、`mention_all`、`face`、`reply`、`image`、`record`、`video`、`forward`、`light_app`。
- [x] 将文件发送建模为独立 `OutgoingFileUpload`，避免误把文件当作 `send_message` 的 message segment；后续由 Milky file API 负责实际发送。
- [x] 为合并转发建模 `IncomingForwardSegment` / `IncomingForwardedMessage` 与 `OutgoingForwardSegment` / `OutgoingForwardedMessage`，支持多层嵌套和渲染深度限制。
- [x] 未识别 segment 必须保留原始 dict，转换为可读降级文本，不允许静默丢弃。
- [x] 为 segment parser 增加单元测试，固定 Milky dict 与内部结构之间的转换样例。

> 后续实现提醒：入站 `forward` 段只是 `forward_id` 引用，真正内容需要 Phase 4.6.3 的 client 支持 `get_forwarded_messages()` 后在 Phase 4.6.6 里递归拉取并填充到 `IncomingForwardSegment.messages`。递归必须受 `max_forward_depth`、`max_forward_messages` 和 `forward_render_max_chars` 限制。

##### Phase 4.6.5 — WebSocket Event Stream

- [x] 新增 `event_stream.py`，实现 `MilkyEventStream`。
- [x] 连接 Lagrange.Milky WebSocket `/event`，只接收事件，不通过该连接发送 API 请求。
- [x] 支持 access token 鉴权，和 HTTP client 使用同一份配置；优先使用 `Authorization: Bearer {access_token}`，在默认 connector 不支持 header 参数时可退回 query token。
- [x] 接收 JSON 文本帧，解析为 `dict[str, Any]` 后交给回调。
- [x] 实现断线重连和指数退避，记录 `milky.ws_connected`、`milky.ws_disconnected`、`milky.ws_reconnect_scheduled`。
- [x] 支持 `start()` / `stop()`，`stop()` 必须能取消任务并关闭 socket，不能阻塞应用退出。
- [x] 忽略或记录非消息事件：`bot_online`、`bot_offline`、`message_recall` 等，后续按需要扩展。
- [x] 暂不实现 SSE；WebHook 模式依赖 Phase 5.x `WebHostService`，不在 MVP 中临时启动独立 HTTP server。

##### Phase 4.6.6 — 入站消息转换

- [x] 新增 `message_converter.py`，实现 `MilkyMessageConverter`。
- [x] 支持 Milky `message_receive` 事件。
- [x] 将私聊消息转为 `InboundMessage(platform="milky", is_group=False)`。
- [x] 将群聊消息转为 `InboundMessage(platform="milky", is_group=True)`。
- [x] 映射字段：`message_seq` -> `message_id`，`peer_id` -> `chat_id`，`sender_id` -> `user_id`，事件时间 -> `timestamp`。
- [x] 将 `reply` segment 映射到 `reply_to`。
- [x] 将 `text` segment 拼接为正文。
- [x] 将 `mention` segment 转为可读 `@QQ号`；如果 mention 目标是当前 bot 且群聊触发策略需要，则剥离该 mention。
- [x] 将图片、文件、语音、视频等媒体 segment 降级为 `[Media: type=..., resource_id=...]` 文本，原始数据保留在 `raw_event`。
- [x] 支持 `allowed_friends` / `allowed_groups` 过滤。
- [x] 支持 `group_trigger_mode`：群聊默认只响应 @bot 或命令前缀，避免无差别响应群消息。
- [x] 支持基于 `get_forwarded_messages()` 的合并转发递归解析，并受 `max_forward_depth`、`max_forward_messages`、`forward_render_max_chars` 限制。
- [x] 对 Lagrange.Milky 暂未实现合并转发拉取的情况做容错，保留 forward 引用与 preview 文本，不中断入站消息处理。

##### Phase 4.6.7 — 出站消息转换与发送

- [x] 新增 `segment_converter.py`，实现 `OutboundMessage` -> Milky outgoing segments / file upload payloads。
- [x] 首版文本回复转换为 `text` segment。
- [x] 如果 `OutboundMessage.reply_to` 存在，前置 `reply` segment。
- [x] 支持通过 `OutboundMessage.extra` 显式传递 `message_scene`（`friend` / `group`）和 `peer_id`。
- [x] `send_message(target, message)` 支持目标前缀约定：`friend:<id>`、`group:<id>`；无前缀时根据 `message.extra` 或最近入站会话场景推断。
- [x] 私聊调用 `send_private_message()`，群聊调用 `send_group_message()`。
- [x] 返回 Milky `message_seq` 或协议端返回的等价消息 id。
- [x] 图片、语音、视频附件转换为对应 outgoing media segment；文件附件转换为 `OutgoingFileUpload` 并走 Milky file API。
- [x] 支持通过 `OutboundMessage.extra["milky_forward"]` / `extra["milky_segments"]` 发送合并转发与 Milky 原生媒体段。
- [x] 对 Lagrange.Milky 暂未实现 rich segment 发送的情况做容错，发送失败时降级为纯文本摘要再重试。

##### Phase 4.6.8 — Plugin 生命周期集成

- [x] 新增 `plugin.py`，实现 `MilkyPlugin(Plugin)` 并满足 `ChannelService` 运行时协议。
- [x] `channel_id` 返回 `"milky"`。
- [x] `on_load()`：解析配置、创建 `MilkyClient`、调用 `get_login_info()` 获取 bot id、初始化 converter、注册 channel。
- [x] `on_enable()`：启动 `MilkyEventStream` 后台任务，注册可选媒体工具。
- [x] `on_disable()`：停止 event stream，关闭 HTTP client。
- [x] `handle_inbound_event()`：过滤事件类型，转换 `InboundMessage`，生成 session id，发布 `MessageReceived`。
- [x] `send_message()`：调用出站转换与 API client。
- [x] 日志字段统一包含 `channel="milky"`，后续真实联调时继续补齐 `peer_id`、`message_scene`、`message_seq` 的发送/接收细节日志。

##### Phase 4.6.9 — 媒体资源工具

> 注意：Lagrange.milky 的媒体部分可能会有问题，需要在收到消息的时候立刻把其中的媒体文件缓存下来，否则 URL 可能会过期。目前暂不确定这个是 milky-tea 的问题还是 Lagrange.milky 的问题，这里可能需要处理。
>
> 注 2：检查了一下 milky 的文档，似乎图片的预期就是一个临时的 URL ，但是 Milky 又确实提供了一个通过 resource_id 获取 temp_url 的 api 端点，这里可能需要考虑一下。

- [x] 注册 `get_resource_temp_url` 工具：基于 Milky `resource_id` 获取临时 URL。（当前工具名为 `milky_get_resource_temp_url`，避免与其他 channel 的工具冲突。）
- [ ] 评估是否实现 `download_media` 工具；如果实现，下载目录沿用 Telegram 的 `media_download_dir` 思路。
- [x] 工具返回 JSON，包含 `resource_id`、`url`、`expires_hint` 等字段；`path`、`file_size` 留待 `download_media` 工具实现。
- [ ] 媒体工具必须处理 access token、下载失败、资源过期和文件大小限制。（临时 URL 获取已走 `MilkyClient` 鉴权；下载失败、文件大小限制留待 `download_media`。）

##### Phase 4.6.10 — 测试与文档

- [x] 编写 `tests/channels/milky/test_client.py`：API 成功、API 错误、鉴权错误、网络错误、超时。（当前文件为 `tests/test_milky_client.py`）
- [x] 编写 `tests/channels/milky/test_segments.py`：incoming/outgoing segment 解析和未知 segment 降级。（当前文件为 `tests/test_milky_segments.py`）
- [x] 编写 `tests/channels/milky/test_message_converter.py`：私聊、群聊、@bot、reply、媒体降级、允许列表过滤。（当前文件为 `tests/test_milky_message_converter.py`）
- [x] 编写 `tests/channels/milky/test_event_stream.py`：事件帧解析、断线重连、取消退出。（当前文件为 `tests/test_milky_event_stream.py`）
- [x] 编写 `tests/channels/milky/test_plugin.py`：`on_load` 注册 channel、`handle_inbound_event` 发布 `MessageReceived`、`send_message` 私聊/群聊路由。（当前文件为 `tests/test_milky_plugin.py`）
- [ ] 编写 `docs/channels/milky.md`：覆盖 Lagrange.Milky `appsettings.jsonc` 示例、Nahida Bot 插件配置、网络安全建议、常见调试项和已知不支持项。
- [x] 在 ROADMAP 或架构文档中记录与 OneBot 单 WebSocket RPC 的差异，避免后续误判 Milky `/event` 能发送 API 请求。

MVP 验收：

- [ ] 使用本地 Lagrange.Milky WebSocket 模式接收 QQ 私聊消息并回复。
- [ ] 使用本地 Lagrange.Milky WebSocket 模式在群聊中通过 @ 或命令前缀触发并回复。
- [ ] 文本、@、回复、图片资源描述至少能稳定进入 Agent 上下文；文本回复能稳定发回 QQ。
- [ ] 断线重连不会阻塞应用关闭；access token 错误、API 错误和协议端离线均有可读日志。
- [ ] 不依赖 `milky-python-sdk` 运行时 Client；消息结构实现可追溯到 Milky 官方文档，并参考 `milky-python-sdk` 的类型设计。

前置依赖：

- Phase 3.5 `ChannelService` 接口与消息标准化流程。
- Phase 4 Telegram Channel 的插件生命周期、发送重试和媒体工具经验。
- Phase 5.x `WebHostService`（仅 WebHook 模式需要；WebSocket MVP 不阻塞）。

风险控制：

- Milky 协议端本身提供 HTTP 服务，默认只建议连接 `127.0.0.1` 或内网地址，必须支持 `access_token`。
- QQ 消息段比当前 `InboundMessage.text` 更丰富，首版不得丢弃原始事件，所有未完全支持的段必须保存在 `raw_event` 并以结构化文本降级。
- Lagrange.Milky 对部分 Milky 能力标记为 wontimpl，插件实现必须以能力探测和错误降级为准，不能假设完整协议覆盖。
- WebHook 模式不要绕过插件宿主扩展点临时开 HTTP server，避免生命周期、鉴权和端口管理分裂。
- 自写 HTTP/WebSocket 客户端需要补齐错误处理和测试，避免把协议细节散落在 `plugin.py` 中。

参考来源：

- Milky 协议文档：`https://milky.ntqqrev.org/`
- Lagrange.Milky README：`https://github.com/LagrangeDev/LagrangeV2/blob/main/Lagrange.Milky/README.md`
- Lagrange.Milky 配置文档：`https://lagrangedev.github.io/Lagrange.Milky.Document/configuration/overview`
- `milky-python-sdk`：仅参考消息结构设计，不作为运行时 Client 依赖，`https://github.com/notnotype/milky-python-sdk`

前置依赖：Phase 3（ChannelService 接口）。

风险控制：

- 平台差异统一收敛在 channel service plugin 实现内部，不渗透进 Agent 核心或其他插件。
- 第一个 channel service plugin 的稳定性直接影响用户体验，务必包含充分的测试和监控。
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

#### Phase 5.x — Host Extension Points（长期规划）

> 这部分不是近期工作，放入长期规划。目标是在不破坏“普通 Plugin + 显式服务注册”模型的前提下，为插件提供少量高价值的宿主能力。

- [ ] 设计 `WebHostService`：支持插件安全挂载 webhook / route，并统一鉴权、限流、生命周期和可观测性。
- [ ] 设计共享 `HttpClientService`：支持插件复用连接池、代理、超时、审计和统一出站策略。
- [ ] 评估 `SchedulerService` / `BackgroundTaskService` 是否需要升级为通用插件扩展点。
- [ ] 评估配置与 secrets 注入能力，避免插件各自实现环境变量/配置文件解析逻辑。
- [ ] 明确宿主扩展点 API 稳定性策略：哪些是公共契约，哪些仍是核心内部实现。
- [ ] 验证“自带 SDK 的插件”和“依赖宿主服务的插件”可以长期共存，不互相绑死实现方式。

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
4. **Workspace Sandbox 安全加固（Phase 2.7）** ⚠️ 安全闸门
5. **Provider 响应健壮性增强（Phase 2.8）** ⚠️ 推荐在 Phase 3 前完成
6. 插件系统与 Channel 接口定义（Phase 3）
7. 基于插件系统的 Channel 实现（Phase 4）
8. Subagent 编排与跨会话管理（Phase 3.8，可在 Phase 4 之后或并行推进）
9. Provider 插件化（Phase 3.9，可在 Phase 4 之后或并行推进）
10. Gateway 与 Node（Phase 5）
11. WebUI 与运维工具（Phase 6）
12. 稳定性、发布与生态（Phase 7）

这个顺序的核心原因是：

- **Phase 0-2.6** 建立最小智能闭环（应用容器 -> 核心运行时 -> Agent + Workspace）
- **Phase 2.7-2.8** 安全与健壮性加固（Phase 2.8 已完成；Phase 2.7 作为开放不可信插件/远程执行前的安全闸门）
- **Phase 3-4** 打通插件和 Channel（先定义接口，允许多种通信协议；再实现具体 Channel 作为插件）
- **Phase 3.8** 本地 Subagent 编排和跨会话管理（不依赖 Gateway-Node，单进程 asyncio 即可实现）
- **Phase 3.9** Provider 插件化（扩展插件系统支持第三方 Provider 注册）
- **Phase 5-6** 扩展分布式与运维（Gateway-Node + WebUI）
- **Phase 7** 稳定化与商业化（发版、CI/CD、生态）

关键设计点：

- **Phase 2.7 是安全闸门**：不安全的沙盒会威胁整个系统安全。为快速形成可运行 MVP，可信本地插件和 Telegram 接入可先推进；但开放不可信第三方插件、远程执行、文件写工具扩权前必须修复。
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
- Workspace 机制是项目的核心资产。当前可在可信本地 MVP 中先保持简单沙盒；任何面向不可信插件、远程节点或高权限文件工具的能力，都必须先补齐文件安全边界。
- Gateway-Node 协议一旦发布，就属于稳定契约，后续只能做兼容性演进。

**⚠️ 关键安全风险（开放不可信扩展前必须解决）**：

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

当前 Provider 层已支持 OpenAI 兼容族、DeepSeek、Groq、GLM、Minimax 和 Anthropic/Claude thinking 解析；剩余风险集中在流式响应和拒绝语义。

| 风险类型 | 严重程度 | 状态 |
|---------|---------|------|
| ~~同 Provider 多模型切换未真正覆盖请求 model~~ | ~~🟡 中~~ | ~~已完成~~ |
| 流式响应不支持 | 🟡 中 | 待规划（Phase 3+） |
| 拒绝标记未处理 | 🟢 低 | 待规划 |

**缓解措施**：推理链适配和 per-request model override 已完成；后续补齐流式响应和更细的 refusal 语义处理，详见 [docs/architecture/provider-architecture.md](architecture/provider-architecture.md)。

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
