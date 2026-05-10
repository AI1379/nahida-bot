# 核心运行流程与模块契约

## 1. 消息主流程（通过 channel service plugin）

```text
外部平台 (QQ/Telegram/Matrix/etc)
  ↓
channel service plugin 接收事件
  ├─ HTTP Server: webhook 推送
  ├─ WebSocket: 双向连接
  ├─ HTTP Client: 轮询或长连接
  └─ SSE: 单向推送
  ↓
InboundMessage 标准化
  ├─ 保留原始平台字段用于路由与审计
  └─ 生成轻量 MessageContext（消息时间、channel、chat 类型、发送方短标签）
  ↓
Session Resolver （映射平台用户 -> Bot 会话）
  ↓
Command Router
  ├─ 命中命令：执行插件注册的 command handler
  │   ├─ router 级超时保护
  │   └─ str / OutboundMessage / CommandResult / None
  └─ 未命中命令：继续进入 Agent Loop
  ↓
Context Builder (workspace 文件注入 + 历史记录)
  ├─ 历史 turn 按持久化 metadata 稳定渲染 per-turn envelope
  └─ 当前 turn 按 InboundMessage 的 MessageContext 渲染 envelope
  ↓
Agent Loop
  ├─ 消息入 LLM
  ├─ Tool calls (optional)
  └─ 最终回复组装
  ↓
Sentinel Token 检测（Phase 2.10）
  ├─ 精确匹配 NO_REPLY → 抑制回复，不持久化 assistant turn
  ├─ JSON 包络 {"action":"NO_REPLY"} → 抑制回复
  ├─ 尾部剥离 NO_REPLY → 剥离后为空则抑制，非空则发送剩余文本
  ├─ 精确匹配 HEARTBEAT_OK → 心跳确认，抑制回复
  └─ 未命中 → 正常发送
  ↓
OutboundMessage（若未被抑制）
  ↓
channel service plugin 发送
  ├─ 调用外部 API
  └─ 或通过 WebSocket 回复
  ↓
持久化历史记录到 SQLite
  ├─ turn.content 保留原始消息文本
  └─ turn.metadata.message_context 保留可重建 envelope 的结构化字段
```

### Per-turn Message Context Envelope

LLM 不通过工具查询“这条消息什么时候发、来自哪里、是谁发的”。这些事实属于消息本身，在进入模型前渲染成每条 turn 的短 envelope：

```text
[2026-05-10 14:03 +08 | milky/group | Alice admin]
消息内容
```

设计约束：

- 时间只属于具体消息，不作为 session 级“当前时间”注入。
- 不维护 participant directory；每条消息直接带可读的发送方短标签，避免模型倒查 ID 映射。
- channel converter 负责提供平台特有 facts，例如 chat 类型、显示名、管理员/群主等角色标签；核心只消费统一 `MessageContext`。
- `raw_event`、完整用户资料、完整群资料默认不进入 LLM 上下文。
- 普通轮次不重写历史 envelope。turn 写入后 `message_context` 稳定，历史重建时使用确定性渲染，提升后端 prompt cache 命中率。
- 会话压缩是唯一允许重写多条历史的边界；压缩摘要必须显式保留时间范围、channel、关键参与者和身份变化。
- 插件后续若需要贡献上下文，只能贡献受预算约束的短 tag 或 key/value fact，不能直接拼接任意 prompt。

## 2. 工具调用流程

```text
LLM tool_call
  → Tool Registry lookup （所有 Plugin 注册的 Tool）
  → Permission check （权限系统）
  → Tool execute
  → Tool result message
  → Loop continues
```

## 3. 命令调用流程

```text
InboundMessage
  → CommandMatcher （按平台 command_prefix 匹配）
  → CommandRegistry lookup
  → asyncio.wait_for(command_timeout_seconds)
  → CommandHandlerResult normalize
    ├─ str → OutboundMessage(text=..., reply_to=inbound.message_id)
    ├─ OutboundMessage → 原样发送
    ├─ CommandResult.none() / None → 不发送响应
    └─ CommandResult.text(...) → 文本响应
  → ChannelService.send_message
```

## 4. Gateway-Node 流程

```text
Node connect
  → auth challenge/response
  → heartbeat
  → command dispatch
  → result return
  → health update
```

Gateway-Node 是 Phase 5 的远程执行传输层，不应进入 AgentLoop 或 Subagent 任务描述。Agent 编排层只预留很薄的 `AgentRunExecutor` 接口：

```text
AgentOrchestrator
  → AgentRunExecutor
    ├─ LocalAgentRunExecutor → SessionRunner.run()
    └─ RemoteNodeRunExecutor → Gateway-Node protocol (Phase 5)
```

Phase 3.8 只实现本地执行器；远程节点扩展必须复用同一套 run / task / session 状态机。

## 5. Subagent 编排流程（Phase 3.8）

```text
父 Agent 通过工具调用 agent_spawn(task, instructions?, context_mode?)
  → AgentOrchestrator 运行粗粒度策略与配额检查
  → 校验调用者是 depth=0 的主 Agent（子 Agent 不能继续 spawn）
  → 基于现有 session_id 创建 child_session_id
  → 创建 BackgroundTask(runtime=subagent)
  → AgentRegistry 注册 child AgentRun(kind=subagent, depth=1)
  → AgentRunQueue 放入 child session lane + subagent global lane
  → 立即返回 task_id / run_id
  → LocalAgentRunExecutor 调用 SessionRunner.run(child_session_id, synthesized task message)
  → 完成后写入任务终态与摘要
  → 向 requester session 投递 subagent_completed 事件
  → agent_yield 可结束父 run 并等待完成事件后续跑
```

Subagent 是一次临时任务，不要求预先定义独立 `AgentProfile`。同一 session 的 run 必须串行，不同 session 可按 lane 并行。详细设计见 [agent-orchestration.md](agent-orchestration.md)。

## 6. 模块契约（建议先固定）

优先稳定以下契约，后续模块都基于这些契约展开：

- **Message Contract**：`InboundMessage` / `OutboundMessage`
  - 所有 channel service plugin 都基于这个统一结构转换平台原生消息
- **ChannelService Contract**（参考 OpenClaw、OneBot）
  - `handle_inbound_event(event: dict) -> None`
  - `send_message(target: str, message: OutboundMessage) -> str`
  - 支持的通信方式声明（HTTP Server/Client、WebSocket、SSE）
  - 权限和生命周期钩子
- **Agent Contract**：`AgentLoop.run()` 输入输出与中断语义
- **Agent Orchestration Contract**：`SubagentSpec` / `AgentRun` / `BackgroundTask` / `AgentRegistry` / `AgentRunQueue` / `AgentRunExecutor`
  - 子 Agent 必须有独立 session、父子关系、终态记录、取消语义和可审计来源
  - 首版固定只支持一层子 Agent，Gateway-Node 只通过 executor 接口预留
- **Reply Signal Contract**：sentinel token 检测函数（`is_silent_reply`、`is_heartbeat_ack`、`strip_trailing_token`）、检测后对 `OutboundMessage` 的抑制策略、assistant turn 持久化决策。参考 Phase 2.10 回复信号协议。
- **Tool Contract**：tool definition、参数校验、执行结果结构
  - 由 Plugin 通过权限系统注册
- **Command Contract**：command registration、命令匹配、超时和 `CommandHandlerResult`
  - 命令用于传统 Bot 操作，命中后不进入 LLM
- **Plugin Manifest Contract**：`plugin.yaml` 字段与版本兼容策略
  - channel service plugin 作为标准 Plugin 的一种，需遵循同一 manifest 规范
- **Gateway Protocol Contract**：消息类型、错误码、版本字段

**设计原则**：

- 契约一旦开放给插件或外部节点使用，默认只做向后兼容改动。
- channel service plugin 的多通信协议支持（HTTP/WebSocket）需在 manifest 中明确声明，确保 Bot 和外部系统能协商使用哪种方式。
- 参考 NapCat/OneBot 的做法：允许外部系统通过注册 webhook 向 Bot 推送，同时 Bot 也能通过 HTTP/WebSocket 主动向外部系统发送消息。
