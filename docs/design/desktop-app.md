# Desktop App 与 Gateway-Node 设计

> 记录时间：2026-06-07
> 状态：规划中
> 相关文档：
>
> - [webui-design.md](webui-design.md) — 现有 Gateway + WebUI 运维面板设计
> - [cross-session-messaging.md](cross-session-messaging.md) — 跨会话消息能力
> - [memory-scoping.md](memory-scoping.md) — 会话与记忆作用域隔离
> - [../architecture/directory-structure.md](../architecture/directory-structure.md) — 当前仓库模块边界

## 1. 背景

Nahida Bot 当前已经具备 Python core、FastAPI Gateway、Vue WebUI、插件系统和多 Channel 接入。后续计划增加一个 Desktop App，作为常驻桌面端入口，与 Nahida Bot 建立连接，并通过 Live2D 实现桌宠形态。

Desktop App 的技术方向暂定为：

- **Tauri + Rust**：负责原生窗口、托盘、系统权限、配置存储、WebSocket 连接与打包。
- **WebView 前端**：负责 UI、状态面板和 Live2D 渲染。
- **WASM / WebGL / Live2D**：负责桌宠模型、动作、表情和交互表现。
- **Gateway-Node 协议**：负责 Desktop App 与 Nahida Bot 之间的双向通信。

当前 Gateway 已有 REST API 和 SSE 事件流，WebUI 只消费公开 API，不直接读取 core 内部状态。这个边界应继续保留：Desktop App 也应作为外部 interface client 或 node 接入 Gateway，而不是嵌入 Python core。

## 2. 目标与非目标

### 2.1 目标

- 将 Desktop App 纳入项目长期架构，明确它和 Gateway、Node、WebUI、Agent core 的边界。
- 定义 Desktop App 在 Gateway-Node 架构中的角色：先作为 Gateway client，后续升级为 Desktop Node。
- 明确 Gateway-Node 协议应是语言无关协议，而不是 Python 私有接口。
- 避免 Rust 到 Python FFI，降低跨平台打包、运行时和 async 集成复杂度。
- 为 Python node、Rust/Tauri desktop node、未来其他语言 node 预留一致协议。
- 支持 Live2D 桌宠能力通过 capability 暴露给 Nahida Bot，例如设置表情、播放动作、展示通知。
- 保持 Desktop App 与现有 WebUI 共享 Gateway API、事件模型和必要的 TypeScript client。

### 2.2 非目标

- 不在 V1 中把 Nahida Bot Python runtime 打包进 Desktop App。
- 不让 Tauri 端直接 import 或 FFI 调用 Python core。
- 不把 Desktop App 做成 WebUI 的换皮版本。WebUI 是运维面板，Desktop App 是常驻交互端。
- 不在协议未稳定前引入复杂二进制协议、protobuf 或跨语言代码生成流水线。
- 不在 V1 中开放高权限本机执行能力。Desktop Node 的 capability 必须显式声明、显式授权和可审计。

## 3. 仓库组织决策

### 3.1 推荐结论

Desktop App 初期建议放在当前仓库中，但不要放入 `nahida_bot/` Python 包内部。

理由：

- 当前仓库已经是事实上的 monorepo：根目录有 `pnpm-workspace.yaml`，已有 `webui/`、`docs/`、`nahida-bot-sdk/` 等多项目结构。
- Gateway-Node 协议尚未稳定，Desktop App 会和 Gateway API 一起频繁迭代，同仓库能减少协议漂移。
- WebUI 和 Desktop App 可以共享 Gateway TypeScript client、schema fixtures、事件类型和设计 token。
- 单独开仓库不会减少协议设计成本，反而会提前增加发布、版本兼容和联调成本。

### 3.2 建议目录

保守演进方案：

```text
nahida-bot/
  nahida_bot/
    gateway/
      node_protocol/
        schemas.py
        dispatcher.py
        server.py
    node/
      client.py
  desktop/
    package.json
    src/
      main.ts
      live2d/
      api/
      stores/
    src-tauri/
      Cargo.toml
      src/
        main.rs
        gateway_node/
          protocol.rs
          client.rs
          dispatcher.rs
          auth.rs
  packages/
    gateway-client/
    gateway-protocol/
  docs/
    design/
      desktop-app.md
    architecture/
      gateway-node-protocol.md
```

`pnpm-workspace.yaml` 后续可扩展为：

```yaml
packages:
  - webui
  - docs
  - desktop
  - packages/*
```

`packages/gateway-client` 和 `packages/gateway-protocol` 是可选但推荐的中期抽象。早期也可以先在 `webui/src/api` 和 `desktop/src/api` 中少量重复，等接口稳定后再抽出共享包。

## 4. 产品与架构边界

Desktop App 有两个阶段性的角色：

| 阶段 | 角色 | 通信方式 | 说明 |
|------|------|----------|------|
| V1 | Gateway Client | REST + SSE | 连接已有 Gateway，发送消息、读取状态、接收实时事件，驱动 Live2D 表现 |
| V2 | Desktop Node | WebSocket Gateway-Node | 注册节点能力，接受 Gateway 调用，向 Gateway 上报本机状态和桌宠事件 |

V1 不需要等待完整 Gateway-Node 实现，可以复用现有 Gateway 能力快速形成桌宠体验。V2 再将桌宠能力纳入统一 node capability 体系。

整体结构：

```text
┌──────────────────────────────────────────────────────────────┐
│                         Desktop App                          │
│                                                              │
│  ┌──────────────────────┐        ┌────────────────────────┐  │
│  │ WebView Frontend     │        │ Tauri Rust Side         │  │
│  │ - Live2D WebGL       │        │ - WebSocket client      │  │
│  │ - UI state           │◄──────►│ - token storage         │  │
│  │ - animations         │        │ - tray/window/native    │  │
│  └──────────────────────┘        └───────────┬────────────┘  │
└──────────────────────────────────────────────┼───────────────┘
                                               │ REST/SSE or WS
                                               ▼
┌──────────────────────────────────────────────────────────────┐
│                       Nahida Gateway                         │
│  FastAPI REST / SSE / Gateway-Node WebSocket / Auth / Audit   │
└────────────────────────────────┬─────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                       Nahida Core                            │
│  Agent Loop / Memory / Workspace / Plugins / Channels         │
└──────────────────────────────────────────────────────────────┘
```

## 5. Gateway-Node 协议设计原则

### 5.1 不使用 Rust 到 Python FFI

Desktop App 不应通过 FFI 调 Python SDK。原因：

- Tauri 打包时需要额外处理 Python runtime、site-packages、动态库和平台 ABI。
- Rust async runtime 与 Python asyncio 跨语言协作复杂，错误边界不清晰。
- FFI 会把 Desktop App 和 Python 实现强耦合，违背 Gateway-Node 的分布式边界。
- 这里的核心问题是“两个进程通过 WebSocket 说同一种协议”，不是“Rust 复用 Python 业务逻辑”。

正确做法是：

- 定义一套语言无关的 WebSocket wire protocol。
- Python 实现一份 protocol SDK，供 Gateway server 和 Python node 使用。
- Rust 实现一份 protocol SDK，供 Tauri Desktop Node 使用。
- 通过 schema、fixtures 和 contract tests 保证两边一致。

### 5.2 协议分层

```text
Wire Protocol
  JSON over WebSocket 的稳定约定，包含 envelope、auth、heartbeat、request/response/event、error。

Python Protocol SDK
  Pydantic models + dispatcher + Gateway session manager + Python node client。

Rust Protocol SDK
  serde structs + async websocket client + reconnect/heartbeat + Tauri command bridge。

TypeScript Client
  WebUI/Desktop frontend 使用的 REST/SSE 类型与辅助函数，可后续从 schema 生成。
```

### 5.3 首版数据格式

V1 建议使用 **JSON over WebSocket**。

理由：

- 调试方便，可以直接用日志和开发工具查看报文。
- 与 FastAPI、Pydantic、serde、TypeScript 都兼容。
- 协议还在演化期，JSON 比二进制协议更易调整。
- Live2D 动作、状态事件、能力调用的数据量较小，不需要首版优化为 binary frame。

大文件、图片、音频等媒体不应直接塞进 node WebSocket JSON 报文。首版应使用：

- 已缓存文件的 `media_id`
- Gateway 可访问的资源 URL
- 文件上传 API
- 后续再按需要增加 binary frame 或独立 media channel

## 6. WebSocket Envelope

所有 Gateway-Node 报文使用统一 envelope。

### 6.1 基础字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `version` | string | 是 | 协议版本，如 `"1.0"` |
| `kind` | string | 是 | `request` / `response` / `event` / `heartbeat` |
| `id` | string | 条件 | request/response 关联 ID |
| `method` | string | 条件 | request 的方法名 |
| `event` | string | 条件 | event 的事件名 |
| `ok` | bool | 条件 | response 是否成功 |
| `payload` | object | 否 | 业务数据 |
| `error` | object | 否 | 错误对象 |
| `meta` | object | 否 | trace、时间戳、兼容标记等扩展信息 |

### 6.2 Request 示例

```json
{
  "version": "1.0",
  "kind": "request",
  "id": "req_01JZ000001",
  "method": "node.register",
  "payload": {
    "node_id": "desktop-local",
    "display_name": "Nahida Desktop",
    "node_type": "desktop",
    "capabilities": [
      {
        "name": "desktop.live2d.set_expression",
        "version": "1.0",
        "description": "Set Live2D expression by expression id"
      },
      {
        "name": "desktop.live2d.play_motion",
        "version": "1.0",
        "description": "Play Live2D motion by group and motion id"
      },
      {
        "name": "desktop.notification.show",
        "version": "1.0",
        "description": "Show a native desktop notification"
      }
    ]
  }
}
```

### 6.3 Response 示例

```json
{
  "version": "1.0",
  "kind": "response",
  "id": "req_01JZ000001",
  "ok": true,
  "payload": {
    "accepted": true,
    "session_id": "node_session_01JZ0000AB",
    "heartbeat_interval_ms": 15000
  }
}
```

### 6.4 Event 示例

```json
{
  "version": "1.0",
  "kind": "event",
  "event": "agent.message.completed",
  "payload": {
    "session_id": "milky:private:10001",
    "source": "agent",
    "text": "今天的计划已经整理好了。"
  }
}
```

### 6.5 Capability 调用示例

Gateway 调用 Desktop Node 的 Live2D 能力：

```json
{
  "version": "1.0",
  "kind": "request",
  "id": "req_01JZ000099",
  "method": "capability.invoke",
  "payload": {
    "capability": "desktop.live2d.set_expression",
    "arguments": {
      "expression": "happy"
    }
  }
}
```

Desktop Node 返回：

```json
{
  "version": "1.0",
  "kind": "response",
  "id": "req_01JZ000099",
  "ok": true,
  "payload": {
    "applied": true
  }
}
```

### 6.6 Error 示例

```json
{
  "version": "1.0",
  "kind": "response",
  "id": "req_01JZ000099",
  "ok": false,
  "error": {
    "code": "capability_denied",
    "message": "Node is not allowed to execute this capability",
    "retryable": false
  }
}
```

## 7. 协议生命周期

### 7.1 连接流程

```text
Desktop App                 Gateway
    │                           │
    │  WebSocket connect         │
    ├──────────────────────────►│
    │                           │
    │  auth.hello / token        │
    ├──────────────────────────►│
    │                           │
    │  auth.accepted             │
    │◄──────────────────────────┤
    │                           │
    │  node.register             │
    ├──────────────────────────►│
    │                           │
    │  node.register response    │
    │◄──────────────────────────┤
    │                           │
    │  heartbeat ping/pong       │
    │◄─────────────────────────►│
```

### 7.2 鉴权与配对

V1 可以复用 Gateway bearer token 或 WebUI session cookie。V2 的 Desktop Node 应增加配对流程：

1. 用户在 Desktop App 中输入 Gateway 地址。
2. Desktop App 请求 pairing code 或引导用户在 WebUI 中授权。
3. Gateway 生成一次性 pairing token。
4. Desktop App 用 pairing token 换取长期 node token。
5. Tauri Rust side 将 node token 存入系统安全存储。

默认部署建议：

- 本地连接默认 `http://127.0.0.1:6185`。
- 远程连接必须启用 HTTPS/WSS 或明确的内网信任配置。
- Browser/WebView 场景不能依赖自定义 Authorization header 的 EventSource。需要 bearer query token、cookie session 或改用 WebSocket。

### 7.3 心跳与重连

Desktop Node 必须实现：

- Gateway 指定 heartbeat interval。
- 超时未收到 pong 时主动重连。
- 指数退避，设置最大退避时间。
- 重连后重新鉴权、重新注册 capability。
- 本地 Live2D 前端进入 disconnected 状态，但不崩溃。

## 8. Python 与 Rust 实现策略

### 8.1 Python 侧

Python 侧负责 Gateway server 和 Python node SDK。

建议模块：

```text
nahida_bot/gateway/node_protocol/
  schemas.py
  dispatcher.py
  sessions.py
  errors.py
  auth.py
  routes.py

nahida_bot/node/
  client.py
  capabilities.py
```

职责：

- `schemas.py`：Pydantic envelope、payload、error、capability models。
- `dispatcher.py`：request routing、response matching、event fanout。
- `sessions.py`：node session 状态、heartbeat、capability registry。
- `auth.py`：node token、pairing token、权限校验。
- `routes.py`：FastAPI WebSocket endpoint。
- `node/client.py`：Python 远程 node client SDK。

### 8.2 Rust/Tauri 侧

Rust 侧负责 Desktop Node 的协议实现和 native 能力。

建议模块：

```text
desktop/src-tauri/src/gateway_node/
  protocol.rs
  client.rs
  dispatcher.rs
  capabilities.rs
  auth.rs
```

职责：

- `protocol.rs`：serde envelope、payload、error、capability structs。
- `client.rs`：WebSocket connect、reconnect、heartbeat、request/response matching。
- `dispatcher.rs`：处理 Gateway 发来的 `capability.invoke` 等 request。
- `capabilities.rs`：注册 Live2D、notification、window 等 desktop capabilities。
- `auth.rs`：pairing token、node token、安全存储。

Rust side 与 WebView frontend 之间通过 Tauri command/event 通信：

```text
Gateway WS event
      │
      ▼
Tauri Rust gateway_node client
      │
      ▼
Tauri event emit
      │
      ▼
WebView frontend
      │
      ▼
Live2D expression / motion / UI update
```

### 8.3 Schema 与一致性

不要通过 FFI 共享实现。通过以下方式保证一致性：

- `docs/architecture/gateway-node-protocol.md` 作为协议说明。
- `tests/fixtures/gateway_node/*.json` 保存跨语言报文样例。
- Python 单测必须能 parse 所有 fixtures。
- Rust 单测必须能 parse 同一批 fixtures。
- Pydantic 可导出 JSON Schema，后续可用于生成 TypeScript 或 Rust 类型。
- 协议稳定前允许 Python/Rust 类型手写，避免过早引入复杂生成链路。

建议 fixtures：

```text
tests/fixtures/gateway_node/
  auth_hello.request.json
  auth_accepted.response.json
  node_register.request.json
  node_register.response.json
  heartbeat_ping.json
  heartbeat_pong.json
  capability_invoke_live2d.request.json
  capability_invoke_live2d.response.json
  capability_denied.response.json
  agent_message_completed.event.json
```

## 9. Desktop App 功能分层

### 9.1 Native 层

Tauri Rust side 负责：

- Gateway 地址和 token 管理。
- WebSocket 连接、重连、心跳。
- 托盘、窗口置顶、透明窗口、拖拽、全局快捷键。
- 系统通知。
- 本地配置和安全存储。
- 将 Gateway 事件转发给 WebView。
- 将 WebView 操作转为 Gateway request。

### 9.2 WebView UI 层

WebView frontend 负责：

- Live2D WebGL 渲染。
- 桌宠状态机：idle、thinking、speaking、error、disconnected。
- 简单输入框或快捷命令。
- 连接状态、当前 session、消息提示。
- 表情和动作映射。
- TTS 播放状态、字幕分段和口型驱动。
- 用户模型管理 UI：导入、预览、切换、动作映射配置。
- 可选的轻量设置页。

### 9.3 DisplayPlan 与 TTS/Live2D 流水线

长回复、TTS 和 Live2D 动作不适合让 LLM 逐句调用工具。工具调用适合明确副作用，例如展示通知、打开窗口、切换页面；但不适合每句话都调用一次“设置表情、播放动作、停顿、调整语气”。

推荐增加一层 **DisplayPlan**：

```text
LLM response
      │
      ▼
DisplayPlan parser / validator
      │
      ├── OutboundMessage.text
      │       └── 发给普通 Channel 的干净文本
      │
      └── OutboundMessage.metadata.display_plan
              └── Desktop/TTS/Live2D 专用表现元数据
```

Desktop 端再执行：

```text
display_plan.segments
      │
      ├── TTS 分段合成
      ├── 字幕/当前句高亮
      ├── 音频音量或时间戳驱动口型
      └── emotion/motion 映射到 Live2D expression/motion
```

DisplayPlan 示例：

```json
{
  "version": "1.0",
  "text": "今天的计划已经整理好了。先处理配置问题，然后再看桌宠协议。",
  "segments": [
    {
      "text": "今天的计划已经整理好了。",
      "emotion": "happy",
      "motion": "nod",
      "pause_after_ms": 250,
      "voice": {
        "style": "bright",
        "speed": 1.0,
        "pitch": 0
      }
    },
    {
      "text": "先处理配置问题，然后再看桌宠协议。",
      "emotion": "thinking",
      "motion": "point",
      "pause_after_ms": 0,
      "voice": {
        "style": "calm",
        "speed": 0.95,
        "pitch": -1
      }
    }
  ]
}
```

关键约束：

- LLM 只输出语义层标签，例如 `emotion="happy"`、`motion="nod"`、`voice.style="calm"`。
- LLM 不允许输出文件路径、shell 命令、原始 capability 名称或任意执行参数。
- parser 使用严格 schema 校验，未知字段和非法枚举丢弃或降级。
- `OutboundMessage.text` 始终是干净文本，不包含控制标签。
- 非 Desktop Channel 忽略 `display_plan`，继续只发送纯文本。
- Desktop Channel 可以使用 `display_plan`，但必须在本地做 capability allowlist 校验。

DisplayPlan 的生成方式可分阶段演进：

| 阶段 | 方式 | 说明 |
|------|------|------|
| V1 | 规则推导 | 根据事件类型、文本长度、错误状态推导 `emotion` / `motion` |
| V2 | LLM 结构化输出 | prompt 要求 LLM 输出文本 + segments，parser 过滤为干净文本和 metadata |
| V3 | 二次规划 | 主回复生成后，用轻量模型或规则生成 DisplayPlan，减少对主回复格式的干扰 |
| V4 | 流式表现 | 支持边生成、边分句、边 TTS，但需要更复杂的增量 parser |

流式响应的处理需要谨慎。首版可以选择：

- Desktop TTS 模式下先等待完整回复，再解析 DisplayPlan。
- 普通文本 Channel 继续走现有回复路径。
- 后续再做按句增量解析和 TTS 队列。

### 9.4 Live2D 能力层

Live2D 不应直接绑定 Agent 内部实现。建议通过语义事件驱动：

| 输入事件 | Live2D 行为 |
|----------|-------------|
| `gateway.connected` | 进入 idle |
| `gateway.disconnected` | 进入 disconnected |
| `agent.message.started` | thinking 动作 |
| `agent.message.completed` | speaking 动作，按情绪设置表情 |
| `plugin.error` | worried / error 表情 |
| `cron.fired` | notify 动作 |
| `capability.invoke: desktop.live2d.*` | 显式动作或表情 |

表情推导可先用规则：

- 出现错误事件：`worried`
- 正常回复完成：`happy` 或 `neutral`
- 等待模型回复：`thinking`
- 断线：`sleepy` 或 `offline`

后续可增加 Agent 输出中的显式 display metadata，例如：

```json
{
  "display_plan": {
    "version": "1.0",
    "text": "我已经准备好了。",
    "segments": [
      {
        "text": "我已经准备好了。",
        "emotion": "happy",
        "motion": "wave"
      }
    ]
  }
}
```

Live2D capability 仍然保留，但它是底层执行层，不要求 LLM 直接调用。调用者通常是 Desktop pipeline：

```text
DisplayPlan emotion/motion
      │
      ▼
Desktop local mapping
      │
      ▼
desktop.live2d.set_expression / play_motion
```

### 9.5 Live2D 模型形态

常见 Live2D Cubism 运行时模型通常以 `.model3.json` 作为入口文件。这个文件本身不是模型顶点数据，而是模型设置文件，负责引用实际运行所需资源：

| 文件 | 说明 |
|------|------|
| `.model3.json` | 模型入口和设置文件，引用 moc、贴图、物理、动作、表情等 |
| `.moc3` | 程序运行时使用的模型数据 |
| `.png` | texture atlas 贴图 |
| `.physics3.json` | 物理设置，例如头发、衣服摆动 |
| `.motion3.json` | 动作数据，例如 idle、wave、tap、thinking |
| `.exp3.json` | 表情数据，例如 happy、angry、sad |
| `.pose3.json` | 姿势或部件切换相关数据 |
| `.userdata3.json` | 用户数据 |
| `.cdi3.json` | 参数和部件名称等显示信息 |
| `.motionsync3.json` | motion-sync 设置，按模型导出情况可选 |

Desktop App 的模型加载应以 `.model3.json` 为入口。用户选择模型时，应用只需要定位入口文件，然后根据其中的相对路径加载资源。

### 9.6 动作、表情与口型设计

动作和表情需要分层处理：

| 层级 | 来源 | 说明 |
|------|------|------|
| 语义动作 | DisplayPlan | `happy`、`thinking`、`wave`、`nod` 等抽象标签 |
| 模型映射 | Desktop 本地配置 | 把抽象标签映射到当前模型的 expression id / motion group |
| 运行时执行 | Live2D renderer | 播放 `.motion3.json`，应用 `.exp3.json`，更新参数 |

每个模型的动作命名、表情命名不一定一致，所以不能让 LLM 直接输出模型内部 motion 名称。应使用模型无关的语义枚举：

```yaml
emotion_map:
  neutral: ["normal"]
  happy: ["smile", "happy"]
  thinking: ["thinking"]
  worried: ["sad", "worry"]

motion_map:
  idle: { group: "Idle", index: 0 }
  wave: { group: "Gesture", index: 1 }
  nod: { group: "Gesture", index: 2 }
  point: { group: "TapBody", index: 0 }
```

如果某个模型缺少指定动作：

1. 优先使用同类 fallback，例如 `thinking` 找不到就用 `idle`。
2. 表情找不到时保持当前表情或回到 `neutral`。
3. 记录一次可诊断日志，但不要中断 TTS 或消息展示。

口型建议不要由 LLM 控制。更可靠的方案是：

- TTS 生成音频。
- Desktop 在播放音频时获取实时音量或分析音频 envelope。
- 将音量归一化到 `0..1`。
- 写入模型声明的 lip-sync 参数。

大多数 Cubism 模型会在 `.model3.json` 中声明 lip-sync 参数。运行时读取这些参数后，在每帧 `model.update()` 前写入嘴部开合值。若模型没有声明 lip-sync 参数，可尝试识别常见参数如 `ParamMouthOpenY`；仍找不到则禁用口型，不影响动作和表情。

口型实现优先级：

| 优先级 | 方法 | 说明 |
|--------|------|------|
| P0 | 音量驱动嘴部开合 | 简单稳定，适合首版 |
| P1 | TTS 字/词时间戳驱动字幕 | 用于当前句高亮，不直接控制嘴型 |
| P2 | phoneme/viseme 驱动 | 更细致，但依赖 TTS 提供音素或口型数据 |
| P3 | motion 内置 lip-sync | 对特定预制动作有效，不适合任意 LLM 回复 |

### 9.7 用户自定义模型

Desktop App 应支持用户导入自己的 Live2D 模型，但必须做安全和兼容性约束。

导入流程：

```text
用户选择目录或 zip
      │
      ▼
扫描 .model3.json
      │
      ▼
校验资源引用、大小、扩展名、路径安全
      │
      ▼
复制到 app data managed directory
      │
      ▼
生成本地 model manifest
      │
      ▼
预览模型并配置 emotion/motion map
```

允许的资源类型首版建议限制为：

- `.model3.json`
- `.moc3`
- `.png`
- `.physics3.json`
- `.motion3.json`
- `.exp3.json`
- `.pose3.json`
- `.userdata3.json`
- `.cdi3.json`
- `.motionsync3.json`

安全限制：

- 禁止绝对路径和 `..` 路径穿越。
- 禁止符号链接。
- 限制单个文件大小和模型总大小。
- 限制文件数量。
- zip 解压必须防 zip slip。
- 模型资源只从 app data 下的受控目录加载。
- 不执行模型目录里的脚本或未知文件。
- 导入前提示用户确认模型授权和来源可信。

用户模型的本地 manifest 示例：

```json
{
  "id": "user_model_01JZ",
  "name": "Custom Nahida",
  "entry": "models/user_model_01JZ/model.model3.json",
  "source": "user_import",
  "version": "1",
  "emotion_map": {
    "neutral": ["normal"],
    "happy": ["smile"],
    "thinking": ["thinking"],
    "worried": ["sad"]
  },
  "motion_map": {
    "idle": { "group": "Idle", "index": 0 },
    "wave": { "group": "Gesture", "index": 0 }
  },
  "lip_sync": {
    "enabled": true,
    "parameter_ids": ["ParamMouthOpenY"]
  }
}
```

UI 上应提供：

- 导入模型。
- 删除模型。
- 切换当前模型。
- 预览表情。
- 预览动作。
- 配置语义标签到模型动作/表情的映射。
- 检测并提示缺失的动作、表情、贴图或 lip-sync 参数。

## 10. Gateway API 需求

### 10.1 V1 复用现有 API

Desktop Gateway Client 可以先复用：

| API | 用途 |
|-----|------|
| `/api/webui/bootstrap` | 获取 app 名称、认证模式、feature flags |
| `/api/auth/*` | 登录或 session 检查 |
| `/api/status` | 系统状态 |
| `/api/send` | 从 Desktop 发消息给指定 target/session |
| `/api/sessions` | 浏览会话 |
| `/api/events/stream` | 接收实时事件 |

### 10.2 V2 新增 Node API

Gateway-Node 需要新增：

| API | 用途 |
|-----|------|
| `/api/nodes` | 列出已注册/在线节点 |
| `/api/nodes/{node_id}` | 查看节点状态 |
| `/api/nodes/{node_id}/capabilities` | 查看节点能力 |
| `/api/nodes/pairing/start` | 创建配对流程 |
| `/api/nodes/pairing/complete` | 完成配对，签发 node token |
| `/api/nodes/ws` | Gateway-Node WebSocket endpoint |

内部服务建议：

```text
nahida_bot/gateway/services/node_registry.py
nahida_bot/gateway/services/node_auth.py
nahida_bot/gateway/services/node_invoker.py
```

## 11. Capability 模型

Desktop Node 的能力应显式声明。

### 11.1 首批能力

| capability | 方向 | 说明 |
|------------|------|------|
| `desktop.live2d.set_expression` | Gateway -> Desktop | 设置表情 |
| `desktop.live2d.play_motion` | Gateway -> Desktop | 播放动作 |
| `desktop.live2d.set_visibility` | Gateway -> Desktop | 显示/隐藏桌宠 |
| `desktop.live2d.load_model` | Gateway -> Desktop | 切换到已授权的本地模型 |
| `desktop.tts.speak` | Gateway -> Desktop | 播放经过校验的分段语音计划 |
| `desktop.tts.stop` | Gateway -> Desktop | 停止当前 TTS 播放 |
| `desktop.notification.show` | Gateway -> Desktop | 展示系统通知 |
| `desktop.window.focus` | Gateway -> Desktop | 聚焦窗口 |
| `desktop.input.submit` | Desktop -> Gateway | 用户从桌宠输入消息 |
| `desktop.state.updated` | Desktop -> Gateway | 上报窗口、模型、连接状态 |
| `desktop.model.imported` | Desktop -> Gateway | 上报用户导入模型的非敏感元数据 |

### 11.2 权限策略

每个 capability 至少包含：

```json
{
  "name": "desktop.notification.show",
  "version": "1.0",
  "direction": "gateway_to_node",
  "risk": "low",
  "requires_user_approval": false
}
```

高风险能力必须额外确认，例如：

- 读取本机文件
- 写入本机文件
- 执行命令
- 录音、截屏、摄像头
- 全局键盘监听

V1 不开放这些高风险能力。

## 12. 安全设计

### 12.1 默认网络边界

- 本地开发和个人使用默认只监听 `127.0.0.1`。
- 远程 Gateway 必须要求 HTTPS/WSS 或明确配置为不安全模式。
- Gateway CORS 不应长期保持生产环境 `*`，Desktop/WebUI 发布时需收紧 origin 或改用专用 token 流程。

### 12.2 Token 存储

- Desktop App 不应把长期 token 明文写入普通 JSON/YAML。
- Tauri side 应使用系统安全存储或平台 keychain。
- token 支持撤销、过期和重新配对。

### 12.3 Capability 权限

- Node 注册 capability 后，Gateway 不应默认信任全部能力。
- Gateway 应保存授权策略：哪些 user/session/plugin 可以调用哪些 node capability。
- 所有 `capability.invoke` 应写入 audit log。
- Node 端也应二次校验本地允许列表，不能只依赖 Gateway。

### 12.4 WebView 安全

- Live2D 资源尽量从本地受控目录加载。
- 不允许未验证远程脚本注入 WebView。
- 设置合理 CSP。
- 模型资源和动作文件需要注意授权，不应默认把受限制 Live2D 模型提交到公开仓库。

### 12.5 用户模型导入安全

- 只允许导入模型资源文件，不执行任何脚本。
- zip 解压前后都要校验路径，防止 zip slip。
- 拒绝绝对路径、`..`、符号链接和特殊文件。
- 限制模型目录总大小、文件数量和单文件大小。
- 导入后复制到 Desktop App 管理的 app data 目录，不直接从任意用户目录长期加载。
- 模型 manifest 只保存必要相对路径和用户配置，不保存不必要的本机隐私路径。
- Desktop App 只向 Gateway 上报模型 ID、名称、能力摘要，不上传模型文件本体。

### 12.6 DisplayPlan 安全

- DisplayPlan 只允许语义枚举和 TTS 参数，不允许任意 capability 名称。
- 所有 display metadata 在进入 Desktop pipeline 前必须 schema 校验。
- 非法标签、过长文本、过多 segments 必须降级为纯文本。
- 语音、动作、表情映射由 Desktop 本地配置决定，LLM 不能直接指定模型内部文件路径。
- 对普通 Channel 输出时必须剥离所有控制标签，避免泄漏给 Telegram、Milky 等平台。

## 13. 状态与持久化

### 13.1 Desktop 本地状态

Desktop App 需要保存：

| 字段 | 说明 |
|------|------|
| `gateway_url` | Gateway 地址 |
| `node_id` | Desktop Node ID |
| `node_token` | 长期 token，安全存储 |
| `last_session_id` | 最近交互 session |
| `live2d_model_path` | 当前模型路径 |
| `live2d_models` | 已导入模型 manifest 列表 |
| `display_mapping` | 语义 emotion/motion 到当前模型资源的映射 |
| `tts_settings` | TTS voice、speed、pitch、音量等用户偏好 |
| `window_state` | 位置、大小、置顶、透明度 |
| `preferences` | 用户设置 |

### 13.2 Gateway 节点状态

Gateway 需要保存或维护：

| 字段 | 说明 |
|------|------|
| `node_id` | 节点 ID |
| `display_name` | 展示名称 |
| `node_type` | `desktop` / `worker` / `tool-host` |
| `online` | 当前连接状态 |
| `last_seen_at` | 最近心跳时间 |
| `capabilities` | 能力列表 |
| `auth_scope` | 授权范围 |
| `metadata` | 平台、版本等 |
| `desktop_state` | Desktop 上报的模型 ID、TTS 状态、窗口状态摘要 |

## 14. 失败处理

| 场景 | 期望行为 |
|------|----------|
| Gateway 未启动 | Desktop 显示 disconnected，提供重连或配置入口 |
| token 失效 | Desktop 进入 auth required 状态，引导重新配对 |
| WebSocket 断线 | 自动重连，退避上限 30s |
| capability 调用超时 | Gateway 返回 timeout error，写 audit log |
| Live2D 渲染失败 | UI 退回简化状态，不影响 Gateway 连接 |
| 用户模型缺少动作 | fallback 到 idle 或 neutral，提示用户配置映射 |
| 用户模型缺少 lip-sync 参数 | 禁用口型，TTS 和字幕继续工作 |
| DisplayPlan 解析失败 | 降级为纯文本回复，不执行动作 |
| TTS 合成失败 | 显示文本并播放默认表情，不阻塞消息 |
| Node 重复连接 | Gateway 按 node_id 策略踢旧连接或拒绝新连接 |

## 15. 测试策略

### 15.1 协议测试

- Python Pydantic models parse 所有 fixtures。
- Rust serde models parse 同一批 fixtures。
- Python/Rust 都能序列化出符合 schema 的 envelope。
- request/response ID 匹配、错误响应、未知字段兼容性均有测试。

### 15.2 Gateway 测试

- WebSocket 鉴权成功/失败。
- node.register 注册 capability。
- heartbeat 超时断开。
- capability.invoke 成功、失败、超时。
- node offline 后 Gateway 不再调用该节点。

### 15.3 Desktop 测试

- mock Gateway 下连接、鉴权、注册、重连。
- Gateway event 能转成 Tauri event。
- WebView 收到事件后能更新 Live2D 状态机。
- token 不落普通明文配置。
- DisplayPlan 能解析为干净文本和 metadata。
- DisplayPlan 非法枚举、过多 segments、未知字段能安全降级。

### 15.4 视觉与交互测试

- 桌宠透明窗口不遮挡核心 UI。
- 不同分辨率和缩放比例下 Live2D 位置正确。
- 断线、思考、回复、错误状态都有可见反馈。
- WebGL canvas 非空，模型加载失败时有降级 UI。
- 用户导入模型后能预览表情和动作。
- 缺失 lip-sync 参数的模型不会导致播放崩溃。
- TTS 播放时口型参数有可见变化。

## 16. 实施路线

### Phase 0：设计与协议草案

- [ ] 新增 `docs/architecture/gateway-node-protocol.md`。
- [ ] 定义 envelope、错误码、认证、心跳、注册、capability 调用。
- [ ] 增加 `tests/fixtures/gateway_node/*.json`。
- [ ] Python 侧 Pydantic models 能 parse fixtures。

### Phase 1：Desktop Gateway Client

- [ ] 新增 `desktop/` Tauri 工程。
- [ ] 连接现有 Gateway REST API。
- [ ] 接入 `/api/events/stream` 或 WebSocket event bridge。
- [ ] 实现 Gateway 地址配置、登录/token 保存。
- [ ] 实现最小 Live2D 渲染和状态映射。
- [ ] 实现用户模型导入、模型预览和当前模型切换。
- [ ] 实现首版 emotion/motion 语义映射配置。

### Phase 2：Gateway-Node WebSocket 基线

- [ ] 新增 `/api/nodes/ws`。
- [ ] 实现 node auth、node.register、heartbeat。
- [ ] Gateway 维护在线节点 registry。
- [ ] Desktop Rust side 实现 WebSocket client。
- [ ] Rust side 通过 fixtures 与 Python 协议对齐。

### Phase 3：Desktop Capability

- [ ] Desktop 注册 Live2D capability。
- [ ] Gateway 调用 `desktop.live2d.set_expression`。
- [ ] Gateway 调用 `desktop.live2d.play_motion`。
- [ ] Desktop 注册 TTS capability。
- [ ] Gateway 或 Desktop pipeline 能提交分段 TTS 播放计划。
- [ ] 调用链路具备超时、错误、审计。
- [ ] Desktop 本地允许列表生效。

### Phase 3.5：DisplayPlan 与 TTS/Live2D 表现层

- [ ] 定义 `DisplayPlan` schema。
- [ ] Agent 输出或后处理器能生成 `DisplayPlan`。
- [ ] Router 将 `DisplayPlan` 拆为 `OutboundMessage.text` 与 `metadata.display_plan`。
- [ ] 普通 Channel 只收到干净文本。
- [ ] Desktop Channel 消费 `display_plan`，驱动 TTS、字幕、表情和动作。
- [ ] TTS 音频播放时驱动 lip-sync 参数。
- [ ] DisplayPlan 解析失败时降级为纯文本。

### Phase 4：产品化与发布

- [ ] 托盘、透明窗口、置顶、拖拽。
- [ ] 配对流程。
- [ ] token 撤销与重新登录。
- [ ] Live2D 模型管理。
- [ ] TTS voice 管理。
- [ ] 用户模型 manifest 导出/导入。
- [ ] Windows/macOS/Linux 打包验证。

## 17. 拆仓库时机

当前不建议拆仓库。满足以下条件后可重新评估：

- Gateway-Node 协议已稳定并承诺向后兼容。
- Desktop App 有独立发布周期和独立维护团队。
- Live2D 模型、二进制产物或平台打包资源显著膨胀。
- 需要为 Desktop App 建立独立 issue、release、签名和分发流程。
- 外部项目需要只依赖 desktop/node SDK，而不关心 Nahida Bot core。

即使拆仓库，也不应通过 FFI 共享 Python 实现。拆分后仍应通过 Gateway-Node 协议、schema fixtures 和版本号维护兼容性。

## 18. 待决问题

- Desktop App 首版是否需要内置聊天输入，还是只做 Live2D 状态和通知。
- Gateway 是否需要为 Desktop 单独提供 WebSocket event stream，替代 SSE 的鉴权限制。
- Live2D 情绪应该由 Agent 显式输出 DisplayPlan，还是由 Desktop 本地规则推导。
- DisplayPlan 应由主 LLM 直接输出，还是由回复后处理器二次生成。
- TTS 首版使用本地引擎、系统语音，还是外部 Provider。
- TTS 时间戳、phoneme、viseme 数据是否需要纳入统一 schema。
- 用户模型是否允许从远程 URL 安装，还是只允许本地文件导入。
- 用户模型 manifest 是否需要支持分享，但不包含模型资源本体。
- Desktop Node capability 是否允许被插件调用，还是只允许 core/system 调用。
- 是否需要支持一个 Gateway 同时连接多个 Desktop Node。
- 是否需要支持 Desktop App 启动、停止或管理本地 Nahida Bot 进程。

## 19. 参考资料

- [Live2D Cubism Editor Manual: File Types and Extensions](https://docs.live2d.com/en/cubism-editor-manual/file-type-and-extension/)：说明 `.model3.json`、`.moc3`、`.motion3.json`、`.exp3.json` 等文件角色。
- [Live2D Cubism SDK Manual: About Models (Web)](https://docs.live2d.com/en/cubism-sdk-manual/model-web/)：说明 Web 场景从 `.model3.json` 加载模型、贴图和 renderer 的基本流程。
- [Live2D Cubism SDK Manual: Lip-sync](https://docs.live2d.com/en/cubism-sdk-manual/lipsync/)：说明 lip-sync 参数如何从 `.model3.json` 获取，以及通过音量值或 motion 驱动嘴部开合。

## 20. 结论

Desktop App 应先作为当前 monorepo 下的独立 Tauri 应用存在，通过公开 Gateway API 接入 Nahida Bot。Gateway-Node 协议应定义为语言无关的 JSON over WebSocket 协议，Python 和 Rust 各实现一份轻量 SDK，通过 fixtures 和 contract tests 保持一致。

Rust/Tauri 端不应通过 FFI 调 Python。Desktop App 与 Python core 的边界就是 Gateway 协议。这样可以保留分布式架构的清晰边界，也能为未来 Python node、Rust desktop node 和其他语言 node 提供一致扩展路径。

长消息、TTS 和 Live2D 表现不应依赖 LLM 逐句调用工具。更合适的方式是让 LLM 或后处理器生成 DisplayPlan，由 Gateway/Router 保留干净文本，并将表现 metadata 交给 Desktop pipeline 消费。Live2D capability 继续作为底层执行能力存在，但它的调用通常来自解析后的表现流水线，而不是直接来自 LLM。
