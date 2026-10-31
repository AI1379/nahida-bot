# 情绪连续性与 Inner Thoughts

Conversation Joiner 可在原有入场/续聊判断中维护当前场面、角色心情和最多三个尚未表达的想法。
它只提供有来源的短时上下文；发送消息、查询资料及工具执行仍由主 AgentLoop 完成。

## 启用

此功能默认关闭。在现有 `conversation_joiner` 配置中增加：

```yaml
conversation_joiner:
  enabled: true
  # 继续使用原有 model、groups、prefilter、engagement 配置
  inner_thoughts:
    enabled: true
    private_enabled: false
    mood_half_life_seconds: 900
    state_ttl_seconds: 3600
    thought_ttl_seconds: 600
    direct_update_interval_seconds: 20
    max_context_chars: 1800
```

群聊沿用 `default_group_enabled` / `groups.<chat_key>.enabled` 范围。私聊需要单独开启
`private_enabled`；`/status full` 可查看当前聊天的 Social Continuity。配置通过正常插件加载生效。
关闭 `inner_thoughts.enabled` 可恢复原有 gate 路径；停用整个插件后，注册的上下文提供器也会移除。

## 状态与成本

- 入场与续聊：原来的一次 gate 调用同时产出决策和 `social` 状态，没有另开独立思考循环。
  即使选择沉默，仍可保存感受与候选想法。采样、冷却、发言预算和活跃任务检查继续生效。
- 直接回复：进入 SessionRunner 时最多增加一次同一 `model` 的评价调用，默认同聊天 20 秒内复用状态。
  超时沿用 `decision_timeout_seconds`，错误时使用尚有效的旧状态，主回复照常执行。
- 无空闲轮询，不因“太久没说话”单独唤醒模型。每个状态最多三个想法、40 条短证据，内存缓存最多 128 个会话。
- `conversation_joiner.social_call` 日志记录模型、延迟及供应商返回的 input/cached/output token。
  缺少 usage 时字段为空。评估成本应分开比较调用次数、实际费用和缓存命中，不能只看命中率。

当前通用 `BotAPI.llm_chat` 接口的 `max_tokens`/`temperature` 参数没有统一下传给供应商；
实际生成限额沿用 provider 配置。本模块控制调用频率、超时和接纳的状态大小，不承诺每次调用的硬 token 上限。

情绪强度随墙上时钟按半衰期衰减；场面与想法分别受 TTL 限制。时间从引用的原始事件计算，
反复旁听同一消息不会刷新其寿命。模型可以明确清除场面/心情，也可以保留尚未说出的想法。
只有当前聊天里存在的消息 ID 和参与者 ID 能作为来源；缺失/错误来源或无效 JSON 不覆盖已有状态。
这些检查验证来源存在与作用域，不能证明模型对玩笑、情绪、意图的语义判断一定正确。

状态通过本插件的 `plugin_data` 持久化，重启可恢复。聊天、会话和人设摘要均参与隔离校验；
新会话和人设改变不继承旧状态。过期内容不会注入，但历史会话的存储行不会被自动物理删除。
状态不写入 SOUL.md、长期事实记忆或 KB。清理持久数据时只处理本插件 `social:` 前缀。

## 回复与工作边界

候选想法包含短意图、动机和证据，不保存长篇推理文本。已经选中的想法在对应回复发送确认后移除；
`NO_REPLY`、失败发送和定时通知不会将它误标为“已经说过”。下一次评价可删除过时或已被别人回答的想法。

主模型收到的是“可能有误的状态数据”，仍需核对发言者、引用和平台 mention 字段。
角色可以表现好奇、开心、尴尬或关心，但不要求每条都加比喻、emoji、安慰或追问。
模型识别为明确资料整理/工作请求时，快照会省略心情和候选想法；用户要求、证据准确性、
工具权限一直优先。cron、subagent、node 来源不调用此社交提供器，斜杠命令也跳过。
未增加 Pi/Codex 适配；它们继续走已有 exec 路径。

## 上下文与缓存

核心增加 `register_context_provider(key, handler)`：接收 `AgentContextRequest`，每次主运行取一次快照，
放在历史之后的 user-role 上下文数据里。一次 AgentLoop 的工具步骤重复使用同一份快照。
提供器沿用 prompt-supplement 的注册、停用和卸载生命周期，但不会被放进 system 指令。
解释状态的行为规则作为固定 supplement 注册一次，只有实际心情、场面与想法快照在尾部变化。

joiner 每轮变化的 `agent_instruction` 同样移到尾部数据，system 只保留稳定的参与规则。
因此不会因为心情/参与意图变化而改写最前面的 system 内容；人设和工具定义也不随心情改变。
历史窗口变化、检索结果、临时块替换和供应商缓存策略仍会影响命中，不能保证缓存率不变。

## 验证

`tests/test_social_continuity.py` 覆盖来源校验、时间衰减、沉默保留、已发想法消耗、SQLite 重载、
群/私聊/会话隔离、直接回复限频、工作快照、禁用与异步竞态、上下文续接和稳定 system 前缀。
这些是确定性回归测试，不代表真实模型的情绪表达质量已经验证。
实聊评估可用 10 月 26 日的匿名片段：同名 bot、作品转载、认错寿星、生日、连续戳人，
分别观察人物归属、发言时机、是否延续具体关注、是否反复说理，并记录 token 成本。
