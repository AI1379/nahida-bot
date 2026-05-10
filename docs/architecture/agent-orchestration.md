# Agent 与 Subagent 编排架构

> 目标：先在本地单进程 `asyncio` 运行时中完成一层 Subagent 编排、任务账本和跨会话事件接口。Gateway-Node、细粒度权限和 A2A 都只预留接口，不成为 Phase 3.8 MVP 的复杂依赖。

## 1. 设计定位

nahida-bot 当前已经有一个可工作的单 Agent 闭环：

```text
SessionRunner
  -> ContextBuilder
  -> AgentLoop
  -> Provider
  -> ToolExecutor / ToolRegistry
  -> MemoryStore
```

这个闭环适合普通对话和短工具链。Phase 3.8 要补齐的是 AgentLoop 上层的运行编排能力：

1. **并行分工**：主 Agent 可以把研究、验证、长耗时工具链拆给后台子 Agent。
2. **会话隔离**：每个子 Agent 使用独立 `session_id`，默认只把摘要回传给父会话。
3. **任务可追踪**：子 Agent、cron、CLI 后台任务都能查询、取消、恢复和审计。
4. **接口可扩展**：将来可以替换本地执行器为 Gateway-Node 远程执行，但 AgentLoop 不感知这一层。

本设计参考 Codex 与 OpenClaw，但做收敛：

- Codex 提供 `spawn_agent` / `send_input` / `wait_agent` 这类 **agent-control tools**。关键经验是：由主 Agent 显式决定是否派生、子任务要具体且自包含、不要把当前关键路径上的阻塞工作随意丢给子 Agent。
- OpenClaw 的 `sessions_spawn` / `sessions_yield` 体现了 **push-based completion**：spawn 后不鼓励轮询，子任务完成后以事件形式回到请求会话。
- nahida-bot 首版只做**一层子 Agent**，不做递归多层 agent tree，不要求为每个子 Agent 配独立长期 profile。

## 2. 设计原则

- **复用现有 session 系统**：`session_id` 是上下文、历史和可见性的基础；子 Agent 只是创建一个新的 child session。
- **Subagent 是一次临时任务，不是长期身份**：主 Agent 用 `task` 和可选 `instructions` 临时描述子任务；不要求维护 `research`、`coder`、`reviewer` 等固定 profile。
- **Agent-as-tool 优先**：`agent_spawn`、`agent_yield`、`agent_list`、`agent_stop` 作为普通内置工具暴露给主 Agent。
- **Gateway-Node 透明**：编排层只依赖 `AgentRunExecutor` 接口；本地执行器和远程节点执行器是实现细节。
- **权限先留钩子**：首版只做粗粒度 hook、配额和工具过滤，不建立复杂 policy DSL。
- **禁止嵌套派生**：MVP 的最大派生深度固定为 1；子 Agent 默认看不到 `agent_spawn`。
- **父会话默认只读摘要**：父 Agent 不自动读取子 session 全量历史，避免上下文膨胀和信息边界混乱。

## 3. 核心概念

### 3.1 SubagentSpec

`SubagentSpec` 是主 Agent 调用 `agent_spawn` 时提交的一次性任务说明。它不是长期 profile。

```python
@dataclass(slots=True, frozen=True)
class SubagentSpec:
    task: str
    label: str | None = None
    instructions: str | None = None
    context_mode: Literal["isolated", "summary", "fork"] = "isolated"
    handoff_summary: str | None = None
    provider_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int | None = None
    tool_allowlist: tuple[str, ...] = ()
    tool_denylist: tuple[str, ...] = ()
    notify_policy: Literal["done_only", "silent"] = "done_only"
```

字段含义：

| 字段 | 含义 |
|---|---|
| `task` | 子 Agent 要完成的具体任务，必须自包含 |
| `label` | 便于 UI / 日志展示的短名称 |
| `instructions` | 主 Agent 为本次任务临时补充的系统指令或工作方式 |
| `context_mode` | 子 Agent 如何获得父会话上下文 |
| `handoff_summary` | `summary` 模式下传给子 Agent 的背景摘要 |
| `provider_id` / `model` | 可选模型覆盖，不改变全局配置 |
| `tool_allowlist` / `tool_denylist` | 本次子任务的工具面收窄 |
| `notify_policy` | 完成后是否向父 session 投递完成事件 |

### 3.2 AgentRun

`AgentRun` 表示一次实际执行。主聊天、子 Agent、cron 和 CLI 后台任务都可以统一建模为 run。

```python
class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    LOST = "lost"


class AgentRunKind(StrEnum):
    MAIN = "main"
    SUBAGENT = "subagent"
    CRON = "cron"
    CLI = "cli"


@dataclass(slots=True)
class AgentRun:
    run_id: str
    kind: AgentRunKind
    session_id: str
    parent_run_id: str | None
    requester_session_id: str | None
    task_id: str | None
    status: AgentRunStatus
    depth: int
    asyncio_task: asyncio.Task[AgentRunResult] | None
    cancellation: CancellationToken
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str = ""
    error: str = ""
```

`run_id` 用于运行时追踪，`task_id` 用于持久化任务账本，`session_id` 复用现有会话历史和上下文系统。子 Agent 的 `depth` 固定为 `1`；主 Agent、cron、CLI run 为 `0`。

### 3.3 BackgroundTask

`BackgroundTask` 是可持久化的任务账本。它不负责调度，只记录状态。

```python
class TaskRuntime(StrEnum):
    SUBAGENT = "subagent"
    CRON = "cron"
    CLI = "cli"
    REMOTE_NODE = "remote_node"  # Phase 5 预留


@dataclass(slots=True)
class BackgroundTask:
    task_id: str
    runtime: TaskRuntime
    status: AgentRunStatus
    requester_session_id: str
    child_session_id: str | None
    parent_task_id: str | None
    title: str
    summary: str = ""
    delivery_target: dict[str, str] | None = None
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    error: str = ""
```

首版用 SQLite 持久化，终态任务保留 7-30 天并可配置清理。

### 3.4 AgentProfile 的位置

`AgentProfile` 不应是 Phase 3.8 子 Agent 的核心依赖。原因：

- Claude Code / Codex / OpenClaw 风格的 subagent 更接近“主 Agent 临时编写任务提示词并派发”，而不是“选择一个长期人格实例”。
- nahida-bot 已经有 workspace 指令、provider slot、工具注册和 session；首版再引入多 profile 会让实现面过大。
- 固定 profile 更适合长期 persona、channel routing、不同用户/工作区默认模型这类问题，可以后续单独设计。

因此首版只保留一个可选的默认 agent 配置来源：

```python
@dataclass(slots=True, frozen=True)
class DefaultAgentRuntimeConfig:
    provider_id: str | None = None
    model: str | None = None
    max_child_agents_per_run: int = 5
    subagent_timeout_seconds: int = 900
    default_context_mode: str = "isolated"
```

未来如需支持 `agent_id` / profile，应该作为 `SubagentSpec` 的可选路由字段，不改变当前 run / session / task 模型。

## 4. 模块边界

建议新增：

```text
nahida_bot/agent/orchestration/
  __init__.py
  models.py            # SubagentSpec, AgentRun, BackgroundTask, status enum
  policy.py            # OrchestrationPolicy 粗粒度 hook
  registry.py          # 进程内 AgentRegistry
  queue.py             # per-session lane + global lane
  executors.py         # AgentRunExecutor, LocalAgentRunExecutor
  service.py           # AgentOrchestrator 高层入口
  task_store.py        # BackgroundTaskStore 协议
  sqlite_task_store.py
  tools.py             # agent_spawn / agent_yield / agent_list / agent_stop
  session_tools.py     # sessions_list / sessions_history / sessions_send / session_status
```

依赖方向：

```text
Router / Scheduler / ToolExecutor / BotAPI
  -> AgentOrchestrator
  -> AgentRunQueue / AgentRegistry / BackgroundTaskStore
  -> AgentRunExecutor
  -> SessionRunner
  -> AgentLoop
```

`AgentLoop` 不反向依赖编排层。`AgentLoop` 只负责一次模型-工具循环；编排层负责 run 创建、排队、session 创建、任务状态、取消和结果投递。

## 5. Session 管理

Phase 3.8 应基于现有 `session_id` 系统扩展，而不是另起一套 agent session。

建议约定：

```text
父会话: session:<platform>:<chat/user/workspace>
子会话: session:<parent-session-id>:subagent:<task_id>
```

实际格式可以继续沿用当前 `SessionResolver` 的生成方式，只需要保证：

- child session id 全局唯一、可从 `BackgroundTask.child_session_id` 找回。
- child session 有 `requester_session_id` 指向父 session。
- 子 Agent 只能写自己的 child session 历史。
- 完成事件只能通过 `AgentOrchestrator` 投递回父 session。

### Context Mode

| 模式 | 首版状态 | 行为 |
|---|---|---|
| `isolated` | 默认实现 | 新 session，只注入 `task`、`instructions`、workspace 指令和必要系统约束 |
| `summary` | 建议首版实现 | 主 Agent 提供 `handoff_summary`，避免复制完整历史 |
| `fork` | 预留或受限实现 | 复制父会话可见历史，成本高且容易泄漏上下文，默认禁用 |

## 6. 运行流程

### 6.1 主会话 run

主会话可分两阶段迁移。

首版：

```text
InboundMessage
  -> SessionResolver 生成 session_id
  -> Router 继续调用 SessionRunner.run()
```

编排稳定后：

```text
InboundMessage
  -> SessionResolver 生成 session_id
  -> AgentOrchestrator.submit_main_run(session_id, message)
  -> AgentRunQueue.enqueue(session lane + main lane)
  -> LocalAgentRunExecutor.run()
  -> SessionRunner.run()
```

### 6.2 子 Agent spawn

```text
父 Agent 调用 agent_spawn(task, instructions?, context_mode?, model?)
  -> OrchestrationPolicy.can_spawn()
  -> 校验 depth == 0，子 Agent 不允许继续 spawn
  -> 创建 task_id / run_id / child_session_id
  -> 写入 BackgroundTask(runtime=subagent, status=queued)
  -> AgentRegistry 注册 AgentRun(kind=subagent, depth=1)
  -> AgentRunQueue 放入 child session lane + subagent global lane
  -> 立即返回 {task_id, run_id, child_session_id, status="queued"}
  -> LocalAgentRunExecutor 后台调用 SessionRunner.run(child_session_id, synthesized task message)
  -> 完成后写 summary / error / status
  -> 按 notify_policy 向 requester session 投递 subagent_completed 事件
```

### 6.3 子 Agent 完成回传

完成回传分两层：

1. **任务账本**：`BackgroundTask.summary/error/status` 是权威结果，供工具、CLI、WebUI 查询。
2. **父会话事件**：完成后向 requester session 写入 `system_event` / `tool_event`，让主 Agent 下次运行时能看到。

事件建议：

```json
{
  "type": "subagent_completed",
  "task_id": "task_...",
  "child_session_id": "session_...",
  "status": "succeeded",
  "summary": "...",
  "stats": {
    "duration_seconds": 31.4,
    "token_usage": {}
  }
}
```

### 6.4 agent_yield 与 agent_wait

OpenClaw 的 `sessions_yield` 是“结束当前 turn，等待子任务完成后把结果作为下一条输入送回”。这比 busy polling 更适合聊天机器人。

建议首版提供：

| 工具 | 语义 | 是否必须 |
|---|---|---|
| `agent_spawn` | 创建后台子 Agent，立即返回 task id | 必须 |
| `agent_yield` | 当前父 run 主动结束，等待任一或指定子任务完成后再续跑 | 建议 |
| `agent_wait` | 在当前工具调用中阻塞等待 task 终态；超时不取消 | 可选 |
| `agent_list` | 查询当前父 session 可见任务 | 必须 |
| `agent_stop` | 取消当前父 session 创建的任务 | 必须 |

如果不想增加 `agent_wait`，也可以让 `agent_yield` 支持 `mode="yield" | "wait"`，但实现上应区分这两种语义。

## 7. Agent-as-tool 与 A2A

### 7.1 Agent-as-tool

Codex 和 OpenClaw 都把多 Agent 控制暴露为工具：

- Codex：`spawn_agent`、`send_input`、`wait_agent`。
- OpenClaw：`sessions_spawn`、`sessions_send`、`sessions_yield`。

nahida-bot 的 MVP 应采用相同思路：**主 Agent 不直接调用内部 Python API，而是通过普通工具请求编排服务**。

```text
LLM tool_call(agent_spawn)
  -> OrchestrationToolExecutor
  -> AgentOrchestrator.spawn_subagent()
  -> BackgroundTask + AgentRun
  -> Tool result 返回 task_id
```

这就是本阶段最值得引入的 “agent as a tool”。它实现简单、边界清楚，并且能自然复用现有 ToolRegistry、权限 hook 和审计日志。

### 7.2 A2A / sessions_send

A2A 可以理解为“一个 agent/session 给另一个 agent/session 发送消息并触发对方行动”。OpenClaw 有 `sessions_send` 和 A2A ping-pong flow，但这不是 nahida-bot MVP 的核心。

首版只保留最小跨会话事件接口：

```json
{
  "target_session_id": "session_...",
  "message": "string",
  "source": "agent:<run_id>",
  "mode": "record_only | enqueue"
}
```

| mode | 行为 |
|---|---|
| `record_only` | 只向目标 session 写入 agent/system 事件，不立即触发 run |
| `enqueue` | 写入事件，并排入目标 session lane 触发一个 run |

不做首版内容：

- 多轮 agent 间 ping-pong。
- 任意 agent 自主发现和私聊其它 agent。
- A2A delivery 参数、复杂 announce/reply 协议。

后续可以把 Phase 2.10 的 `ANNOUNCE_SKIP` / `REPLY_SKIP` 这类回复信号用于 A2A 完成后的“是否对外宣布”控制，但这应是 Phase 3.8 之后的增强。

## 8. Gateway-Node 预留接口

Gateway-Node 不应渗透进 Agent 侧设计。对 AgentOrchestrator 来说，本地执行和远程执行只是 `AgentRunExecutor` 的不同实现。

```python
class AgentRunExecutor(Protocol):
    async def start(self, run: AgentRun, payload: AgentRunPayload) -> AgentRunHandle:
        ...

    async def cancel(self, run_id: str, reason: str) -> None:
        ...
```

首版：

```text
LocalAgentRunExecutor
  -> SessionRunner.run(...)
```

Phase 5：

```text
RemoteNodeRunExecutor
  -> Gateway protocol
  -> Node executes
  -> result/status callback
```

约束：

- `AgentLoop` 不知道 Gateway-Node。
- `SubagentSpec` 不出现 node 细节。
- `BackgroundTask.runtime = remote_node` 是 Phase 5 预留，不影响本地 subagent。
- 本地和远程都必须回写同一套 task/run 状态机。

## 9. 权限与策略接口

首版不做细粒度 policy DSL，只留粗粒度接口并执行必要配额。

```python
class OrchestrationPolicy(Protocol):
    async def can_spawn(self, requester_session_id: str, spec: SubagentSpec) -> None:
        ...

    async def can_read_session(self, requester_session_id: str, target_session_id: str) -> None:
        ...

    async def can_send_session(self, requester_session_id: str, target_session_id: str) -> None:
        ...

    async def filter_tools_for_child(
        self,
        requester_session_id: str,
        spec: SubagentSpec,
        available_tools: Sequence[ToolDefinition],
    ) -> Sequence[ToolDefinition]:
        ...
```

MVP 必须做的检查：

- `depth == 0` 才能调用 `agent_spawn`。
- 每个父 run 最多创建 `max_child_agents_per_run` 个子任务。
- 全局 subagent 并发受 `subagent` lane 限制。
- 子 Agent 默认禁用 `agent_spawn`，并收窄高风险工具。
- `sessions_history` 返回安全过滤视图，不返回 raw media、临时 URL、base64、raw_event、reasoning 原文。
- `sessions_send` 必须标记 `source`，不能伪装成用户消息。

## 10. 队列与并发

编排层需要两类 lane：

```text
session:<session_id>  # 同一 session 串行，避免历史写竞争
main                  # 主聊天全局并发上限
subagent              # 子 Agent 全局并发上限
cron                  # 定时任务全局并发上限
```

默认策略：

| lane | 默认并发 | 说明 |
|---|---:|---|
| `session:*` | 1 | 同一 session 永远只允许一个 run 写历史 |
| `main` | 4 | 不同聊天可并行 |
| `subagent` | 4-8 | 后台任务可并行，但受 Provider 限流约束 |
| `cron` | 2-5 | 与 scheduler 配置对齐 |

首版实现可以是进程内 `asyncio.Queue` + `asyncio.Semaphore`。跨进程队列、Redis、远程节点调度都不进入 Phase 3.8。

## 11. 工具契约

### 11.1 agent_spawn

```json
{
  "task": "string, required",
  "label": "string, optional",
  "instructions": "string, optional",
  "context_mode": "isolated | summary | fork",
  "handoff_summary": "string, optional",
  "model": "string, optional",
  "timeout_seconds": "number, optional",
  "notify": "done_only | silent",
  "tool_allowlist": ["string"],
  "tool_denylist": ["string"]
}
```

返回：

```json
{
  "task_id": "task_...",
  "run_id": "run_...",
  "child_session_id": "session_...",
  "status": "queued"
}
```

### 11.2 agent_yield

```json
{
  "task_id": "task_..., optional",
  "timeout_seconds": 300
}
```

语义：父 Agent 当前 turn 结束。编排层等待指定任务或任一可见子任务完成，再把完成事件注入父 session 并触发下一轮 run。超时不取消子任务，只投递当前状态事件。

### 11.3 agent_wait

可选工具。如果实现，语义是当前工具调用内等待结果：

```json
{
  "task_id": "task_...",
  "timeout_seconds": 30,
  "include_history": false
}
```

返回终态摘要；超时只返回当前状态，不取消子 Agent。

### 11.4 agent_list / agent_stop

`agent_list` 只列出当前 requester session 创建的直接子任务。

`agent_stop` 只能取消当前 requester session 创建的子任务，管理员能力以后再加。

### 11.5 sessions_history

返回安全过滤后的历史视图：

- 默认只允许读当前 session 和自己创建的 child session。
- 截断单条消息和总条数。
- 移除 base64、临时 URL、raw_event、raw provider payload。
- 不返回 reasoning 原文，只返回必要 metadata。

### 11.6 sessions_send

```json
{
  "target_session_id": "session_...",
  "message": "string",
  "mode": "record_only | enqueue"
}
```

编排层自动补充 `source="agent:<run_id>"`，目标 session 中必须标记为 agent/system 事件。

## 12. 历史与上下文策略

后续应逐步把历史从“最终 user/assistant 文本”升级为可表达中间事件的扁平结构：

```text
user_message
assistant_message
tool_call
tool_result
reasoning_summary
subagent_spawned
subagent_completed
system_event
compaction_summary
```

首版不要求立即改表结构，可以先通过 `metadata["event_type"]` 承载。

关键规则：

- tool call 必须和 tool result 配对。
- subagent spawned 必须和 terminal event 配对。
- 父 session 默认只看到 `subagent_completed.summary`，不自动加载子 session 全文。
- `context=fork` 必须做脱敏和 token 预算控制。
- 子 Agent 的最终回答应被转为任务摘要，不直接作为用户可见回复发送。

## 13. 可观测性

结构化事件：

| 事件 | 关键字段 |
|---|---|
| `agent_run_queued` | run_id, session_id, lane, kind |
| `agent_run_started` | run_id, task_id, provider_id, model |
| `agent_run_completed` | run_id, status, duration, token_usage |
| `subagent_spawned` | parent_run_id, child_run_id, task_id, context_mode |
| `subagent_completed` | task_id, child_session_id, status, summary_chars |
| `agent_run_cancelled` | run_id, reason |
| `agent_queue_wait` | lane, wait_ms |
| `task_delivery_failed` | task_id, target, error |

指标：

- active runs by lane
- queued runs by lane
- subagent success/failure/timeout count
- average queue wait
- average subagent duration
- token usage by main/subagent
- task delivery failures

## 14. 实施路线

### Step 1：任务账本与运行时注册表

- 新增 `BackgroundTaskStore` + SQLite 实现。
- 新增 `AgentRegistry`，支持 register/start/complete/cancel/list。
- 复用现有 session_id，先不迁移主 Router。

### Step 2：本地执行器与子 Agent 工具

- 实现 `LocalAgentRunExecutor`，内部调用 `SessionRunner.run()`。
- 实现 `agent_spawn`、`agent_list`、`agent_stop`。
- 默认 `context_mode=isolated`，支持 `summary`。
- 子 Agent 默认禁用 `agent_spawn`。

### Step 3：完成事件与 yield

- 实现 `subagent_completed` 事件写回 requester session。
- 实现 `agent_yield` 的结束当前 turn / 等待完成 / 续跑语义。
- 可选实现 `agent_wait`。

### Step 4：队列与并发控制

- 引入 per-session lane 和 subagent global lane。
- 同一 session 串行，不同 child session 可并行。
- 增加 timeout/cancel。

### Step 5：跨会话最小接口

- 实现 `sessions_list`、`session_status`。
- 实现安全过滤版 `sessions_history`。
- 实现 `sessions_send(record_only|enqueue)`，不做 ping-pong A2A。

### Step 6：主消息迁移到编排层

- Router/Scheduler 统一通过 `AgentOrchestrator.submit()` 提交 run。
- Scheduler cron run 接入 `BackgroundTask(runtime=cron)`。

### Step 7：Gateway-Node 扩展

- 在 Phase 5 新增 `RemoteNodeRunExecutor`。
- 复用 task/run/session 状态机，不改变 AgentLoop。

## 15. 测试要求

必须覆盖：

- `agent_spawn` 创建独立 child session，不污染父 session。
- 子 Agent 默认不能调用 `agent_spawn`。
- `max_child_agents_per_run` 生效。
- 多个子 Agent 并行执行，总耗时接近最长子任务而非总和。
- 同一 session 两个 run 串行，不产生历史写竞争。
- `agent_yield` 超时不取消子 Agent。
- `agent_stop` 能取消任务并写 `cancelled`。
- 子 Agent 异常记录为 `failed`，不影响父 Agent。
- 完成事件能写回 requester session。
- `sessions_history` 不返回 base64、临时 URL、raw_event、reasoning 原文。
- `sessions_send` 注入的是 agent/system 事件，不伪装成 user message。

## 16. 本地参考源码

- Codex agent tool 定义：`codex\codex-rs\tools\src\agent_tool.rs`
- Codex spawn handler：`codex\codex-rs\core\src\tools\handlers\multi_agents\spawn.rs`
- Codex send/wait handlers：`codex\codex-rs\core\src\tools\handlers\multi_agents\send_input.rs`、`wait.rs`
- Codex agent control/registry：`codex\codex-rs\core\src\agent\control.rs`、`registry.rs`
- OpenClaw subagent spawn：`openclaw\src\agents\subagent-spawn.ts`
- OpenClaw sessions tools：`openclaw\src\agents\tools\sessions-spawn-tool.ts`、`sessions-yield-tool.ts`、`sessions-send-tool.ts`
- OpenClaw A2A send flow：`openclaw\src\agents\tools\sessions-send-tool.a2a.ts`
