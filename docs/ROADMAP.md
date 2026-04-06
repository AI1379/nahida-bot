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
| `workspace`（文件即上下文） | OpenClaw, AstrBot | 指令文件注入、工作区隔离、状态持久化 | 路径安全必须先于易用性 |
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

- [ ] 实现 `Application` 主类，统一管理生命周期（init/start/stop）。
- [ ] 使用 `pydantic-settings` 建立分层配置（默认值、配置文件、环境变量）。
- [ ] 接入 `structlog`，区分开发态可读日志和生产态 JSON 日志。
- [ ] 建立基础异常树（配置错误、启动错误、插件错误、通信错误等）。
- [ ] 实现轻量事件总线（内部事件与订阅机制）。
- [ ] 验证应用可独立启动/优雅退出，关键错误可结构化记录。

前置依赖：Phase 0。

风险控制：不要在本阶段引入具体平台逻辑，保证核心层中立。

参考来源：OpenClaw（生命周期）、AstrBot（配置日志）、pydantic-settings。

### Phase 2 - Agent 与 Workspace 联合阶段

目标：同步打通 Agent 核心回路与 Workspace 上下文能力，形成最小可用智能闭环。

任务清单：

- [ ] 实现 workspace 初始化、模板复制、默认空间和多空间切换。
- [ ] 文件读写统一走安全 API，完成路径归一化与路径穿越防护。
- [ ] 定义 `AGENTS.md`、`SOUL.md`、`USER.md` 的上下文注入优先级与拼接规则。
- [ ] 实现 Agent Loop（消息组装、模型调用、工具调用、结果回填、终止条件）。
- [ ] 定义 Provider 抽象接口，先接入一个 OpenAI 兼容 Provider。
- [ ] 实现 tool calling 协议、参数校验和执行结果协议。
- [ ] 实现会话记忆（短期）和长期记忆（可检索）模型。
- [ ] 先用 SQLite 打通记忆持久化，并预留存储抽象接口。
- [ ] 接入上下文预算与最小可用截断策略。
- [ ] 增加重试、超时、回退提示，避免单次错误中断对话。
- [ ] 验证至少一条完整闭环：workspace 指令加载 -> provider 调用 -> tool 调用 -> 最终回复。

前置依赖：Phase 1。

风险控制：

- 工具协议要尽早固定，后续插件系统将强依赖该协议。
- 先保证安全边界，再做便捷 API。

参考来源：OpenClaw（Agent + Workspace 模式）、claude-code（流程模式层）、AstrBot（运行时文件组织）、LiteLLM/OpenAI SDK。

### Phase 3 - 插件系统与 Channel 接口定义

目标：建立声明式、可治理的插件系统，并定义 Channel 作为标准插件接口。

> 本阶段的关键设计决策：Channel 不是独立层，而是通过插件系统接入。这样可以复用权限模型、生命周期管理和能力注册机制。参考 OneBot/NapCat 等协议，定义统一的 Channel 接口，但支持多种底层通信方式（HTTP、WebSocket、SSE）。

任务清单：

- [ ] 定义 `plugin.yaml` 字段、版本与兼容策略。
- [ ] 实现 discover/load/enable/disable/reload/unload 生命周期。
- [ ] 实现声明式权限校验（文件、网络、环境变量、命令执行）。
- [ ] 定义 ChannelPlugin 基类和标准接口（见下文详解）。
- [ ] 定义 Tool、Hook 两类标准接口。
- [ ] 提供基础内置插件（读文件、命令执行、网页读取、记忆检索）。
- [ ] 实现插件异常隔离与降级告警。
- [ ] 验证不改核心代码可新增并加载插件，越权行为可拦截可追踪。

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
3. Agent 与 Workspace 联合阶段（Phase 2）
4. 插件系统与 Channel 接口定义（Phase 3）
5. 基于插件系统的 Channel 实现（Phase 4）
6. Gateway 与 Node（Phase 5）
7. WebUI 与运维工具（Phase 6）
8. 稳定性、发布与生态（Phase 7）

这个顺序的核心原因是：

- **Phase 0-2** 建立最小智能闭环（应用容器 -> 核心运行时 -> Agent + Workspace）
- **Phase 3-4** 打通插件和 Channel（先定义接口，允许多种通信协议；再实现具体 Channel 作为插件）
- **Phase 5-6** 扩展分布式与运维（Gateway-Node + WebUI）
- **Phase 7** 稳定化与商业化（发版、CI/CD、生态）

关键设计点是 **Phase 3 中的 Channel 接口设计直接服务于 Phase 4**，避免核心层改造。

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
