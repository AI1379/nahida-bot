# Agent Core 重建计划

> 记录时间：2026-05-11
> 状态：搁置，等待后续集中处理

## 背景

近期 agent 系统反复出现可用性问题，主要表现为：

- 模型说“我去做/我来查/让我执行”，但没有真正发起结构化工具调用，AgentLoop 随即终止。
- OpenAI-compatible / DeepSeek 请求偶发 400：
  - `An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'`
  - `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`
- 短 session 也可能触发工具消息协议错误，说明问题不只来自长历史裁剪。
- Provider、ContextBuilder、SessionRunner、Memory 之间共享的 transcript 结构过于松散，很多关键不变量只能靠 `ContextMessage.metadata` 隐式维持。

这些问题消耗了大量调试时间。当前判断是：暂时不整体替换成第三方 agent 框架，但应该在后续把 agent core 的内部模型重建为更严格、可验证的结构。

## 当前临时修复

本轮已先做了止血处理：

1. `ContextBuilder` 在滑动窗口裁剪时，将 `assistant(tool_calls)` 和后续连续 `tool` 结果作为原子组处理，避免预算裁剪拆开工具调用 transcript。
2. `OpenAICompatibleProvider` 在发送请求前清洗 tool transcript：
   - 丢弃孤立 `tool` message。
   - 丢弃没有完整 tool result 的 assistant `tool_calls` 组。
   - 输出协议诊断日志。
3. `SessionRunner._load_history()` 从 memory 恢复历史时保留 `metadata`，避免 `tool_call_id`、assistant `tool_calls`、reasoning 等信息在跨轮恢复时丢失。
4. 增加诊断日志：
   - `session_runner.history_context_built`
   - `provider.openai_compatible.serialized_protocol`
   - `provider.openai_compatible.dropped_incomplete_tool_transcript`
   - `provider.openai_compatible.dropped_orphan_tool_messages`
   - `provider.openai_compatible.sanitized_tool_transcript`

这些修复能降低 400 中断概率，但不是最终架构形态。

## 不建议直接替换成第三方框架的原因

不建议现在整体替换成 LangChain、LlamaIndex、AutoGen 等现成框架，原因如下：

- 当前系统已经深度集成了 Telegram/Milky、workspace、plugin tools、MCP、subagent、provider switching、多模态和 memory。迁移外层系统成本高。
- 当前 bug 的核心是 transcript/protocol 不变量没有建模清楚。第三方框架也需要适配 DeepSeek thinking、OpenAI Responses、Anthropic thinking、MCP 和本项目 memory，未必能避免协议适配 bug。
- 替换框架可能把问题从“本地代码可调试”变成“框架黑盒 + 适配层不透明”。
- 现有 AgentLoop 外围模块仍有价值，真正需要重建的是 agent core 内部的 transcript、event、provider protocol 边界。

更合理的方向是：保留项目外壳，重建 agent core 的内部脊梁。

## 目标架构

### 1. 引入强类型 AgentTranscript

用明确的数据结构替代松散的 `ContextMessage + metadata` 协议。

建议的数据模型：

```text
AgentTranscript
  - items: list[AgentItem]

AgentItem
  - UserMessage
  - AssistantMessage
  - ToolCallBatch
  - ToolResultBatch
  - ReasoningSummary
  - BuiltinToolEvent
  - CompactionSummary
```

关键不变量：

- `ToolCallBatch` 必须和后续 `ToolResultBatch` 按 call id 完整配对。
- 孤立 `ToolResultBatch` 不允许进入 provider request。
- 缺 result 的 tool call 组必须显式标记为 aborted/cancelled/error，而不是静默丢失。
- 历史裁剪、持久化、provider 序列化都必须基于同一组 transcript invariants。

### 2. AgentEvent 作为 loop 内部事实来源

AgentLoop 应该产生事件，而不是只返回最终字符串：

```text
AssistantTextDelta
AssistantMessageDone
ToolCallRequested
ToolCallStarted
ToolCallCompleted
ToolCallFailed
ReasoningDelta
FinalResponse
LoopAborted
```

这样可以解决几个问题：

- 用户能看到中间文本和工具执行状态。
- Router/channel 可以选择流式发送、合并发送或只发送最终消息。
- Memory 可以持久化完整 transcript，而不是只存最终 assistant 文本。
- 调试时可以回放一次 agent run 的完整生命周期。

### 3. Provider adapter 边界收紧

每个 provider adapter 只做两件事：

```text
native request <- AgentTranscript + available tools
native response -> AgentEvent / AgentItem
```

Provider 不应该依赖外部模块隐式维护 metadata 格式。

需要分别处理：

- OpenAI-compatible / DeepSeek chat completions。
- OpenAI Responses。
- Anthropic Messages。
- Minimax / Groq / GLM 的兼容差异。

每个 provider 都要有 invariant tests：

- 文本 + tool calls 同时存在。
- 多个 tool calls。
- tool calls 后缺少一条 tool result。
- 孤立 tool result。
- reasoning replay。
- provider-native replay metadata。

### 4. ContextBuilder 只处理 transcript 视图

ContextBuilder 不应直接操作 provider 格式。它应该输入 `AgentTranscript`，输出一个经过预算控制的 transcript slice。

裁剪策略：

- tool call/result 作为原子组。
- tool output 可单独截断，但不能丢 call id。
- reasoning 优先丢弃或摘要化。
- assistant/user 消息按轮保留。
- summary 不能插入到 tool call 和 tool result 中间。

### 5. Memory 持久化完整 agent run

当前 memory 更像聊天记录，不适合保存 agent transcript。

建议保留现有 `ConversationTurn` 作为用户可读历史，同时新增一层 agent run 持久化：

```text
agent_runs
  - run_id
  - session_id
  - status
  - started_at
  - completed_at

agent_events
  - run_id
  - index
  - event_type
  - payload_json
```

这样：

- 用户历史仍然简洁。
- 调试和恢复可以使用完整事件流。
- 跨轮上下文可以选择“用户可读历史 + 最近完整 transcript”组合。

## 分阶段计划

### Phase 0: 稳定现状

已完成部分止血：

- 保留 history metadata。
- tool transcript 发送前清洗。
- tool call/result 原子裁剪。
- 增加协议诊断日志。

后续短期可补：

- 在 provider 400 时，把 `serialized_protocol` summary 和 response body 绑定到同一条 error 日志。
- 增加命令导出最近一次 run 的 provider request summary。
- 增加 DB 检查脚本，找出历史中孤立 tool turn 或缺 metadata 的 tool turn。

### Phase 1: Transcript 数据模型

目标：引入 `AgentTranscript`，但不立即替换全部调用链。

任务：

- 新建 transcript models。
- 写 transcript invariant validator。
- 写 `ContextMessage <-> AgentTranscript` 临时桥接。
- 给现有 bug 场景补测试。

验收：

- validator 能识别孤立 tool result、缺 tool result、重复 call id、summary 插入到工具组中间。
- 当前 AgentLoop 可继续运行，但内部先能生成 transcript。

### Phase 2: Provider adapter 改造

目标：把 provider serialization/parsing 迁移到 transcript/event 模型。

任务：

- OpenAI-compatible adapter 改为从 transcript 构造 messages。
- DeepSeek thinking replay 逻辑绑定到 transcript item，而不是散在 metadata。
- OpenAI Responses 保留 native `response_output` / `previous_response_id`，但映射到 transcript replay item。
- Anthropic thinking/tool_use 映射到统一 tool call/result batch。

验收：

- 每个 provider 的协议 invariant tests 独立通过。
- 删除大部分 provider 内部对松散 metadata 的猜测逻辑。

### Phase 3: AgentLoop 事件化

目标：AgentLoop 输出事件流，Router/SessionRunner 决定如何消费。

任务：

- `AgentLoop.run()` 内部产出 `AgentEvent`。
- 增加 callback 或 async iterator 接口。
- Router 默认只发送 final response，后续可配置发送中间事件。
- ToolExecutor 执行状态事件化。

验收：

- “模型先输出文本，再 tool call，再继续输出” 可以在事件流中完整表示。
- 即使 channel 不支持流式，也能合并出最终用户可读响应。

### Phase 4: Memory 和调试工具

目标：让 agent run 可回放、可诊断。

任务：

- 新增 agent run/event 持久化表。
- SessionRunner 持久化完整 run events。
- 增加命令查看最近 run 的 transcript/protocol summary。
- 增加 DB migration 和旧数据兼容策略。

验收：

- 线上遇到 tool protocol 400 时，可以从日志或 DB 还原最终 provider request 的协议摘要。
- 不依赖猜测上下文长度来定位问题。

## 搁置期间的调试指南

如果之后继续遇到 agent loop 或 DeepSeek 400，优先看这些日志：

```text
session_runner.history_context_built
provider.openai_compatible.serialized_protocol
provider.openai_compatible.dropped_incomplete_tool_transcript
provider.openai_compatible.dropped_orphan_tool_messages
provider.openai_compatible.sanitized_tool_transcript
agent_loop.terminal_without_tool_calls
```

判断方式：

- `serialized_protocol.issue_count > 0`：最终请求仍然存在协议问题，provider 清洗逻辑可能没覆盖某种形状。
- `history_context_built.tool_messages_missing_ids > 0`：DB 里有 tool turn 缺 metadata，或恢复流程仍丢 metadata。
- 出现 `dropped_*tool*`：说明上下文里已有破损 transcript，当前只是避免 400，不代表根因已消失。
- `agent_loop.terminal_without_tool_calls` 且 `looks_like_tool_promise=true`：模型承诺做事但没发结构化 tool call，需要看工具是否传入、模型 capability、provider response shape。

## 设计原则

- 不让 provider API 协议散落在 SessionRunner、ContextBuilder、Memory 和 Provider 之间。
- 不用纯文本摘要破坏结构化 transcript。
- 不把 tool call/result 当普通聊天消息裁剪。
- 不把最终 assistant 文本当作完整 agent run。
- 优先做可验证的不变量，再做更复杂的流式和并行能力。

## 暂不处理的问题

以下问题相关但不在第一轮重建范围：

- 多 agent / subagent orchestration 的完整调度模型。
- 前端或 channel 的实时流式 UI。
- 复杂权限模型和可撤销工具调用。
- 远程压缩服务。
- 第三方 agent 框架接入。

这些可以在 transcript 和 event 模型稳定后再评估。
