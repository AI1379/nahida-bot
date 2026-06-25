# Agent Loop 与 Context Builder 审计报告：#21 / #24

> 审计日期：2026-06-24
> 结论状态：静态代码审计完成；生产请求级复现与 A/B 实验尚未执行。
> 相关问题：[#21](https://github.com/AI1379/nahida-bot/issues/21)、[#24](https://github.com/AI1379/nahida-bot/issues/24)

## 1. 执行摘要

Nahida 已经实现了 workspace-first 的上下文设计：每个 workspace 的 `AGENTS.md`、`SOUL.md`、`USER.md` 都会在每轮请求中作为 `system` 消息注入。这一点是有效的，也不是 #21/#24 的根因。

问题在于，当前实现把两类本应由运行时保证的事实交给了 Markdown 和模型自觉：

1. **执行事实**：模型说“已经读取、修改、测试、发送”并不等于工具真的执行过。
2. **历史事实**：跨轮重放时只保留了 assistant 的可见文字，丢弃了工具调用和工具结果；随后记忆整理可能把这段文字提升为长期事实。

因此，workspace 指令能够提高模型使用工具的概率，却无法把“无工具调用”从成功路径中移除，也无法为后续轮次提供可验证的执行证据。

当前最危险的链路如下：

```text
用户要求当前状态/外部动作
        │
        ▼
模型输出“我已检查/已发送 …”，但没有有效 tool call
        │
        ├─ AgentLoop：将 text 事件立即对外发送
        ├─ AgentLoop：无 tool_calls => 正常 done（不是 unverified/failed）
        ├─ SessionRunner：只持久化这段 assistant 文本
        └─ MemoryConsolidator：可能把文本中的断言写入长期记忆
                                      │
                                      ▼
                         下一轮以 system 级 memory 再次注入
```

这解释了两个问题的共同结构：

- **#21** 是“未执行却被当作完成”的当前轮错误。
- **#24** 是“未执行或错误断言被去证据化后长期化”的跨轮错误。

Codex、OpenCode 和 OpenClaw 都不能从语义上证明模型永远不会漏调工具；它们的优势不在于模型不会出错，而在于它们更完整地保留工具调用—结果配对、显式保存失败/中断状态，并以更强的基础提示约束工具行为。Nahida 当前的状态投影恰好把这些防线移除了。

## 2. 审计范围、版本与证据等级

审计的本地源码版本：

| 项目 | 审计来源 | 分支 / 提交 |
|---|---|---|
| Nahida Bot | 当前仓库工作树 | `v2` / `221333f` |
| Codex | 本地比较检出 | `main` / `4fe02f4fcf` |
| OpenCode | 本地比较检出 | `dev` / `51eb87b25` |
| OpenClaw | 本地比较检出 | `main` / `7bae391` |

本报告采用以下证据等级：

| 标记 | 含义 |
|---|---|
| **已验证** | 可由当前源码直接推导出的控制流或数据流。 |
| **高概率机制** | 已验证代码能够造成该现象，但尚未将某一次生产请求与完整 trace 对齐。 |
| **待实验** | 需要固定模型、端点、参数和工具集进行回放才能量化。 |

报告不假定 #21/#24 的任何一条生产响应一定来自某个具体 provider 解析缺陷。模型本身返回终止文本、流式 tool-call 聚合丢失字段、或工具选择受上下文影响，都会进入同一个“不含有效 `tool_calls`”分支；当前 loop 没有区分和处理这些来源。

## 3. 当前 workspace-first 设计：已实现的部分

### 3.1 工作区文件确实被每轮注入

`ContextBuilder.build_context()` 的固定前缀顺序是：

```text
1. system baseline（config.system_prompt）
2. AGENTS.md
3. SOUL.md
4. USER.md
5. workspace skill catalog（仅名称与描述）
6. workspace Markdown memory
7. history
8. 当前轮的 assistant/tool transcript 与 user turn
```

对应实现见：

- `nahida_bot/agent/context.py:303-321`：加载 `AGENTS.md`、`SOUL.md`、`USER.md`，每个文件均为 `role="system"`。
- `nahida_bot/agent/context.py:360-398`：组装前缀、历史和受保护当前轮。
- `nahida_bot/agent/memory/markdown.py:263-291`：`MEMORY.md`、`memory_summary.md`、最近三天 daily memory 按顺序加载，总量上限 6000 字符。

被审计的默认 workspace 当前包含：

| 文件 | 现有规则 / 内容 | 审计判断 |
|---|---|---|
| `AGENTS.md` | 工作区是用户工作状态；缺上下文时使用 workspace 工具；编辑前“Prefer `workspace_read`”。 | 有效的工作流建议，但“Prefer”不是执行前置条件；没有结果回执要求。 |
| `SOUL.md` | 直接、技术精准；保护用户文件；memory 是 helpful context 而非绝对真理。 | 正确地降低 memory 权威性，但这是模型行为约束而非冲突解决机制。 |
| `USER.md` | 用户偏好占位模板。 | 当前默认 workspace 中未填充业务约束。 |
| `MEMORY.md` / `memory_summary.md` | 当前含 workspace 路径和 Teyvat 知识库等结构化记忆投影。 | 文件本身没有 #24 中的错误域名；风险在自动写入链路和以后可能写入的断言。 |
| `skills/workspace-files/SKILL.md` | 读取再修改、使用相对路径。 | 对文件编辑足够明确，但需先由模型加载。 |
| `skills/memory/SKILL.md` | 仅写稳定偏好、决策、项目事实、任务结果；memory 非绝对真理。 | 意图正确，但“任务结果”没有要求来自成功工具回执。 |

### 3.2 现有文件保护对策略升级有影响

Workspace bootstrap 不会覆盖现有非空指令或 skill 文件：`WorkspaceManager._ensure_default_instruction_files()` 与 `_ensure_default_skill_files()` 仅在文件不存在或为空时写入默认内容（`nahida_bot/workspace/manager.py:315-342`）。

这保护了用户定制，但也意味着将来修改默认 `AGENTS.md`、`SOUL.md` 或内置 skill 不会自动修复已存在 workspace。任何依赖 workspace Markdown 的策略升级都需要：

1. 明确迁移机制、版本标记或管理员审阅；或
2. 将不可缺失的运行时不变量放在基础 system prompt / 代码状态机中。

当前受审计的 `data/workspace/workspaces/default/AGENTS.md` 也与源码中的最新默认文案略有差异：源码默认提示 memory 工具，而既有文件未被覆盖。这是上述设计的正常结果，不是读写失败。

### 3.3 需要纠正的一处文档—实现差异

`docs/guide/workspace-files.md` 目前称完整 `skills/*/SKILL.md` 在每轮自动注入，且工具调用回填随后进入上下文。当前实现并非如此：

- `ContextBuilder.load_workspace_skills()` 虽然存在（`nahida_bot/agent/context.py:323-339`），但 `build_context()` 没有调用它。
- 实际注入的是 `SkillCatalog.build_catalog_message()`，只含技能名和描述（`nahida_bot/agent/context.py:126-145, 386-390`）。
- 完整技能正文必须通过 `skill` 工具按需读取（`nahida_bot/plugins/builtin/commands.py:2054-2137`）。
- 当前轮工具回填会放入 `active_turn_messages`；跨轮持久化时却被投影丢弃，详见第 5 节。

这不是否定按需加载 skills 的设计。相反，按需加载能控制 token 成本；问题是文档应描述真实行为，且运行时需要在“技能明确匹配但尚未读取”时给模型足够强的基础指引。

## 4. 基础提示与 Markdown 的正确分工

将大部分可编辑行为放在 workspace Markdown 中是合理设计，OpenClaw 同样使用 `AGENTS.md`、`SOUL.md`、`USER.md`、`MEMORY.md` 作为每轮上下文。问题不在于“是否允许用户编辑规则”，而在于所有规则都不能由用户可编辑文本单独承担。

建议划分如下：

| 层 | 应承担的内容 | 是否应可由 workspace 覆盖 |
|---|---|---|
| **基础 system prompt** | 工具协议是真实来源；计划不等于执行；无工具结果不得声称外部状态；失败/中断不是完成；任务匹配技能时先加载。 | 否。它是运行时语义。 |
| **Loop / 状态机** | required-evidence 任务无回执时不得进入 completed；回执关联、终态、重试和对外发布规则。 | 否。它是硬约束。 |
| **Workspace `AGENTS.md`** | 本项目的工具顺序、测试命令、目录约定、何时需要审批、交付格式。 | 是。 |
| **`SOUL.md` / `USER.md`** | 人格、语言、用户偏好、长期边界。 | 是。 |
| **Skills** | 领域流程、特定 API/URL、复杂操作步骤。 | 是；必须有加载机制。 |
| **Memory** | 带来源和验证状态的稳定事实。 | 可编辑，但自动写入必须受运行时验证。 |

换言之，基础提示无需复制完整的 workspace 工作流。它只需要保留一小段不可绕过的“证据与终态契约”。其余内容继续由 Markdown 承担，才能保持你希望的可维护性。

当前配置中的基础提示是 `You are a helpful assistant.`（`config.yaml:33`），有工具时才追加约 50 个英文词的 `tool_use_system_prompt`（`nahida_bot/core/config.py:66-71`）。该追加文本要求“需要工具时使用 structured interface”，但没有定义：

- 哪些用户请求属于必须有外部证据的任务；
- 没有调用或调用失败时的终态；
- “我会做”和“我已做”的区别；
- 不能从 memory 猜测 URL、路径、当前文件内容；
- 技能与 memory 冲突时的证据优先级。

## 5. 已验证的 Nahida 运行时缺陷

### 5.1 #21：空工具调用被视为成功终态

`AgentLoop.run_stream()` 的每一步顺序是：

1. 调用 provider；
2. 构建并保存 assistant message；
3. 立即产生 `text` 事件；
4. 若 `response.tool_calls` 为空，产生 `done` 并正常返回；
5. 否则才执行工具，并在下一步将结果作为当前轮受保护 transcript 发送给模型。

关键路径：`nahida_bot/agent/loop.py:339-430`。

```python
display = self._display_content(response)
if display or reasoning:
    yield LoopEvent(type="text", ...)

if not response.tool_calls:
    self._log_terminal_without_tool_calls(...)
    yield LoopEvent(type="done", final_response=display, ...)
    return
```

这意味着下列两种根因在控制流上完全等价：

1. 模型选择只返回文字，没有发起工具调用；
2. provider 返回 `finish_reason="tool_calls"`，但适配器最终没有解析出有效调用。

后者并非没有诊断：OpenAI-compatible 适配器会在 `finish_reason == "tool_calls"` 且解析结果为空时记录 warning（`nahida_bot/agent/providers/openai_compatible.py:292-324`）。但 loop 不消费这个信号，仍会进入正常完成分支。

`_log_terminal_without_tool_calls()` 自己也明确标记为临时 debug 检测（`nahida_bot/agent/loop.py:602-659`）：它仅以关键词判断文字是否“看起来像要调用工具”，不改变结果。关键词覆盖不包括“审查”“修改”“发送”“归档”“完成”等大量中文任务表达，因此不能作为正确性机制。

### 5.2 过程文本会先于执行结果抵达渠道

`run_stream()` 的 docstring 明确说明：即使同一 provider turn 随后含有工具调用，文本也立即产生，以支持进度流式显示（`nahida_bot/agent/loop.py:200-220`）。Router 对 `text` 事件直接发送渠道消息（`nahida_bot/core/router.py:751-782`）。

这并不一定错误：交互式代理可以展示过程信息。但必须区分：

- **progress/commentary**：例如“我先读取配置。”
- **final/completed**：例如“配置已读取，结果为……”。

当前事件模型只把两者都当作文本。对外渠道因此无法可靠区分“意图”与“已证实的结果”。当工具没有真正调用时，过程文本还会紧接着被 `done` 固化为最终答复。

### 5.3 max_steps、取消与协议错误的终态不够精确

达到 `max_steps` 后，loop 将最后一个 assistant message 的内容当作 `final_response` 返回（`nahida_bot/agent/loop.py:432-456`）。如果最后一条是工具前的“我来处理”，或者模型尚未根据工具结果给出结论，这会错误地形成用户可见答复。

类似地，`LoopEvent.done` 的 `error` 字段只在部分取消/provider 异常路径设置；没有一个独立的 `completed / unverified / incomplete / failed / cancelled` 终态枚举。调用方只能从文本和可选 error 猜测语义。

### 5.4 工具选择和能力门控不足

`SessionRunner._collect_tools()` 将已注册的工具按 deny/allow list 汇总后直接暴露（`nahida_bot/core/session_runner.py:1786-1846`）。当前源码中可见 38 处静态 `register_tool` 调用；实际数随启用插件变化，但在一个会话内没有按任务意图分组、延迟发现或基于 agent profile 的细粒度筛选。

`ModelCapabilities.tool_calling` 被记录到日志，但 `_collect_tools()` 不利用它来拒绝给不支持 tool calling 的模型发送工具 schema。`ChatProvider.chat()` 也没有统一 `tool_choice` / required-tool 参数（`nahida_bot/agent/providers/base.py:166-201`）。

工具太多本身不会必然导致 #21，但会降低模型从大量 schema 中选到正确工具的概率，也使同一模型与 Codex/OpenCode 的“相同模型”实验并不等价。

### 5.5 #24：跨轮历史故意丢弃执行证据

`SessionRunner._assistant_visible_turns()` 的文档和实现明确表示：工具调用 metadata 不持久化；有工具请求的 assistant 文字将被重放为普通自然语言历史（`nahida_bot/core/session_runner.py:2411-2466`）。

`_persist_turns()` 只写入 user turn 和上述 assistant visible turns；`result.tool_messages` 仅参与日志，不写入会话历史（`nahida_bot/core/session_runner.py:2549-2642`）。下一轮 `_build_history_context()` 因而无法重建工具调用/结果配对，只能重放 assistant 文本（`nahida_bot/core/session_runner.py:1063-1136`）。

原设计意图是避免向 provider 重放“不完整的工具协议 transcript”。这个顾虑正确，但当前投影的副作用是更严重的语义损失：

```text
原始当前轮：assistant(tool_call) -> tool(success/error) -> assistant(final)
跨轮历史：assistant("我已完成 …")
```

正确的解决方案不是把 tool call 裸重放成普通文字，也不是删除 tool result；而是保存 canonical 的结构化事务，并用 provider-specific serializer 保证重放时 call/result 成对。

### 5.6 assistant 断言可自动进入长期 memory

会话持久化之后，`_consolidate_memory_after_turn()` 将 user text 和拼接后的 assistant visible text 交给 consolidation（`nahida_bot/core/session_runner.py:2636-2693`）。`MemoryConsolidator.consolidate_turn()` 可使用规则抽取器和可选 LLM dreamer，并把结果以 `source="consolidation"` 写入 durable memory（`nahida_bot/agent/memory/consolidation.py:352-502`）。

当前内存安全校验只拒绝：空内容、秘密标记、base64、带 token/signature 的临时 URL（`nahida_bot/agent/memory/markdown.py:68-80`）。普通域名、URL、文件路径、命令和 assistant 推断均可通过。

因此，“模型在没有读取 skill 的情况下断言某域名”**有能力**被写进 memory 并投影至 `MEMORY.md` / `memory_summary.md`。这是一条高概率机制；是否已发生在 #24 的具体实例需要查该会话的数据库记录和 trace 后才能断言。

### 5.7 memory 的系统级注入放大了冲突

除了 workspace Markdown memory，`_load_relevant_memory()` 还会最多取 5 条、4000 字符的结构化记忆，以 `role="system"`、`source="long_term_memory"` 注入（`nahida_bot/core/session_runner.py:1256-1363`）。它提示模型“current user instructions and current files take precedence”，但没有：

- provenance（用户陈述、工具结果、模型推断）；
- verified 时间、TTL 或失效策略；
- 最低检索分数阈值；
- URL/路径等高风险事实的二次验证；
- skills 与 memory 冲突时的自动读取/裁决。

在当前上下文顺序里，完整 skill 正文默认并不在上下文，只有 catalog 描述；而 memory 有完整断言并作为 system message 注入。这会让错误 memory 比正确但未加载的 skill 更容易影响模型。

### 5.8 可观测性存在“有类型、未接线”的缺口

`MetricsCollector` 与 `Trace` 类型已实现，但 Application 创建 `AgentLoop` 时没有传入 metrics 参数（`nahida_bot/core/app.py:397-415`）。正常运行的 `trace_id` 因而通常为空；默认日志配置也是 `INFO` 且未启用文件日志（`config.yaml:6-15`）。

这使 #21/#24 难以用“实际发送给 provider 的上下文、工具 schema、原始 finish reason、解析调用、执行回执、最终投影”逐项复盘。

### 5.9 reasoning policy 配置尚未形成实际裁剪策略

`ContextConfig` 声明了 `reasoning_policy` 和 `max_reasoning_tokens`（`nahida_bot/core/config.py:77-92`），但 `ContextBuilder` 当前只在 token 估算中序列化 reasoning 字段（`nahida_bot/agent/context.py:928-966`），未按 policy 对 reasoning 做 strip/append/budget 转换。这是次要问题，但会影响长期上下文的预算稳定性。

## 6. 两个问题的逐步因果链

### 6.1 #21：未调工具但回复已完成

以下路径是源码已验证的：

1. 用户提出需要当前工作区、网络、渠道或其他外部状态的请求。
2. ContextBuilder 注入基础提示、workspace Markdown、skill catalog、memory、历史和当前 user turn。
3. 模型返回普通文本，或者 provider 适配后得到空 `tool_calls`。
4. loop 先生成 text event，Router 可能立即发送给用户。
5. loop 在空 `tool_calls` 分支生成正常 `done`，没有 corrective turn、没有 `unverified`、没有失败状态。
6. 会话持久化该 assistant 文本；随后 memory consolidation 读取它。

造成“同一模型在其他 harness 不明显”的直接差别，不是模型知道或不知道工具，而是 Nahida 把第 4/5 步定义为完成；其他 harness 通常会保留更强的调用状态和结果反馈。

### 6.2 #24：错误域名/规则为何能压过 skill

高概率路径如下：

1. 正确的 URL 或流程位于某个 `SKILL.md`，但当前轮只看到 skill catalog，模型未调用 `skill` 工具读取正文。
2. 模型根据猜测、旧历史或不可靠 memory 给出域名/流程断言。
3. 当前轮或下一轮将该断言作为正常 assistant 文本保存；如果曾有工具，其结果不会被跨轮保存。
4. consolidation 把文本抽取为 project fact/procedure；普通域名不触发 `validate_memory_content()`。
5. 该条目进入 `MEMORY.md`、`memory_summary.md` 和/或 structured retrieval。
6. 之后它以 system memory 被注入，模型再次选择该断言而非主动读取完整 skill。

现有 `SOUL.md` 与 memory skill 中“memory 不是绝对真理”的文案能提供软提示，但不能检查域名是否真实、也不能让 state machine 拒绝未验证的完成声明。

## 7. 与 Codex、OpenCode、OpenClaw 的对照

### 7.1 结论先行

三者都不是“模型不可能漏调工具”的证明。OpenCode 的默认 `toolChoice` 仍多为 auto，Codex 也能接受模型直接给普通回答。它们更可靠的部分是：**一旦模型产生工具动作，调用、结果、失败和中断不会被静默压平为一段 assistant 文字；基础提示也比 Nahida 的当前基线明确得多。**

| 维度 | Nahida 当前 | Codex | OpenCode | OpenClaw |
|---|---|---|---|---|
| 基础行为提示 | 通用一句 + 小型 tool guidance | Codex 专用任务/证据提示 | 软件工程专用默认提示 | OpenClaw 所有者维护的完整运行时提示 |
| Workspace Markdown | 每轮 system 注入；skills 仅 catalog | 可按任务注入相关 skills/plugins | 指令文件与系统提示组合 | bootstrap 文件每轮注入 |
| 工具状态 | 当前轮有 transcript，跨轮只存可见文本 | 持久化 typed response items | 持久化 tool parts 及状态 | session transcript 守卫 call/result 配对 |
| 失败/中断 | 可能退化为最后 assistant 文本 | 回灌 function output / error | `completed/error/pending/running` 明确状态 | 可补 synthetic missing result，避免悬空调用 |
| 历史压缩 | 支持当前轮 tool group，但持久化已丢失历史 group | 规范化 call/output 配对 | 重放时显式错误化中断工具 | compaction 保持 call/result 同组 |
| 不猜 URL | 无专门规则 | 依赖任务指令与证据要求 | 默认 prompt 显式禁止猜 URL | 基础 prompt 要求不要发明 CLI；具体 URL 仍依赖 skill/工具 |

### 7.2 Codex：保留 typed response item 与 function output

Codex 的 turn loop 以工具路由器暴露 model-visible tool specs，并在响应流中把 tool call 作为结构化 item 处理。工具调用会立即持久化；`RespondToModel` 类型的失败也会转为 function-call output 继续给模型，而不是消失。

关键参考：

- `codex-rs/core/src/session/turn.rs`：turn 处理、tool future、follow-up。
- `codex-rs/core/src/tools/parallel.rs`：调用 ID、成功/失败结果回灌。
- `codex-rs/core/src/context_manager/history.rs`：保存完整 `ResponseItem`。
- `codex-rs/core/src/context_manager/normalize.rs`：补齐缺失结果、移除孤立项。
- `codex-rs/prompts/templates/goals/continuation.md`：要求从权威证据推进，而非依据意图、memory 或“看似合理的最终答案”。

Codex 并非把所有工具轮文本都压住；它也可能流式显示 commentary。差别在于模型可见的协议状态、工具结果和最终回复的生命周期是分离的，且恢复历史不会把“调用过工具”压缩为无证据的普通自然语言。

### 7.3 OpenCode：状态化 tool part 与 URL 约束

OpenCode 的默认系统提示包含两项与本问题直接相关的策略：工具驱动的查找/实现/验证流程，以及“不要生成或猜测 URL”（`packages/opencode/src/session/prompt/default.txt:1-4`）。这会明显改变同一模型在相同用户请求下的 tool-use 概率。

其会话处理器为每个工具调用维护 `pending`、`running`、`completed`、`error` 状态，并将输入、输出、metadata、附件和时间写入工具 part（`packages/opencode/src/session/processor.ts:138-245, 549-670`）。模型历史重放时：

- completed 映射为 output-available；
- error 映射为 output-error；
- pending/running 映射为 `[Tool execution was interrupted]`；
- 不会静默删除调用结果。

见 `packages/opencode/src/session/message-v2.ts:304-375`。

这不代表 OpenCode 的模型绝不会只说“已完成”；它说明当工具实际进入 loop 时，状态会留存并能反馈到后续推理，而 Nahida 当前会在跨轮投影时丢弃它。

### 7.4 OpenClaw：同样 workspace-first，但不是只有 Markdown

OpenClaw 是当前设计的重要参考，且它证明“workspace Markdown + 基础运行时提示”可以共存：

- `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`USER.md`、`TOOLS.md`、`MEMORY.md` 等 bootstrap 文件会被注入每轮 prompt；`SYSTEM_PROMPT` 负责告诉模型这些文件已加载。
- 其系统 prompt 是 OpenClaw 自己维护的，不是仅依赖 workspace 文件；包含 tooling、tool call style、execution bias、skills、runtime、workspace、safety 等固定章节。
- Skills 章节要求先扫描 catalog；当一个 skill 明确匹配时，必须读取相应 `SKILL.md`，最多预读一个（`src/agents/system-prompt.ts:107-123`）。
- 其系统 prompt 将 structured tool definitions 定义为工具名、描述和参数的真实来源，并要求优先使用一等工具（`src/agents/system-prompt.ts:485-550`）。

OpenClaw 的 session tool-result guard 会追踪 pending tool call，在需要时写入 synthetic missing result，并对 tool result 做持久化前的规范化和大小控制（`src/agents/session-tool-result-guard.ts:86-288`）。其 compaction 也明确维护 assistant tool call 与对应 tool result 的边界（`src/agents/compaction.ts:153-205`）。

这同样不是“OpenClaw 从语义上阻止所有假完成”的机制；它针对的是协议一致性、历史可恢复性和上下文完整性。它给 Nahida 的可借鉴点是：**workspace 内容是可编辑项目上下文，运行时拥有工具协议与 transcript 完整性的底线。**

## 8. 为什么“同一个模型”会表现不同

同一个模型名称不等于同一个推理条件。至少有以下可观测变量：

1. **系统/开发提示不同**：Nahida 当前的基线是通用提示；OpenCode 明示验证和 URL 规则；OpenClaw 有模型/运行时相关 sections；Codex 有专门的任务和工具工作流。
2. **工具 schema 不同**：工具数、工具名、描述、JSON Schema、是否按任务延迟暴露、是否带审批/权限都会改变选择概率。
3. **provider 与协议不同**：Chat Completions、Responses、Anthropic tool use、AI SDK/Pi transport 的流式合并与 tool-call 序列化并不相同。尤其要区分“模型没有调用”与“适配层未解析出调用”。
4. **历史不同**：Nahida 下一轮读取的是 assistant 可见文本；其他实现更多读取 call/result 成对的结构化事实。
5. **对外事件不同**：Nahida 的 text 事件可在完成前直接送到聊天渠道；CLI/IDE 型 harness 通常将 tool state、commentary、final 和错误分层展示。
6. **采样与服务端默认值不同**：temperature、thinking、streaming、模型路由、缓存、provider 端 system prompt 拼接均可能不同。

因此，不能仅用“相同模型”推断 harness 无关。应在第 11 节的受控 A/B 实验中固定模型、端点、temperature、工具集和输入，再分别替换提示、状态持久化与工具过滤，量化每个因素。

## 9. 建议的目标架构

### 9.1 引入明确的完成契约，而不是关键词猜测

为每次 run 建立由调用方/路由层声明的 `CompletionContract`。建议最小模型：

```text
AnswerOnly                 # 纯解释、创作、一般知识回答
Observe(required receipts) # 读取当前文件、查询当前状态、搜索、检查
Mutate(required receipts)  # 修改文件、执行命令、更新配置
Deliver(required receipts) # 发消息、发布、提交、外部写入
```

每项 required action 必须关联 `ExecutionReceipt`：

```text
call_id, tool_name, status, started_at, finished_at,
input_fingerprint, evidence (file hash / exit code / message_id / artifact),
verification_status
```

规则：

- required receipt 缺失时，run 只能是 `unverified` 或 `incomplete`，不能是 `completed`。
- 模型不得仅靠文本把任务转成完成；可追加一次纠偏 turn，要求选择并调用合适工具。
- 不自动替模型执行有副作用操作；纠偏只允许模型发起受正常权限控制的工具调用。
- 纯回答任务不需要强行调用工具，避免把通用问答误判为失败。

这比“文本里有‘已完成’就报警”的关键词策略可靠。关键词检测可以保留作 telemetry/告警，但不能决定正确性。

### 9.2 明确终态并隔离对外文本

将 `LoopEvent.done` 的语义由“有最后文本”改为显式状态：

```text
completed | unverified | incomplete | failed | cancelled
```

建议事件模型至少区分：

```text
commentary/progress  # 可选显示，永远不等于完成
tool_started
tool_result          # 带 receipt
final                # 只有 terminal state=completed 时才可标为完成
done                 # 包含 terminal state 与 trace id
```

渠道适配层默认只发送 `final`；如果产品需要过程可见性，再将 `commentary/progress` 显式渲染为进度而非最终消息。`max_steps` 应返回 `incomplete`，不得回收最后一条 assistant 文本伪装成功。

### 9.3 保存 canonical transcript，不保存“可见文本投影”作为唯一事实

推荐将一次 run 持久化为独立的、可审计的事件流：

```text
user_input
assistant_output (text/reasoning, phase)
tool_call (call_id, name, arguments)
tool_result (call_id, status, normalized output, receipt)
terminal (state, final message, reason)
```

重放到 provider 时由 provider-specific serializer 将 call/result 成对编码；展示给用户时只取 final 或明确的 progress。不要再使用“将带工具调用的 assistant 文本重放成普通自然语言”来绕过协议约束。

如果短期无法迁移完整存储，过渡方案至少应：

1. 不持久化工具前的 assistant 叙述为正常聊天历史；
2. 将每个成功/失败工具调用持久化为结构化 receipt；
3. 把 final assistant answer 与其 receipt IDs 关联；
4. 恢复时注入简短、事实化的 execution summary，而非模型自述。

### 9.4 memory 必须只吸收有来源的稳定事实

建议为 memory item 增加：

```text
provenance: user_explicit | workspace_file | skill | tool_receipt | model_inference
verified_at, source_ref, expires_at, confidence, conflict_policy
```

默认策略：

- 用户明确偏好/决定可以自动写入。
- 读取到的稳定配置、文件事实只能从成功工具 receipt 或已加载 skill 写入。
- 模型推断、URL、域名、路径、命令、网络事实默认只存为候选，不能自动注入为 authoritative memory。
- `model_inference` 不能覆盖 `skill`、当前 workspace 文件或成功工具结果。
- memory 与 skill/current file 冲突时，必须读取权威源；无法读取时明确不确定，而不是选一个看似合理的值。

这不是单纯扩充 `validate_memory_content()` 的黑名单。错误域名并不含秘密或临时 token；必须从写入来源和验证状态处理。

### 9.5 保持 Markdown 可编辑，同时给基础提示加最小不变量

建议基础提示保留以下语义，长度可以很短：

```text
Structured tool definitions and returned tool results are the source of truth
for external state. Plans and narration are not actions. Before claiming that
you read, changed, tested, sent, searched, or otherwise observed external
state, obtain and use the corresponding tool result. If no result exists, say
that the action is not verified. Treat memory as unverified context unless it
has a current source; load a clearly matching skill before relying on its
domain-specific facts. Tool failures and interrupted calls are not completion.
```

这段只定义运行时不变量；项目命令、文件位置、风格、审批规则和复杂流程仍放在 `AGENTS.md`/skills 中。也应在文档中明确：workspace 文件是用户拥有的上下文，不是硬安全策略；权限、审批、工具 allowlist 和状态转换必须由代码强制。

### 9.6 工具面与技能加载策略

- 对 `tool_calling=False` 的模型，不发送常规工具 schema，或明确拒绝需要执行证据的任务。
- 按 agent profile、任务类别和权限缩小初始工具面；大型插件生态可采用 `tool_search`/deferred tools。
- 将 skill catalog 的“明确匹配则先读取完整 SKILL.md”提升为基础提示不变量；仍可保持一次只加载一个 skill 以控制上下文。
- Skill 中的 URL、命令和规则应带稳定来源，并优先于无验证 memory。

### 9.7 可观测性和运维

每个 run 应始终产生可持久化的 trace，至少记录：

- provider/model/API family、请求参数摘要和工具 schema 名称；
- context source 列表、字符/token 预算与截断；
- 原始 finish reason、解析后的 tool calls、解析失败原因；
- 每个 tool receipt、retry、timeout、权限拒绝；
- terminal state 及其判定依据；
- memory candidate 的输入来源、写入/拒绝原因。

生产环境应启用可检索的文件/JSON 日志或 trace store。日志中不得存密钥、完整附件或未经脱敏的敏感 tool output。

## 10. 分阶段实施顺序

| 优先级 | 改动 | 解决的问题 | 兼容性要点 |
|---|---|---|---|
| P0 | 增加 terminal state；`max_steps` => incomplete；`finish_reason=tool_calls` 但解析为空 => protocol error。 | #21 的假成功。 | 对 API/渠道新增状态字段，保留旧文本字段作兼容。 |
| P0 | 对 `Observe/Mutate/Deliver` 引入 required receipt；无回执进入 corrective/unverified。 | 未执行却声称完成。 | 初期可仅对明确的 router 命令/工具任务启用，避免误伤普通问答。 |
| P0 | 将工具前文本标为 progress；渠道默认只发送 final。 | 用户先看到错误完成结论。 | WebUI 可继续显示进度事件。 |
| P0 | 始终接入 trace/metrics，并存储 provider parse anomaly。 | 无法复盘 #21。 | 不改变模型行为。 |
| P1 | 用 canonical tool transaction 替换 visible-text-only 历史投影。 | #24 的证据丢失。 | 需要 migration 和 provider serializer 测试。 |
| P1 | memory provenance/verified 状态；禁用模型断言自动升格为事实。 | 错误长期化。 | 旧 memory 标为 `legacy_unverified`，不直接删除。 |
| P1 | 更新 workspace guide，纠正完整 skill 自动注入的描述。 | 运维认知与实际不一致。 | 保留按需加载设计。 |
| P2 | 工具延迟发现、模型能力门控、agent profile。 | 工具选择质量与提示成本。 | 对插件作者提供稳定的工具可见性契约。 |
| P2 | 落实 reasoning policy 与 prefix 预算。 | 长会话稳定性。 | 需为 reasoning-enabled provider 做回归测试。 |

## 11. 必需的测试与受控实验

### 11.1 单元/集成测试

新增至少以下案例：

1. required-evidence 任务中，模型返回“已读取/已发送”但无 tool call：结果为 `unverified`，不能为 completed。
2. `finish_reason="tool_calls"` 但 tool call 解析为空：标记 protocol error，不能正常 done。
3. 工具轮同时含文本和调用：外部渠道在 receipt 前不能显示“已完成”。
4. 发送工具没有 `message_id` receipt：不得宣称消息已送达。
5. `max_steps`、取消、timeout：必须产生 `incomplete/cancelled/failed`，不得复用最后 assistant 文本作为成功答案。
6. 跨轮恢复时，成功、失败和中断工具均保留 call/result 对或事实化 execution summary。
7. 自动 consolidation 不得将无 receipt 的 assistant URL/域名提升为 verified memory。
8. 错误 memory 与匹配 skill 冲突时，模型必须先读取 skill/current file；无法读取则返回不确定。
9. 普通知识问答不应因为没有工具调用而被误判为 `unverified`。
10. 既有 workspace 文件不被覆盖时，管理员可看见 policy 版本过期或执行显式迁移。

此前运行过的相关测试集：

```text
python -m pytest tests/test_agent_loop.py tests/test_agent_context.py \
  tests/test_session_runner.py tests/test_provider_openai_compatible.py
64 passed
```

这些测试覆盖了大量局部功能，但未覆盖上述 required-evidence、渠道文本隔离、跨轮 receipt 持久化、assistant 断言 memory 晋升和 memory-skill 冲突场景。

### 11.2 受控 A/B：证明“为什么同模型不同”

静态审计能说明代码允许问题发生，不能量化每个因素对某个模型的影响。建议使用 #21 类任务构造一组固定提示（至少包含读取、修改、测试、发送、URL 查询），并固定：

- 同一模型 ID、同一 provider endpoint、同一 API key/账户；
- temperature、top_p、thinking 开关、streaming；
- 同一工具 schema、同一 workspace snapshot、同一历史；
- 同一运行次数与随机种子（provider 支持时）。

至少比较四组：

| 组 | 变化 | 主要回答的问题 |
|---|---|---|
| A | 当前 Nahida 基线 | 当前真实失败率。 |
| B | 仅换成最小执行不变量基础提示 | 提示词对 tool-call 率的影响。 |
| C | 仅增加 receipt/terminal guard | 即使模型漏调，是否还会假成功。 |
| D | B + C + 工具筛选/按需 skill | 完整改造的端到端收益。 |

指标：

- 首轮有效 tool-call rate；
- tool-call parse anomaly rate；
- 有有效 receipt 的完成率；
- 无 receipt 却声称完成率；
- 错误 URL/路径断言率；
- 错误断言进入 memory 的比例；
- 平均 steps、延迟、token 和用户可见 progress 条数。

记录请求快照时必须脱敏 API key、cookie、附件原文和敏感 tool output。

## 12. 结论与决策建议

保留 workspace Markdown 作为可编辑上下文是正确方向，且与 OpenClaw 的设计相符。需要修正的是边界：

> Markdown 决定“这个 workspace 希望 agent 如何工作”；运行时决定“什么算已经发生、什么算完成、什么能够成为长期事实”。

对 #21，最先落地的修复应是 required receipt + 显式终态，而不是继续扩展关键词检测。对 #24，最关键的是停止将 assistant 可见文本作为唯一跨轮事实，并禁止无来源的模型断言自动进入 verified memory。

在这两项完成前，强化 `AGENTS.md`、`SOUL.md` 和 skill 文案仍值得做，但只能降低模型犯错概率，不能构成正确性保证。

具体的实现切分、数据模型、兼容迁移、测试门槛与灰度/回滚方案见[《AgentLoop 改造实施计划》](../design/agent-loop-repair-plan.md)。
