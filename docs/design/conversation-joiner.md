# Conversation Joiner 主动入话题设计

> 记录时间：2026-06-08
> 最近更新：2026-06-08
> 状态：设计草案，Phase 0 已完成
> 来源：GitHub Issue #4 的“随机发言”表述不准确；真实目标是让 Bot 判断是否自然加入群聊话题。
> 相关文档：
>
> - [插件系统](../architecture/plugin-system.md)
> - [事件系统](../architecture/event-system.md)
> - [聊天地址与会话 ID](chat-address-and-session-id.md)
> - [跨会话消息](cross-session-messaging.md)
> - [配置参考](../guide/configuration.md)

## 1. 背景

“随机发言”容易被理解成 Bot 定时或随机从消息池里抽一句话直接发送。这个方向不符合当前需求。

实际需要的是 **conversation joiner**：当群聊有新消息进入时，Bot 观察最近上下文，通过规则、关键词和便宜秘书模型判断“现在是否适合自然加入话题”。如果适合，则触发主 Agent 进入 AgentLoop，由主 Agent 根据上下文组织回复；如果不适合，则保持沉默。

因此本文档不再设计确定性主动投递、消息池随机抽取或直接发送预设消息。

## 2. 目标与非目标

### 2.1 目标

1. 支持 Bot 观察群聊上下文，但不让所有群消息都直接进入 AgentLoop。
2. 使用轻量规则和便宜秘书模型判断是否应该加入话题。
3. 判断通过后，通过专用事件请求主 Agent 响应，而不是伪造普通用户消息。
4. 保留现有 mention / command / always / none 硬触发模式。
5. 通过冷却、频率上限、置信度阈值和并发保护避免刷屏。

### 2.2 非目标

1. 不设计消息池。
2. 不设计“随机抽一句直接发”的确定性投递。
3. 不让秘书模型生成最终群聊回复。
4. 不让普通插件直接发布 `MessageReceived` 作为正式触发入口。
5. 不把重型 LLM 判定放进 Router 同步路径。

## 3. 总体架构

```text
Channel normalized inbound message
  -> group trigger policy
      -> hard trigger: MessageReceived -> Router command / AgentLoop
      -> observe only: MessageObserved / observed inbound stream
  -> ConversationJoinerPlugin async worker
      -> heuristic prefilter
      -> cheap secretary model
      -> should_join=false: do nothing
      -> should_join=true: AgentResponseRequested
  -> Router handles AgentResponseRequested
      -> source_tag="proactive_join"
      -> main Agent builds final response
      -> Agent may still return NO_REPLY
```

核心原则：

- Router 只处理硬触发和正式 Agent 请求。
- Conversation joiner 异步观察，不阻塞 channel 入站和 Router。
- 秘书模型只做 gating；主 Agent 负责最终表达。
- 主 Agent 必须知道这是“主动加入话题”，不是用户直接点名。

## 4. 群聊触发基础语义

`group_trigger_mode` 决定“是否进入 Router 的 command / AgentLoop 路径”。

| 模式 | 群聊普通消息 | 裸命令 | mention 普通消息 | mention 命令 |
|------|--------------|--------|------------------|--------------|
| `none` | 不触发 | 不触发 | 不触发 | 不触发 |
| `mention` | 不触发 | 不触发 | 触发 | 触发 |
| `command` | 不触发 | 触发 | 触发 | 触发 |
| `always` | 触发 | 触发 | 触发 | 触发 |

说明：

- `mention` 是严格 @ 模式，命令也必须 @ 才触发。
- `command` 表示显式召唤：裸命令或 @ 都触发。
- `none` 只表示不进入 AgentLoop，不代表一定丢弃消息。

Phase 0 已完成上述语义修正。后续还需要把“触发”和“观察”完全拆开：

```yaml
milky:
  group_trigger_mode: mention
  group_observe_mode: all      # none | triggering | all
```

在 `group_observe_mode` 落地前，继续使用现有 `group_context_capture` 作为兼容开关。

## 5. 观察与触发分层

需要区分三种行为：

| 行为 | 事件/路径 | 说明 |
|------|-----------|------|
| 硬触发 | `MessageReceived` | 明确要进入 Router，例如 mention、command、always |
| 观察 | `MessageObserved` 或后续统一观察事件 | 写入群聊上下文，供插件判断 |
| 主动请求 Agent | `AgentResponseRequested` | 插件判断通过后请求主 Agent 入话题 |

普通插件不应直接发布 `MessageReceived`。`MessageReceived` 表示真实 channel 收到并决定响应的一条用户消息；conversation joiner 的行为是“请求 Agent 主动加入话题”，语义不同。

## 6. AgentResponseRequested

后续新增专用事件：

```python
class AgentResponseRequested(Event[AgentResponseRequestPayload]):
    ...

@dataclass(slots=True, frozen=True)
class AgentResponseRequestPayload:
    message: InboundMessage
    session_id: str
    chat_address: ChatAddress
    requester_plugin_id: str
    reason: str
    instruction: str = ""
    synthetic: bool = False
```

新增 BotAPI helper：

```python
async def request_agent_response(
    self,
    message: InboundMessage,
    *,
    session_id: str = "",
    reason: str = "",
    instruction: str = "",
) -> None: ...
```

Router 订阅 `AgentResponseRequested` 后：

1. 校验 target 是 typed group address。
2. 检查同 session 是否已有 active run。
3. 设置 session context。
4. 以 `source_tag="proactive_join"` 进入 `_dispatch_message()` 或后续专门的 Agent request path。
5. 将 `instruction` 注入本轮 system/developer context，让主 Agent 知道这是主动入话题。

主 Agent 的行为约束：

```text
你不是被用户直接点名，而是在系统判断合适后自然加入群聊。
根据最近上下文简短接话。
如果不该说话、上下文不足或会打扰用户，回复 NO_REPLY。
```

## 7. ConversationJoinerPlugin

### 7.1 配置示例

```yaml
conversation_joiner:
  enabled: false
  model: cheap
  threshold: 0.75
  max_context_messages: 12
  max_context_chars: 3000
  cooldown_seconds: 300
  max_triggers_per_hour: 3
  debounce_seconds: 20
  decision_timeout_seconds: 8

  prefilter:
    ignore_commands: true
    ignore_mentions: true
    min_text_chars: 4
    keyword_hints: []

  groups:
    milky:group:123456789:
      enabled: true
      threshold: 0.78
      cooldown_seconds: 300
```

### 7.2 处理流程

```text
Observed group message
  -> ignore self/bot/command/direct mention
  -> update group context window
  -> debounce per group
  -> heuristic prefilter
  -> call cheap secretary model
  -> parse structured decision
  -> cooldown / rate limit / active run guard
  -> request_agent_response()
```

### 7.3 启发式预过滤

秘书模型不应每条消息都调用。调用前先做廉价过滤：

- 群未启用：跳过。
- 当前消息来自 Bot 自己或其他 Bot：跳过。
- 当前消息已经硬触发 Router：跳过。
- 当前 session 有 active run：跳过。
- 处于冷却或小时上限：跳过。
- 文本过短且无关键词提示：跳过。
- 最近 N 秒已安排过判定：debounce。

## 8. 秘书模型

秘书模型只做“是否加入话题”的判定，不生成最终群聊回复。

输入包括：

- 当前消息。
- 最近 N 条群聊观察上下文。
- Bot 上次发言时间。
- 当前群配置和冷却状态。
- 简短行为准则。

输出必须是 JSON：

```json
{
  "should_join": true,
  "confidence": 0.78,
  "reason": "群里正在讨论 Bot 可以自然补充的技术问题",
  "entry_style": "short_comment",
  "focus": "解释最近提到的概念，不要显得像被点名"
}
```

通过条件：

- `should_join == true`
- `confidence >= threshold`
- `reason` 非空
- JSON 解析成功
- 未触发冷却、频率和 active run guard

失败策略：

- JSON 解析失败：记录 debug，跳过。
- LLM 超时：记录失败退避，不重试当前消息。
- 置信度不足：跳过。
- 秘书模型建议加入但主 Agent 返回 `NO_REPLY`：正常记录，不视为失败。

## 9. 状态持久化

使用 `plugin_data`，key namespace 属于 `conversation_joiner`。

| Key | Value | 说明 |
|-----|-------|------|
| `group:{chat_key}` | `GroupJoinerConfig` | 动态分群配置覆盖 |
| `state:{chat_key}` | `GroupJoinerState` | 冷却、最近触发、失败退避 |
| `decision:{chat_key}:{message_id}` | `SecretaryDecision` | 可选调试记录，默认短期保留 |

`GroupJoinerState`：

```python
class GroupJoinerState:
    chat_key: str
    last_decision_at: float
    last_triggered_at: float
    triggered_timestamps: list[float]
    failure_backoff_until: float
```

## 10. 权限

```yaml
permissions:
  plugin_data:
    read: true
    write: true
  llm_access: true
capabilities:
  emits:
    - AgentResponseRequested
```

后续建议补事件发布权限：

```yaml
permissions:
  events:
    emit:
      - AgentResponseRequested
```

在权限系统完善前，`request_agent_response()` 应只允许显式白名单事件，不复用完全开放的 `publish_event()`。

## 11. 安全与防滥用

1. 全局默认关闭，分群默认关闭。
2. 只支持 typed group address。
3. 不响应 private target。
4. 每群有冷却和小时上限。
5. 秘书模型不允许工具调用。
6. 秘书模型输出 token 极小，并强制 JSON。
7. 主 Agent 仍可用 `NO_REPLY` 二次否决。
8. active run 期间不重复触发。
9. 管理命令写操作必须配置管理员。

## 12. 实施 checklist

### Phase 0：群触发基础语义

> 状态：已完成（2026-06-08）

- [x] `GroupTriggerMode` 增加 `none`。
- [x] 修正 `mention`：裸 command 不再触发。
- [x] 修正 `command`：裸 command 或 mention 都触发。
- [x] 更新 Milky converter 的早期过滤语义。
- [x] 更新 Telegram / OneBot / Milky config 类型与文档。
- [x] 补测试：`none`、严格 `mention`、`command` 合并语义。

### Phase 1：观察模式与 Agent 请求事件

- [ ] 设计并实现 `AgentResponseRequested` 事件。
- [ ] 增加 `BotAPI.request_agent_response()`。
- [ ] Router 订阅 `AgentResponseRequested` 并以 `source_tag="proactive_join"` 进入 AgentLoop。
- [ ] 增加事件发布权限或最小 allowlist。
- [ ] 规划 `group_observe_mode`，从 `group_context_capture` 兼容迁移。
- [ ] 补测试：插件请求 Agent 响应、权限拒绝、source_tag 注入。

### Phase 2：ConversationJoiner MVP

- [ ] 新增 `nahida_bot/plugins/conversation_joiner/`。
- [ ] 实现配置解析。
- [ ] 实现群上下文窗口和 debounce。
- [ ] 实现启发式预过滤。
- [ ] 通过 `BotAPI.llm_chat()` 调用便宜秘书模型。
- [ ] 解析结构化 JSON decision。
- [ ] 判定通过后调用 `request_agent_response()`。
- [ ] 补测试：秘书模型 should_join false/true、置信度阈值、冷却、并发主 run 保护。

### Phase 3：调试与管理

- [ ] 管理命令：`/conversation_joiner status|enable|disable|test`。
- [ ] 可选记录最近 decision，用于调试。
- [ ] WebUI 展示分群配置、最近 decision、触发原因。
- [ ] 增加 cost/latency 统计。

### Phase 4：观察模式重构

- [ ] 正式新增 `group_observe_mode`。
- [ ] 减少 channel converter 内的早期策略过滤。
- [ ] 让普通群消息可被观察，但不默认进入 AgentLoop。
- [ ] 迁移配置文档和 channel plugin 测试。

## 13. 测试矩阵

| 测试 | 重点 |
|------|------|
| 触发模式 | `none`、严格 `mention`、`command` 合并语义、`always` |
| 观察模式 | 未触发消息可进入观察路径，但不进 AgentLoop |
| Agent request | 插件通过专用事件请求 Agent 响应 |
| Secretary model | JSON 解析、置信度、超时、失败退避 |
| 安全 | 裸 id 拒绝、private 拒绝、非管理员写操作拒绝 |
| 并发 | active run 时不重复触发 |
| NO_REPLY | 主 Agent 二次否决后不发送消息 |

## 14. 风险

1. 在 `group_observe_mode` 落地前，未触发群消息仍依赖 `group_context_capture` 才能被插件看到。
2. `conversation_joiner` 会增加 LLM 调用成本，必须有启发式预过滤和速率限制。
3. 如果直接复用 `MessageReceived`，会混淆真实入站与插件主动触发，必须避免。
4. 当前事件系统不收集 handler 返回值，不适合作为同步 routing decision hook。
