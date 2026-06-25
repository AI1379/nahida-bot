# AgentLoop 改造实施计划

> 状态：提案，尚未开始实现。
> 关联：[Agent Loop 与 Context Builder 审计报告](../architecture/agent-loop-context-audit.md)（#21 / #24）。

## 1. 目标与边界

本计划将 AgentLoop 从“模型停止输出即成功”的文本循环，改造成具备明确终态、工具执行证据和可恢复历史的运行时。它修复以下两类问题：

1. 模型未调用工具却声称已经读取、修改、测试或发送，系统仍向用户报告成功。
2. 工具调用与结果在跨轮历史中丢失，assistant 的自然语言断言被自动提升为长期 memory。

保留的设计原则：

- 保留 workspace-first：`AGENTS.md`、`SOUL.md`、`USER.md`、skills 和 memory 继续可编辑、每轮可用。
- 不把项目流程、人格和领域知识复制进固定 system prompt。
- 不因纯问答、创作或用户已提供完整资料而强制工具调用。
- 不根据关键词自动执行任何有副作用工具。
- 不破坏已有会话、插件工具和 provider 的正常调用协议。

不在本次改造范围内：多 agent 编排重写、工具权限模型重写、替换 provider SDK、将所有历史会话离线重写、引入新的外部数据库。

## 2. 目标运行时契约

### 2.1 终态

每个 agent run 必须有且只有一个终态：

| 终态 | 含义 | 可否对用户称“已完成” |
|---|---|---|
| `completed` | 满足本 run 的完成契约；必需回执齐全。 | 可以。 |
| `unverified` | 文本包含未被回执支持的外部状态声明，或必需动作未获得证据。 | 不可以；必须说明未验证。 |
| `incomplete` | 达到步数/上下文/主动停止边界，尚未形成可验证结果。 | 不可以。 |
| `failed` | provider、协议、工具或持久化发生不可恢复错误。 | 不可以。 |
| `cancelled` | 用户、路由或 shutdown 取消了 run。 | 不可以。 |

`max_steps`、解析到空工具调用的 tool finish、工具执行器缺失和中断工具调用不得再回收最后一条 assistant 文本作为 `completed` 的 final response。

### 2.2 完成契约与执行回执

不要把“是否需要工具”做成全局关键词规则。应由调用上下文、已选择 skill、显式命令和运行时检查共同决定，并在 run 中表达成结构化契约：

```python
class EvidenceMode(StrEnum):
    NONE = "none"          # 普通问答/创作
    OBSERVE = "observe"    # 当前文件、检索、查询、检查
    MUTATE = "mutate"      # 修改文件、运行命令、更新配置
    DELIVER = "deliver"    # 向外发送、发布、提交

@dataclass(frozen=True)
class CompletionContract:
    evidence_mode: EvidenceMode
    required_tool_names: frozenset[str] = frozenset()
    required_receipt_kinds: frozenset[str] = frozenset()
    allow_unverified_answer: bool = True
```

每次工具执行产生 `ExecutionReceipt`：

```python
@dataclass(frozen=True)
class ExecutionReceipt:
    call_id: str
    tool_name: str
    status: Literal["ok", "error", "cancelled", "timed_out"]
    started_at: datetime
    finished_at: datetime
    input_fingerprint: str
    evidence: dict[str, JsonValue]  # 如文件哈希、exit_code、message_id、artifact
    verification_status: Literal["verified", "partial", "unverified"]
```

规则：

- `OBSERVE`、`MUTATE`、`DELIVER` 中声明完成的动作必须能关联一个成功且合适的 receipt。
- `DELIVER` 至少要求渠道/外部服务返回稳定 ID；没有 `message_id`、提交 ID 或等价证据时不可宣称送达。
- 工具返回错误、超时或取消必须记录 receipt，不能被省略。
- `NONE` 仍可在无工具时 `completed`，以避免将正常回答误判成失败。
- 一个文本断言没有回执时，将其标为 `unverified`；这是一层防御，不是执行需求判断的唯一来源。

### 2.3 用户可见事件

将当前笼统的 `text` 事件拆分为语义明确的事件：

```text
commentary     # 可选进度；不是完成证据
tool_started   # call_id、tool_name
tool_result    # receipt 摘要，按渠道策略可隐藏
final          # 仅表示本轮的用户答复文本
done           # terminal_state、run_id、trace_id、reason
```

渠道默认只发送 `final`。WebUI 可以显示 commentary/tool 状态；消息渠道若要展示进度，必须以“进行中”样式渲染，不能把它当最终答复。`done` 为 `unverified`、`incomplete`、`failed` 或 `cancelled` 时，final 文本必须反映该状态。

## 3. 目标数据模型与存储方案

### 3.1 为什么不继续只使用 `memory_turns`

当前 `memory_turns` 是 user/assistant 可见会话历史，`ConversationTurn` 只有 `role`、`content`、`source` 和自由 metadata。它适合搜索与展示，不适合保证 provider 工具协议的成对关系。继续把完整 tool transcript 塞进该表会导致：

- 旧查询和关键词索引把工具输入/输出当自然语言；
- provider-specific tool call 格式泄漏到通用历史层；
- 历史压缩和 user-facing transcript 的职责混杂；
- 无法可靠表达一个 run 的终态与执行证据。

推荐新增独立的 canonical run ledger，`memory_turns` 保留为兼容的可见会话投影。

### 3.2 新表与领域对象

新增 migration（当前 schema 后的下一版本）和 repository：

```text
agent_runs
  run_id TEXT PRIMARY KEY
  session_id TEXT NOT NULL
  workspace_id TEXT
  provider_id TEXT
  model TEXT
  api_family TEXT
  terminal_state TEXT NOT NULL
  completion_contract_json TEXT NOT NULL
  trace_id TEXT
  started_at TEXT NOT NULL
  ended_at TEXT
  failure_code TEXT
  failure_detail TEXT

agent_run_events
  event_id INTEGER PRIMARY KEY AUTOINCREMENT
  run_id TEXT NOT NULL
  sequence INTEGER NOT NULL
  event_type TEXT NOT NULL
  payload_json TEXT NOT NULL
  created_at TEXT NOT NULL
  UNIQUE(run_id, sequence)

agent_execution_receipts
  receipt_id TEXT PRIMARY KEY
  run_id TEXT NOT NULL
  call_id TEXT NOT NULL
  tool_name TEXT NOT NULL
  status TEXT NOT NULL
  verification_status TEXT NOT NULL
  input_fingerprint TEXT NOT NULL
  evidence_json TEXT NOT NULL
  started_at TEXT NOT NULL
  finished_at TEXT
  UNIQUE(run_id, call_id)
```

事件类型固定为：

```text
user_input | assistant_output | tool_call | tool_result |
provider_anomaly | terminal
```

`agent_run_events.payload_json` 保存经过脱敏、大小限制后的 canonical 内容；原始 provider payload 不进入该表。工具大输出仅保存摘要、哈希、截断标记与必要 evidence，完整产物仍由原有工具/附件存储管理。

新增模块建议：

| 模块 | 职责 |
|---|---|
| `nahida_bot/agent/runtime/models.py` | `TerminalState`、`CompletionContract`、`ExecutionReceipt`、canonical event。 |
| `nahida_bot/agent/runtime/verification.py` | receipt 匹配、主张检测、终态判定。 |
| `nahida_bot/agent/runtime/transcript.py` | 当前 run 的 append-only in-memory transcript。 |
| `nahida_bot/db/repositories/sqlite_agent_run_repo.py` | run/event/receipt 的 SQLite 访问。 |
| `nahida_bot/agent/runtime/store.py` | 抽象存储接口，便于未来换后端。 |

### 3.3 向后兼容与迁移

迁移策略：

1. **不重写历史 `memory_turns`。** 它们没有足够证据恢复真实工具事务。
2. 新 run 双写：写 canonical ledger，同时按现有方式写 user/final visible turn，保证现有会话列表、搜索和渠道功能可用。
3. 旧 assistant 文字在恢复时标记为 `legacy_unverified`，不能被作为新的 verified memory 来源。
4. 新 history builder 优先读取 canonical ledger；没有 ledger 的历史回退到旧 visible turns，并在内部标记其证据等级。
5. migration 前先备份数据库；新表只追加，不修改或删除旧表。

建议将数据模型和 dual-write 置于 feature flag，例如：

```yaml
agent_runtime:
  canonical_ledger_enabled: false
  receipt_verification_enabled: false
  channel_progress_enabled: false
  legacy_history_mode: fallback
```

第一阶段只收集数据，不改变用户可见行为；确认稳定后再打开终态强制。

## 4. 分阶段实施

### Phase 0：冻结契约、样本与可观测性基线

**目的：** 在改变行为前建立可量化基线，避免“修复后感觉更好”而没有证据。

实施项：

1. 为 `AgentLoop` 接入现有 `MetricsCollector`，保证每个 run 生成 trace ID。
2. 扩充结构化日志：provider/model、context sources、工具 schema 名、raw finish reason、解析后 tool-call 数、每个工具结果和最终分支原因。
3. 在不影响行为的情况下记录 `terminal_without_tool_calls`、`tool_finish_without_parsed_calls`、`max_steps_reached` 的计数。
4. 从 #21/#24 与典型工作流建立脱敏评测集：读取、检查、修改、测试、发送、技能 URL 查询、纯问答。

涉及文件：

- `nahida_bot/core/app.py`：创建并注入 metrics。
- `nahida_bot/agent/loop.py`：补充原因码与 trace fields。
- `nahida_bot/core/session_runner.py`：记录 provider/model、contract 候选和持久化摘要。

验收：每个 run 可按 `trace_id` 查询到 provider 调用、工具调用、终态和持久化结果；不改变当前渠道输出。

### Phase 1：引入类型与 canonical ledger（无行为变化）

**目的：** 先得到可靠的执行事实，再调整终态。

实施项：

1. 新增第 3 节的 runtime domain models 和 `AgentRunStore` 抽象。
2. 新增 SQLite migration、repository 和异步写入实现。
3. `AgentLoop.run_stream()` 在开始时创建 run；每次 provider response、tool call、tool result、异常和结束都按 sequence 写事件。
4. `ToolExecutor` 返回值映射为 `ExecutionReceipt`。对现有工具先定义通用证据：`status`、输出摘要、输出 hash、调用 ID；再为发送/写入/命令工具逐步补充强证据。
5. run ledger 写入失败时记录 warning，但在本 phase 不改变旧行为；同时暴露失败指标。

设计约束：

- 事件 sequence 由 loop 单线程分配，工具并发化前先保证稳定顺序。
- 不把 reasoning 原文或敏感完整 output 直接落库。
- `tool_result` 不得先于对应 `tool_call` 写入；repository 应拒绝违反该约束的事件。

验收：一个多工具 run 能从数据库完整恢复 `user -> assistant -> tool_call -> tool_result -> assistant -> terminal` 顺序，且与现有工具行为一致。

### Phase 2：显式终态与协议错误处理

**目的：** 移除“没有工具调用即成功”的错误默认值。

实施项：

1. 给 `AgentRunResult` 和 `LoopEvent` 增加 `terminal_state`、`terminal_reason`、`run_id`；保留 `error` 字段一个版本周期以兼容调用方。
2. 将 `max_steps_reached` 映射为 `incomplete`。
3. 将 `finish_reason in {tool_calls, tool_use}` 但解析调用为空映射为 `failed`，原因码 `provider_tool_protocol_invalid`；可配置一次安全重试。
4. 将工具执行器缺失、工具超时、工具取消映射到明确状态和 receipt。
5. 只有 `completed` 才使用正常完成措辞；其他状态使用由运行时生成的简短、诚实说明，模型文本只作为可选上下文。
6. **（计划缺口，见 §10）** 引入 doom-loop 检测：同一 `tool + 输入指纹` 连续重复 N 次即停止或转人工审批（参考 OpenCode `DOOM_LOOP_THRESHOLD=3`）。同时把 `max_steps` 默认值从 128 下调到 sane 值——实测中出现过 22 步 / 43 次工具调用的失控 run，未触达上限就凭空消失（trace `dcefa8c3`）。
7. **（计划缺口，见 §10）** 增加 incomplete-turn 守卫：当一轮 0-payload 且 `stop_reason ∈ {tool_use, error}` 时判为 `failed`/`incomplete`，而非 `completed`。注意它只治「provider 说 toolUse 却没产出」，不治「模型主动选择文本」（后者由 Phase 3 receipt 门控处理）。

需要修改：

- `nahida_bot/agent/loop.py`
- `nahida_bot/core/session_runner.py`
- `nahida_bot/core/router.py`
- 依赖 `AgentRunResult` 的 scheduler、orchestration executor 与 WebAPI 响应模型。

验收：所有退出路径都有确定终态；单元测试中不存在“max steps 后 final_response 等于工具前文本且 terminal_state=completed”。

### Phase 3：完成验证与纠偏轮

**目的：** 对确实需要外部证据的任务，阻止无回执完成。

实施项：

1. 在 `SessionRunner.run_stream()` 构建 `CompletionContract`。
2. 第一版 contract 来源只使用可确定信号：显式命令、工具驱动的内部流程、技能 manifest 标记、scheduler/job 类型、外部发送请求和调用方显式参数。
3. 对普通自然语言任务默认 `NONE`，同时启用 conservative 的 assistant-claim detector；它只会把“已读取/已发送/已修改”等与 receipt 不匹配的说法降级为 `unverified`，不自动触发工具执行。
4. 对 `OBSERVE/MUTATE/DELIVER`，若模型在一轮结束时没有满足 contract，则向模型追加一次受保护纠偏消息：说明缺少的证据类别，要求调用现有工具或明确说明无法执行。
5. 纠偏最多一次；仍不满足时以 `unverified` 结束，避免循环。

示例纠偏消息：

```text
The requested outcome requires verified execution evidence. No matching tool
result exists for the claimed action. Call an available appropriate tool, or
state clearly that the action was not completed. Do not claim completion without
a result.
```

验收：

- `DELIVER` 没有 message ID 时不能 completed。
- `OBSERVE` 任务仅输出“我已检查”且未调用工具时最多多采样一次，最终是 unverified。
- 普通“解释一下这个概念”仍能一轮 completed。

### Phase 4：渠道发布与进度事件改造

**目的：** 不让未证实的过程文本变成渠道中的最终结论。

实施项：

1. 将当前 `text` 事件替换为 `commentary`，或保留别名但增加 `visibility=progress`。
2. `Router` 默认缓存 progress，不直接向聊天渠道发送；WebUI/SSE 显示它和 tool status。
3. `final` 仅在 loop 取得 terminal state 后产生；`done` 只传状态，不重复发送文本。
4. 配置渠道策略：`final_only`（默认）、`progress_updates`、`silent_tool_status`。
5. 对 `unverified/incomplete/failed/cancelled` 使用统一、可本地化的交付模板，附带 trace ID 的管理员可见引用而非暴露内部异常。

验收：带工具的 run 在工具回执前不会在外部消息渠道显示“已完成”；WebUI 仍能看到完整进度。

### Phase 5：跨轮 transcript 重放与 Context Builder

**目的：** 让模型在后续轮次看到执行事实，而不是孤立的 assistant 叙述。

实施项：

1. 新增 `TranscriptProjector`，从 canonical ledger 重建 `ContextMessage` 序列。
2. 对支持 function/tool 协议的 provider，输出成对的 assistant tool-call 和 tool result；复用并扩展现有 provider serializer。**任何缺 result 的 tool call 必须注入 synthetic result（成功/中断/缺失三类），永不留悬空 tool_use（见 §10：Codex/OpenCode/OpenClaw 均如此；这是「长上下文→#21 恶化」的结构根因）。**
3. 对无法/不应重放原始协议的 provider，输出有来源的 execution summary，例如：

```text
Verified execution receipt:
- call_id: call_123
- tool: workspace_read
- status: ok
- evidence: file digest …
```

4. 更新 ContextBuilder 的原子分组逻辑，使 `assistant tool_call + N tool_result` 在 history 裁剪、摘要和 **compaction** 中不可拆分（见 §10：OpenClaw `compaction.ts` 追踪 `pendingToolCallIds`，拒绝在 call 与其 results 之间切分，裁剪后重跑配对修复）。
5. 从 `_assistant_visible_turns()` 移除“工具 metadata 故意不持久化”的职责；该方法改为只生成面向会话列表的 visible projection。
6. 修正 `docs/guide/workspace-files.md`：默认注入 skill catalog，不是所有完整 `SKILL.md`；完整正文由 `skill` 工具按需加载。

验收：完成、失败和中断的工具调用在下一轮都有匹配状态；没有孤立 tool result，也没有把工具前 assistant 文本单独当作完成历史。

### Phase 6：Memory provenance 与冲突裁决

**目的：** 防止 #24 类助手断言进入长期、系统级 memory。

实施项：

1. 扩展 `MemoryItem.evidence` / `metadata`，记录 `provenance`、`source_ref`、`verified_at`、`expires_at`、`verification_status`。
2. 调整 `MemoryConsolidator` 输入：提供 canonical transcript 与 receipts，不再只输入拼接后的 assistant 文本。
3. 默认自动写入白名单：用户明确偏好、用户明确决定、成功 receipt 支撑的稳定 workspace/project 事实。
4. URL、域名、路径、命令、网络事实和 model inference 默认创建 pending candidate，不注入 active memory；可由用户或后续成功读取确认。
5. `RetrievalService` 默认过滤 `pending`、`unverified`、过期和低置信度项；注入时展示来源/验证状态。
6. 技能/当前文件与 memory 冲突时，优先读取权威源；未读取则输出不确定，不允许基于 memory 选择外部地址。
7. 旧 `source="consolidation"` 条目迁移标为 `legacy_unverified`，不删除，但降低自动检索优先级。

验收：模型仅凭文字生成的域名不会成为 active verified memory；成功 `workspace_read`/skill 加载的稳定事实才可被自动固化。

### Phase 7：基础提示、workspace 和工具面

**目的：** 保留 Markdown 灵活性，同时为模型提供不可缺失的执行语义。

实施项：

1. 将最小“证据与终态契约”加入运行时拥有的基础提示，不由 workspace 覆盖。
2. `AGENTS.md`、`SOUL.md`、`USER.md` 继续在其后注入；不覆盖用户现有文件。
3. 为默认 workspace 增加 policy/version 注释和可选管理员迁移工具，提示已有文件与最新模板的差异。
4. 对 model capability `tool_calling=False`：不暴露普通工具 schema；若 contract 要求 evidence，直接产生可解释的 `unverified`/`failed`，不让模型假装执行。
5. 引入初始工具 allowlist（按 agent profile、渠道、skill 和风险类别），后续再考虑 deferred tool discovery。
6. `ChatProvider` 增加 provider-neutral tool selection policy：`auto | required | none`。只有 contract 明确要求且 provider 支持时才使用 `required`；不要对所有回答强制工具。**这是唯一能在物理上阻止「声称却不调用」的手段，建议优先于 receipt 门控落地：在 router 命令→具体工具、skill manifest 指定工具、DELIVER contract 等确定信号上用 `required` / specific tool（见 §10）。**

验收：同一 workspace 的用户定制继续生效；基础 prompt 即使在空 workspace 下也能禁止无回执的外部状态声明。

## 5. 文件级改造清单

| 文件 / 模块 | 主要改动 |
|---|---|
| `nahida_bot/agent/loop.py` | 终态状态机、纠偏轮、canonical events、receipt 生命周期、`text` 事件语义调整。 |
| `nahida_bot/agent/providers/base.py` | tool selection policy、provider protocol anomaly 表达。 |
| `nahida_bot/agent/providers/openai_compatible.py` | 将 tool finish 但解析为空提升为结构化异常；支持 required tool choice。 |
| `nahida_bot/agent/providers/anthropic.py`、`openai_responses.py` | 同步 provider-specific tool-choice/协议错误映射。 |
| `nahida_bot/plugins/tool_executor.py` | 将工具返回标准化为 receipt，补充强 evidence adapters。 |
| `nahida_bot/core/session_runner.py` | contract 解析、ledger 双写、history projector、memory consolidation 输入。 |
| `nahida_bot/core/router.py` | progress/final/done 的渠道策略和终态映射。 |
| `nahida_bot/agent/context.py` | canonical transcript 分组、provider-safe 重放、reasoning policy 落地。 |
| `nahida_bot/agent/memory/consolidation.py` | provenance-aware 写入和候选审批。 |
| `nahida_bot/agent/memory/markdown.py` | memory projection 中显示验证状态；不投影 pending/unverified。 |
| `nahida_bot/db/engine.py` | 新增 ledger migration。 |
| `nahida_bot/db/repositories/` | 新增 agent-run repository。 |
| `nahida_bot/core/app.py` | 接入 `MetricsCollector`、run store 和 feature flags。 |
| `docs/guide/workspace-files.md` | 修正 skills 注入说明，描述模板升级不覆盖既有文件。 |

## 6. 测试计划

### 6.1 AgentLoop 单元测试

扩展 `tests/test_agent_loop.py`：

- `NONE` contract 下的直接回答仍为 `completed`。
- `OBSERVE` contract 下空调用 + 完成性文本，触发一次纠偏后为 `unverified`。
- `MUTATE`/`DELIVER` receipt 缺失、错误、超时、取消均不可能 completed。
- `finish_reason="tool_calls"` 却无有效解析调用为 `failed`。
- `max_steps` 为 `incomplete`，不返回工具前叙述作为成功 final。
- 多工具、并行工具（引入并发时）仍有稳定 call/result 配对和 sequence。

### 6.2 Context 与 provider 测试

扩展 `tests/test_agent_context.py`、`tests/test_provider_openai_compatible.py` 和 Responses/Anthropic provider 测试：

- history 裁剪不能拆 tool call/result group；
- 从 ledger 生成的各 provider payload 满足 API 排序和 ID 要求；
- 失败/中断工具被编码为合法 tool result 或事实化 summary；
- no-tool 模型不接收 tools；
- required tool choice 只在支持的 provider/contract 生效；
- skill catalog 与完整 skill 按需加载的上下文顺序符合文档。

### 6.3 Session、memory 与渠道集成测试

新增或扩展 SessionRunner/Router 测试：

- ledger 与 visible history dual-write 不改变会话列表；
- 下一轮看到 receipt，不只看到 assistant 声称；
- 未验证 assistant URL 不得被 consolidation 写成 active memory；
- verified skill/current-file 来源可被写成对应 provenance 的 memory；
- memory 与 skill 冲突时要求读取权威源；
- `final_only` 渠道不会在 receipt 前发送完成文本；
- `progress_updates` 可显示进行中状态但最终状态正确；
- 旧没有 ledger 的 session 仍能读取与继续对话。

### 6.4 数据库迁移与回归测试

新增迁移测试：

- 空数据库从零初始化后表、索引和 schema version 正确；
- 已有最新数据库只新增 ledger 表，不损坏 `memory_turns`、`memory_items` 和现有索引；
- migration 中断后可安全重跑；
- ledger repository 拒绝重复 sequence、孤立 result 和 run 结束后的新事件；
- rollback 时关闭 feature flag 能使用旧 history reader。

## 7. 灰度、监控与回滚

### 7.1 发布顺序

1. 部署 Phase 0 观测。
2. 部署 Phase 1 ledger，启用 dual-write，但读取和渠道行为保持旧逻辑。
3. 对测试 workspace 启用 Phase 2/3 终态与 receipt guard。
4. 对内部 WebUI 启用 progress 分层；消息渠道保持 `final_only`。
5. 启用 canonical history reader，并对比旧/新上下文差异与 token 成本。
6. 最后启用 memory provenance gate；旧 memory 先降权，不批量删除。

### 7.2 必备指标

```text
agent_runs_total{terminal_state}
agent_terminal_without_tool_calls_total
agent_provider_tool_protocol_invalid_total
agent_receipt_missing_total{evidence_mode}
agent_claim_receipt_mismatch_total
agent_correction_turn_total
agent_history_legacy_fallback_total
agent_memory_candidate_total{provenance,status}
agent_memory_rejected_total{reason}
agent_channel_progress_suppressed_total
```

告警建议：`provider_tool_protocol_invalid` 激增、`unverified` 比例突增、ledger 写入失败、历史重放孤立工具项、外部发送无稳定 receipt。

### 7.3 回滚原则

- 使用 feature flag 回退读取、纠偏和渠道策略，不能删除 ledger 数据。
- 数据库 migration 必须是向前兼容的追加操作；业务回滚不依赖 schema downgrade。
- 出现 provider 序列化兼容问题时，先退回“事实化 execution summary”，不要回退为丢弃工具结果。
- memory gate 出现误阻时，将条目留为 pending candidate 并提供管理员确认，不恢复自动把 assistant 断言写为 active fact。

## 8. 风险与决策点

| 风险 / 决策 | 推荐处理 |
|---|---|
| 普通回答被误判为需要工具 | contract 默认 `NONE`；只对可确定流程要求 receipt；文本检测仅作为降级防线。 |
| 纠偏增加一次模型调用 | 上限一次，只在 contract 或无证据完成性断言触发；指标评估额外延迟/成本。 |
| provider 不支持 required tool choice | 不伪造调用；使用提示 + 一次纠偏，最终 unverified。 |
| tool output 过大或敏感 | ledger 保存摘要、哈希和 evidence，原输出沿用受控附件/工具存储。 |
| 旧会话没有 call/result | 作为 legacy history 读取，不追溯伪造 receipt。 |
| 插件无法提供强 evidence | 先标准化通用 receipt；高风险/外发工具要求插件补充 message ID、artifact ID 或等价证据。 |
| workspace 模板升级无法覆盖用户文件 | 保持不覆盖；以版本标记、diff 提示和可选迁移工具处理。 |

## 9. Definition of Done

改造完成必须满足：

1. 所有 AgentLoop 退出路径都有可查询 terminal state 和 run ID。
2. 每个工具调用都能找到一个成功、失败、取消或超时 receipt；无孤立结果。
3. 需要外部证据的任务不能在没有匹配 receipt 时进入 `completed`。
4. 渠道不会把工具前/未证实叙述作为完成答复发送。
5. 新会话跨轮重放保存工具事实；旧会话安全兼容。
6. assistant 自述不再单独作为自动 memory fact 的来源；memory 有 provenance 和验证状态。
7. `AGENTS.md`/skills 仍可编辑、仍按 workspace-first 方式使用，且基础运行时不变量不依赖它们存在。
8. 上述测试通过，评测集上“无 receipt 却声称完成”的比例为零，纯问答完成率未出现不可接受回归。

## 10. 跨 harness 机制对照（结构层，非提示 / 非模型）

> 来源：2026-06-25 对 Claude Code（逆向）、Codex、OpenCode、OpenClaw 的代码级对照，配合本仓库 `data/log.debug.jsonl` 实测基线。「模型档位」假设已被校准：经同模型（deepseek-v4-pro / gpt-5.4）接入 CC / Codex 复现确认，#21 不是模型层问题，模型只调节发生频率，且**随上下文增长而恶化**。

### 10.1 核心结论

五个 harness（含 Nahida）的**终止逻辑完全一致**——文本无工具调用即结束本轮；**无一在主循环强制 `tool_choice`**（CC `query.ts:674`、Codex `client.rs:857` hardcoded `"auto"`、OpenCode `prompt.ts:1381` 仅 json_schema 时 required、OpenClaw 透传、Nahida 全仓零匹配）。因此终止逻辑与 tool_choice 都**不是**差异化变量。

真正的 harness 变量只有两个：

1. **提示词内容**（→ Phase 7）。Nahida 基线是 `"You are a helpful assistant."` + ~50 词；其余 harness 有明确的 anti-promise / anti-fabrication / 「文本沟通、工具行动」语义。
2. **历史重放里有什么**（→ Phase 5，本附录重点）。

变量 2 解释了「上下文越长、#21 越多」：Nahida 跨轮剥离工具调用/结果（#24），长上下文里模型只看到自己过去的文本叙述，**in-context learning 反向 priming**；CC/Codex/OpenClaw 保留 tool_use↔result 配对，长上下文反而不断示范「发射工具→拿到结果」。这个不对称随上下文增长而放大。

旁证：Claude Code 自身提示词注释 `claude-code/src/constants/prompts.ts:237` 标注 `False-claims mitigation for Capybara v8 (29-30% FC rate vs v4's 16.7%)`——前沿模型同样有约 30% 虚假声明率，CC 部分靠 prompt 规则（`:240`）缓解，且 `USER_TYPE==='ant'` 灰度 + A/B 验证。说明 prompt 注入有效但非 100%，需用 Phase 0 基线测量。

### 10.2 可借鉴的结构机制（按对 #21/#24 的杀伤力排序）

**Tier 1 — 直接打「长上下文恶化」根因**

| 机制 | 来源（已核实代码） | 防什么 | 落在 |
|---|---|---|---|
| 规范化历史重放：任何缺 result 的 tool call 注入 synthetic 输出 | Codex `context_manager/normalize.rs:14-122` + `history.rs:133,351-359`（synthetic `"aborted"` + `remove_orphan_outputs`）；OpenCode `session/message-v2.ts:363-374`（pending/running→`output-error`，注释 "every tool_use must have a corresponding tool_result"）；OpenClaw `session-transcript-repair.ts:135-152,355-530`（`makeMissingToolResult` + 重排配对 + 去重） | 模型看到自己悬空的 tool_use，转而用文本描述动作 | Phase 5（补 synthetic 兜底层） |
| compaction 不拆 call/result 组 | OpenClaw `compaction.ts:171-194`（追踪 `pendingToolCallIds`，拒绝在 call 与 results 间切分，裁剪后重跑 `repairToolUseResultPairing`） | 长上下文压缩时丢失工具事实 | Phase 5（原子分组须覆盖 compaction） |
| 窄场景 `tool_choice=required` / specific tool | OpenCode `session/prompt.ts:1381`；CC `WebSearchTool.ts:281` / `yoloClassifier.ts:1152` / `permissionExplainer.ts:183`（均仅窄 side-query） | 「声称却不调用」在物理上不可能 | Phase 7（**建议提升优先级**） |

**Tier 2 — 协议边缘鲁棒性**

| 机制 | 来源 | 防什么 | 落在 |
|---|---|---|---|
| parse 失败回灌 synthetic `invalid` 工具结果继续循环（repair 优于 fail） | OpenCode `session/llm.ts:296-312`（隐藏的 `invalid` 工具，`:317` 从 activeTools 隐藏，模型无法主动选） | provider 返回残缺 tool call | Phase 2（设计选择：原「parse 为空→failed」可改为回灌重试） |
| incomplete-turn 守卫：0-payload + `stopReason ∈ {toolUse, error}` → error 而非 completed | OpenClaw `pi-embedded-runner/run/incomplete-turn.ts:101-127` | provider 说 toolUse 却没产出 | Phase 2（**计划缺口**；不治模型主动选文本） |
| doom-loop 检测 + sane `max_steps` | OpenCode `session/processor.ts:35`（`DOOM_LOOP_THRESHOLD=3`，同 tool+input 重复 3 次触发审批） | 失控工具循环（实测 `dcefa8c3`：22 步 / 43 工具 / 10 min 凭空消失） | Phase 2（**计划缺口**；`max_steps=128` 操作上过大） |

**Tier 3 — 渠道 / 完成语义**

| 机制 | 来源 | 防什么 | 落在 |
|---|---|---|---|
| commentary / final 事件分离，渠道默认只发 final | CC / Codex / OpenCode 均分层展示 progress vs final | 未证实叙述被当作最终答复发送 | Phase 4 |
| 完成是 asserted（receipt 满足）而非 defaulted（无更多工具调用） | Codex goal mode `update_goal` + `continuation.md:30-41`（"audit must prove completion, not merely fail to find remaining work"） | 「无工具调用」被默认当 completed | Phase 2/3 |

### 10.3 建议优先级

若受限于精力只挑三个，且考虑「长上下文」痛点：

1. **Phase 5（含 synthetic-result 兜底 + compaction 不拆组）**——长上下文恶化的真正结构解药，#24 根治。其余 harness 全有，Nahida 独缺。
2. **Phase 7 窄场景 `tool_choice=required`**——唯一硬保证，改动小、性价比高。
3. **Phase 2 doom-loop + 砍 `max_steps`**——已有真实失控 case，计划原本完全没覆盖。

Phase 2/3/4 的其余机制按原计划推进即可；Phase 2 的 parse 处理建议从「fail」改为「repair」。
