# Agent Loop 优化调研：Codex vs nahida-bot 架构对比

> 调研时间：2026-05-08
> 目标：通过对比 Codex (OpenAI CLI) 的 agent loop 实现，识别 nahida-bot 可参考的优化方向

---

## 一、架构总览对比

| 维度 | nahida-bot | Codex |
|------|-----------|-------|
| 语言 | Python (asyncio) | Rust (Tokio) |
| 核心循环 | `AgentLoop.run()` (`loop.py`) | `run_turn()` (`turn.rs`) |
| 上下文管理 | `ContextBuilder` (`context.py`) | `ContextManager` (`history.rs`) |
| 历史结构 | 按 turn 对 (user/assistant) 持久化到 SQLite | 扁平 `Vec<ResponseItem>`（含 tool call/output） |
| 压缩策略 | 滑动窗口 + 简单摘要 | 多层：截断 → 摘要压缩 → 远程压缩 |
| 思维链 | 部分支持（3 种策略） | 完整支持（加密内容 + 摘要 + 流式输出） |
| 工具执行 | 串行，有超时和重试 | 并行执行，RwLock 控制并发 |

---

## 二、Agent Loop 核心循环对比

### 2.1 nahida-bot 当前实现

**文件**: `nahida_bot/agent/loop.py:131-277`

```
用户消息 → 追加到 conversation → 循环(max_steps=8) {
    build_context() → 调用 provider → 检查 tool_calls?
    → 无: 终止，返回最终响应
    → 有: 执行工具 → 追加结果到 conversation → 继续循环
}
```

**特点**:

- 最大 8 步（`max_steps`），不可动态调整
- 每步都调用 `build_context()` 重新组装完整上下文
- 工具串行执行（`_execute_tools` 内 for 循环逐个调用）
- 无中途用户中断支持
- Provider 错误直接中断整个循环

### 2.2 Codex 实现

**文件**: `codex-rs/core/src/session/turn.rs:136`

```
用户输入 → 预压缩检查 → 注册 hooks/tools → loop {
    排队输入 → build prompt → 采样请求(流式) → {
        处理 streaming events (文本/tool_call/reasoning)
        工具并行执行(futures queue)
        drain_in_flight() 等待所有工具完成
    }
    → 需要继续? (tool calls 存在 / end_turn=false / 有新输入)
    → token 超限? → 触发中途压缩 → continue
    → 否则: break
}
```

**特点**:

- 无硬编码步数上限，靠 token 预算自然限制
- 支持中途用户中断（pending input 机制）
- 工具并行执行，用 RwLock 区分可/不可并行工具
- 流式处理，边收边处理 tool call
- 多层错误恢复：重试 + 传输降级 (WebSocket → HTTPS) + 压缩
- 支持 hooks 系统（session-start、user-prompt-submit、stop hooks）

### 2.3 可参考的优化点

| 优化 | 优先级 | 说明 |
|------|--------|------|
| 工具并行执行 | **高** | 当前串行执行浪费等待时间，可改为 `asyncio.gather` 并行 |
| 中途压缩检测 | **高** | 当前无 token 超限的 mid-loop 压缩，长 tool chain 会爆上下文 |
| 取消/中断机制 | **中** | 当前无 `CancellationToken` 等效物，用户无法中途取消 |
| 动态步数上限 | **低** | 硬编码 8 步在复杂任务中可能不够，可考虑基于 token 消耗动态调整 |
| Hook 系统 | **低** | 按 hook 时机注入/修改 prompt，当前 nahida-bot 无此机制 |

---

## 三、上下文管理对比

### 3.1 nahida-bot 当前实现

**文件**: `nahida_bot/agent/context.py:183-320`

**组装顺序**（固定）:

1. System baseline（系统提示词）
2. Workspace instructions（AGENTS.md → SOUL.md → USER.md）
3. Workspace skills（SKILL.md）
4. History messages（历史消息）
5. Tool messages（工具结果）

**Token 预算**: `max_tokens(8000) - reserved_tokens(1000) = 7000 usable`

**溢出处理**:

1. 滑动窗口：从最新消息往回保留，丢弃最旧的
2. 摘要生成：被丢弃的消息压缩为一行 `- <role>: <前120字符>`，总限 600 字符
3. 二分截断：如果单条消息太大，二分搜索最大可容纳前缀

**每步重建**: Agent loop 每一步都调用 `build_context()`，整个 conversation 列表传入，每次都重新估算 token。

### 3.2 Codex 实现

**文件**: `codex-rs/core/src/context_manager/history.rs`

**结构**: 扁平 `Vec<ResponseItem>`，所有消息类型（user、assistant、tool call、tool output、reasoning、developer）都在同一个列表中。

**关键机制**:

1. **规范化（normalize）**: 每次 `for_prompt()` 时强制执行：
   - 每个 tool call 必须有对应 output（缺失的补 `"aborted"`）
   - 每个 output 必须有对应 call（孤立的删除）
   - 不支持图片的模型自动剥离图片内容

2. **增量上下文更新**: 不是每步重建，而是通过 `reference_context_item` 做差分更新：
   - 环境变化（shell、cwd）
   - 模型指令变化
   - 权限变化
   - 协作模式变化
   → 只注入变化的部分，避免重复发送完整系统提示

3. **Tool Output 截断**: 每个工具输出单独截断（中间截断保留首尾），使用 `TruncationPolicy`（Bytes 或 Tokens），乘以 1.2 系数补偿序列化开销。

4. **版本号**: `history_version` 在压缩/回滚时递增，用于检测缓存失效。

### 3.3 可参考的优化点

| 优化 | 优先级 | 说明 |
|------|--------|------|
| Tool output 截断 | **高** | 当前只有 `max_tool_log_chars=400` 的日志截断，工具输出本身无截断，大输出直接占用大量 token |
| 历史规范化 | **高** | 当前无 call/output 配对校验，若 tool call 出现在历史末尾但 output 丢失会导致 provider 报错 |
| 差分上下文更新 | **中** | 当前每步完整重建上下文，浪费计算；可缓存 prefix 部分（system + workspace instructions） |
| 版本号机制 | **低** | 用于检测压缩/回滚导致的缓存失效，当前无缓存层 |

---

## 四、历史持久化对比

### 4.1 nahida-bot 当前实现

**文件**: `nahida_bot/core/session_runner.py:1264-1331`

**持久化策略**:

- 一次交互存 2 条 `ConversationTurn`：user + assistant
- Tool call / tool result **不持久化**
- Assistant turn 仅保存 `final_response`（最终文本响应）
- Reasoning 通过 metadata 保存：`reasoning`, `reasoning_signature`, `has_redacted_thinking`

**加载策略**:

- `SQLiteMemoryStore.get_recent(session_id, limit=50)`
- SQL: `SELECT * FROM memory_turns WHERE session_id = ? ORDER BY created_at DESC LIMIT ?`
- 逆序取最新 50 条，再 reverse 回时间序

**问题**:

- `max_history_turns=50` 实际只有约 25 轮完整对话
- Tool calling 的中间推理过程完全丢失
- `RouterConfig.max_history_turns` 和 `SessionRunner.max_history_turns` 的接线可能断裂（`app.py` 创建 SessionRunner 时未传入 router 的配置值）

### 4.2 Codex 实现

**历史结构**: 扁平列表，所有 `ResponseItem` 都在内存中：

- `FunctionCall` / `FunctionCallOutput`
- `CustomToolCall` / `CustomToolCallOutput`
- `LocalShellCall` + `FunctionCallOutput`
- `Reasoning`（含加密内容 + 摘要）
- `ToolSearchCall` / `ToolSearchOutput`
- `WebSearchCall`, `ImageGenerationCall`
- 普通 user / assistant 消息

**不持久化到数据库**：Codex 的历史是会话内内存状态。持久化由外部机制（rollout trace）处理，用于遥测而非对话恢复。

**压缩后保留策略**:

| 保留 | 丢弃 |
|------|------|
| Assistant 消息 | Reasoning items |
| 近期 User 消息（≤20k tokens） | Developer 消息（过期的指令） |
| 压缩摘要 | FunctionCall/Output |
| Compaction 标记 | CustomToolCall/Output |
| Hook prompt | WebSearchCall |
| | ImageGenerationCall |

### 4.3 可参考的优化点

| 优化 | 优先级 | 说明 |
|------|--------|------|
| 持久化 tool call/result | **高** | 当前中间推理完全丢失，重开对话后工具调用上下文断裂 |
| 配置接线修复 | **高** | `RouterConfig.max_history_turns` 未传入 `SessionRunner`，配置不生效 |
| 按轮而非按条计数 | **中** | 50 "turns" 实为 25 轮对话，语义不清，易配置错误 |
| 关键词搜索增强 | **低** | 当前 jieba 分词搜索功能存在但可能未充分利用 |

---

## 五、压缩策略对比

### 5.1 nahida-bot 当前实现

**文件**: `nahida_bot/agent/context.py:322-421`

唯一策略是**滑动窗口** + 简单摘要：

```
1. 从最新消息往回扫描，保留 fit 的消息
2. 被丢弃的消息生成摘要（每条一行，前 120 字符）
3. 摘要尝试插入，如果放不下就继续丢弃最旧的保留消息
```

**局限**:

- 摘要是纯文本压缩，丢失了结构信息（tool call 的输入输出关系）
- 没有 LLM 参与的语义压缩，上下文质量退化严重
- 没有区分"可丢弃"和"不可丢弃"的消息类型

### 5.2 Codex 实现

三层压缩策略：

**Layer 1: Output Truncation（输出截断）**

- 每个 tool output 单独处理
- 中间截断：保留首尾，截断中间
- `TruncationPolicy::Bytes(n)` 或 `TruncationPolicy::Tokens(n)`
- 乘以 1.2 系数补偿 JSON 序列化开销

**Layer 2: Inline Compaction（本地摘要压缩）**

- 将整个历史发送给 LLM 生成摘要
- 摘要提示词要求包含：进度、关键决策、用户偏好、待办事项、关键数据
- 保留近期用户消息（≤20k tokens，从最新往回取）
- 用摘要替换旧历史
- 压缩后的历史结构：`[initial_context?] + [recent_user_messages] + [summary_as_user_message]`

**Layer 3: Remote Compaction（远程压缩）**

- 调用专用 `/compact` API 端点
- 服务端返回预压缩的 transcript
- 过滤策略：保留 assistant/user/compaction 标记，丢弃 tool call/reasoning/developer 等

**触发时机**:

| 时机 | 位置 | 注入方式 |
|------|------|----------|
| Turn 开始前（pre-sampling） | `turn.rs:702` | 不注入初始上下文 |
| Turn 中途（mid-turn） | `turn.rs:485` | 在最后一条用户消息前注入 |
| 手动触发（`/compact` 命令） | 用户主动 | 独立 turn |

**自动触发阈值**: `min(config_override, 90% × context_window)`

### 5.3 可参考的优化点

| 优化 | 优先级 | 说明 |
|------|--------|------|
| Tool output 单独截断 | **高** | 当前 tool output 无 token 预算控制，一个大型工具返回就能吃掉整个上下文 |
| LLM 参与的语义压缩 | **高** | 当前摘要只是截取前 120 字符，信息损失巨大。可引入 LLM 生成结构化摘要 |
| 分类丢弃策略 | **中** | 当前无差别的滑动窗口丢弃，应区分：tool output（优先丢弃）> reasoning > assistant > user（最后丢弃） |
| 中途压缩触发 | **高** | 当前只在 build_context 时检测，但循环内每步都重建上下文，无 mid-loop 压缩能力 |
| 压缩后重新计算 token | **中** | 压缩后应重新估算 token 用量，避免后续步骤仍用旧的 token 计数 |

---

## 六、思维链 / Reasoning 管理对比

### 6.1 nahida-bot 当前实现

**文件**: `nahida_bot/agent/context.py:20-31`, `nahida_bot/agent/providers/reasoning.py`

**三种策略** (`ReasoningPolicy`):

- `STRIP`: 完全丢弃 reasoning，只保留 signature（用于 Anthropic 的签名验证）
- `APPEND`: 完整注入 reasoning 文本
- `BUDGET`（默认）: 在 token 预算内注入，超出则丢弃

**Reasoning 提取**:

- Anthropic: 从 `thinking` content block 提取，保存 `signature`
- DeepSeek: 从 `reasoning_content` 字段提取，或 `<think/>` 标签
- Groq: 从 `reasoning` 字段提取
- OpenAI Responses: 从 reasoning summary 提取

**持久化**: 通过 `ConversationTurn.metadata` 保存 `reasoning`, `reasoning_signature`, `has_redacted_thinking`。

**局限**:

- Reasoning token 不参与 token 预算计算（`_estimate_tokens` 不单独处理）
- 无流式 reasoning 输出
- Anthropic 的 `redacted_thinking` 处理较粗糙（只存标志，无实际内容）

### 6.2 Codex 实现

**数据模型** (`models.rs:757-767`):

```rust
ResponseItem::Reasoning {
    id: String,
    summary: Vec<ReasoningSummary>,        // LLM 生成的推理摘要
    content: Option<Vec<ReasoningContent>>, // 原始推理内容
    encrypted_content: Option<String>,      // 加密的完整推理（Opaque）
}
```

**流式处理**:

- `ReasoningSummaryDelta`: 推理摘要增量流式输出
- `ReasoningSummaryPartAdded`: 推理段落分隔事件
- `ReasoningContentDelta`: 原始推理内容增量流式

**Token 估算**: 使用 `estimate_reasoning_length()` 从 base64 编码长度推算：`encoded_len × 3/4 - 650`

**服务端协调**: `server_reasoning_included` 标志跟踪服务端 token 计数是否已包含 reasoning tokens，避免重复计算。

**压缩行为**: Reasoning items 在压缩时**始终丢弃**（不保留），因为它们被视为模型内部临时状态。

### 6.3 可参考的优化点

| 优化 | 优先级 | 说明 |
|------|--------|------|
| Reasoning token 计入预算 | **高** | 当前 `_estimate_tokens` 未区分 reasoning，可能导致实际 token 消耗远超预算 |
| Reasoning 在压缩时优先丢弃 | **中** | Reasoning 是模型内部状态，压缩时应最先丢弃以腾出空间 |
| 流式 reasoning 输出 | **低** | 当前不支持流式，用户体验上无法实时看到"思考过程" |
| 加密 reasoning 支持 | **低** | Anthropic extended thinking 的 encrypted_content 未支持 |

---

## 七、工具调用生命周期对比

### 7.1 nahida-bot 当前实现

**文件**: `nahida_bot/agent/loop.py:475-618`

```
for each tool_call:
    验证 schema → async wait_for(timeout=135s) → 捕获异常
    → 成功: ToolExecutionResult.success(output)
    → 超时: ToolExecutionResult.error(code="tool_timeout")
    → 异常: ToolExecutionResult.error(code="tool_execution_exception")
```

**特点**:

- 串行执行（for 循环）
- 单工具超时 135 秒
- 单工具重试 1 次（总共 2 次尝试）
- 结果格式化为 JSON: `{status, output, error, logs}`
- 日志截断 400 字符
- 工具参数 schema 验证（类型、required、additionalProperties）

### 7.2 Codex 实现

**文件**: `codex-rs/core/src/tools/parallel.rs`, `codex-rs/core/src/stream_events_utils.rs`

```
streaming events:
    on OutputItemDone(tool_call):
        record to history → spawn tool future → push to in_flight queue

after stream ends:
    drain_in_flight(): await all futures → record results to history
```

**特点**:

- **并行执行**: 所有 tool call 同时 spawn 为 Tokio task
- **并发控制**: RwLock 区分可并行（read lock）和不可并行（write lock）工具
- **取消支持**: `tokio::select!` 监听 CancellationToken
- **无显式步数上限**: 靠 token 预算自然限制
- **无超时**: 依赖任务级取消，非固定超时
- **流式 tool call 参数**: `ToolCallInputDelta` 增量流式传输参数

### 7.3 可参考的优化点

| 优化 | 优先级 | 说明 |
|------|--------|------|
| 并行工具执行 | **高** | 多个 tool call 时，`asyncio.gather` 可显著减少等待时间 |
| 工具并行性声明 | **中** | 允许插件声明 `supports_parallel`，不支持的串行执行 |
| 取消机制 | **中** | 长时间工具调用应有取消能力 |

---

## 八、Token 预算管理对比

### 8.1 nahida-bot 当前实现

```
max_tokens(8000) - reserved_tokens(1000) = 7000 usable
每步: serialize所有消息 → tokenizer.count_tokens() → 超限则滑动窗口
```

**Token 估算**: 消息序列化为 `role:...\nsource:...\ncontent:...\n` 格式，然后调用 tokenizer。

**Tokenizer 降级链**: 显式 tokenizer → provider tokenizer → HeuristicTokenizer → CharacterEstimateTokenizer (`ceil(len/4)`)

**问题**:

- 每步完整重建和完整计算 token，无缓存
- 7000 usable tokens 对大多数模型偏小（GPT-4 128k, Claude 200k）
- `reserved_tokens` 的含义不明确（给谁预留？）

### 8.2 Codex 实现

**Token 来源优先级**:

1. 服务端 `ResponseEvent::Completed` 返回的 `total_tokens`（最准确）
2. 客户端估算：`approx_tokens_from_byte_count(serialize_to_json(item))`
3. Reasoning 特殊处理：`encoded_len × 3/4 - 650`

**Token 累积**:

```rust
struct TokenUsageInfo {
    total_token_usage: TokenUsage,    // 累积
    last_token_usage: TokenUsage,     // 最近一次 API 返回
    model_context_window: Option<i64>,
}
```

**计算公式**:

```
total = server_total_tokens
      + items_added_after_last_response (客户端估算)
      + reasoning_tokens (如果 server 未计入)
```

**自动压缩阈值**: `min(config_override, 90% × context_window)`

### 8.3 可参考的优化点

| 优化 | 优先级 | 说明 |
|------|--------|------|
| 利用服务端 token 计数 | **高** | 当前完全依赖客户端估算，provider 返回的 usage 信息未用于预算管理 |
| 提高默认 token 预算 | **高** | 7000 对现代模型太小，浪费了模型的大上下文能力 |
| Token 计数缓存 | **中** | prefix 部分不变时可缓存，避免每步重复计算 |
| 动态压缩阈值 | **中** | 根据模型 context window 动态设置，而非固定值 |

---

## 九、优化路线图建议

> 补充：本文件聚焦单个 `AgentLoop` 的循环与上下文优化；多 Agent / Subagent / 后台任务 / 跨会话管理的完整架构已拆到 [docs/architecture/agent-orchestration.md](../architecture/agent-orchestration.md)。后续实现时应先保持两条线边界清晰：`AgentLoop` 负责一次模型-工具循环，`AgentOrchestrator` 负责 run、queue、task、session 和父子关系。

### Phase 1: 基础完善（高优先级）

1. **修复配置接线** — `RouterConfig.max_history_turns` 正确传入 `SessionRunner`
2. **工具输出截断** — 在 `ContextBuilder` 或 `AgentLoop` 中为 tool result 添加 token 预算，超出时中间截断
3. **利用 provider usage** — 将 provider 返回的 token usage 信息用于上下文预算管理
4. **提高 token 预算** — 默认值应参考模型实际 context window，而非硬编码 8000

### Phase 2: 核心优化（中优先级）

1. **并行工具执行** — `asyncio.gather` 替代串行 for 循环
2. **中途压缩检测** — Agent loop 每步后检查 token 用量，超限时触发压缩
3. **LLM 语义压缩** — 引入 LLM 生成结构化摘要替代简单的字符截取摘要
4. **分类丢弃策略** — 压缩时按类型优先级丢弃：tool output > reasoning > assistant > user
5. **持久化 tool call/result** — 可选地将关键 tool interaction 持久化，支持断点续对话
6. **历史规范化** — 加载历史后校验 tool call/output 配对完整性

### Phase 3: 高级特性（低优先级）

1. **取消/中断机制** — 引入 `CancellationToken` 等效物
2. **增量上下文更新** — 缓存 prefix 部分，只更新动态部分
3. **流式 reasoning 输出** — 将 reasoning token 流式传递给前端
4. **差分 context 更新** — 跟踪环境变化，只注入差异部分
5. **Hook 系统** — 在关键时机提供扩展点

---

## 十、关键文件索引

### nahida-bot

| 文件 | 职责 |
|------|------|
| `nahida_bot/agent/loop.py` | Agent 循环核心 |
| `nahida_bot/agent/context.py` | 上下文组装与 token 预算 |
| `nahida_bot/core/session_runner.py` | 会话运行器，协调 provider/agent/memory |
| `nahida_bot/core/router.py` | 消息路由与分发 |
| `nahida_bot/core/config.py` | 配置模型定义 |
| `nahida_bot/agent/memory/store.py` | Memory store 抽象接口 |
| `nahida_bot/agent/memory/sqlite.py` | SQLite 实现（含 jieba 分词搜索） |
| `nahida_bot/agent/memory/models.py` | 数据模型 |
| `nahida_bot/agent/providers/reasoning.py` | Reasoning 提取公共逻辑 |
| `nahida_bot/agent/tokenization.py` | Token 计数器（启发式/字符估算） |

### Codex

| 文件 | 职责 |
|------|------|
| `codex-rs/core/src/session/turn.rs` | Turn 执行循环核心 |
| `codex-rs/core/src/context_manager/history.rs` | 上下文管理器 |
| `codex-rs/core/src/context_manager/normalize.rs` | 历史规范化 |
| `codex-rs/core/src/context_manager/updates.rs` | 差分上下文更新 |
| `codex-rs/core/src/compact.rs` | 本地摘要压缩 |
| `codex-rs/core/src/compact_remote.rs` | 远程压缩 |
| `codex-rs/core/src/tools/parallel.rs` | 工具并行执行 |
| `codex-rs/protocol/src/models.rs` | ResponseItem 数据模型 |
| `codex-rs/protocol/src/protocol.rs` | TokenUsage / TruncationPolicy |
