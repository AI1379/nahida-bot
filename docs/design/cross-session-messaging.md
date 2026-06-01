# 跨会话消息发送设计

> 记录时间：2026-05-20
> 最近更新：2026-05-24
> 状态：部分实现，target 已统一为 typed ChatAddress
> 相关文档：
>
> - [ROADMAP.md §3.6 — message 工具](../ROADMAP.md)
> - [ROADMAP.md §3.8 — sessions_send 等工具](../ROADMAP.md)
> - [agent-orchestration.md §7.2 — A2A / sessions_send](../architecture/agent-orchestration.md)
> - [cron-and-webapi-optimization.md — WebAPI 投递接口](cron-and-webapi-optimization.md)

## 1. 背景与动机

nahida-bot 当前所有 LLM 工具都绑定在 `current_session` 上下文中，Agent 无法在对话中主动向其他会话/平台发送消息。但底层管道已经打通：

- `ChannelRegistry` 可按 platform 名查找任意通道服务
- `BotAPI.send_message(target, message, channel=...)` 支持指定 channel + typed target
- `POST /api/send` 已实现跨 session 发送（仅纯文本，要求 `channel:type:id` target）
- `OrchestrationPolicy.can_send_session()` 存根已存在

需要补齐的是 **LLM 面向的工具链** 和 **WebAPI 的富消息能力**。

## 2. 能力分层

跨会话消息涉及三个不同层次的工具，各有明确边界：

```
┌────────────────────────────────────────────────────────────┐
│ Layer 3: sessions_send（A2A 事件接口）                       │
│   语义: Agent → Agent / Session 间事件传递                    │
│   模式: record_only | enqueue                                │
│   安全: 必须标记 source，不伪装用户消息                        │
├────────────────────────────────────────────────────────────┤
│ Layer 2: message（跨 Channel 发消息）                         │
│   语义: Agent → 用户，直接走 Channel 投递                     │
│   能力: 文本 + 附件（图片/文件/音频/视频）+ 回复引用            │
│   场景: 主动通知、跨平台转发、工具已发回复                     │
├────────────────────────────────────────────────────────────┤
│ Layer 1: WebAPI POST /api/send（HTTP 外部接口）               │
│   语义: 外部脚本/CLI → Channel 投递                           │
│   当前: 仅纯文本                                             │
│   增强: 支持附件、reply_to                                    │
└────────────────────────────────────────────────────────────┘
```

三者的关键区别：

| 维度 | message 工具 | sessions_send | WebAPI send |
|------|-------------|---------------|-------------|
| 调用方 | LLM Agent | LLM Agent | 外部脚本/CLI |
| 消息形式 | OutboundMessage（完整） | agent/system 事件 | HTTP request |
| 是否触发目标 run | 否（直接投递） | record_only: 否, enqueue: 是 | 否（直接投递） |
| 前置条件 | sessions_list 发现目标 | sessions_list 发现目标 | 知道 typed target |

## 3. 已有基础设施

| 组件 | 状态 | 位置 |
|------|------|------|
| `ChannelRegistry` | **已实现** | `core/channel_registry.py` — 按 platform 名查找 ChannelService |
| `BotAPI.send_message` | **已实现** | `plugins/api_bridge.py` — 支持任意 channel + target |
| `ChannelService.send_message` | **已实现** | Telegram/Milky 各自实现了文本+附件投递 |
| `POST /api/send` | **已实现（纯文本）** | `gateway/routes/messages.py` |
| `OrchestrationPolicy.can_send_session` | **存根** | `agent/orchestration/policy.py` — 从未被调用 |
| `sessions_send` denylist 引用 | **已存在** | `agent/orchestration/service.py`、`policy.py` — 工具未注册 |
| `OutboundMessage` 模型 | **已实现** | `plugins/base.py` — text/reply_to/reasoning/attachments/extra |
| `Attachment` 模型 | **已实现** | `plugins/base.py` — type/path/filename/mime_type/caption |

## 4. 设计详情

### 4.1 `message` 工具（Layer 2）

> 来源：[ROADMAP.md Phase 3.6](../ROADMAP.md)，优先级 P1

让 Agent 能通过 channel service 向任意已注册平台发送消息。这是跨会话消息的最常用形式。

**工具契约**：

```json
{
  "target": "string, required — channel:type:id，例如 milky:group:20001",
  "text": "string, required — 消息文本",
  "delivery": "notify | record, optional — 默认 notify",
  "attachments": [
    {
      "type": "photo | document | audio | video",
      "path": "string — 本地文件路径（工作空间内）",
      "caption": "string, optional"
    }
  ]
}
```

**实现要点**：

- 在 `plugins/builtin/commands.py` 注册 `message` 工具
- `target` 解析为 `ChatAddress`
- 调用 `ctx.bot_api.send_message(address.target_id, OutboundMessage(..., extra={"chat_address": address.chat_key}), channel=address.channel)`
- `reply_signals` 的 `NO_REPLY` 与本工具协同：Agent 用 `message` 工具已发送回复后，主回复可用 `NO_REPLY` 避免重复
- 附件路径必须在 workspace sandbox 内，防止路径穿越
- 需要权限检查：`check_network_outbound` 已在 `BotAPI.send_message` 中存在

**路由**：

- Agent 不需要知道 channel 的具体实现，只需提供 typed `target`
- `BotAPI.send_message` 从 `ChannelRegistry` 查找对应 ChannelService 并委托投递
- 不存在的 channel 返回错误，不存在的 target id 由 channel 层报错

**安全**：

- `OrchestrationPolicy` 增加 `can_send_message(requester_session_id, target)` 检查
- 子 Agent 默认禁用 `message` 工具（加入 denylist）
- 附件路径必须在 workspace 内（复用 workspace sandbox 校验）

### 4.2 `sessions_send` 工具（Layer 3）

> 来源：[agent-orchestration.md §7.2](../architecture/agent-orchestration.md)、[ROADMAP.md Phase 3.8](../ROADMAP.md)

A2A 最小跨会话事件接口，用于 Agent 间结构化通信。

**工具契约**：

```json
{
  "target_session_id": "string, required",
  "message": "string, required",
  "mode": "record_only | enqueue"
}
```

编排层自动补充 `source="agent:<run_id>"`。

**模式说明**：

| mode | 行为 | 场景 |
|------|------|------|
| `record_only` | 向目标 session 写入 agent/system 事件，不触发 run | 留言、状态同步 |
| `enqueue` | 写入事件 + 排入目标 session lane 触发一个 run | 跨会话任务触发、通知 |

**前置依赖**：

- `sessions_list` — Agent 需要知道有哪些 session 可以发
- `session_status` — 查询目标 session 的运行状态
- `OrchestrationPolicy.can_send_session()` — 从存根升级为实际调用

**实现要点**：

- 在 `agent/orchestration/session_tools.py` 中注册（目前文件不存在，需新建）
- `record_only`：向目标 session 的 history 写入一条 `role=system` 的消息，metadata 标记 `event_type=agent_message` + `source`
- `enqueue`：`record_only` 的行为 + 通过 `AgentOrchestrator` 排入目标 session lane
- 目标 session 的事件必须标记 `source`，不能伪装成用户消息

**安全**：

- 只能发送到自己创建的 child session 或当前 session（首版限制）
- `can_send_session()` 复用现有的 `can_read_session()` 策略：target 必须是 requester session 或其 child
- 子 Agent 默认禁用 `sessions_send`（已在 denylist 中）

### 4.3 `sessions_list` / `session_status` / `sessions_history`

> 来源：[agent-orchestration.md §11.5-11.6](../architecture/agent-orchestration.md)

`sessions_send` 和 `message` 的前置工具。

**sessions_list**：

```json
// 输入：无参数（列出当前 session 可见的所有 session）
// 输出：
{
  "sessions": [
    {
      "session_id": "telegram:private:12345",
      "target": "telegram:private:12345",
      "platform": "telegram",
      "chat_id": "12345",
      "has_active_run": false,
      "last_active_at": "2026-05-20T10:00:00Z"
    }
  ]
}
```

首版只返回当前 session + 自己创建的 child session。

**session_status**：

```json
// 输入
{ "session_id": "string, optional — 默认当前 session" }
// 输出
{
  "session_id": "...",
  "active_run": { "run_id": "...", "kind": "main", "status": "running" } | null,
  "recent_tasks": [{ "task_id": "...", "status": "succeeded", "summary": "..." }],
  "queue_depth": 0
}
```

**sessions_history（安全过滤版）**：

```json
// 输入
{
  "session_id": "string — 只能读自己或 child session",
  "limit": 20,
  "offset": 0
}
// 输出
{
  "messages": [
    { "role": "user", "content": "...(截断)...", "timestamp": "..." },
    { "role": "assistant", "content": "...(截断)..." }
  ],
  "total": 42,
  "truncated": true
}
```

过滤规则：

- 单条消息截断到 2000 字符
- 移除 base64、临时 URL、raw_event、raw provider payload
- 不返回 reasoning 原文，只保留 metadata
- 只允许读当前 session 和自己创建的 child session

### 4.4 WebAPI `POST /api/send` 增强（Layer 1）

> 来源：[cron-and-webapi-optimization.md §3.1](cron-and-webapi-optimization.md)

当前只支持纯文本。增强为支持完整 `OutboundMessage`。

**增强后的请求 Schema**：

```python
class SendMessageRequest(BaseModel):
    target: str          # channel:type:id
    text: str
    session_id: str | None = None
    # 未来增强字段
    reply_to: str = ""
    attachments: list[AttachmentSchema] = Field(default_factory=list)

class AttachmentSchema(BaseModel):
    type: str          # "photo" | "document" | "audio" | "video"
    path: str          # 本地文件路径
    filename: str = ""
    caption: str = ""
```

**实现要点**：

- 构造完整的 `OutboundMessage` 而非只传 text
- 附件路径需要安全校验：限制在 workspace 或指定目录内
- `platform/chat_id` 不再作为写入 schema；旧格式只用于历史查询类接口

### 4.5 Cron 工具跨 session 参数

当前 `_tool_cron_create` 读取当前 session 的 typed `ChatAddress`。如果后续要支持跨 session cron，应增加 typed `target` 参数。

**增强**：

- `cron_create` 工具增加可选参数 `target`
- 不提供时默认使用当前 session 的 typed `ChatAddress`
- 底层 `SchedulerService` 已支持，只需工具层透传

## 5. 实施路线

### Phase 1：`message` 工具（Layer 2）

最小可用，让 Agent 能发消息。

- [ ] 在 `plugins/builtin/commands.py` 注册 `message` 工具
- [ ] 实现 `message` 工具 handler：解析参数 → 构造 `OutboundMessage` → 调用 `BotAPI.send_message`
- [ ] `OrchestrationPolicy` 增加 `can_send_message` 权限检查
- [ ] 子 Agent denylist 增加 `message`
- [ ] workspace sandbox 校验附件路径
- [ ] 测试：同平台发送、跨平台发送、不存在的 channel 报错

### Phase 2：Session 发现工具

`sessions_send` 和 `message` 都需要先发现目标 session。

- [ ] 新建 `agent/orchestration/session_tools.py`
- [ ] 实现 `sessions_list` — 列出当前 session + child session
- [ ] 实现 `session_status` — 查询 session 的 run 状态和队列
- [ ] 实现安全过滤版 `sessions_history` — 截断、脱敏、去除敏感字段
- [ ] 测试：session 可见范围、history 过滤、越权访问拒绝

### Phase 3：`sessions_send`（Layer 3）

A2A 最小事件接口。

- [ ] 实现 `sessions_send(record_only|enqueue)` 工具
- [ ] `record_only`：向目标 session history 写入 system 事件
- [ ] `enqueue`：`record_only` + 排入目标 session lane 触发 run
- [ ] 接通 `OrchestrationPolicy.can_send_session()`（从存根升级）
- [ ] 自动补充 `source="agent:<run_id>"` 标记
- [ ] 测试：record_only 写入验证、enqueue 触发 run、source 标记、越权拒绝

### Phase 4：WebAPI 增强（Layer 1）

- [ ] `SendMessageRequest` 增加 `reply_to` 和 `attachments` 字段
- [ ] handler 构造完整 `OutboundMessage`
- [ ] 附件路径安全校验
- [ ] 向后兼容验证

### Phase 5：Cron 跨 session（可选）

- [ ] `cron_create` 工具增加 typed `target` 可选参数
- [ ] 底层透传到 `SchedulerService`（已支持）

## 6. 不做的事

- **不做多轮 A2A ping-pong** — 首版 `sessions_send` 只支持单向事件，不实现 agent 间来回对话
- **不做任意 agent 自主发现** — 首版 session 可见范围限制在当前 session + child session
- **不做复杂 announce/reply 协议** — `ANNOUNCE_SKIP` / `REPLY_SKIP` 留给 Phase 3.8 之后
- **不做 WebSocket 消息推送** — WebAPI 只做 REST，实时推送是后续需求
- **不做 `sessions_send` 的 delivery 参数** — 首版只有 `record_only | enqueue` 两种模式

## 7. 与其他系统的协同

| 系统 | 协同方式 |
|------|---------|
| 回复信号协议 | `message` 工具发送后，Agent 主回复用 `NO_REPLY` 避免重复（[ROADMAP §2.10](../ROADMAP.md)） |
| Subagent 编排 | 子 Agent 完成事件通过 `sessions_send(record_only)` 投递回父 session（[agent-orchestration.md §6.3](../architecture/agent-orchestration.md)） |
| Cron 系统 | `message` 工具让 Agent 可在 cron turn 中向其他 chat 发通知 |
| WebAPI | `POST /api/send` 增强后，脚本和 CLI 可发送富消息（[cron-and-webapi-optimization.md](cron-and-webapi-optimization.md)） |

## 8. 参考源码

- OpenClaw sessions tools：`openclaw\src\agents\tools\sessions-send-tool.ts`、`sessions-yield-tool.ts`、`sessions-spawn-tool.ts`
- OpenClaw A2A send flow：`openclaw\src\agents\tools\sessions-send-tool.a2a.ts`
- nahida-bot 现有 channel：`nahida_bot/channels/telegram/plugin.py`、`nahida_bot/channels/milky/plugin.py`
- nahida-bot BotAPI：`nahida_bot/plugins/api_bridge.py`
- nahida-bot 编排策略：`nahida_bot/agent/orchestration/policy.py`
