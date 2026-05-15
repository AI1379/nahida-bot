# Agent Compaction 设计

> 状态：设计草案
> 日期：2026-05-15
> 目标：为 nahida-bot 增加真正可恢复、可审计、可自动触发的 compact/compaction 机制，解决长任务中上下文膨胀、工具结果丢失、模型重复“我要调用工具但没有继续”的问题。

---

## 1. 结论

当前 `ContextBuilder` 的滑动窗口和 `history_summary` 只是最后防线，不是 compact。真正的 compact 应该是一次 **transcript 变换**：

1. 读取会话中模型可见的完整 transcript。
2. 选择一个安全 cut point，把旧上下文压缩成语义摘要。
3. 保留最近尾部消息，尤其是当前用户请求、assistant tool call、tool result。
4. 把 compact checkpoint 持久化到会话状态。
5. 后续 turn 从“compact summary + recent tail”继续，而不是每次重新滑动窗口丢消息。

参考优先级：

- **主要参考 Codex**：ContextManager 扁平 history、tool output 截断、pre-turn / mid-turn / manual compact、replacement history、initial context reinjection、remote compact filter、history version。
- **吸收 OpenClaw**：manual `/compact`、context overflow 后 compact-and-retry、compaction entry 持久化、tool call/result split boundary、memory flush、successor transcript 和可观测 compaction count。

MVP 不做远程 `/compact` API，也不做复杂 branch transcript；先实现本地 LLM summary + SQLite checkpoint。

---

## 2. 现状

### 2.1 已有能力

- `AgentLoop` 每步都会调用 `ContextBuilder.build_context()`。
- `ContextBuilder` 有预算估算、滑动窗口、简单 `history_summary`。
- 当前 active turn 已拆成 `protected_messages`，预算紧张时会优先保留：
  - 当前用户请求
  - assistant tool-call 消息
  - tool result 消息
- `OpenAICompatibleProvider` 会在序列化前 sanitize tool transcript，避免 orphan tool result 或缺失 tool result 直接导致 provider 400。
- `SessionRunner` 每轮结束后会做 durable memory consolidation，但这不是 transcript compact。

### 2.2 当前缺口

- 只有 user turn 和最终 assistant turn 持久化，tool call / tool result / 中间 assistant 消息没有完整跨轮保留。
- `history_summary` 是机械摘要，每条旧消息截前 120 字符，不理解任务进度。
- 没有 manual `/compact`。
- 没有 pre-turn auto compact。
- 没有 provider context overflow 后 compact-and-retry。
- 没有 mid-loop compact；长工具链中只能靠 protected truncation 止血。
- 没有 compaction checkpoint、tokens before/after、compaction count、可审计安装点。

---

## 3. 目标与非目标

### 3.1 目标

1. 长任务能在多轮工具调用后继续执行，不因旧上下文丢失而重复承诺。
2. compact 后保留任务目标、已完成步骤、关键工具输出、文件路径、ID、错误、待办。
3. tool call 和 tool result 永远不能被 compact cut point 拆开。
4. compact 是持久化状态，重启后仍能从 compact summary + recent tail 继续。
5. 支持自动触发、手动触发、overflow recovery。
6. 与 provider 解耦；OpenAI-compatible、Responses、Anthropic 都走同一上层 transcript 模型。
7. 保留原始历史用于审计和搜索，默认不物理删除旧 turn。

### 3.2 非目标

- 不在 MVP 中实现服务端 remote compaction。
- 不在 MVP 中实现 OpenClaw 那种完整 successor JSONL transcript tree。
- 不把 durable memory (`MEMORY.md` / `memory_summary.md`) 当作 compact 结果。
- 不保存或回放 raw chain-of-thought。只允许保存 provider 明确返回的 reasoning summary 或已有 reasoning metadata，且受 `reasoning_policy` 控制。

---

## 4. 参考经验

### 4.1 Codex

Codex 的核心启发：

- history 是扁平 item 列表，tool call、tool output、reasoning、compaction 都是同一序列里的 item。
- `for_prompt()` 前先 normalize：
  - call 必须有 output。
  - output 必须有 call。
  - 不支持图片时剥离图片。
- tool output 先单独截断，再进入 context。
- compact 生成 replacement history，而不是只在本次 prompt 临时塞 summary。
- manual / pre-turn compact 不注入旧 initial context；下一轮会完整 reinject。
- mid-turn compact 要把 initial context 插在最后真实 user message 前。
- compact 后重算 token usage，并记录 analytics。
- remote compaction 输出要过滤 stale developer / tool / reasoning 等 item，只保留安全的 user / assistant / compaction 类 item。

### 4.2 OpenClaw

OpenClaw 的可吸收设计：

- compact entry 存入 transcript，近期消息保持原样。
- split chunk 时保持 assistant tool calls 和 matching `toolResult` 成对。
- 自动触发：
  - 接近 context limit。
  - provider 返回 context overflow。
- overflow 后 compact 并 retry。
- manual `/compact [instructions]` 可引导摘要关注点。
- compact 前可运行 silent memory flush，把重要长期信息写入 memory 文件。
- `truncateAfterCompaction` 会生成 successor transcript，旧 transcript 归档，不原地重写。
- compaction 和 pruning 区分：
  - compaction 是语义摘要，持久化。
  - pruning 是轻量 tool output trim，通常只影响请求上下文。

---

## 5. 核心概念

### 5.1 Transcript Item

新增一个内部模型，区别于 provider request 的 `ContextMessage`：

```python
@dataclass(slots=True, frozen=True)
class TranscriptItem:
    item_id: str
    session_id: str
    run_id: str | None
    role: Literal["user", "assistant", "tool", "system"]
    source: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime
    ordinal: int
```

`ContextMessage` 继续作为“送给 provider 的消息格式”。`TranscriptItem` 是“会话事实日志”。二者通过 adapter 转换。

### 5.2 Compaction Checkpoint

compact 安装后生成 checkpoint：

```python
@dataclass(slots=True, frozen=True)
class CompactionCheckpoint:
    compaction_id: str
    session_id: str
    trigger: Literal["manual", "pre_turn", "mid_loop", "overflow_retry"]
    reason: str
    summary: str
    first_kept_item_id: str | None
    tokens_before: int
    tokens_after: int
    model: str
    provider_id: str
    created_at: datetime
    metadata: dict[str, Any]
```

后续 prompt 的 active view：

```text
[system baseline / workspace instructions]
[latest compaction summary as user message]
[items >= first_kept_item_id]
[current protected active turn]
```

### 5.3 Compact Summary Message

summary 用 `role="user"` 注入，而不是 `system`。原因：

- Codex 把 compact summary 当 user-message-like history item。
- system role 应保留给高优先级稳定指令，避免 summary 污染系统约束。
- provider 对 user role 的历史兼容性更好。

建议内容前缀：

```text
Conversation compact summary. This is a checkpoint summary of older context.
Use it as factual background, but prefer newer messages when conflicts appear.

...
```

`source="compaction_summary"`，metadata 标记：

```json
{
  "kind": "compaction_summary",
  "compaction_id": "...",
  "first_kept_item_id": "...",
  "tokens_before": 12345,
  "tokens_after": 3456
}
```

---

## 6. 数据存储设计

### 6.1 MVP：SQLite checkpoint，不物理删除

新增 migration：

```sql
CREATE TABLE IF NOT EXISTS session_compactions (
    compaction_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_session_compactions_session_created
    ON session_compactions(session_id, created_at);
```

并给 `sessions.metadata_json` 写入：

```json
{
  "latest_compaction_id": "...",
  "compaction_count": 3,
  "active_first_turn_id": 123
}
```

### 6.2 是否复用 `memory_turns`

MVP 可以复用 `memory_turns`，但必须补齐两个能力：

1. 持久化完整 agent transcript item。
2. 加载 history 时使用 active view，而不是简单 `get_recent_turns()`。

推荐逐步扩展：

- Phase 1：继续把普通 user/final assistant 存到 `memory_turns`；新增 compaction table；`_load_history()` 如果存在 latest compaction，就注入 latest summary + recent tail。
- Phase 2：将 tool call、tool result、中间 assistant response 也持久化为 `memory_turns`：
  - assistant tool-call：`role="assistant"`, `source="provider_response"`, metadata 带 `tool_calls`。
  - tool result：`role="tool"`, `source="tool_result:<name>"`, metadata 带 `tool_call_id`。
- Phase 3：如果 `memory_turns` 负担过重，再拆 `agent_transcript_items` 表。

不建议一开始就创建 successor session。对聊天机器人来说 session id 是用户可见的会话连续性，先用 checkpoint active view 更稳。

---

## 7. 服务边界

新增模块：

```text
nahida_bot/agent/compaction/
  __init__.py
  models.py
  service.py
  prompts.py
  policy.py
  transcript.py
```

### 7.1 `CompactionService`

职责：

- 判断是否需要 compact。
- 选择 compact cut point。
- 构造 summarization input。
- 调 provider 生成 summary。
- 验证 summary。
- 安装 checkpoint。
- 返回 active view 的 token 变化。

接口草案：

```python
class CompactionService:
    async def maybe_compact_before_turn(
        self,
        *,
        session_id: str,
        workspace_id: str | None,
        system_prompt: str,
        model_context: ModelContext,
        trigger: str = "pre_turn",
    ) -> CompactionResult | None: ...

    async def compact_session(
        self,
        *,
        session_id: str,
        workspace_id: str | None,
        instruction: str = "",
        trigger: Literal["manual", "pre_turn", "mid_loop", "overflow_retry"],
        reason: str,
        protected_messages: list[ContextMessage] | None = None,
    ) -> CompactionResult: ...
```

### 7.2 `TranscriptPolicy`

职责：

- 将 `MemoryRecord` / active run messages 转为 `TranscriptItem`。
- 按 provider 能力剥离不支持的媒体。
- sanitize tool transcript。
- 估算 tokens。
- 找 cut point。

关键规则：

- 不切开 assistant tool-call 和其后全部 matching tool result。
- 如果 split 落在 tool block 内，cut point 前移到 assistant tool-call 前。
- 当前 active turn 的 `protected_messages` 不参与 summary，直接进入 recent tail。
- orphan tool result 默认丢弃；缺 tool result 的 assistant tool-call 补一个 synthetic aborted result，或整组留在 recent tail。

---

## 8. 触发策略

### 8.1 手动触发

新增命令：

```text
/compact
/compact Focus on API design decisions and pending TODOs
```

行为：

- 强制 compact 当前 session。
- 如果没有明确 `keep_recent_tokens`，可作为 hard checkpoint，只保留 summary + 当前最新 user turn。
- 如果配置了 `keep_recent_tokens`，保留 recent tail。
- 返回一条简短可见消息：

```text
Compacted session context. Kept recent tail: 6 turns. Summary: 842 chars.
```

如果 `notify_user=false`，自动 compact 不发消息；手动 compact 永远回复。

### 8.2 Pre-turn 自动触发

在 `SessionRunner.run_stream()` 加载 history、relevant memory、observed group context 后，调用：

```text
maybe_compact_before_turn()
```

触发条件：

```text
estimated_prompt_tokens >= min(
  context.max_tokens - compaction.reserve_tokens,
  context.max_tokens * compaction.trigger_ratio
)
```

默认建议：

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

### 8.3 Mid-loop 自动触发

在 `AgentLoop` 执行工具并把 tool result 加入 `active_turn_messages` 后，下一次 provider call 前做 precheck。

触发条件：

- active prompt 超过 hard threshold。
- 或 `ContextBuilder` 已经需要截断 protected tool output。
- 或连续 N 次 provider call 接近 context 上限。

处理顺序：

1. 先对过大的 tool result 做 request-local truncation/pruning。
2. 如果仍超预算，compact older history，不 compact 当前 active tool block。
3. 重新 build context，再继续 loop。

MVP 可先只做 pre-turn 和 overflow retry，mid-loop 放 Phase 3。

### 8.4 Provider overflow retry

新增标准错误码：

```text
provider_context_window_exceeded
```

Provider 层识别常见形态：

- `request_too_large`
- `context length exceeded`
- `input exceeds the maximum number of tokens`
- `input token count exceeds the maximum number of input tokens`
- `input is too long for the model`

`AgentLoop` 或 `SessionRunner` 捕获后：

1. 若本次 run 尚未 overflow compact retry，执行 compact。
2. 重新 build context。
3. retry 当前 provider call。
4. 若仍失败，返回可解释错误。

---

## 9. Compact 算法

### 9.1 Build Candidate Transcript

输入：

- latest compaction summary（如果有）
- uncompacted tail
- current protected active turn
- relevant memory context

排除：

- system baseline / workspace instructions：不送去 summary，后续会重新注入。
- transient group observed context：默认不 compact，除非已进入正式 history。
- raw media data / base64：只保留 media id、description、path、安全元数据。

### 9.2 Output Truncation First

参考 Codex：tool output 进入 transcript 前先单独截断。

建议策略：

- 文本 tool output 用 head+tail 截断，不只保留开头。
- 保留 JSON 外壳和 status/error 字段。
- 超大数组保留前 N 项、后 N 项和总数。
- metadata 标记：

```json
{
  "truncated": true,
  "original_chars": 120000,
  "retained_chars": 8000,
  "policy": "head_tail"
}
```

### 9.3 Select Cut Point

目标：

```text
summary_input = active_history_before_cut
recent_tail = active_history_from_cut
```

约束：

- `recent_tail` 至少包含最近 `keep_recent_turns_min` 个 user turn。
- `recent_tail` token 不超过 `keep_recent_tokens`，但 protected active turn 可超过该值。
- cut point 不拆 tool group。
- compact summary + recent tail + next user + reserved output 应低于 `target_tokens_after`。

cut point 算法：

1. 按 transcript group 分组：
   - plain message group
   - assistant tool-call + tool results group
   - compaction summary group
2. 从最新组往前累加 recent tail。
3. 到达 `keep_recent_tokens` 或 `keep_recent_turns_min` 后停止。
4. cut point 设为 recent tail 第一组的起点。

### 9.4 Summarization Prompt

摘要应是结构化 Markdown，不要求 JSON，减少 parse failure。

模板要点：

```text
You are compacting an agent transcript. Produce a concise but complete checkpoint.

Preserve exact identifiers:
- file paths
- URLs
- IDs
- tool names
- command names
- user-stated constraints

Do not invent facts. Mark uncertainty explicitly.

Include these sections:
1. User Goal
2. Current State
3. Completed Work
4. Tool Results and Evidence
5. Decisions and Constraints
6. Open TODOs
7. Important Identifiers
8. Risks or Failed Attempts
```

中文会话可以要求摘要使用用户当前语言；但代码符号、路径、ID 不翻译。

### 9.5 Staged Summary

如果 summary input 本身过大：

1. 分 chunk summary，chunk boundary 仍遵守 tool group 原子性。
2. 合并 chunk summaries，再做 reduce summary。
3. 对旧 compaction summary 不原样无限拼接，要 redistill。

这是 Phase 2 能力；MVP 可以先设置 `max_compaction_input_tokens`，超出则先丢最旧 raw item 并记录 warning。

### 9.6 Validate Summary

最小验证：

- 非空。
- token 数低于 `summary_max_tokens`。
- 包含 `User Goal` 和 `Open TODOs` 标题。
- 如果输入中有明显 file path / tool name / error code，摘要中应保留至少部分关键 identifier。

失败策略：

1. retry 一次，附带“previous summary was missing required sections”。
2. 仍失败则 fallback 到 deterministic summary：
   - 最近 user goals
   - tool calls list
   - last assistant final response
   - pending TODO guessed from latest user request

---

## 10. ContextBuilder 的职责变化

`ContextBuilder` 继续负责“最终请求预算防线”，但不再承担真正 compact：

- 保留 sliding window。
- 保留 protected active turn。
- 保留 request-local tool output truncation。
- 如果看到 `source="compaction_summary"`，必须把它当普通 dynamic message，但优先级高于旧 history tail。

新增返回诊断更好：

```python
@dataclass
class ContextBuildResult:
    messages: list[ContextMessage]
    estimated_tokens: int
    dropped_messages: list[ContextMessage]
    protected_truncated: bool
    needs_compaction: bool
```

Phase 1 可不改公开返回值；Phase 3 做 mid-loop compact 时建议改。

---

## 11. 配置设计

新增 `ContextCompactionConfig`：

```python
class ContextCompactionConfig(BaseModel):
    enabled: bool = True
    auto_enabled: bool = True
    manual_enabled: bool = True
    overflow_retry_enabled: bool = True
    mid_loop_precheck_enabled: bool = False

    trigger_ratio: float = Field(default=0.85, ge=0.1, le=1.0)
    hard_ratio: float = Field(default=0.95, ge=0.1, le=1.0)
    reserve_tokens: int = Field(default=2000, ge=0)
    keep_recent_tokens: int = Field(default=12000, ge=0)
    keep_recent_turns_min: int = Field(default=6, ge=0)

    summary_max_tokens: int = Field(default=1200, ge=100)
    max_compaction_input_tokens: int = Field(default=50000, ge=1000)

    model: str = ""  # provider/model; empty = active session model
    notify_user: bool = False
    memory_flush_enabled: bool = True
    identifier_policy: Literal["strict", "off"] = "strict"
```

挂到：

```python
class ContextConfig(BaseModel):
    ...
    compaction: ContextCompactionConfig = ContextCompactionConfig()
```

后续可接入 model routing：

```yaml
model_routing:
  tasks:
    compaction: "openai/gpt-5.4-mini"
```

---

## 12. 与 Memory 的关系

compact summary 是 **短期 transcript checkpoint**，不是长期记忆。

区别：

| 层 | 作用 | 注入方式 | 生命周期 |
|---|---|---|---|
| compaction summary | 让当前 session 继续 | 当前 session history | 随 session |
| memory item / MEMORY.md | 跨 session durable facts | workspace memory / retrieval | 长期 |
| memory_summary.md | durable memory projection | system context | 长期 |

OpenClaw 的 memory flush 值得做，但不要阻塞 MVP。

Phase 2 设计：

1. compact 前调用 `MemoryConsolidator` 的窗口级 API。
2. 把 compact 输入中的 durable 候选写入 memory candidates。
3. 如果 workspace memory 可写，更新 `MEMORY.md` / `memory_summary.md`。
4. 这是 silent housekeeping，不发送用户消息。

---

## 13. 用户与工具接口

### 13.1 Builtin 命令

新增 `/compact`：

```text
/compact
/compact Focus on runtime settings design and pending implementation steps
```

返回字段：

- compaction id
- trigger
- tokens before / after
- kept recent turns
- summary chars

### 13.2 可选工具

后续可给 agent 暴露工具：

```text
session_compact(instruction?: string)
```

默认不在 MVP 暴露，避免模型滥用 compact 来逃避当前任务。先只做命令和自动策略。

### 13.3 Status

`/status` 增加：

```text
Compactions: 3
Last compaction: 2026-05-15 15:42, pre_turn, 18k -> 6k tokens
```

---

## 14. 落地阶段

### Phase 0：补 transcript 可观测性

- [ ] 在 `AgentRunResult` 中保留完整 active transcript。
- [ ] `SessionRunner._persist_turns()` 可选择持久化中间 assistant/tool messages。
- [ ] 给 `SessionRunner._build_history_context()` 增加 active view 诊断日志。
- [ ] 补测试：跨轮恢复 tool call metadata 不丢。

### Phase 1：Manual compact MVP

- [ ] 新增 `session_compactions` 表和 repository。
- [ ] 新增 `CompactionService.compact_session()`。
- [ ] 新增 summary prompt。
- [ ] 安装 checkpoint：latest summary + first_kept_turn_id。
- [ ] `_load_history()` 如果存在 latest compaction，加载 summary + tail。
- [ ] 新增 `/compact` 命令。
- [ ] 测试 manual compact 后 prompt 包含 summary + tail，不包含旧 raw history。

### Phase 2：Pre-turn auto compact + overflow retry

- [ ] 新增 config。
- [ ] `SessionRunner.run_stream()` pre-turn 检查并自动 compact。
- [ ] Provider 层识别 context overflow 错误，归一为 `provider_context_window_exceeded`。
- [ ] overflow 后 compact-and-retry 一次。
- [ ] 增加 metrics/logging：trigger、reason、tokens before/after、status。

### Phase 3：Mid-loop compact 和更强 tool output pruning

- [ ] `ContextBuilder` 返回 `ContextBuildResult`，暴露 `needs_compaction` 和 protected truncation 状态。
- [ ] `AgentLoop` 工具执行后、下一次 provider call 前调用 mid-loop precheck。
- [ ] 不 compact 当前 active tool block，只 compact older history。
- [ ] tool output head+tail truncation 独立成 `ToolOutputTruncator`。

### Phase 4：Memory flush

- [ ] 为 `MemoryConsolidator` 增加窗口级 consolidation。
- [ ] compact 前执行 silent memory flush。
- [ ] 写入 memory candidates / `memory_summary.md`。
- [ ] 配置 `memory_flush_enabled` 和 compaction model override。

### Phase 5：Successor transcript / cleanup

- [ ] 评估是否需要 OpenClaw 式 successor session。
- [ ] 如果需要，增加 archived transcript/checkpoint 视图。
- [ ] 保证 UI、CLI、history search 仍能访问完整历史。

---

## 15. 测试计划

### Unit

- cut point 不拆 tool group。
- orphan tool result 被丢弃或修复。
- missing tool result 不进入 compacted prompt。
- summary message 注入顺序正确。
- identifier preservation prompt 包含路径、ID、tool names。
- active view = latest compaction summary + tail。
- config threshold 计算正确。

### Integration

- 长工具输出触发 tool truncation，不触发重复工具承诺。
- pre-turn 超预算自动 compact。
- provider context overflow mock 后 compact-and-retry 成功。
- manual `/compact` 后下一轮能引用 compact 前事实。
- compact 后原始历史仍能通过 memory search 找到。
- 重启后 active view 仍从 latest compaction 恢复。

### Regression

- OpenAI-compatible tool transcript serialization 仍合法。
- Anthropic tool_result 顺序合法。
- Responses API replay output 不被 compact 误注入 stale developer messages。
- group observed context 不被错误 compact 成正式会话历史。

---

## 16. 风险与取舍

| 风险 | 对策 |
|---|---|
| 摘要遗漏关键事实 | 严格 prompt + validation + user manual instruction + memory flush |
| 模型编造 compact summary | 要求不确定标注；保留原始历史；可审计 checkpoint |
| compact 太频繁 | trigger_ratio + reserve_tokens + cooldown |
| compact 本身超 context | staged summary；先丢最旧 raw item；fallback deterministic summary |
| provider/tool transcript 协议损坏 | transcript normalize + provider sanitize 双层防线 |
| 与 durable memory 混淆 | 文档和 metadata 明确区分 compaction_summary 与 memory |

---

## 17. 推荐优先级

最高优先级：

1. Phase 1 manual compact MVP。
2. Phase 2 overflow retry。
3. Phase 2 pre-turn auto compact。
4. Phase 3 mid-loop precheck。

原因：

- manual compact 能最快验证 summary prompt 和 active view 安装。
- overflow retry 能直接解决 provider 400 和长任务中断。
- pre-turn auto 避免多数长会话进入危险区。
- mid-loop 最复杂，但对长工具链价值最大。

---

## 18. 参考链接

- Codex inline compaction: <https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs>
- Codex remote compaction: <https://github.com/openai/codex/blob/main/codex-rs/core/src/compact_remote.rs>
- Codex ContextManager/history: <https://github.com/openai/codex/blob/main/codex-rs/core/src/context_manager/history.rs>
- OpenClaw compaction concept: <https://github.com/openclaw/openclaw/blob/main/docs/concepts/compaction.md>
- OpenClaw session management compaction deep dive: <https://github.com/openclaw/openclaw/blob/main/docs/reference/session-management-compaction.md>
- OpenClaw memory overview: <https://github.com/openclaw/openclaw/blob/main/docs/concepts/memory.md>
