# Gateway-Node 调用入口与授权设计

> 状态：设计稿，作为 Gateway-Node M6/M7 的实施基线
> 适用范围：Gateway 主动调用 Node capability，以及这些 capability 暴露给 REST、Agent Tool、Plugin 和内部服务的方式
> 相关文档：
>
> - [gateway-node-protocol.md](gateway-node-protocol.md) — WebSocket wire protocol、节点注册与 capability.invoke
> - [plugin-system.md](plugin-system.md) — ToolRegistry、Plugin manifest 与插件权限
> - [security-observability.md](security-observability.md) — 安全与可观测性基线
> - [../design/person-identity-system.md](../design/person-identity-system.md) — account/person identity 与管理员身份

## 1. 背景

当前 Gateway 已具备 `NodeInvoker.invoke()`，能够定位在线节点、发送
`capability.invoke`、等待 response、处理超时并生成内存审计记录。但它还不是一个可公开使用的调用系统：

- 生产代码没有 REST、Agent Tool 或 Plugin API 调用入口。
- `caller` 仍是普通字符串，无法可靠表达用户、Agent、Plugin、Scheduler 或系统服务身份。
- `_authorize()` 当前默认允许，节点注册 capability 后缺少 Gateway 侧细粒度授权。
- capability 没有完整的 arguments/result JSON Schema，无法安全暴露为动态 Tool 或公共 API。
- 审计仅保存在进程内，不足以支持追责、撤销检查和运维查询。
- Node 断线、重新注册、授权撤销时，动态 Tool 的注册和在途调用行为尚未定义。

本设计把 `NodeInvoker` 收敛为传输执行器，在它上方增加统一的调用服务与授权边界。所有入口必须经过同一条管线，禁止 REST route、Plugin 或 Agent Tool 直接取得 `NodeSession.request`。

## 2. 目标与非目标

### 2.1 目标

- 为 operator REST、Agent Tool、Plugin API、Scheduler 和内部服务提供明确入口。
- 使用类型化调用身份，阻止调用方伪造 `system` 或其他高权限 caller。
- 对 actor、entrypoint、session、node、capability 和 risk 进行统一授权。
- capability 注册、Gateway 授权和 Node 本地 allowlist 形成三层防线。
- 所有允许、拒绝、超时、取消和失败调用都有持久化审计。
- 支持节点上下线时动态 Tool 的安全注册与注销。
- 保持 transport、policy、approval、audit 和入口 adapter 可独立测试与替换。

### 2.2 非目标

- 本轮不设计 node-to-node 直连或 Gateway 中转。
- 不让任意 Node capability 自动成为 Agent Tool。
- 不在首版支持 capability 流式结果；长结果继续使用 artifact 引用。
- 不用复杂通用 policy DSL 替代清晰的 capability grant 模型。
- 不把 Node token 当成调用者授权凭证。Node token 只证明“这个连接是哪一个 Node”。

## 3. 核心安全原则

1. **注册不等于授权**：Node 声明 capability 只表示“可以执行”，不表示任何调用者自动获准。
2. **入口不决定权限**：REST、Tool、Plugin 最终都调用同一个 `NodeInvocationService`。
3. **调用身份不可由请求体提供**：actor 必须由认证/session/plugin/runtime 上下文构造。
4. **默认拒绝**：公开入口没有匹配 grant 时拒绝；不以 `caller="system"` 作为默认值。
5. **委托链不丢失**：Agent 或 Plugin 代表用户调用时，同时记录直接 actor 和原始用户身份。
6. **高风险双重确认**：Gateway approval 通过后，Node 仍必须执行本地 allowlist/用户确认。
7. **先审计再下发**：发送 WebSocket request 前必须写入 `running` 或 `awaiting_approval` 记录。
8. **结果不扩大权限**：Node response 只作为不可信远程结果处理，必须限长并按 schema 校验。
9. **撤销及时生效**：grant、token 或 capability 撤销后，新调用立即拒绝；必要时取消在途调用。
10. **最小暴露**：Desktop 表现 capability 默认由内部 DisplayPlan pipeline 使用，不自动暴露给 LLM。

## 4. 目标架构

```text
REST operator ─────────┐
Agent Tool ────────────┤
Plugin API ────────────┤
Scheduler ─────────────┤
DisplayPlan dispatcher ┘
          │
          ▼
 InvocationContextFactory
          │  生成可信 actor / delegation / trace / session
          ▼
 NodeInvocationService
   1. normalize command
   2. resolve node + capability descriptor
   3. validate direction/version/arguments
   4. authorize policy
   5. approval / rate limit / idempotency
   6. write audit: running
          │
          ▼
 NodeInvoker（transport only）
          │ capability.invoke
          ▼
 Node local allowlist / local approval / handler
          │ response
          ▼
 result schema + size validation
          │
          ├── audit terminal state
          └── entrypoint-specific response adapter
```

建议新增模块：

```text
nahida_bot/gateway/services/
  node_invocation_service.py   # 统一编排入口
  node_invocation_context.py   # actor/context factory
  node_capability_catalog.py   # 在线 capability 快照与 schema
  node_capability_policy.py    # grant/deny 决策
  node_approval.py             # 一次性审批
  node_invocation_audit.py     # 持久审计 repository/service
  node_tool_bridge.py          # 受控映射到 ToolRegistry

nahida_bot/gateway/routes/
  nodes.py                     # 管理/查询
  node_invocations.py          # operator invoke API
```

`NodeRegistry` 继续负责连接和在线状态；`NodeInvoker` 只负责已授权 command 的 request/response transport，不再自行判断 caller 权限。

## 5. 类型化调用上下文

### 5.1 Actor

禁止继续使用可自由填写的 `caller: str`。统一使用：

```python
ActorKind = Literal["operator", "agent", "plugin", "scheduler", "system"]

@dataclass(frozen=True, slots=True)
class InvocationActor:
    kind: ActorKind
    actor_id: str
    account_key: str = ""
    person_id: str = ""
    plugin_id: str = ""
    run_id: str = ""
```

语义：

| kind | actor_id 示例 | 身份来源 |
|------|---------------|----------|
| `operator` | `account:telegram:123` | WebUI session / bearer principal |
| `agent` | `run:01J...` | Agent Loop 当前 run |
| `plugin` | `plugin:calendar` | `RealBotAPI` 绑定的 manifest |
| `scheduler` | `job:daily_report` | Scheduler job record |
| `system` | `service:display_plan` | 代码内固定 service identity |

`system` actor 只能由 `InvocationContextFactory.for_system_service()` 使用注册过的 service id 创建，不能由 REST body、Plugin 参数或普通工具参数创建。

### 5.2 Delegation chain

Agent 和 Plugin 经常代表当前用户执行，必须保留原始主体：

```python
@dataclass(frozen=True, slots=True)
class Delegation:
    on_behalf_of_account_key: str = ""
    on_behalf_of_person_id: str = ""
    session_id: str = ""
    workspace_id: str = ""
```

授权时同时检查：

- 直接 actor 是否允许使用该入口。
- 被代表用户是否允许该风险等级和 session 范围。
- Plugin 是否在 manifest 中声明 Node invoke 权限。

任何一层失败都拒绝，防止 Plugin 或 Agent 成为 confused deputy。

### 5.3 InvocationContext

```python
@dataclass(frozen=True, slots=True)
class InvocationContext:
    actor: InvocationActor
    delegation: Delegation
    entrypoint: Literal["rest", "agent_tool", "plugin_api", "scheduler", "internal"]
    request_id: str
    trace_id: str
    created_at: datetime
```

上下文必须显式传给 `NodeInvocationService.invoke()`，不得通过一个具有高权限默认值的可选参数隐式补齐。`current_session` 只用于 context factory 读取当前用户/session，不作为 service 内部的隐藏授权来源。

## 6. 调用命令与 capability 描述

### 6.1 InvocationCommand

```python
@dataclass(frozen=True, slots=True)
class NodeInvocationCommand:
    capability: str
    arguments: dict[str, Any]
    node_id: str | None = None
    timeout_ms: int | None = None
    idempotency_key: str = ""
```

规则：

- `node_id` 为空时只允许 catalog 中存在唯一候选；多个候选返回 `ambiguous_target`。
- timeout 必须被 capability 上限和 Gateway 全局上限夹紧。
- idempotency key 的作用域是 `(actor, node_id, capability, key)`。
- command 不携带 caller、risk、session 或 approval 结果，这些只能来自可信上下文和 catalog/policy。

### 6.2 NodeCapability 扩展

当前 capability 只有名称、版本、方向和风险。要开放调用入口，建议兼容性新增：

```json
{
  "name": "desktop.notification.show",
  "version": "1.0",
  "direction": "gateway_to_node",
  "risk": "low",
  "description": "Show a desktop notification",
  "input_schema": {
    "type": "object",
    "properties": {
      "title": {"type": "string", "maxLength": 100},
      "message": {"type": "string", "maxLength": 2000}
    },
    "required": ["message"],
    "additionalProperties": false
  },
  "output_schema": {
    "type": "object",
    "properties": {"applied": {"type": "boolean"}},
    "required": ["applied"]
  },
  "max_timeout_ms": 5000,
  "idempotent": true,
  "tool_exposable": false,
  "requires_user_approval": false
}
```

约束：

- 无 `input_schema` 的 capability 只能被明确 allowlist 的内部 service 调用，不能暴露给 REST、Agent Tool 或第三方 Plugin。
- `direction` 必须是 `gateway_to_node` 或 `bidirectional`。
- Gateway 只信任注册时的 schema 作为验证材料，不把它当授权依据。
- token scope 可以限制 Node 能注册的 capability pattern；超出 scope 的 capability 在注册阶段拒绝或剔除。
- schema 大小、深度和正则复杂度必须设上限，避免恶意 Node 造成资源消耗。

## 7. 统一调用服务

公开 service 形态：

```python
class NodeInvocationService:
    async def invoke(
        self,
        command: NodeInvocationCommand,
        context: InvocationContext,
    ) -> NodeInvocationResult: ...
```

固定执行顺序：

1. 规范化 capability、node_id、timeout 和 idempotency key。
2. 解析目标 Node；显式 node_id 不在线时返回 `node_offline`，不静默切换其他 Node。
3. 从当前 `NodeSession` 取得 capability descriptor 快照。
4. 检查 direction、版本、风险和 schema 可用性。
5. 使用 JSON Schema 校验 arguments；拒绝未知字段。
6. 调用 `NodeCapabilityAuthorizer.decide(context, target, capability)`。
7. 需要 approval 时创建或消费一次性 approval。
8. 检查 actor/node/capability rate limit、并发和 idempotency。
9. 写入持久审计记录，状态为 `running`。
10. 调用 transport-only `NodeInvoker.invoke_authorized()`。
11. 校验 response 大小和 output schema。
12. 在 `finally` 中写入唯一 terminal audit 状态并释放并发配额。

入口 adapter 不得复制上述步骤。

## 8. 调用入口

### 8.1 Operator REST

建议接口：

```text
POST /api/nodes/{node_id}/capabilities/{capability}/invoke
GET  /api/node-invocations/{invocation_id}
POST /api/node-invocations/{invocation_id}/cancel
```

同步 MVP request：

```json
{
  "arguments": {"expression": "happy"},
  "session_id": "milky:private:10001",
  "timeout_ms": 5000,
  "idempotency_key": "display-turn-01J..."
}
```

规则：

- HTTP 认证层生成 operator principal；body 中的 user/caller 字段一律忽略或拒绝。
- `session_id` 是授权约束和审计上下文，不改变 caller 身份。
- 初版只允许 owner/admin operator；普通 WebUI 用户后续通过显式 grant 开放。
- 请求进入 approval 时返回 `202` 和 `approval_id`，不能阻塞 HTTP 连接等待人工确认。
- 重复 idempotency key 返回已有结果或在途 invocation id。

建议状态映射：

| 场景 | HTTP | 稳定错误码 |
|------|------|------------|
| arguments schema 错误 | 422 | `invalid_arguments` |
| 无授权 | 403 | `capability_denied` |
| 需要人工批准 | 202 | `approval_required` |
| Node/capability 不存在 | 404 | `node_not_found` / `capability_not_found` |
| Node 已知但离线 | 409 | `node_offline` |
| 多个候选且未指定 node | 409 | `ambiguous_target` |
| 限流/并发已满 | 429 | `rate_limited` |
| Node 超时 | 504 | `capability_timeout` |
| Node 本地拒绝 | 403 | `capability_local_denied` |

### 8.2 Agent Tool

Agent 不直接获得一个可调用所有 capability 的万能 `node_invoke` 工具。推荐采用受控动态映射：

- 只有 `tool.*` capability 或策略显式标记 `expose_as_tool=true` 的 capability 才进入候选。
- capability 必须有完整 `input_schema`、描述和稳定版本。
- Tool 名必须由 policy/config 指定稳定 alias；不根据不可信 display name 自动生成。
- 本地 Tool 与远程 Tool 重名时，本地 Tool 优先，远程 Tool 暴露失败并产生审计/告警；不静默覆盖。
- `ToolEntry.plugin_id` 使用 `node:{node_id}:{node_session_id}`，以便只注销当前连接拥有的条目。
- Node 离线后立即从 `ToolRegistry` 移除；已经进入执行阶段的调用按在途调用规则完成或取消。
- tool handler 从当前 run/session 构造 `InvocationContext(kind="agent")`，不能使用固定 `system` actor。

现有 `AuthorizationGate` 继续在 Agent Loop tool-dispatch 边界检查高权限工具。Node Tool handler 内还要执行 Node capability policy：

```text
LLM tool call
  → AuthorizationGate（用户是否可执行 privileged tool）
  → NodeCapabilityAuthorizer（该 actor/session/node/capability 是否有 grant）
  → approval（如需要）
  → Node local allowlist
```

Desktop 的 Live2D/TTS/notification capability 默认不暴露给 LLM；DisplayPlan dispatcher 通过内部入口调用，避免模型逐条控制表现。

### 8.3 Plugin API

SDK 后续新增：

```python
await api.invoke_node_capability(
    capability="desktop.notification.show",
    arguments={"message": "任务完成"},
    node_id="desktop-local",
    session_id=session_id,
)
```

Plugin 调用必须同时满足：

1. manifest 声明 Node 权限，例如：

   ```yaml
   permissions:
     nodes:
       invoke:
         - "desktop-*:desktop.notification.show"
   ```

2. Gateway runtime policy 给该 `plugin_id` 对应 grant。
3. 如果 Plugin 代表当前用户，用户/session 也必须获准。

Plugin API bridge 负责构造 actor，Plugin 不得传入或覆盖 `plugin_id`。

### 8.4 Scheduler

Scheduler actor 使用 `job:{job_id}`，调用上下文记录 job 创建者及创建来源。默认规则：

- job 创建时验证一次“是否有资格创建该远程动作”。
- 每次执行时重新验证当前 grant、Node 状态和 capability 版本。
- grant 撤销后旧 job 不再执行，不使用创建时权限永久穿透。
- 高风险 capability 不允许无人值守 Scheduler 调用，除非 policy 显式允许且 approval 使用预授权模板。

### 8.5 内部服务

内部 service 必须使用固定 service id 和窄 capability allowlist：

| service id | 允许用途 |
|------------|----------|
| `service:display_plan` | `desktop.live2d.*`、受控 TTS/audio 表现 |
| `service:notification` | `desktop.notification.show` |
| `service:node_health` | 只读 health/state capability |

禁止提供通用的 `for_system()`。新增内部调用者时必须注册 service id、默认 capability pattern 和风险上限，并增加测试。

### 8.6 Node 输入不是 capability invoke

`node.input.submit` 是 Node → Gateway 的入站消息，不走上述 Gateway → Node 调用入口，但必须使用同一身份思想：

- node token 确定 node_id。
- Gateway 保存 node 与允许 session/chat 的 binding。
- Node 只能向绑定范围提交输入。
- 入站消息继续标记 `source=node`，不得伪装成平台认证用户。
- 当前只校验 typed session/channel；在开放远程 Node 前必须补齐 binding policy。

## 9. Capability 授权策略

### 9.1 Policy record

首版采用结构化 grant/deny，不引入自由表达式：

```python
@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    policy_id: str
    effect: Literal["allow", "deny"]
    actor_kinds: frozenset[ActorKind]
    actor_ids: tuple[str, ...]
    entrypoints: frozenset[str]
    node_patterns: tuple[str, ...]
    capability_patterns: tuple[str, ...]
    session_scope: Literal["current", "bound", "any"]
    max_risk: Literal["low", "medium", "high"]
    approval: Literal["never", "when_declared", "always"]
    enabled: bool = True
```

pattern 使用受限 glob，不允许正则。`*` 不能跨 capability 的 `.` 分段，避免一个宽泛 pattern 意外覆盖新命名空间；全局授权必须显式写 `**` 并仅允许 owner/admin 配置。

### 9.2 决策顺序

1. actor/context 不完整：拒绝。
2. capability 未注册、方向不允许、schema 缺失：拒绝。
3. 内建 hard deny 命中：拒绝。
4. 任一匹配的显式 deny：拒绝。
5. 找出完整覆盖 actor、entrypoint、node、capability、session、risk 的 allow。
6. 没有 allow：拒绝。
7. 多个 allow 时选择最具体记录；同等具体时取更严格 approval。
8. capability `requires_user_approval=true` 或 risk=high 时，policy 不能降级为无审批。
9. 返回带 `policy_id`、reason 和 approval requirement 的决定。

“最具体”按 exact actor id、exact node id、exact capability、窄 session scope 的顺序评分；评分算法必须固定并单测，不能依赖配置文件顺序。

### 9.3 默认策略

启用公开入口后使用 fail-closed 默认值：

| actor/入口 | low | medium | high |
|------------|-----|--------|------|
| owner operator / REST | 需 grant | 需 grant | grant + 每次审批 |
| Agent Tool | 需 grant + tool exposure | 默认拒绝 | 拒绝 |
| Plugin API | manifest + grant | manifest + grant + 可选审批 | 默认拒绝 |
| Scheduler | 需 grant | 默认拒绝 | 拒绝 |
| 内部 service | 固定 allowlist | 固定 allowlist | 默认拒绝 |

identity 关闭时，不允许因此把公开 Node 入口变成全开放。兼容模式只适用于尚未暴露的内部低风险调用；REST/Agent Tool/Plugin API 一旦开启必须使用 enforce 模式。

### 9.4 Node 注册范围

长期 node token 增加注册 scope，例如：

```text
capability:register:desktop.live2d.*
capability:register:desktop.notification.show
event:subscribe:agent.message.*
session:bind:milky:private:10001
```

Node 注册 capability 时取“声明集合 ∩ token scope”。这只能限制 Node 冒充其他类型节点，不能替代调用者授权。

## 10. Approval 模型

需要审批的 invocation 不先发送给 Node。Gateway 创建：

```python
ApprovalRequest(
    approval_id,
    invocation_id,
    actor_fingerprint,
    node_id,
    capability,
    arguments_digest,
    session_id,
    expires_at,
    status,
)
```

约束：

- approval 绑定 canonical arguments digest，修改参数后必须重新审批。
- approval 单次消费，默认 5 分钟过期。
- 审批人不能与低权限 Plugin actor 混同；必须是 owner/admin operator。
- `approve once` 和未来 `create grant` 是两个不同动作，不能在 UI 中混淆。
- Node 的本地确认是第二层，Gateway approval 不能强制 Node 执行。
- 审批、拒绝、过期和消费都写审计。

## 11. 目标解析与多节点

- 显式 `node_id`：只能调用该 Node，离线时不自动 failover。
- 未指定 `node_id`：只有一个授权且在线的候选 Node 时自动选择。
- 多个候选：返回 `ambiguous_target`，由调用者或 policy 配置 preferred node。
- preferred node 必须属于当前 actor 的授权候选，不能先选节点再绕过 policy。
- capability version 由调用者声明最小/兼容范围时，catalog 负责筛选。
- Node 重连产生新 `node_session_id`；新调用使用新 session，在途调用仍绑定旧 session 并按断线失败。

## 12. 超时、取消、并发与幂等

### 12.1 Timeout

有效 timeout：

```text
min(requested_timeout, capability.max_timeout, policy.max_timeout, gateway.max_timeout)
```

入口未指定时使用 capability default，再回落到 Gateway default。HTTP timeout 和 WebSocket invocation timeout 使用同一个 deadline，避免双层相同 timeout 造成 pending future 清理竞态。

### 12.2 Cancellation

`capability.cancel` 必须从“只 acknowledge”升级为真实协作取消：

- Gateway 为 invocation 保存 pending record 和 target session。
- timeout、用户取消或 grant/token 紧急撤销时发送 `capability.cancel`。
- Node 按 `invoke_id` 取消本地 task，并返回最终 `capability_cancelled`。
- Python Node Client capability handler 必须独立 task 执行，不能阻塞 read loop。
- 不可取消的 handler 必须声明 `cancellable=false`；Gateway 只能丢弃迟到结果。

### 12.3 Concurrency/rate limit

至少配置：

- Gateway 全局在途上限。
- 每 Node 在途上限。
- 每 capability 并发上限。
- 每 actor 的 token bucket。
- approval pending 数量上限。

限流必须发生在 transport send 前，并写 `rate_limited` 审计。

### 12.4 Idempotency

- 只对 capability 声明 `idempotent=true` 时接受自动重放。
- 相同 key + 不同 arguments digest 返回冲突。
- 在途重复请求返回同一 invocation id。
- 已完成重复请求返回已存结果摘要；大结果使用 artifact 引用。

## 13. 审计与可观测性

### 13.1 持久审计记录

建议表 `node_invocations`：

| 字段 | 说明 |
|------|------|
| `invocation_id` | 全局唯一 ID |
| `request_id` / `trace_id` | HTTP/Agent/WS 关联 |
| `actor_kind` / `actor_id` | 直接调用者 |
| `account_key` / `person_id` | 被代表用户 |
| `plugin_id` / `run_id` / `job_id` | 来源细节 |
| `entrypoint` | REST/Tool/Plugin/Scheduler/Internal |
| `session_id` / `workspace_id` | 上下文范围 |
| `node_id` / `node_session_id` | 目标连接 |
| `capability` / `version` / `risk` | 能力快照 |
| `policy_id` / `decision` / `approval_id` | 授权证据 |
| `arguments_digest` | canonical JSON hash |
| `arguments_summary` | 脱敏、限长摘要 |
| `status` / `error_code` | 生命周期与结果 |
| `started_at` / `finished_at` / `duration_ms` | 时序 |
| `result_summary` | 限长结果，不保存大型原文 |

状态机：

```text
received
  ├── denied
  ├── rate_limited
  ├── awaiting_approval ── approved ─┐
  │                      └─ rejected/expired
  └──────────────────────────────────► running
                                        ├── succeeded
                                        ├── failed
                                        ├── timed_out
                                        └── cancelled
```

每个 invocation 只能写入一个 terminal 状态。service 使用 `try/finally` 保证 transport 异常也能收尾。

### 13.2 脱敏

- capability schema 支持 `x-sensitive: true` 标记字段。
- 同时保留 key 名启发式兜底：token、secret、password、authorization、cookie、key。
- 嵌套对象递归脱敏，数组设元素和长度上限。
- arguments 原文默认不持久化，只保存 digest 和摘要。
- Node error.message 进入审计前限长；details 默认不落盘。
- reasoning、raw_event、临时下载 URL 和二进制不得进入审计。

### 13.3 指标

建议指标：

- `node_invocation_total{entrypoint,capability,status}`
- `node_invocation_duration_seconds{capability}`
- `node_invocation_denied_total{reason}`
- `node_invocation_inflight{node_id}`
- `node_approval_pending`
- `node_tool_registered{node_id}`
- `node_late_response_total`

高基数字段如 invocation_id、session_id、actor_id 只进入日志/trace，不作为 metric label。

## 14. NodeToolBridge 生命周期

### 14.1 注册

1. Node 完成 register。
2. Catalog 保存 capability descriptor 快照。
3. Bridge 筛选 `tool.*` / `tool_exposable` capability。
4. 校验 token registration scope、schema 和 exposure policy。
5. 解析配置的稳定 Tool alias。
6. 检查 ToolRegistry 冲突。
7. 注册 handler，owner 标记为当前 `node_session_id`。

### 14.2 注销

- Node offline、duplicate、token revoke、capability revoke 时注销当前 session 拥有的 Tool。
- 重连后重新评估，不沿用旧 session 的授权决定缓存。
- 注销时只移除 owner 完全匹配的 entry，避免旧连接 finally 删除新连接注册的 Tool。
- Agent 已经拿到旧 ToolDefinition 但随后调用时，handler 必须返回 `node_offline`，不能切换到同名未授权节点。

### 14.3 Tool 返回值

- Node result 经 output schema 校验后转换为字符串或结构化 Tool result。
- 超限结果写入 artifact store，Tool 返回 artifact id/摘要。
- Node 错误码映射为稳定 ToolExecutionResult；不向模型暴露内部堆栈或 token 信息。

## 15. Policy 与配置来源

长期以 SQLite policy store 为准，YAML 仅作为启动 seed：

```yaml
webapi:
  nodes:
    invocation:
      enabled: true
      authorization_mode: enforce
      max_timeout_ms: 30000
      max_inflight: 64
      per_node_inflight: 8
      policies:
        - id: owner-desktop-low
          effect: allow
          actor_kinds: [operator]
          actor_ids: ["account:milky:12345"]
          entrypoints: [rest]
          nodes: ["desktop-local"]
          capabilities:
            - "desktop.live2d.*"
            - "desktop.notification.show"
          session_scope: bound
          max_risk: low
          approval: when_declared

        - id: display-plan-internal
          effect: allow
          actor_kinds: [system]
          actor_ids: ["service:display_plan"]
          entrypoints: [internal]
          nodes: ["desktop-*"]
          capabilities: ["desktop.live2d.*"]
          session_scope: bound
          max_risk: low
          approval: never
```

启动 seed 规则：

- 相同 policy_id 做幂等 upsert。
- 数据库中由 operator 创建的 policy 不因 YAML 缺失而自动删除。
- 删除/禁用必须是显式管理动作并写审计。
- policy 更新使 decision cache 立即失效。

## 16. API 与服务边界

建议拆分接口：

```python
class NodeCapabilityCatalog(Protocol):
    def resolve(self, command, context) -> ResolvedCapability: ...

class NodeCapabilityAuthorizer(Protocol):
    async def decide(self, context, target) -> AuthorizationDecision: ...

class NodeApprovalService(Protocol):
    async def require_or_consume(self, context, target, command) -> ApprovalDecision: ...

class NodeInvocationAuditStore(Protocol):
    async def create(self, record) -> None: ...
    async def finish(self, invocation_id, outcome) -> None: ...

class AuthorizedNodeTransport(Protocol):
    async def invoke_authorized(self, request, target, deadline) -> NodeEnvelope: ...
```

依赖方向：

```text
routes / tool bridge / plugin API
          ↓
NodeInvocationService
  ↓ catalog  ↓ authorizer  ↓ approval  ↓ audit
          ↓
NodeInvoker / NodeRegistry / WebSocket
```

`identity.AuthorizationGate` 不依赖 gateway；Node authorizer 可以读取标准化 account key，但不能让 identity 模块反向依赖 Gateway-Node。

## 17. 测试策略

### 17.1 Policy 单测

- 每个 actor kind、entrypoint、risk、session_scope 的 allow/deny 矩阵。
- deny override、最具体 allow、approval 合并规则。
- identity disabled 时公开入口仍 fail-closed。
- Plugin manifest 与 runtime grant 缺一不可。
- system service id 伪造失败。

### 17.2 Service 单测

- schema、direction、版本、timeout clamp、目标歧义。
- 审计先于 transport send，所有异常都有唯一 terminal 状态。
- idempotency 同参复用、异参冲突。
- rate limit 和并发释放。
- result schema/大小校验。

### 17.3 入口一致性

同一个 context/command 通过 REST、Tool 和 Plugin adapter 时，authorization decision 必须一致。adapter 只改变结果表现，不改变 policy 语义。

### 17.4 集成测试

- REST authenticated operator → authorize → Python/Rust mock Node → audit。
- Agent Tool → AuthorizationGate → Node policy → response。
- Node 断线/重连时 Tool 注册与注销。
- duplicate connection 的旧 session 不能注销新 Tool。
- grant/token revoke 后新调用拒绝，在途调用按策略取消。
- approval 参数 digest 防篡改和单次消费。
- capability.cancel 真正终止 Python Node handler。

### 17.5 必须长期保持的安全不变量

1. 没有 `InvocationContext` 就不能调用 transport。
2. 请求体不能改变 actor。
3. Node 注册 capability 不能自动生成 allow grant。
4. 没有 schema 的 capability 不能进入公共入口。
5. denied 调用不能产生 WebSocket request。
6. 每个 transport request 必须关联持久 audit invocation id。
7. 旧 node_session 不能影响新 node_session 的 Tool 或 pending request。

## 18. 分阶段实施

### Phase A：服务边界与持久审计

- 新增类型化 context/command/result。
- 把 `NodeInvoker` 改为 transport-only，移除默认 `caller="system"`。
- 实现 SQLite invocation audit store。
- 补 schema/direction/timeout/result 限制。
- 暂不开放新公共入口。

验收：现有内部测试全部迁移到 `NodeInvocationService`，每次调用都有持久 audit。

### Phase B：Owner REST 入口

- 实现 policy store 和 fail-closed authorizer。
- WebUI/bearer auth 生成 operator principal。
- 开放 low-risk、完整 schema capability 的 REST invoke。
- 实现 rate limit、idempotency 和查询 API。

验收：未配置 grant 一律 403；允许调用可完整关联 HTTP、WS 和 audit trace。

### Phase C：内部 DisplayPlan dispatcher

- 注册 `service:display_plan`。
- 只开放受控 Desktop 表现 allowlist。
- DisplayPlan schema 降级后调用 capability，不把 capability 直接交给 LLM。

验收：真实回复可以驱动 Desktop 表情/动作，失败不影响 transcript 主流程。

### Phase D：NodeToolBridge

- 扩展 capability schema/tool exposure metadata。
- 动态注册/注销 ToolEntry。
- 复用 AuthorizationGate，并增加 Node policy 二次检查。
- 完成断线、冲突和重连测试。

验收：只暴露显式授权的 `tool.*`，Node 离线后 Agent 得到稳定 `node_offline`。

### Phase E：Plugin/Scheduler 与 Approval

- Plugin manifest nodes permission。
- Scheduler 每次执行重新授权。
- 一次性 approval 和 WebUI 审批。
- 高风险 capability 仍默认关闭，逐项开放。

## 19. 已收敛决策

- 使用统一 `NodeInvocationService`，不为每个入口复制授权逻辑。
- `NodeInvoker` 只做 transport，不保留 permissive `_authorize()`。
- actor 必须类型化且由可信 adapter 创建。
- Node token scope、Gateway caller policy、Node local allowlist 三层并存。
- Agent 默认只看显式 tool exposure 的 capability。
- Desktop 表现优先走内部 DisplayPlan dispatcher，而不是万能 LLM Tool。
- policy 默认拒绝；identity disabled 不等于 Node invocation disabled authorization。
- 审计必须持久化，arguments 默认只保存 digest 与脱敏摘要。
- capability cancellation 必须异步 task 化后才宣称支持。

## 20. 后续待决问题

- WebUI principal 是否直接复用 account identity，还是增加独立 operator identity 表。
- 多用户共同拥有一个 Node 时，binding 如何表达 owner/member/guest。
- approval 是否需要移动端/系统通知，以及审批人离线时的降级策略。
- remote worker 的 `tool.*` schema 是否由 Plugin manifest 生成并签名，防止被攻陷 Node 随意改变描述。
- policy 和 capability version 更新时，已经排队但尚未发送的 invocation 是否重新授权；建议答案是“发送前重新检查”。
- 高可用多 Gateway 部署时，Node registry、rate limit、pending invocation 和 approval 需要迁移到共享 backend。
