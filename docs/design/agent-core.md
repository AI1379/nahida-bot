# Agent Core 设计

> 整合自 agent-core-rebuild、agent-loop-optimization、agent-compaction-design 三篇文档。
> 状态：部分实现。协议止血、事件流、流式发送已落地；强类型 Transcript、语义压缩、并行工具执行仍未实现。

---

## 1. 现状与问题

### 1.1 核心问题

近期 agent 系统反复出现可用性问题：

- 模型说"我去做/我来查/让我执行"，但没有真正发起结构化工具调用，AgentLoop 随即终止。
- OpenAI-compatible / DeepSeek 请求偶发 400：
  - `An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'`
  - `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`
- Provider、ContextBuilder、SessionRunner、Memory 之间共享的 transcript 结构过于松散，很多关键不变量只能靠 `ContextMessage.metadata` 隐式维持。

### 1.2 不建议替换成第三方框架

不建议整体替换成 LangChain、LlamaIndex、AutoGen 等现成框架：

- 当前系统已深度集成 Telegram/Milky、workspace、plugin tools、MCP、subagent、provider switching、多模态和 memory。迁移成本高。
- 核心问题是 transcript/protocol 不变量没有建模清楚。第三方框架也未必能避免协议适配 bug。
- 替换框架可能把问题从"本地代码可调试"变成"框架黑盒 + 适配层不透明"。

合理方向：保留项目外壳，重建 agent core 的内部脊梁。

---

## 2. 已落地的临时修复

1. `ContextBuilder` 在滑动窗口裁剪时，将 `assistant(tool_calls)` 和后续连续 `tool` 结果作为原子组处理，避免预算裁剪拆开工具调用 transcript。
2. `OpenAICompatibleProvider` 在发送请求前清洗 tool transcript：
   - 丢弃孤立 `tool` message。
   - 丢弃没有完整 tool result 的 assistant `tool_calls` 组。
   - 输出协议诊断日志。
3. `SessionRunner._load_history()` 从 memory 恢复历史时保留 `metadata`，避免 `tool_call_id`、assistant `tool_calls`、reasoning 等信息在跨轮恢复时丢失。
4. `AgentLoop.run_stream()` 已输出 `LoopEvent(text/tool_start/tool_end/done)`，Router/SessionRunner 已能消费流式事件。
5. provider/tool 调用已有 retry、timeout、metrics 和 tool lifecycle metadata。
6. DeepSeek reasoning replay、OpenAI Responses native replay、Anthropic tool/result 序列化均已有 provider 级适配测试。
7. 已有 `/stop` 命令、`ActiveRunTracker` 和 `stop_event`，可以请求取消当前 session 的 agent run。
8. 动态上下文预算已接入模型级 `context_window`（详见 [model-context-budget](../architecture/model-context-budget.md)）。

### 仍存在的缺口

- 没有强类型 `AgentTranscript` / `AgentItem` 数据模型，也没有统一 invariant validator。
- 工具调用仍在 `_execute_tools()` 中串行执行。
- tool result 的 `content` 完整进入上下文，只截断 `logs`，没有对大 `output` 做 token/字节中间截断。
- 没有 mid-loop compaction 和 LLM 语义压缩；仍是滑动窗口 + 简单摘要。
- tool call/result 不会作为完整 agent run transcript 持久化。
- provider `usage` 尚未反哺 `ContextBuilder` 的预算管理。

---

## 3. 目标架构

### 3.1 强类型 AgentTranscript

用明确的数据结构替代松散的 `ContextMessage + metadata` 协议。

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
- 缺 result 的 tool call 组必须显式标记为 aborted/cancelled/error。
- 历史裁剪、持久化、provider 序列化都必须基于同一组 transcript invariants。

### 3.2 AgentEvent 作为 loop 内部事实来源

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

### 3.3 Provider adapter 边界收紧

每个 provider adapter 只做两件事：

```text
native request <- AgentTranscript + available tools
native response -> AgentEvent / AgentItem
```

### 3.4 ContextBuilder 只处理 transcript 视图

ContextBuilder 不应直接操作 provider 格式。它应该输入 `AgentTranscript`，输出一个经过预算控制的 transcript slice。

裁剪策略：

- tool call/result 作为原子组。
- tool output 可单独截断，但不能丢 call id。
- reasoning 优先丢弃或摘要化。
- summary 不能插入到 tool call 和 tool result 中间。

### 3.5 Memory 持久化完整 agent run

保留现有 `ConversationTurn` 作为用户可读历史，同时新增 agent run 持久化：

```text
agent_runs
  - run_id, session_id, status, started_at, completed_at

agent_events
  - run_id, index, event_type, payload_json
```

---

## 4. 优化方向

> 通过对比 Codex (OpenAI CLI) 的 agent loop 实现识别的优化方向。本节聚焦单个 `AgentLoop` 的循环与上下文优化；多 Agent / Subagent 的完整架构见 [agent-orchestration](../architecture/agent-orchestration.md)。

### 4.1 架构对比总览

| 维度 | nahida-bot | Codex |
|------|-----------|-------|
| 语言 | Python (asyncio) | Rust (Tokio) |
| 核心循环 | `AgentLoop.run()` | `run_turn()` |
| 上下文管理 | `ContextBuilder` | `ContextManager` |
| 历史结构 | 按 turn 对持久化到 SQLite | 扁平 `Vec<ResponseItem>` |
| 压缩策略 | 滑动窗口 + 简单摘要 | 多层：截断 → 摘要压缩 → 远程压缩 |
| 工具执行 | 串行 | 并行执行，RwLock 控制并发 |

### 4.2 优化路线图

#### Phase 1：基础完善（高优先级）

1. [x] 配置接线 — `RouterConfig.max_history_turns` 正确传入 `SessionRunner`
2. [x] 提高 token 预算 — 默认值提升，并按模型实际 context window 重算预算
3. [ ] 工具输出截断 — 为 tool result 添加 token 预算，超出时中间截断
4. [ ] 利用 provider usage — 将 provider 返回的 token usage 信息用于上下文预算管理

#### Phase 2：核心优化（中优先级）

1. [ ] 并行工具执行 — `asyncio.gather` 替代串行 for 循环
2. [ ] 中途压缩检测 — Agent loop 每步后检查 token 用量，超限时触发压缩
3. [ ] LLM 语义压缩 — 引入 LLM 生成结构化摘要替代简单的字符截取摘要
4. [ ] 分类丢弃策略 — 压缩时按类型优先级丢弃：tool output > reasoning > assistant > user
5. [ ] 持久化 tool call/result — 可选地将关键 tool interaction 持久化
6. [~] 历史规范化 — 已有原子组裁剪和发送前清洗；仍缺统一 transcript validator

#### Phase 3：高级特性（低优先级）

1. [x] 取消/中断机制 — 已有 `/stop`、`ActiveRunTracker` 和 `stop_event`
2. [ ] 增量上下文更新 — 缓存 prefix 部分，只更新动态部分
3. [~] 流式 reasoning 输出 — `LoopEvent.text` 可携带 reasoning，但 provider token 级流式尚未贯通
4. [ ] Hook 系统 — 在关键时机提供扩展点

---

## 5. Compaction 设计

> 当前 `ContextBuilder` 的滑动窗口和 `history_summary` 只是最后防线，不是 compact。真正的 compact 应该是一次 **transcript 变换**。

### 5.1 核心概念

compact 流程：

1. 读取会话中模型可见的完整 transcript。
2. 选择一个安全 cut point，把旧上下文压缩成语义摘要。
3. 保留最近尾部消息，尤其是当前用户请求、assistant tool call、tool result。
4. 把 compact checkpoint 持久化到会话状态。
5. 后续 turn 从"compact summary + recent tail"继续。

参考优先级：

- **主要参考 Codex**：扁平 history、tool output 截断、pre-turn / mid-turn / manual compact、replacement history。
- **吸收 OpenClaw**：manual `/compact`、overflow 后 compact-and-retry、compaction entry 持久化。

MVP 不做远程 `/compact` API，先实现本地 LLM summary + SQLite checkpoint。

### 5.2 数据模型

新增 `session_compactions` 表：

```sql
CREATE TABLE IF NOT EXISTS session_compactions (
    compaction_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    trigger TEXT NOT NULL,        -- manual / pre_turn / mid_loop / overflow_retry
    reason TEXT NOT NULL,
    summary TEXT NOT NULL,
    first_kept_turn_id INTEGER,
    tokens_before INTEGER NOT NULL DEFAULT 0,
    tokens_after INTEGER NOT NULL DEFAULT 0,
    provider_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
```

后续 prompt 的 active view：

```text
[system baseline / workspace instructions]
[latest compaction summary as user message]
[items >= first_kept_item_id]
[current protected active turn]
```

### 5.3 触发策略

| 触发方式 | 时机 | 说明 |
|---------|------|------|
| 手动 `/compact` | 用户命令 | 强制 compact 当前 session |
| Pre-turn 自动 | `run_stream()` 加载 history 后 | `estimated_tokens >= trigger_ratio × context_window` |
| Mid-loop 自动 | 工具执行后、下次 provider call 前 | 仅当 active prompt 超过 hard threshold |
| Overflow retry | provider 返回 context overflow 后 | compact-and-retry 一次 |

建议默认配置：

```yaml
context:
  compaction:
    enabled: true
    auto_enabled: true
    trigger_ratio: 0.85
    hard_ratio: 0.95
    reserve_tokens: 2000
    keep_recent_tokens: 12000
```

### 5.4 Compact 算法

1. **Build Candidate Transcript**：排除 system baseline、transient group observed context、raw media data。
2. **Output Truncation First**：tool output 先单独截断（head+tail），保留 JSON 外壳和 status/error。
3. **Select Cut Point**：按 transcript group 分组，从最新组往前累加 recent tail，到达 `keep_recent_tokens` 后停止。cut point 不拆 tool group。
4. **Summarization**：调用 LLM 生成结构化 Markdown 摘要，保留精确标识符（路径、URL、ID）。
5. **Validate Summary**：非空、token 数不超限、包含必要标题。

### 5.5 服务边界

```text
nahida_bot/agent/compaction/
  models.py       # CompactionCheckpoint, CompactionResult
  service.py      # CompactionService
  prompts.py      # Summarization prompt
  policy.py       # TranscriptPolicy (cut point, normalize, token estimate)
```

### 5.6 与 Memory 的关系

compact summary 是 **短期 transcript checkpoint**，不是长期记忆：

| 层 | 作用 | 生命周期 |
|---|---|---|
| compaction summary | 让当前 session 继续 | 随 session |
| memory item / MEMORY.md | 跨 session durable facts | 长期 |

### 5.7 落地阶段

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 0 | 补 transcript 可观测性 | 部分完成 |
| Phase 1 | Manual compact MVP（`/compact` 命令 + checkpoint 表 + active view 加载） | 未开始 |
| Phase 2 | Pre-turn auto compact + overflow retry + 配置接入 | 未开始 |
| Phase 3 | Mid-loop compact + tool output head+tail truncation | 未开始 |
| Phase 4 | Memory flush（compact 前把 durable 候选写入 memory） | 未开始 |

---

## 6. 调试指南

如果遇到 agent loop 或 provider 400，优先看这些日志：

```text
session_runner.history_context_built
provider.openai_compatible.serialized_protocol
provider.openai_compatible.dropped_incomplete_tool_transcript
provider.openai_compatible.dropped_orphan_tool_messages
provider.openai_compatible.sanitized_tool_transcript
agent_loop.terminal_without_tool_calls
```

判断方式：

- `serialized_protocol.issue_count > 0`：最终请求仍存在协议问题。
- `history_context_built.tool_messages_missing_ids > 0`：DB 里有 tool turn 缺 metadata。
- 出现 `dropped_*tool*`：上下文里已有破损 transcript，当前只是避免 400。
- `agent_loop.terminal_without_tool_calls` 且 `looks_like_tool_promise=true`：模型承诺做事但没发结构化 tool call。

---

## 7. 设计原则

- 不让 provider API 协议散落在 SessionRunner、ContextBuilder、Memory 和 Provider 之间。
- 不用纯文本摘要破坏结构化 transcript。
- 不把 tool call/result 当普通聊天消息裁剪。
- 不把最终 assistant 文本当作完整 agent run。
- 优先做可验证的不变量，再做更复杂的流式和并行能力。
