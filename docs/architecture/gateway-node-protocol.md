# Gateway-Node 协议设计

> 状态：设计稿（协议未冻结，迭代中）
> 相关文档：
>
> - [../design/desktop-app.md](../design/desktop-app.md) — Desktop App 与 Gateway-Node 总体设计、产品形态、capability 清单
> - [../ROADMAP.md](../ROADMAP.md) Phase 5 — 交付目标与勾选状态
> - [event-system.md](event-system.md) — 核心事件总线
> - [security-observability.md](security-observability.md) — 安全基线
> - [gateway-node-invocation-authorization.md](gateway-node-invocation-authorization.md) — capability 调用入口、授权、审批与审计

## 1. 背景与目标

Nahida Bot 的 Gateway 已经具备 REST API + SSE 事件流，WebUI 只消费公开 API。但部分能力（Desktop 桌宠远程表现控制、未来 Python worker 节点、远程 GPU 推理节点）需要**双向、长连接、可被 Gateway 主动调用**的通道。SSE 是单向的，REST 又缺乏主动推送能力，因此需要一套基于 WebSocket 的 Gateway-Node 协议。

本协议的设计目标：

- **语言无关**：Python 与 Rust/Tauri Desktop 各自实现一份 SDK，不通过 FFI 共享实现。
- **双向**：Gateway 可向 Node 下发请求（capability.invoke），Node 也可向 Gateway 上报事件与请求。
- **可演进**：协议带版本字段，未知字段向前兼容，破坏性变更走新主版本。
- **可测试**：通过 `tests/fixtures/gateway_node/*.json` 固定报文样例，Python/Rust 双方 parse 同一批 fixtures 保证一致。

非目标：

- 不在首版承载音频、图片等大二进制。媒体走 `media_id` / Gateway Media API。
- 不在首版做复杂二进制协议或 protobuf 代码生成。
- 不在首版开放高权限本机执行能力（文件读写、命令执行、录音、截屏等），这些必须显式授权。

## 2. 设计原则

### 2.1 JSON over WebSocket

V1 使用 JSON 文本帧。理由：调试方便、与 FastAPI/Pydantic/serde/TypeScript 全兼容、协议演化期调整成本低。Node 业务数据量小（状态事件、能力调用参数），不需要首版优化为 binary frame。

### 2.2 不使用 FFI

Desktop 不通过 Rust→Python FFI 复用业务逻辑。两个进程通过 WebSocket 说同一种协议，边界清晰，便于分布式部署和跨语言扩展。

### 2.3 协议分层

```text
Wire Protocol
  JSON over WebSocket 的稳定约定（envelope、auth、heartbeat、request/response/event、error）。

Python Protocol SDK
  Pydantic models + dispatcher + Gateway node session manager + Python node client。

Rust Protocol SDK
  serde structs + async websocket client + reconnect/heartbeat + Tauri command bridge（Desktop）。
```

## 3. 传输与连接

### 3.1 Endpoint

- WebSocket endpoint：`/api/nodes/ws`
- 默认本地连接：`ws://127.0.0.1:6185/api/nodes/ws`
- 远程连接必须使用 `wss://`，或明确配置为不安全模式（仅开发环境）。

### 3.2 鉴权载体

连接鉴权支持两种方式：

1. **长期 node token**：通过 HTTP `Sec-WebSocket-Protocol` 子协议或 query 参数 `?token=<node_token>` 携带。query 参数方式兼容浏览器/WebView 的 WebSocket 构造限制。
2. **一次性 pairing token**：配对流程中签发，换取长期 node token 后失效。

首版 query token 路径必须可用，因为 WebView 的 WebSocket 无法设置自定义 Authorization header。

### 3.3 默认网络边界

- 本地开发和个人使用默认只监听 `127.0.0.1`。
- 远程 Gateway 必须要求 WSS 或明确配置为不安全模式。
- Gateway CORS 不应长期保持生产环境 `*`。

## 4. Envelope

所有报文使用统一 envelope。envelope 字段使用 `snake_case`（wire format），各语言内部可转 `camelCase`。

### 4.1 基础字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `version` | string | 是 | 协议版本，如 `"1.0"` |
| `kind` | string | 是 | `request` / `response` / `event` / `heartbeat` |
| `id` | string | 条件 | request/response 关联 ID（kind 为 request/response 时必需） |
| `method` | string | 条件 | request 的方法名（kind=request 时必需） |
| `event` | string | 条件 | event 的事件名（kind=event 时必需） |
| `ok` | bool | 条件 | response 是否成功（kind=response 时必需） |
| `payload` | object | 否 | 业务数据 |
| `error` | object | 否 | 错误对象（见 4.7） |
| `meta` | object | 否 | trace、时间戳、兼容标记等扩展信息 |

### 4.2 Request

`kind="request"`，必须带 `id` 和 `method`，可选 `payload`。由任一端发起，对端必须回一个相同 `id` 的 response。

```json
{
  "version": "1.0",
  "kind": "request",
  "id": "req_01JZ000001",
  "method": "node.register",
  "payload": { "node_id": "desktop-local" }
}
```

### 4.3 Response

`kind="response"`，`id` 对应 request，`ok=true` 时带 `payload`，`ok=false` 时带 `error`。

```json
{
  "version": "1.0",
  "kind": "response",
  "id": "req_01JZ000001",
  "ok": true,
  "payload": { "accepted": true }
}
```

### 4.4 Event

`kind="event"`，带 `event` 名，无 `id`（事件不需要回应）。用于单向通知。

```json
{
  "version": "1.0",
  "kind": "event",
  "event": "agent.message.completed",
  "payload": { "session_id": "milky:private:10001", "text": "计划已整理好。" }
}
```

### 4.5 Heartbeat

`kind="heartbeat"`，payload 含 `type: "ping"` 或 `"pong"`，可选 `ts`。heartbeat 不要求严格的 request/response 配对，但 pong 应引用最近 ping 的 `ts`。

```json
{ "version": "1.0", "kind": "heartbeat", "payload": { "type": "ping", "ts": 1752100000000 } }
```

### 4.6 ID 生成约定

- request `id` 由发起端生成，建议使用 ULID 或 `{prefix}_{timestamp}_{random}`。
- 同一连接内 `id` 不可重复。
- response `id` 必须等于对应 request 的 `id`；收到未知 `id` 的 response 应忽略并记录警告。

### 4.7 Error 对象

```json
{
  "code": "capability_denied",
  "message": "Node is not allowed to execute this capability",
  "retryable": false,
  "details": {}
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `code` | string | 是 | 机器可读错误码（见第 9 节） |
| `message` | string | 是 | 人类可读说明 |
| `retryable` | bool | 否 | 是否值得重试，默认 `false` |
| `details` | object | 否 | 附加诊断信息 |

## 5. 连接生命周期

### 5.1 完整流程

```text
Node                           Gateway
  │                               │
  │  WebSocket connect + token    │
  ├──────────────────────────────►│
  │                               │  校验 token
  │                               │
  │  node.register (capabilities) │
  ├──────────────────────────────►│
  │                               │
  │  node.register response       │
  │   (session_id, heartbeat)     │
  │◄──────────────────────────────┤
  │                               │
  │  heartbeat ping/pong          │
  │◄─────────────────────────────►│
  │                               │
  │  event: agent.message.*       │  (Gateway → Node)
  │◄──────────────────────────────┤
  │                               │
  │  request: capability.invoke   │  (Gateway → Node)
  │◄──────────────────────────────┤
  │  response                      │
  ├──────────────────────────────►│
  │                               │
  │  request: node.state.updated  │  (Node → Gateway)
  ├──────────────────────────────►│
```

### 5.2 连接前鉴权

WebSocket 握手阶段携带 token。Gateway 在接受升级前校验：

1. token 存在且未过期/未撤销。
2. token 类型正确（node token 或 pairing token）。
3. 握手成功后进入「已认证但未注册」状态，此时只允许 `node.register`，其他 method 返回 `not_registered` 错误。

> 设计选择：把鉴权放在握手阶段（而非连接后第一帧），是因为 WebSocket 握手失败语义清晰（401/403），且避免半连接状态。

### 5.3 注册（node.register）

Node 连接成功后必须先发送 `node.register`：

```json
{
  "version": "1.0",
  "kind": "request",
  "id": "req_register_001",
  "method": "node.register",
  "payload": {
    "node_id": "desktop-local",
    "display_name": "Nahida Desktop",
    "node_type": "desktop",
    "capabilities": [
      {
        "name": "desktop.live2d.set_expression",
        "version": "1.0",
        "direction": "gateway_to_node",
        "risk": "low",
        "description": "Set Live2D expression by expression id"
      }
    ],
    "metadata": { "platform": "windows", "app_version": "0.1.0" }
  }
}
```

Gateway 响应：

```json
{
  "version": "1.0",
  "kind": "response",
  "id": "req_register_001",
  "ok": true,
  "payload": {
    "accepted": true,
    "session_id": "node_session_01JZ0000AB",
    "heartbeat_interval_ms": 15000,
    "heartbeat_timeout_ms": 45000,
    "server_time": "2026-07-09T12:00:00Z"
  }
}
```

注册约束：

- `node_id` 在 Gateway 范围内唯一。重复 `node_id` 连接时，Gateway 按「踢旧连接」策略处理（旧连接收到 `node.duplicate_connection` event 后被关闭）。
- `node_type` 首版枚举：`desktop` / `worker` / `tool-host`。
- `capabilities` 可为空（纯消费事件的 node）。
- 注册后 node 进入「在线」状态，开始心跳。

### 5.4 心跳

Gateway 按 `heartbeat_interval_ms` 间隔发送 ping，Node 必须在 `heartbeat_timeout_ms` 内回 pong。超时未回，Gateway 判定 Node 离线，关闭连接。

Node 也可主动发 ping（例如检测链路活性），Gateway 必须回 pong。

```text
Gateway ping → Node pong （主路径）
Node ping → Gateway pong （可选）
```

### 5.5 重连

Node 必须实现：

- 指数退避重连，初始 1s，上限 30s。
- 重连后重新鉴权、重新 `node.register`。
- 重连期间本地状态进入 `disconnected`，但不崩溃。
- Gateway 侧：Node 重连后视为同一 `node_id` 的会话恢复，重新建立在线状态。

### 5.6 状态机

```text
disconnected ──connect──► authenticating ──ok──► registering ──accepted──► online
                                │                       │                      │
                              fail                   reject                 heartbeat timeout
                                │                       │                      │
                                ▼                       ▼                      ▼
                            disconnected            disconnected            offline (待重连)
```

Gateway 侧维护的 Node 会话状态：`authenticating` / `registering` / `online` / `offline`。`offline` 表示曾在线但当前断开，保留最近状态一段时间供查询。

## 6. Methods（请求方法注册表）

### 6.1 Node → Gateway

| method | 说明 | payload 关键字段 |
|--------|------|------------------|
| `node.register` | 注册节点与能力 | `node_id`, `node_type`, `capabilities`, `metadata` |
| `node.state.updated` | 上报本地状态摘要 | `state`（窗口/模型/TTS/渲染模式摘要） |
| `node.unsubscribe` | 取消订阅部分事件 | `events: list[str]` |
| `node.input.submit` | Node 端用户输入消息 | `session_id`, `text`（见 6.3） |
| `node.capability.result` | 异步 capability 结果回传 | `invoke_id`, `status`, `result`（可选异步路径） |

### 6.2 Gateway → Node

| method | 说明 | payload 关键字段 |
|--------|------|------------------|
| `capability.invoke` | 调用 Node 能力 | `invoke_id`, `capability`, `arguments` |
| `capability.cancel` | 取消进行中的能力调用 | `invoke_id` |
| `node.subscribe` | 告知 Node 将推送哪些事件 | `events: list[str]` |
| `node.set_event_filter` | 调整事件过滤策略 | `filter` |

### 6.3 `node.input.submit` 语义

Desktop 桌宠气泡输入框的消息通过此方法注入到指定 session。Gateway 将其标记为 `source=node` 的用户消息，进入 Agent Loop。注意：这不能伪装成普通用户消息，必须在 turn metadata 中标记来源，便于审计。

### 6.4 Capability 调用

```json
{
  "version": "1.0",
  "kind": "request",
  "id": "req_invoke_099",
  "method": "capability.invoke",
  "payload": {
    "invoke_id": "inv_01JZ0000ZZ",
    "capability": "desktop.live2d.set_expression",
    "arguments": { "expression": "happy" }
  }
}
```

成功响应：

```json
{
  "version": "1.0",
  "kind": "response",
  "id": "req_invoke_099",
  "ok": true,
  "payload": { "applied": true }
}
```

失败响应（capability 不存在 / 未授权 / 执行异常）：

```json
{
  "version": "1.0",
  "kind": "response",
  "id": "req_invoke_099",
  "ok": false,
  "error": {
    "code": "capability_not_found",
    "message": "Capability desktop.live2d.set_expression not registered",
    "retryable": false
  }
}
```

约束：

- Gateway 在调用前必须校验 Node 已注册该 capability，且调用方（user/session/plugin）被授权。
- Node 端必须二次校验本地 allowlist，不能只依赖 Gateway。
- 所有 `capability.invoke` 写入 audit log（调用方、node_id、capability、arguments 摘要、结果、耗时）。
- `arguments` 由 capability schema 约束；非法字段 schema 校验失败返回 `invalid_arguments`。
- 高风险 capability（文件、命令、媒体采集）首版不开放。

## 7. Events

事件是单向通知（`kind="event"`），不需要回应。

### 7.1 Gateway → Node（订阅式）

Node 通过 `node.register` 或 `node.subscribe` 声明感兴趣的事件类别。默认订阅集合由 Gateway 配置决定（例如 Desktop 默认订阅当前 session 的 agent 事件）。

| event | 说明 | payload 关键字段 |
|-------|------|------------------|
| `agent.message.started` | Agent 开始处理 | `session_id` |
| `agent.message.completed` | Agent 回复完成 | `session_id`, `text`, `display_plan` |
| `agent.message.error` | Agent 处理出错 | `session_id`, `error` |
| `cron.fired` | 定时任务触发 | `job_id`, `session_id`, `prompt` |
| `plugin.error` | 插件错误 | `plugin_id`, `error` |
| `gateway.shutdown` | Gateway 即将关闭 | `reason`, `retry_after_ms` |
| `node.duplicate_connection` | 同 node_id 新连接取代当前连接 | `new_session_id` |
| `capability.revoked` | 能力授权被撤销 | `capability` |

`agent.message.completed` 的 `display_plan` 字段承载 [desktop-app.md §9.3](../design/desktop-app.md) 的 DisplayPlan，用于驱动 Desktop Live2D/TTS 表现。Gateway 保留干净文本进入 transcript/memory，DisplayPlan 只作为 metadata 透传给订阅的 node。

### 7.2 Node → Gateway

| event | 说明 | payload 关键字段 |
|-------|------|------------------|
| `node.state.updated` | 上报状态摘要（也可走 request，event 为轻量通知） | `state` |
| `node.model.imported` | 用户导入模型元数据 | `model_id`, `name`, `capabilities` |
| `node.event.custom` | Node 自定义事件（透传，不参与 core 语义） | `type`, `data` |

### 7.3 事件过滤与隐私

- Gateway 不向 Node 推送其无权感知的 session 事件。
- 事件 payload 不包含 base64 媒体、临时 URL、`raw_event`、reasoning 原文。
- DisplayPlan 在进入 Node 前已完成 schema 校验与降级。

## 8. 鉴权与配对

### 8.1 配对流程（pairing）

V2 Desktop Node 推荐配对流程，避免长期 token 手动复制：

```text
1. 用户在 Desktop App 输入 Gateway 地址。
2. Desktop App POST /api/nodes/pairing/start
   → Gateway 生成一次性 pairing code（短码，例如 6 位）。
3. 用户在 WebUI 授权页输入 pairing code（或扫码）确认。
4. Gateway POST 返回长期 node token + node_id。
5. Tauri Rust side 把 node token 存入系统安全存储（keychain）。
6. Desktop 用 node token 建立 WebSocket。
```

### 8.2 Token 类型

| token | 用途 | 生命周期 |
|-------|------|----------|
| pairing token / code | 一次性换取 node token | 短（分钟级） |
| node token | WebSocket 连接鉴权 | 长，可撤销/过期 |
| (现有) bearer token / webui session | REST/SSE 鉴权 | 现有体系 |

node token 与现有 bearer/webui 体系并行，由 `node_auth` service 独立管理。token 存储建议：

- Python 端：哈希存储（不存明文），记录签发时间、过期、撤销、关联 node_id。
- Desktop 端：系统 keychain，不写普通 JSON/YAML。

### 8.3 授权策略

Gateway 维护 capability 授权策略：哪些 user/session/plugin 可以调用哪些 node 的哪些 capability。Node 注册 capability 不等于默认信任。调用入口、类型化 actor、策略模型、审批和持久审计见 [Gateway-Node 调用入口与授权设计](gateway-node-invocation-authorization.md)。公开入口启用后采用 fail-closed；owner 也需要显式 grant，高风险调用还需要 approval。

Node credential 与人员身份分离。配对/签发时，Gateway 可为 credential 绑定：

- `actor_account_key`：该 credential 获准代表的账号，例如
  `desktop:user:owner`；不能从 `node_id` 推导。
- `conversation_id`：该 credential 默认使用的短期 history lane，例如
  `conversation:private:owner-desktop`。

运行时由 Gateway 从已验证 token 恢复这两个绑定，客户端声明的 person 或
node id 均不能替代 actor account。`IdentityStore` 再独立解析
`actor_account_key -> person_id`；worker/tool-host 可不绑定人员账号，但不能
提交人员输入。

## 9. 错误码

错误码是稳定契约，新增允许，已有码语义不破坏性变更。

| code | 含义 | retryable 典型值 |
|------|------|------------------|
| `auth_failed` | 鉴权失败（token 无效/过期） | false |
| `auth_required` | 未鉴权就发非 register 请求 | false |
| `not_registered` | 未注册就发业务请求 | false |
| `register_rejected` | 注册被拒（node_id 冲突策略等） | 视情况 |
| `capability_not_found` | 能力未注册 | false |
| `capability_denied` | 调用方未授权 | false |
| `capability_local_denied` | Node 本地 allowlist 拒绝 | false |
| `invalid_arguments` | arguments schema 校验失败 | false |
| `capability_failed` | 能力执行异常 | 视情况 |
| `capability_timeout` | 能力调用超时 | true |
| `capability_cancelled` | 能力调用被取消 | false |
| `method_not_found` | 未知 method | false |
| `node_input_unavailable` | Node 输入入口或目标 channel 当前不可用 | true |
| `rate_limited` | 频率超限 | true |
| `internal_error` | Gateway 内部错误 | true |
| `unknown_request_id` | response 引用了未知 request id | false |

错误响应必须包含 `code` 和 `message`。`retryable` 帮助 Node/Gateway 决定是否重试。

## 10. 版本与兼容

### 10.1 版本字段

`version` 字段格式 `"<major>.<minor>"`。

- `minor` 升级：新增字段、新增 method/event/code、放宽约束。双方必须向前兼容（忽略未知字段、忽略未知 method 时返回 `method_not_found` 而非断连）。
- `major` 升级：破坏性变更。Gateway 可同时支持多个主版本 endpoint（如 `/api/nodes/ws` 与 `/api/nodes/ws/v2`），Node 通过握手声明版本。

### 10.2 兼容策略

- 接收端遇到未知字段：忽略（Pydantic 默认行为，Rust serde 用 `#[serde(default)]` + deny_unknown_fields=false）。
- 接收端遇到未知 method：返回 `method_not_found`，不断连。
- 接收端遇到未知 event：忽略并记录 debug 日志。
- 接收端遇到未知 `error.code`：按 `retryable=false` 处理，展示 `message`。
- `version` 缺失或无法解析：Gateway 拒绝连接，Node 重连时降级重试。

### 10.3 协议冻结时机

协议进入「稳定」状态后（里程碑 3 完成且 Desktop Rust 对齐后），默认只做向后兼容变更。破坏性变更必须走新主版本。

## 11. 模块布局

### 11.1 Python 侧

```text
nahida_bot/gateway/
  node_protocol/
    __init__.py
    schemas.py        # Pydantic envelope / payload / capability / error models
    dispatcher.py     # request routing / response matching / event fanout
    sessions.py       # node session 状态机、心跳、capability registry
    errors.py         # 错误码常量、NodeProtocolError 异常树
    auth.py           # node token / pairing token 校验
    routes.py         # FastAPI WebSocket endpoint（/api/nodes/ws）
  services/
    node_registry.py  # 在线/离线节点表、查询、状态摘要
    node_auth.py      # token 签发/撤销/存储、pairing 流程
    node_invoker.py   # capability.invoke 调用、超时、audit
  routes/
    nodes.py          # REST: /api/nodes, /api/nodes/{id}, pairing/*
```

```text
nahida_bot/node/
  __init__.py
  client.py           # Python Node WebSocket client SDK
  capabilities.py     # capability 注册辅助、本地 allowlist
```

### 11.2 Rust/Tauri 侧（Desktop，后续阶段）

```text
desktop/src-tauri/src/gateway_node/
  protocol.rs         # serde envelope / payload / error structs
  client.rs           # WebSocket connect / reconnect / heartbeat / request-response matching
  dispatcher.rs       # 处理 Gateway 发来的 capability.invoke
  capabilities.rs     # 注册 Live2D / notification / window capabilities
  auth.rs             # pairing / node token / 安全存储
```

### 11.3 Schema 一致性

不通过 FFI 共享实现。一致性保证：

- 本文档作为协议说明（single source of truth）。
- `tests/fixtures/gateway_node/*.json` 保存跨语言报文样例。
- Python 单测必须能 parse 所有 fixtures，并序列化出等价 JSON。
- Rust 单测必须能 parse 同一批 fixtures。
- 协议稳定前允许 Python/Rust 类型手写，避免过早引入代码生成链路。
- Pydantic 可导出 JSON Schema，后续可用于生成 TypeScript/Rust 类型。

## 12. Fixtures

固定报文样例作为跨语言契约。每个 fixture 是一个独立 JSON 文件，文件名即语义。

```text
tests/fixtures/gateway_node/
  auth_register.request.json
  auth_register.response.json
  auth_register_rejected.response.json
  heartbeat_ping.json
  heartbeat_pong.json
  node_state_updated.event.json
  capability_invoke_live2d.request.json
  capability_invoke_live2d.response.json
  capability_denied.response.json
  capability_timeout.response.json
  agent_message_completed.event.json
  gateway_shutdown.event.json
  node_duplicate_connection.event.json
  node_input_submit.request.json
```

fixtures 约定：

- 真实 `id` / `session_id` 使用示例前缀（`req_` / `node_session_` / `inv_`），便于人工识别。
- 时间戳使用 ISO8601 字符串或整数毫秒，保持同一 fixture 内一致。
- 每个 fixture 应附一行注释（`_comment` 字段，parse 时忽略）说明用途。
- fixtures 改动必须在文档同步说明，并更新 Python/Rust 双方测试。

## 13. 安全设计

### 13.1 Token 存储

- Desktop App 不应把长期 token 明文写入普通 JSON/YAML。
- Tauri side 使用系统安全存储或平台 keychain。
- token 支持撤销、过期和重新配对。

### 13.2 Capability 权限

- Node 注册 capability 后，Gateway 不默认信任全部能力。
- Gateway 保存授权策略：哪些 user/session/plugin 可调用哪些 node capability。
- 所有 `capability.invoke` 写入 audit log。
- Node 端二次校验本地 allowlist。

### 13.3 高风险能力

V1 不开放以下高风险能力（显式声明、显式授权、可审计后才能逐步开放）：

- 读取/写入本机文件
- 执行命令
- 录音、截屏、摄像头
- 全局键盘监听

### 13.4 事件隐私

- Gateway 不向 Node 推送无权感知的 session 事件。
- 事件 payload 不含 base64 媒体、临时 URL、`raw_event`、reasoning 原文。
- DisplayPlan 进入 Node 前完成 schema 校验与降级。
- Node 上报的状态/模型元数据只含非敏感摘要（模型 ID、名称、能力），不上传模型文件本体。

### 13.5 网络边界

- 本地默认 `127.0.0.1`。
- 远程必须 WSS 或明确不安全模式。
- Node 端校验 Gateway 证书；自签证书走显式信任配置。

## 14. 测试策略

### 14.1 协议测试

- Python Pydantic models parse 所有 fixtures。
- Rust serde models parse 同一批 fixtures。
- Python/Rust 都能序列化出符合 schema 的 envelope。
- request/response id 匹配、错误响应、未知字段兼容性均有测试。

### 14.2 Gateway 测试

- WebSocket 鉴权成功/失败（无效 token、过期 token、缺失 token）。
- `node.register` 注册 capability、重复 node_id 处理。
- heartbeat 超时断开。
- `capability.invoke` 成功、失败（未授权/未注册）、超时、取消。
- node offline 后 Gateway 不再调用该节点。
- 事件订阅过滤：Node 只收到授权范围内的事件。

### 14.3 Node Client 测试

- 连接、鉴权、注册、重连、心跳。
- 收到 Gateway event 能正确转交上层。
- 收到 `capability.invoke` 能 dispatch 到本地 handler。
- 本地 allowlist 拒绝非法 capability 调用。

### 14.4 集成测试

- Python node ↔ Gateway 全链路：注册 → 订阅事件 → 收到 agent 事件 → capability 被调用 → 状态上报。
- 断线重连后会话恢复。
- capability 超时/取消/异常路径。

## 15. 与现有系统的集成边界

### 15.1 与 EventBroadcaster 的关系

现有 `EventBroadcaster`（SSE）把核心 EventBus 事件 fan-out 给 HTTP 客户端。Node 协议复用同一批核心事件源（`MessageReceived`/`MessageSent`/`AgentRunStarted`/`AgentRunFinished`/`PluginErrorOccurred`），但：

- Node 协议有自己的事件转换层：核心事件 → node envelope event payload（含 DisplayPlan 等额外字段）。
- Node 订阅是 per-node 的，受授权过滤；SSE 是 per-client token 的全量（当前实现）。
- 两者共享 EventBus 订阅，但 fan-out 路径独立，避免相互阻塞。

### 15.2 与 AgentOrchestrator / AgentRunExecutor 的关系

[agent-orchestration.md](agent-orchestration.md) 已为远程执行预留 `AgentRunExecutor` 接口（`LocalAgentRunExecutor` + 未来 `RemoteNodeRunExecutor`）。本协议为 `RemoteNodeRunExecutor` 提供传输层，但**首版不实现远程 agent run**。首版 Node 协议聚焦 Desktop 表现控制与事件投递。

### 15.3 与 WebHostService 的关系

Node WebSocket endpoint 是独立长连接通道，不经过 `WebHostService`（webhook 扩展点）。两者并存：webhook 走 `/webhooks/{path}`，node 走 `/api/nodes/ws`。

### 15.4 Python Worker Node 的能力来源（设计方向，暂不实现）

当前 `nahida_bot/node/` 是 SDK + 测试对端，没有定义「一个 Python node 进程启动时能力从哪来」。长期方向是**复用现有插件系统**：同一个插件，装在 gateway 端就是 gateway 的工具（通过 `api.register_tool`），装在 node 端就由 node 把它的能力暴露成 capability 给 gateway 远程调用。位置决定角色，不搞第二套插件系统。

核心机制：

```text
node 进程                              gateway 进程
┌─────────────────────┐               ┌──────────────────────────┐
│ 插件 on_load        │               │ AgentLoop                │
│  api.register_tool( │               │  调用 web_search         │
│    "web_search",h)  │               │      ↓                   │
│       ↓             │               │ ToolRegistry 查找        │
│ NodeBotAPI          │               │  发现 web_search 在 node │
│  → capability       │──register────►│  → NodeToolBridge        │
│    "tool.web_search"│               │      ↓                   │
│  + 本地 handler h   │◄──invoke──────│ NodeInvocationService    │
│      ↓ 执行 h       │──response────►│      ↓                   │
│  返回结果           │               │ 结果回到 Agent           │
└─────────────────────┘               └──────────────────────────┘
```

需要的新组件：

1. **`NodeBotAPI`**（实现 `BotAPI` 协议）：node 端的 BotAPI 实现，按方法类别分流。
2. **`NodeToolBridge`**（gateway 侧）：监听 node register，把符合 token scope、schema 和 exposure policy 的 `tool.*` capability 注册成 `ToolEntry`（handler 统一走 `NodeInvocationService`）；node 断线时按 `node_session_id` 安全注销。详细约束见 [调用入口与授权设计](gateway-node-invocation-authorization.md#14-nodetoolbridge-生命周期)。

`BotAPI` 的 ~40 个方法在 node 端分三类处理：

| 类别 | 方法 | node 端策略 |
|------|------|------------|
| 工具/命令注册 | `register_tool` / `register_command` | 转 capability（`tool.{name}`），本地保留 handler |
| 事件订阅 | `subscribe` / `on_event` | 告诉 gateway 订阅，事件经 WebSocket 转发 |
| workspace/memory/llm | `workspace_read` / `memory_search` / `llm_chat` | RPC 回 gateway（需新增 node→gateway 方法） |
| session/message_router | `request_agent_response` / `start_new_session` | 不支持（依赖 `EventBus.context.app` 反向引用） |
| channel/provider | `register_channel` / `register_provider_type` | 不支持（必须在 gateway） |
| plugin_data | `plugin_data_get/set` | 本地 SQLite（node 自带轻量存储） |
| logger/tasks | `logger` / `spawn_task` | 本地实现，无依赖 |

已知约束（来自插件系统依赖边界审计）：

- `plugin_data_*` 在 repo 为 None 时**硬抛 RuntimeError**（而 memory/workspace 静默降级）。node 必须自带本地 plugin_data store，否则 kb、mcp 等插件 on_load 就会崩。
- `EventBus.context.app` 反向引用是 node 端的天生障碍——`start_new_session` / `get_active_session_id` / `get_session_run_status` / `request_agent_response` 四个方法无法在 node 直接工作。
- 不是所有插件都能跑在 node：channel/provider 插件必须在 gateway；依赖 `message_router` 的插件不适合。需要 manifest 加 `node_compatible` 或 `requires` 声明。

分阶段方案：

| 阶段 | 范围 | 验收 |
|------|------|------|
| A | `NodeBotAPI` 基础版：`register_tool`→capability、`logger`/`spawn_task` 本地、其余不支持 | 纯计算/外部 API 插件（web_fetch、image_generate）能在 node 跑，gateway 自动桥接成 Agent tool |
| B | node→gateway RPC：`workspace_read`/`memory_search`/`llm_chat` 远程调用 | 依赖宿主服务的插件也能在 node 跑 |
| C | manifest `node_compatible`/`requires` 声明 + `nahida-bot node` CLI | operator 能指定插件目录启动 node |

当前状态：**暂不实现**，优先推进 #9（Desktop，走 Rust node）。阶段 A 是后续 Python worker（GPU 推理节点等）的基础。

## 16. 待决问题

- 一个 Gateway 同时连接多个 Desktop Node 时，未指定 node_id 的调用如何选择目标。当前收敛为：只有一个“在线且已授权”的候选时自动选择，否则返回 `ambiguous_target`；不使用 owner 全权或任意首节点策略。
- `node.input.submit` 注入的消息是否计入会话历史，以及是否允许触发工具调用。
- DisplayPlan 由主 LLM 输出、规则推导器生成，还是二次 planner 生成（见 desktop-app.md §18）。
- pairing code 是 6 位短码（人工输入）还是扫码；首版建议短码，扫码后置。
- heartbeat 由 Gateway 单向驱动，还是双向（Node 也可主动 ping）。当前设计为双向，主路径 Gateway→Node。
- Node 离线后 Gateway 保留最近状态多久供 REST 查询。
- capability.invoke 是否支持流式/分片结果（例如长 TTS 音频分段）。首版不支持，走 SpeechArtifact 引用。
- 是否需要 node-to-node 中转（Gateway 路由两个 node 通信）。首版不支持。
- Python worker node 的能力来源：复用插件系统 + NodeBotAPI 方案（见 §15.4），暂不实现，待 #9 Desktop 落地后再推进。

## 17. 实施里程碑

与 [desktop-app.md §16](../design/desktop-app.md) Phase 5/6/7 对齐：

| 里程碑 | 范围 | 验收 |
|--------|------|------|
| M1 协议设计 | 本文档 + fixtures | 双方 review，fixtures 冻结为回归基线 |
| M2 Python SDK（服务端） | `node_protocol/` + `services/node_*` | Pydantic parse 全部 fixtures；dispatcher 单测通过 |
| M3 Gateway 集成 | `/api/nodes/*` + WS endpoint + 事件桥 + pairing | mock node 能连、鉴权、注册、收事件、被调用 |
| M4 Python Node Client | `nahida_bot/node/` | Python node ↔ Gateway 全链路集成测试通过 |
| M5 Desktop Rust Node | `desktop/src-tauri/gateway_node/` | Desktop 作为 node 连接，Rust parse 同一批 fixtures |
| M6 Capability 桥接 | DisplayPlan 投递、capability 真实执行 | Gateway 能通过 capability 控制 Desktop 表现 |
| M7 Python Worker Node | `NodeBotAPI` + `NodeToolBridge` + `nahida-bot node` CLI（见 §15.4） | 插件装在 node 端，Agent 无感知远程调用 |

里程碑 M1-M4 属于本轮 gateway+node 架构推进范围；M5-M6 属于 Desktop 接入，依赖协议稳定；M7 是 Python worker node 的长期方向，暂不实现。
