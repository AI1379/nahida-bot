# Conversation Joiner 主动入话题设计

> 记录时间：2026-06-08
> 最近更新：2026-06-29
> 状态：Phase 0-4 的 in-memory MVP 已完成；持久化、管理界面和正式 `group_observe_mode` 仍在后续阶段。第 16 节：互动事件（戳一戳）接入设计。
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

当前实现已经支持 conversation engagement 状态机：Bot 触发一次后进入当前话题，在后续一小段时间内持续旁听，将短窗口内的群聊消息打成 batch，再交给便宜 gate 模型判断是否应该让主 Agent 接话。话题冷却、相关度下降、时间上限或主 Agent `NO_REPLY` 都会推动状态退出。

因此本文档不再设计确定性主动投递、消息池随机抽取或直接发送预设消息。

## 2. 目标与非目标

### 2.1 目标

1. 支持 Bot 观察群聊上下文，但不让所有群消息都直接进入 AgentLoop。
2. 使用轻量规则和便宜秘书模型判断是否应该加入话题。
3. 判断通过后，通过专用事件请求主 Agent 响应，而不是伪造普通用户消息。
4. 保留现有 mention / command / always / none 硬触发模式。
5. 通过冷却、频率上限、置信度阈值和并发保护避免刷屏。
6. 支持 per group/session 的 conversation engagement 状态机：入场、持续参与、降频、退出。
7. 支持将一段时间窗口内的多条 observed message 打包给主 Agent，而不是只围绕单条消息回复。

### 2.2 非目标

1. 不设计消息池。
2. 不设计“随机抽一句直接发”的确定性投递。
3. 不让秘书模型生成最终群聊回复。
4. 不让普通插件直接发布 `MessageReceived` 作为正式触发入口。
5. 不把重型 LLM 判定放进 Router 同步路径。
6. 不把主动聊天策略硬编码进 Router 核心；核心只提供观察、上下文和 Agent request 基础设施。

## 3. 总体架构

```text
Channel normalized inbound message
  -> group trigger policy
      -> hard trigger: MessageReceived -> Router command / AgentLoop
      -> observe only: MessageObserved / observed inbound stream
  -> ConversationJoinerPlugin async worker
      -> state machine per group/session
          -> observing: heuristic prefilter + join gate
          -> joining: AgentResponseRequested
          -> engaged: buffer observed messages + continue/exit gate
          -> cooling: rate limit; exit returns to observing
  -> Router handles AgentResponseRequested
      -> source_tag="proactive_join"
      -> main Agent builds final response from current message or message batch
      -> Agent may still return NO_REPLY
```

核心原则：

- Router 只处理硬触发和正式 Agent 请求。
- Conversation joiner 异步观察，不阻塞 channel 入站和 Router。
- 便宜 gate 模型只做入场、续聊和退出判定；主 Agent 负责最终表达。
- 主 Agent 必须知道这是“主动加入话题”，不是用户直接点名。
- 策略先保留在内置插件里；核心只补通用能力。

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

状态：已落地。

已新增专用事件：

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
    observed_messages: tuple[InboundMessage, ...] = ()
    reply_to_message_id: str | None = None
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
    observed_messages: tuple[InboundMessage, ...] = (),
    reply_to_message_id: str | None = None,
) -> None: ...
```

语义：

- `message` 是触发本次 Agent run 的锚点消息。单条主动入话题时它就是当前 observed message；batch 接话时它通常是 batch 中被选中的回复锚点，或 batch 的最后一条消息。
- `observed_messages` 是可选的附加群聊消息批次，用于让主 Agent 理解一小段后续对话。
- `reply_to_message_id` 控制 channel 的引用回复锚点：`None` 表示使用 Router 默认引用策略；`""` 表示禁用引用；非空字符串表示强制引用指定 message id。
- Router 注入 batch 时保留每条消息的 sender、timestamp、text 和 message_id，并复用现有 `<message_context ...>` envelope 渲染结构。

Router 订阅 `AgentResponseRequested` 后：

1. 校验 target 是 typed group address。
2. 解析当前 active session。主动请求会进入当前群的 active session，而不是盲目信任 payload 中的旧 session。
3. 检查同 session 是否已有 active run；主动入话题请求不会排队等待已有 run。active run 冲突会向事件发布方抛错，joiner 保留 batch 并稍后重试。
4. 设置 session context。
5. 以 `source_tag="proactive_join"` 进入 `_dispatch_message()`，并跳过命令匹配。
6. 将 batch 上下文作为本轮 user message 的前置上下文注入，让主 Agent 看到结构化群聊批次；`instruction` 作为本轮 Agent instruction，说明这是主动入话题或续聊。

权限边界：

- `BotAPI.request_agent_response()` 是窄口，只能发布 `AgentResponseRequested`。
- 插件必须声明 `capabilities.emits: ["AgentResponseRequested"]`。
- 当前没有放开普通插件直接发布 `MessageReceived` 的正式能力。

主 Agent 的行为约束：

```text
你不是被用户直接点名，而是在系统判断合适后自然加入群聊。
根据最近上下文简短接话。
如果不该说话、上下文不足或会打扰用户，回复 NO_REPLY。
```

## 7. ConversationJoinerPlugin

状态：in-memory 状态机 MVP 已落地，默认关闭。

当前实现边界：

- 作为内置插件 `conversation_joiner` 发现，默认关闭。
- 订阅 `MessageObserved`，收到事件后创建后台任务，不阻塞 channel 入站发布。
- `engagement.enabled=false` 时保留单次触发路径：一条 observed message 通过 join gate 后请求主 Agent 响应一次。
- `engagement.enabled=true` 时启用状态机：`observing -> joining -> engaged -> cooling`，后续消息按短窗口打 batch。
- 群上下文窗口、冷却、debounce、小时上限、状态机和 batch 暂存在内存中；持久化和管理命令放到后续阶段。
- 在 `group_observe_mode` 落地前，仍依赖各 channel 的 `group_context_capture` 让未触发消息进入 `MessageObserved`。
- 秘书模型和 continue gate 只输出 JSON decision；最终群聊回复仍由主 Agent 生成。

### 7.1 配置示例

```yaml
conversation_joiner:
  enabled: false                       # 全局开关；可被 groups.<chat_key>.enabled 覆盖
  model: cheap
  threshold: 0.75
  max_context_messages: 12
  max_context_chars: 3000
  cooldown_seconds: 300
  max_triggers_per_hour: 3
  debounce_seconds: 20
  decision_timeout_seconds: 8

  persona_context:
    enabled: true
    files:
      - SOUL.md
    max_chars: 4000
    cache_ttl_seconds: 60

  prefilter:
    ignore_commands: true
    ignore_mentions: true
    min_text_chars: 4
    keyword_hints: []
    sample_rate: 0.15
    keyword_sample_rate: 1.0

  engagement:
    enabled: false                  # 打开后进入 observing/joining/engaged/cooling 状态机
    join_state_ttl_seconds: 600     # 进入话题后的默认持续观察窗口
    idle_exit_seconds: 180          # 窗口内无足够消息时退出
    max_engaged_seconds: 1800       # 单次话题参与绝对上限
    response_cooldown_seconds: 45   # engaged 状态下两次主动回复之间的最小间隔
    engagement_score_alpha: 0.2
    engagement_score_exit_threshold: 0.2
    score_decay_half_life_seconds: 600
    score_decay_floor: 0.0

    batching:
      window_seconds: 8             # 收集后续消息的短窗口
      max_messages: 6
      max_chars: 2000
      flush_on_mention: true

    continue_gate:
      enabled: true                 # 默认每个 flushed batch 都经过 continue gate
      model: cheap
      threshold: 0.55
      min_messages: 1
      evaluate_interval_seconds: 8

    exit_gate:
      enabled: true
      low_value_threshold: 0.35
      low_value_strikes: 3
      min_messages_per_window: 1
      activity_window_seconds: 120

  groups:
    milky:group:123456789:
      enabled: true
      threshold: 0.78
      cooldown_seconds: 300
      engagement:
        enabled: true
```

单群开关由 `groups.<typed_chat_key>` 覆盖：

- `conversation_joiner.enabled=false` 且某个群 `groups.<chat_key>.enabled=true`：只启用这个群的 joiner。
- `engagement.enabled=false` 且某个群 `groups.<chat_key>.engagement.enabled=true`：只让这个群进入状态机。
- 全局 `engagement.enabled=true` 且某个群 `groups.<chat_key>.engagement.enabled=false`：该群仍可走单次触发路径，但不进入 engaged/cooling 状态机。

这些配置不应让 Bot 在 `engaged` 状态中回复每一条消息。它们的职责是让 Bot 更像“正在参与话题但保持克制”：收集短窗口消息，判断是否现在接话，必要时退出。

### 7.2 处理流程

`observing` 或单次触发路径：

```text
Observed group message
  -> ignore self/bot/command/direct mention
  -> update group context window
  -> debounce per group
  -> heuristic prefilter
  -> load cached lightweight persona context
  -> call cheap secretary model
  -> parse structured decision
  -> cooldown / rate limit / active run guard
  -> request_agent_response()
```

`engaged` / `cooling` 路径：

```text
Observed group message
  -> lazy decay engagement_score
  -> update observation timestamps
  -> check exit conditions
  -> append to batch with max_messages / max_chars sliding caps
  -> wait for batching.window_seconds, unless full or flush_on_mention
  -> when flushed: run continue_gate on the whole batch
  -> reply_mode=no_reply or should_join=false: keep listening or exit by low-value policy
  -> reply_mode=direct_reply: request Agent with a specific reply anchor
  -> reply_mode=group_comment: request Agent without quoting one specific message
```

### 7.3 启发式预过滤

秘书模型不应每条消息都调用。调用前先做廉价过滤：

- 群未启用：跳过。
- 当前消息来自 Bot 自己或其他 Bot：跳过。
- 当前消息已经硬触发 Router：跳过。
- 当前 session 有 active run：跳过。
- 处于冷却或小时上限：跳过。
- 文本过短且无关键词提示：跳过。
- 未通过 `sample_rate` / `keyword_sample_rate` 概率采样门禁：跳过。
- 最近 N 秒已安排过判定：debounce。

`sample_rate` 使用运行时随机 roll，不把消息内容 hash 成固定结果。这样同一类消息不会因为 message id / 文本被永久绑定为“永远触发”或“永远不触发”。测试中通过 `sample_rate=0/1` 或替换随机函数来稳定覆盖分支。

## 8. Conversation Engagement 状态机

状态机按 typed group chat key 维护，只在该群有效配置的 `engagement.enabled=true` 时启用。它不替代硬触发；mention / command / always 仍然由 Router 直接处理，但硬触发可以反向刷新 joiner 的最近上下文和参与状态。

| 状态 | 含义 | 进入条件 | 退出条件 |
|------|------|----------|----------|
| `observing` | 只旁听，不主动回复 | 默认状态；退出话题后回到这里 | `join_gate` 判定通过 |
| `joining` | 已决定加入，正在请求主 Agent | `join_gate.should_join=true` 且通过 cooldown / active run guard | Agent run 完成后进入 `engaged` 或回到 `observing` |
| `engaged` | 已加入当前话题，持续观察并择机接话 | 主 Agent 成功回复，或显式硬触发表明 Bot 正在参与 | 退出条件满足，或进入 `cooling` |
| `cooling` | 刚刚主动回复过，短暂降频 | `engaged` 中发起一次主动回复后 | cooldown 结束后回到 `engaged`，或退出到 `observing` |

核心转移：

```text
observing
  -> join_gate true
  -> joining
  -> AgentResponseRequested
  -> Agent sends message: engaged
  -> Agent returns NO_REPLY: observing

engaged
  -> collect observed messages into batch
  -> continue_gate false: stay engaged, keep listening
  -> continue_gate true: AgentResponseRequested(batch)
  -> after response: cooling

engaged / cooling
  -> idle timeout / low value strikes / max duration / low activity
  -> observing
```

### 8.1 Engaged 状态下的消息批处理

进入 `engaged` 后，不应每条新消息都请求 join gate，也不应每条消息都请求主 Agent。当前流程：

```text
Observed group message
  -> apply lazy score decay
  -> append to engagement buffer
  -> if hard trigger: let Router handle, refresh engagement state
  -> if buffer window not ready: wait
  -> if active run or response cooldown: wait
  -> cheap continue_gate on buffered messages
  -> if continue_gate true: AgentResponseRequested(observed_messages=batch)
  -> clear flushed batch, or retain it when the request is blocked
```

批处理原则：

- 使用短窗口，例如 5-12 秒，避免主 Agent 对每条消息碎片化回复。
- 使用 `max_messages` / `max_chars` 防止 batch 过大；超过上限时从 batch 头部滑动丢弃旧消息，避免 blocked flush 导致 buffer 无界增长。
- mention 或命令仍然走硬触发，可以同时刷新 engaged 状态，但不由 joiner 吞掉。
- 如果窗口内用户已经换话题，continue gate 可以返回 false 或要求退出。
- 主 Agent 仍可返回 `NO_REPLY`，这会降低 engagement score。

continue gate 的 `reply_mode` 控制主 Agent 回复时如何引用消息：

| `reply_mode` | 行为 |
|--------------|------|
| `direct_reply` | 需要回答 batch 中某一条消息；`reply_anchor_message_id` 必须指向 batch 内有效 message id，Router 会尽量引用这条消息 |
| `group_comment` | 对整个 batch 做群聊式接话；Router 禁用单条引用，避免错误引用最后一条 |
| `no_reply` | 本轮不请求主 Agent，只更新 engagement score / low-value strikes |

如果 `direct_reply` 没有给出有效 anchor，joiner 会使用 batch 最后一条作为 Agent anchor，但传给 Router 的 `reply_to_message_id` 为空字符串，表示不要强制引用，避免 quote 到错误消息。

### 8.2 Engagement Score 与时间衰减

`engagement_score` 同时受事件反馈和时间影响：

```text
event update: score = score * (1 - alpha) + signal * alpha
time decay:   score = floor + (score - floor) * 0.5 ** (elapsed / half_life)
```

其中：

- `alpha` 来自 `engagement_score_alpha`。
- `half_life` 来自 `score_decay_half_life_seconds`，表示每过一个半衰期，分数距离 floor 的部分衰减一半。
- `floor` 来自 `score_decay_floor`，默认 0。
- 时间衰减是 lazy 计算，不持续挂后台任务；在观察消息、batch flush、`MessageSent` / `NO_REPLY` 反馈和 `/status` 状态提供器读取时更新。

### 8.3 退出策略

退出不应只靠一个固定时间。建议组合多种信号：

| 信号 | 说明 |
|------|------|
| 固定 TTL | `join_state_ttl_seconds` 到期后退出或重新评估 |
| 空闲超时 | `idle_exit_seconds` 内没有足够新消息则退出 |
| 绝对上限 | `max_engaged_seconds` 防止单次话题无限持续 |
| 消息密度 | `activity_window_seconds` 内消息数低于阈值时退出 |
| 价值下降 | 连续 `continue_gate=false`、低置信度或主 Agent `NO_REPLY` 累积为 low value strike |
| active run | 主 Agent 已在跑时不新增主动请求，只继续缓冲或丢弃过期 batch |

当前实现已覆盖固定 TTL、空闲超时、绝对上限、消息密度、low-value strikes 和 engagement score 阈值。`exit_gate.enabled=false` 只关闭低活跃度和低价值退出；TTL、idle 和绝对上限仍然作为安全边界保留。

### 8.4 是否进入核心

状态机策略先保留在 `conversation_joiner` 这个内置插件里。原因：

- 加入、续聊、退出的阈值和人格化策略会频繁调整，放在插件里更容易实验。
- Router 应保持清晰：处理硬触发、命令、Agent run 和上下文注入，不直接决定 Bot 是否主动聊天。
- 不同 Bot 人设可能需要不同 engagement 策略，核心不应把某一种策略固定为默认行为。

当前核心已补齐这些通用能力：

- `AgentResponseRequested` 的 batch 扩展。
- Router 对 proactive batch 的 user-context 注入。
- `reply_to_message_id` 覆盖语义，用于区分默认引用、禁用引用和强制引用。
- active session 查询，避免 `/new` 后 joiner 继续请求旧 session。
- active run 冲突向发布方抛错，让 joiner 能保留 batch 并稍后重试。
- `MessageSent` 反馈与 joiner 侧 `NO_REPLY` 监视，用于更新 engagement score。

仍待补齐的核心能力：

- 正式的 `group_observe_mode`，让观察和触发彻底分离。
- 可查询的 observed group context buffer，避免各插件重复维护窗口。

因此推荐边界是：**策略在插件，基础设施在核心**。等状态机稳定后，可以把它提升为 core service，但仍不应进入 Router 的同步触发判断路径。

## 9. Gate 模型与秘书模型

秘书模型不生成最终群聊回复。当前实现使用多个 cheap gate / rule gate：

| Gate | 使用状态 | 职责 |
|------|----------|------|
| `join_gate` | `observing` | 判断是否进入当前话题 |
| `continue_gate` | `engaged` | 判断 buffered messages 是否值得现在回复 |
| `exit_gate` | `engaged` / `cooling` | 规则型退出门，控制低活跃度和 low-value strikes 退出 |

`join_gate` 输入包括：

- 当前消息。
- 最近 N 条群聊观察上下文。
- 轻量人设上下文，默认从 active workspace 读取 `SOUL.md`，只用于判断“主 Agent 是否适合自然加入”，不用于生成最终回复。
- 当前群配置中的阈值和冷却参数。
- 简短行为准则。

`continue_gate` 输入包括：

- 当前 batch 的每条消息，包含 message id、sender、timestamp、text 和 message_context envelope。
- engaged 状态下的行为准则：只决定“现在是否值得接话”，不写最终回复。
- 当前阈值、batch 大小、回复风格要求。

`join_gate` 输出必须是 JSON：

```json
{
  "should_join": true,
  "confidence": 0.78,
  "reason": "群里正在讨论 Bot 可以自然补充的技术问题",
  "entry_style": "short_comment",
  "focus": "解释最近提到的概念，不要显得像被点名"
}
```

`continue_gate` 输出在上述字段基础上增加：

```json
{
  "reply_mode": "direct_reply",
  "reply_anchor_message_id": "message-id-from-batch"
}
```

`reply_mode` 只能是 `direct_reply`、`group_comment` 或 `no_reply`。

join gate 通过条件：

- `should_join == true`
- `confidence >= threshold`
- `reason` 非空
- JSON 解析成功
- 未触发冷却、频率和 active run guard

continue gate 通过条件：

- `should_join == true`
- `confidence >= continue_gate.threshold`
- `reply_mode != "no_reply"`
- active run / response cooldown 未阻塞

失败策略：

- JSON 解析失败：记录 debug，跳过。
- LLM 超时：记录失败退避，不重试当前消息。
- 置信度不足：跳过。
- 秘书模型建议加入但主 Agent 返回 `NO_REPLY`：正常记录，不视为失败。

## 10. 状态持久化

当前状态机是 in-memory MVP。`GroupJoinerState` 已有序列化 / 反序列化 hook，但插件还没有把运行态写入 `plugin_data`，因此进程重启后 engagement 状态、batch、冷却窗口会重置。

后续持久化可使用 `plugin_data`，key namespace 属于 `conversation_joiner`。

| Key | Value | 说明 |
|-----|-------|------|
| `group:{chat_key}` | `GroupJoinerConfig` | 动态分群配置覆盖 |
| `state:{chat_key}` | `GroupJoinerState` | 冷却、最近触发、当前 engagement 状态 |
| `buffer:{chat_key}` | `ObservedMessageBatch` | 可选的短期消息 buffer，默认可只保留内存 |
| `decision:{chat_key}:{message_id}` | `SecretaryDecision` | 可选调试记录，默认短期保留 |

`GroupJoinerState`：

```python
class GroupJoinerState:
    chat_key: str
    state: Literal["observing", "joining", "engaged", "cooling"]
    topic_started_at: float
    state_updated_at: float
    last_decision_at: float
    last_triggered_at: float
    last_agent_reply_at: float
    last_observed_at: float
    triggered_timestamps: list[float]
    observation_timestamps: list[float]
    low_value_strikes: int
    engagement_score: float
    score_updated_at: float
```

`ObservedMessageBatch` 至少包含：

```python
class ObservedMessageBatch:
    chat_key: str
    started_at: float
    messages: list[InboundMessage]
    total_chars: int
```

第一版状态机已选择只在内存里保存 batch。后续持久化 `GroupJoinerState` 更重要，因为它影响冷却、退出和重启后的刷屏风险；batch 可以继续只保留内存，避免重启后回复过期消息。

## 11. 权限

```yaml
permissions:
  filesystem:
    read:
      - workspace
  plugin_data:
    read: true
    write: true
  llm_access: true
capabilities:
  emits:
    - AgentResponseRequested
```

当前实现使用 `capabilities.emits` 做最小 allowlist。后续如果权限模型需要更细粒度，可以再扩展为：

```yaml
permissions:
  events:
    emit:
      - AgentResponseRequested
```

`request_agent_response()` 保持为显式窄口，不复用完全开放的 `publish_event()`。

## 12. 安全与防滥用

1. 全局默认关闭，分群默认关闭。
2. 只支持 typed group address。
3. 不响应 private target。
4. 每群有冷却和小时上限。
5. 秘书模型不允许工具调用。
6. 秘书模型输出 token 极小，并强制 JSON。
7. 主 Agent 仍可用 `NO_REPLY` 二次否决。
8. active run 期间不重复触发。
9. 管理命令写操作必须配置管理员。
10. `engaged` 状态仍必须遵守 cooldown、小时上限和最大参与时长。
11. batch 请求必须有 `max_messages` / `max_chars`，不能把无限群聊记录塞给主 Agent。

## 13. 实施 checklist

### Phase 0：群触发基础语义

> 状态：已完成（2026-06-08）

- [x] `GroupTriggerMode` 增加 `none`。
- [x] 修正 `mention`：裸 command 不再触发。
- [x] 修正 `command`：裸 command 或 mention 都触发。
- [x] 更新 Milky converter 的早期过滤语义。
- [x] 更新 Telegram / OneBot / Milky config 类型与文档。
- [x] 补测试：`none`、严格 `mention`、`command` 合并语义。

### Phase 1：观察模式与 Agent 请求事件

- [x] 设计并实现 `AgentResponseRequested` 事件。
- [x] 增加 `BotAPI.request_agent_response()`。
- [x] Router 订阅 `AgentResponseRequested` 并以 `source_tag="proactive_join"` 进入 AgentLoop。
- [x] 主动入话题请求跳过命令匹配，避免把观察消息当成用户命令。
- [x] 增加 `capabilities.emits` 最小 allowlist。
- [x] 将 proactive join 基础约束、插件 instruction 和 batch 上下文注入本轮 Agent 运行上下文。
- [x] 补测试：插件请求 Agent 响应、权限拒绝、target 拒绝、source_tag/指令注入。
- [x] 保持 `group_context_capture` 兼容观察路径；正式 `group_observe_mode` 迁移放到 Phase 6。

### Phase 2：ConversationJoiner 单次触发 MVP

- [x] 新增 `nahida_bot/plugins/conversation_joiner/`。
- [x] 实现内置插件发现和配置 schema 暴露。
- [x] 实现配置解析和分群覆盖。
- [x] 实现群上下文窗口和 debounce。
- [x] 实现启发式预过滤。
- [x] 实现 `sample_rate` / `keyword_sample_rate` 概率采样门禁。
- [x] 默认读取 workspace `SOUL.md` 作为秘书模型轻量人设上下文，并加 TTL 缓存。
- [x] 通过 `BotAPI.llm_chat()` 调用便宜秘书模型。
- [x] 解析结构化 JSON decision。
- [x] 判定通过后调用 `request_agent_response()`。
- [x] 补测试：秘书模型 should_join false/true、置信度阈值、冷却、并发主 run 保护。

### Phase 3：Conversation Engagement 状态机

- [x] 增加 `engagement` 配置模型：TTL、idle exit、max duration、response cooldown、batching、continue gate、exit gate、score decay。
- [x] 增加 per group `GroupJoinerState`：`observing` / `joining` / `engaged` / `cooling`。
- [x] `join_gate` 通过后从 `observing` 进入 `joining`，主 Agent 成功回复后进入 `engaged`。
- [x] 主 Agent 返回 `NO_REPLY` 时降低 engagement score，并按策略回到 `observing`。
- [x] `engaged` 状态下维护 observed message buffer，不再每条消息都走 join gate。
- [x] 实现 batching：短窗口、最大消息数、最大字符数、mention flush。
- [x] 实现 `continue_gate`：对 buffered messages 判断是否现在回复。
- [x] 实现 `exit_gate` / 规则退出：固定 TTL、idle timeout、消息密度、low value strikes、绝对上限。
- [x] 实现 lazy engagement score 时间衰减。
- [x] active run / cooldown 期间只缓冲或丢弃过期 batch，不重复请求 Agent。
- [ ] 将 cooldown / hourly budget / engagement state 持久化到 `plugin_data`。
- [x] 补测试：状态转移、NO_REPLY 退出、batch flush、continue false 保持沉默、idle/TTL 退出、active run 保护。

### Phase 4：Batch Agent Request 与核心基础能力

- [x] 扩展 `AgentResponseRequestPayload`，支持 `observed_messages` batch 和 `reply_to_message_id`。
- [x] 扩展 `BotAPI.request_agent_response()`，允许插件传入 batch 和 reply anchor。
- [x] Router 注入 batch 时保留 sender、timestamp、message_id、text，而不是简单拼接。
- [x] SessionRunner 的 proactive instruction 区分单条主动入话题和 batch 接话。
- [x] 使用 `MessageSent` 反馈和 joiner 侧 `NO_REPLY` 监视更新 engagement score。
- [ ] 提供核心 observed group context 查询 API，避免各插件重复维护窗口。
- [x] 补测试：batch payload 权限、Router 注入、NO_REPLY 反馈、上下文大小限制。

### Phase 5：调试与管理

- [ ] 管理命令：`/conversation_joiner status|enable|disable|test`。
- [x] 通过 status provider 将当前 engagement state 接入 `/status`。
- [ ] 可选记录最近 join/continue/exit decision，用于调试。
- [ ] WebUI 展示分群配置、当前 engagement state、最近 decision、触发原因。
- [ ] 增加 cost/latency 统计。
- [ ] 增加 secretary / gate failure backoff 和可观测统计。

### Phase 6：观察模式重构

- [ ] 正式新增 `group_observe_mode`。
- [ ] 减少 channel converter 内的早期策略过滤。
- [ ] 让普通群消息可被观察，但不默认进入 AgentLoop。
- [ ] 迁移配置文档和 channel plugin 测试。

## 14. 测试矩阵

| 测试 | 重点 |
|------|------|
| 触发模式 | `none`、严格 `mention`、`command` 合并语义、`always` |
| 观察模式 | 未触发消息可进入观察路径，但不进 AgentLoop |
| Agent request | 插件通过专用事件请求 Agent 响应 |
| Batch request | 多条 observed message 能作为 batch 注入主 Agent |
| Secretary model | JSON 解析、置信度、超时、失败退避 |
| Engagement state | observing/joining/engaged/cooling 状态转移 |
| Continue gate | engaged 状态下 batch 通过/拒绝后行为正确 |
| Exit gate | TTL、idle、低消息密度、low value strikes 触发退出 |
| 安全 | 裸 id 拒绝、private 拒绝、非管理员写操作拒绝 |
| 并发 | active run 时不重复触发 |
| NO_REPLY | 主 Agent 二次否决后不发送消息 |

## 15. 风险

1. 在 `group_observe_mode` 落地前，未触发群消息仍依赖 `group_context_capture` 才能被插件看到。
2. `conversation_joiner` 会增加 LLM 调用成本，必须有启发式预过滤和速率限制。
3. 如果直接复用 `MessageReceived`，会混淆真实入站与插件主动触发，必须避免。
4. 当前事件系统不收集 handler 返回值，不适合作为同步 routing decision hook。
5. 状态机当前仍是 in-memory MVP；重启会清空 engagement 状态、batch 和冷却窗口，后续需要持久化关键状态。
6. batch 注入如果只拼字符串，会丢失 sender/timestamp/message_id，影响主 Agent 判断和审计。
7. `engaged` 状态可能让 Bot 显得过度参与，必须有 TTL、idle exit、低价值退出和小时上限。

## 16. 互动事件接入（戳一戳等）

> 记录时间：2026-06-29
> 来源：GitHub Issue #15（戳一戳事件）。#15 的 M1（通道接收 → 解析 → 类型化 `PokeEvent` → EventBus 派发）已完成；本节设计「订阅方如何消费」，即接入 conversation joiner 的响应策略。
> 相关：[`nahida_bot_sdk/events.py`](../../nahida-bot-sdk/nahida_bot_sdk/events.py) 的 `PokeEvent` / `PokePayload`；`MessageReactionEvent`（本节暂不接入）。

### 16.1 背景

`PokeEvent` 已经作为普通事件接入 EventBus（issue #15 M1）：milky 通道把 `friend_nudge` / `group_nudge` 归一成 `PokeEvent` 派发，通道层只负责「送事件」，不耦合响应策略。剩下的问题是 conversation joiner **作为订阅方** 怎么消费它。

### 16.2 核心判断：戳一戳是弱信号

戳一戳是**比 mention / 关键词都弱得多**的群成员互动信号，不是「直接召唤」。它成本低、频率相对较高，更多表达成员之间的轻互动，而非对 Bot 的强请求。

这一点决定了所有后续设计：

- **不享受强信号待遇**：不立即 flush、不打破冷却、不强制回复。
- **走和普通消息一样的状态机流程**：合成后进 batch、进上下文、由便宜 gate 判断，复用既有的冷却 / 频率上限 / 并发保护。
- 弱信号程度的判断**交给 gate 模型**，不写死成规则。

### 16.3 目标与非目标

#### 目标

1. 把戳一戳作为「带标记的普通消息」接入状态机，复用全部既有门控与状态转换，不新增 FSM 状态。
2. 戳一戳作为独立的、可配置的输入类别（自己的 sample 系数），满足「哪些事件进入 / 以多大权重进入可由配置注入」。
3. 通道层保持 dumb：只负责派发 `PokeEvent`，响应策略全部留在 joiner 内部，与 issue #15 的分层一致。
4. 弱信号判断下沉到 secretary / continue_gate，通过渲染 `extra_tags` 让模型看到 `poke` 标记后自行权衡。

#### 非目标

1. 不为戳一戳新增 FSM 状态（`observing/joining/engaged/cooling` 四态描述的是 Bot 的对话姿态，戳一戳是一种可落在任意态的输入）。
2. 不在 `engaged` 立即 flush、不在 `cooling` 打破冷却（那是强信号才有的待遇）。
3. 不在本期接入 `MessageReactionEvent`：当前它捕获所有群内反应，无法廉价过滤「对 Bot 自己消息的反应」，需先补 sent-`message_seq` 对账机制。
4. 不在本期支持 friend 场景戳一戳：状态机与 `request_agent_response` 都是 group-only。
5. 不为戳一戳单设互动专用频率桶：既有 `max_triggers_per_hour` / `cooldown_seconds` / batch 窗口已天然限流，除非实测证明戳一戳挤占了正常 join 预算，否则不分桶。

### 16.4 归一：`PokeEvent` → 带标记的 `InboundMessage`

`request_agent_response` 强制要求真实的 `InboundMessage` + typed group 地址（非 group 直接抛 `ValueError`）。因此接入点是：在 joiner 内部把 `PokeEvent` 合成一个 `InboundMessage`，字段映射如下（**全部复用既有字段，不新增**）：

| `InboundMessage` 字段 | 取值 | 作用 |
|---|---|---|
| `text` | 固定格式，如 `"[{poker}] 戳了戳你"` | 让 prefilter 的 `min_text_chars` 自然通过；进 `_contexts` 作为上下文 |
| `raw_event` | `{"poke": True, "scene": "group", **原始 payload}` | 机器可读 envelope |
| `message_context.extra_tags` | `("poke",)` | **专门给 LLM 看的结构化标记**（该字段 docstring 原话即 "structured LLM-visible context data"）|
| `sender_context` | poker 的 display_name / platform_user_id，`is_self=is_bot=False` | 避免 prefilter 的 `bot_or_self` 误杀 |
| `mentions_bot` | **`False`** | ⚠️ 关键：绝不能设 True，否则 engaged 态会触发 `flush_on_mention` 立即 flush，变回强信号处理 |
| `message_id` | 合成 `poke:{user_id}:{ts}` | 给 batch anchor / reply 使用 |

合成完成后，直接调用 joiner 内部既有的消息处理路径（不必真的发布一个 `MessageObserved` 事件），即可让戳一戳从 prefilter 起完全走普通消息流程。

### 16.5 门控：第三种概率（调低）

戳一戳是独立可配置类别，对应第三个 sample 系数，与 `sample_rate`（无关键词）/ `keyword_sample_rate`（有关键词）并列：

```text
sample_rate = poke_sample_rate        if poke
            = keyword_sample_rate     if keyword_hit
            = sample_rate             otherwise
```

因为「弱信号 + 高频」，`poke_sample_rate` **默认应低于 keyword，甚至低于 ambient**，否则戳一戳会主导主动发言预算。

「偶尔无视」由两层叠加实现（对应既定回复策略）：

1. **sample gate**（`poke_sample_rate` 低）：廉价层先砍掉大部分戳一戳。
2. **secretary**：prompt 渲染 `extra_tags` 后，模型看到 `poke` 标记自然倾向判「不值得加入」。弱信号程度由模型判断，不写死规则。

### 16.6 每态语义

| 状态 | 戳一戳行为 | 与普通消息的差异 |
|---|---|---|
| `observing` | prefilter → `poke_sample_rate` → secretary（看 `poke` tag）→ join | 仅 sample 系数不同 |
| `joining` | 忽略（等自己回复） | 无 |
| `engaged` | **进 batch，不立即 flush** | 无（靠 `mentions_bot=False` 保证不被 `flush_on_mention` 截获）|
| `cooling` | **进 batch，等冷却结束** | 无，**不打破冷却** |

唯一需要小心维护的「例外」是 `mentions_bot=False` —— 而这本质上正是「不要给它特殊待遇」，与「全同普通消息」方向一致。

### 16.7 配置注入

新增 `poke` 子配置挂在 `prefilter` 下（与现有 `sample_rate` / `keyword_sample_rate` 同层），并支持 per-group 覆盖（既有 `GroupJoinerConfig` 已支持）：

```yaml
conversation_joiner:
  prefilter:
    sample_rate: 0.15
    keyword_sample_rate: 1.0
    poke_sample_rate: 0.1          # 弱信号，默认低
    poke_text_template: "[{poker}] 戳了戳你"
    enable_poke: true              # 哪些事件进入：总开关
  engagement:
    enabled: true
  groups:
    "<group-key>":
      prefilter:
        poke_sample_rate: 0.0      # 某群完全忽略戳一戳
```

`enable_poke` + per-group 的 `poke_sample_rate` 共同实现「具体哪些事件进入、以多大权重进入可由配置注入」。未来接入 `MessageReactionEvent` 时，新增 `reaction_sample_rate` / `enable_reaction` 即可，模式一致。

### 16.8 归一落点：策略留 joiner，通道保持 dumb

`PokeEvent` → 带标记 `InboundMessage` 的合成**放在 conversation joiner 内部**（订阅 `PokeEvent`，合成后喂进自己的处理路径），而非放在 milky 通道。

理由即 issue #15 的哲学：通道只负责送 `PokeEvent`，响应策略由订阅方决定。「戳一戳 = 弱信号、进 batch、低概率」这套**策略**若固化进通道层，`PokeEvent` 抽象就形同虚设，且未来换状态机策略就得改通道。所以通道继续 dumb，只发 `PokeEvent`；策略全部留在 joiner。

### 16.9 实施 checklist（Phase 7）

1. `JoinerPrefilterConfig` 增加 `poke_sample_rate` / `poke_text_template` / `enable_poke`。
2. joiner `on_load` 中订阅 `PokeEvent`，新增 `_on_poke` handler。
3. 新增 `PokeEvent` → 带标记 `InboundMessage` 的合成函数（注意 `mentions_bot=False`、`extra_tags=("poke",)`、合成 `message_id`）。
4. 合成消息接入既有 `_handle_observed` / `_handle_with_state_machine` 路径；在 sample 选择处加 `poke` 分支。
5. `_build_secretary_prompt` / `_build_continue_gate_prompt` 渲染 `extra_tags`（当前未渲染），让模型看到 `poke` 标记。
6. 单测：戳一戳合成字段正确性、`observing` 第三概率门、`engaged` 进 batch 不 flush、`cooling` 不打破冷却、`enable_poke=false` 丢弃。

### 16.10 风险

1. `poke_sample_rate` 设高了会主导主动发言预算，必须默认低并有 per-group 覆盖。
2. 若遗漏 `mentions_bot=False`，戳一戳在 engaged 态会被 `flush_on_mention` 当成 mention 立即 flush，退化为强信号——属回归风险，必须有测试覆盖。
3. 合成消息进入 `_contexts` 会占据上下文配额（`max_context_messages` / `max_context_chars`），高频戳一戳可能挤占真实对话上下文，需观察。
4. secretary / continue_gate 的 prompt 必须真的渲染 `extra_tags`，否则模型看不到 `poke` 标记，弱信号判断失效。
5. 合成 `message_id` 需保证稳定且不与真实消息碰撞，否则 batch anchor / reply 锚定可能错乱。
