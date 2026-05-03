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
  ↓
Agent Loop
  ├─ 消息入 LLM
  ├─ Tool calls (optional)
  └─ 最终回复组装
  ↓
OutboundMessage
  ↓
channel service plugin 发送
  ├─ 调用外部 API
  └─ 或通过 WebSocket 回复
  ↓
持久化历史记录到 SQLite
```

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

## 5. 模块契约（建议先固定）

优先稳定以下契约，后续模块都基于这些契约展开：

- **Message Contract**：`InboundMessage` / `OutboundMessage`
  - 所有 channel service plugin 都基于这个统一结构转换平台原生消息
- **ChannelService Contract**（参考 OpenClaw、OneBot）
  - `handle_inbound_event(event: dict) -> None`
  - `send_message(target: str, message: OutboundMessage) -> str`
  - 支持的通信方式声明（HTTP Server/Client、WebSocket、SSE）
  - 权限和生命周期钩子
- **Agent Contract**：`AgentLoop.run()` 输入输出与中断语义
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
