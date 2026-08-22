# Desktop App 与 Gateway-Node 设计

> 记录时间：2026-06-07
> 状态：部分落地（Desktop App 已接入 Gateway Node 协议、TTS、配对与远控；Live2D 本地渲染与动作智能见 [live2d-motion-intelligence.md](live2d-motion-intelligence.md)）
> 相关文档：
>
> - [webui-design.md](webui-design.md) — 现有 Gateway + WebUI 运维面板设计
> - [cross-session-messaging.md](cross-session-messaging.md) — 跨会话消息能力
> - [memory-scoping.md](memory-scoping.md) — 会话与记忆作用域隔离
> - [../architecture/directory-structure.md](../architecture/directory-structure.md) — 当前仓库模块边界
> - [../architecture/gateway-node-protocol.md](../architecture/gateway-node-protocol.md) — Gateway-Node 协议

## 1. 背景

Nahida Bot 当前已经具备 Python core、FastAPI Gateway、Vue WebUI、插件系统和多 Channel 接入。后续计划增加一个 Desktop App，作为常驻桌面端入口，与 Nahida Bot 建立连接，并通过 Live2D 实现桌宠形态。

Desktop App 的产品形态不是 WebUI 的运维面板，也不是单纯的 Live2D 调试器。首版应更接近一个边缘隐藏式桌面助手：平时贴在屏幕角落或边缘，只露出一个小的提示部件；当鼠标靠近、Gateway 推送通知、CRON 消息到达、番茄钟到点等事件发生时，再从屏幕边缘唤出 Live2D、气泡文本和轻量输入框。

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
- 提供边缘隐藏、鼠标靠近唤出、通知唤出、气泡对话和轻量输入体验，而不是默认显示完整控制台。
- 支持 Desktop 本地轻量功能，例如番茄钟提醒；更高风险的摄像头/OpenCV 久坐提醒应作为后续可选能力单独授权。
- 实施顺序上优先完成纯本地 Live2D 桌宠闭环，再接入 Gateway Client 和 Gateway-Node，降低与 Gateway 分支并行开发时的冲突。

### 2.2 非目标

- 不在 V1 中把 Nahida Bot Python runtime 打包进 Desktop App。
- 不让 Tauri 端直接 import 或 FFI 调用 Python core。
- 不把 Desktop App 做成 WebUI 的换皮版本。WebUI 是运维面板，Desktop App 是常驻交互端。
- 不在协议未稳定前引入复杂二进制协议、protobuf 或跨语言代码生成流水线。
- 本机高权限执行能力（读取本地文件、执行命令）已在「远程控制模式」中实现，
  但**默认关闭**，且必须显式授权、可审计（详见 [10.3 远程控制模式](#103-远程控制模式)）。
- 不在首版承诺任意第三方 Live2D 模型自动具备挥手、指向、特殊姿态等动作；缺失贴图、ArtMesh 或参数时只能降级表现。
- 不实现逐帧 Live2D 参数曲线编辑器。Desktop 只提供语义映射、预览和少量校准工具，不复刻 Cubism Editor。

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

Desktop App 有三个阶段性的角色。前两个是产品可用路径，第三个是长期架构形态：

| 阶段 | 角色 | 通信方式 | 说明 |
|------|------|----------|------|
| Local | Desktop Local Runtime | Mock/local events | 不连接 Gateway，先完成 Live2D、贴边窗口、气泡、TTS、番茄钟和配置体验 |
| V1 | Gateway Client | REST + SSE | 连接已有 Gateway，发送消息、读取状态、接收实时事件，驱动边缘桌宠与 Live2D 表现 |
| V2 | Desktop Node | WebSocket Gateway-Node | 注册节点能力，接受 Gateway 调用，向 Gateway 上报本机状态和桌宠事件 |

开发顺序应先做 Local，再接 V1 Gateway Client，最后升级 V2 Desktop Node。这样 Live2D 表现、窗口状态机、模型映射和本地提醒都能在一个分支里独立收敛；Gateway 接入只作为事件源和消息发送适配层接进来，避免在桌宠体验还不稳定时同时修改 Gateway-Node 协议导致频繁 merge。

V1 不需要等待完整 Gateway-Node 实现，可以复用现有 Gateway 能力形成真实消息体验。V2 再将桌宠能力纳入统一 node capability 体系。

### 4.1 首版产品形态

首版产品目标应围绕一个常驻但低打扰的桌面助手，而不是全屏或常显控制面板：

```text
hidden at edge
      │
      ├── mouse near edge / tray action
      ├── Gateway notification / CRON delivery
      ├── local pomodoro timer
      ▼
peek / emerge animation
      │
      ▼
Live2D + speech bubble + compact input
      │
      ├── optional TTS playback
      ├── lip-sync parameter animation
      ├── DisplayPlan expression/motion
      └── send user reply into current session
      │
      ▼
auto retreat / stay pinned by user
```

建议首版固定默认模型和默认窗口位置，先把“贴边隐藏、唤出、通知气泡、TTS/口型、输入框回复同一 session”做通。当前 Live2D Debug、Expression Map 和 Motion Map 属于设置/调试能力，应退到配置界面，而不是主体验第一屏。

Local 阶段不应等待 Gateway。它需要提供一套本地事件源和 mock 回复能力，让以下体验可以独立调通：

- 手动触发通知、回复、错误、思考中、TTS 播放等状态。
- 本地番茄钟触发提醒。
- 直接输入一段文本或 DisplayPlan，验证气泡、TTS、口型、expression、motion 和收回逻辑。
- 用 Debug/Mapping 面板调整模型表现，但主体验仍然是边缘桌宠。

Gateway Client 后续只需要把 Gateway 事件转换成同一种 `DesktopEvent`，并把气泡输入框里的用户回复发送回当前 session。Live2D renderer、pet window 状态机和 TTS pipeline 不应直接依赖 Gateway API。

本地功能可以分层处理：

| 功能 | 首版策略 |
|------|----------|
| Gateway 消息通知 | 触发唤出、气泡、可选 TTS |
| CRON 消息投送 | 作为 Gateway/插件事件进入同一通知 pipeline |
| 番茄钟 | Desktop 本地轻量功能，可直接触发桌宠提醒 |
| 久坐/摄像头提醒 | 后置能力，需要显式权限、隐私提示和可关闭设置 |

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
- 托盘、窗口置顶、透明 pet window、贴边隐藏、拖拽、全局快捷键。
- pet window 的点击穿透、交互模式、鼠标靠近唤出、自动收回。
- 系统通知。
- 本地轻量定时器，例如番茄钟。
- 本地配置和安全存储。
- 将 Gateway 事件转发给 WebView。
- 将 WebView 操作转为 Gateway request。

### 9.2 WebView UI 层

WebView frontend 负责：

- Live2D WebGL 渲染。
- 桌宠状态机：hidden、peek、emerging、idle、thinking、speaking、chat、retreating、error、disconnected。
- 贴边提示部件、气泡文本、紧凑输入框或快捷命令。
- 连接状态、当前 session、消息提示。
- 表情和动作映射。
- TTS 播放状态、字幕分段和口型驱动。
- 模型调试与映射配置 UI：表情预览、动作预览、语义标签映射。
- 渲染性能模式 UI：省电、平衡、活跃。
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
      ├── 选择远程 SpeechArtifact 或本地 fallback
      ├── 音频分段播放、队列、打断和暂停
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
      "expression": "star",
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

- LLM 只输出语义层标签，例如 `emotion="happy"`、`expression="star"`、`motion="nod"`、`voice.style="calm"`。
- LLM 不允许输出文件路径、shell 命令、原始 capability 名称或任意执行参数。
- parser 使用严格 schema 校验，未知字段和非法枚举丢弃或降级。
- `OutboundMessage.text` 始终是干净文本，不包含控制标签。
- 非 Desktop Channel 忽略 `display_plan`，继续只发送纯文本。
- Desktop Channel 可以使用 `display_plan`，但必须在本地做 capability allowlist 校验。

当前前端 mock 落地约定：

- mock 阶段不连接 Gateway，由前端面板直接输入 mock LLM 返回结果。
- 输入可以是纯文本、完整 `DisplayPlan` JSON、`metadata.display_plan` 包装结构，或 provider envelope 中的 `choices[0].message.content`。
- 外部 wire format 优先使用 snake_case，例如 `pause_after_ms`；前端内部状态统一为 camelCase，例如 `pauseAfterMs`。
- `emotion` 保持少量内置基础状态；`expression` 是可由用户在 Expression Map 面板维护的 DisplayPlan 关键词，用来映射当前模型的具体 expression。
- `motion` 通过 Motion Map 面板映射到模型原生 `.motion3.json`、Base 参数动作或 None；缺失或播放失败时可降级到常见 Live2D 参数动作。
- 当前模型的 `DisplayPlan keyword -> expression` 映射由前端 Expression Map 面板维护，mock 阶段保存到本地浏览器存储；后续 Tauri 版本迁移到 app data 配置。
- 当前模型的 `DisplayPlan motion -> model/base/none` 映射由前端 Motion Map 面板维护，mock 阶段保存到本地浏览器存储；后续 Tauri 版本迁移到 app data 配置。
- 解析失败或非法枚举时降级为纯文本 + `neutral` 表情，不阻塞字幕和 transcript。

真实 Gateway 接入时不建议强制主 Agent 把用户可见回复直接写成 JSON。更稳妥的边界是：

- 主 Agent 的 assistant message 保持自然语言，进入 transcript、memory 和普通 Channel 的都是干净文本。
- DisplayPlan 由回复后处理器、规则推导器或独立轻量 planner 生成，并作为 `OutboundMessage.metadata.display_plan` 附着。
- 如果为了节省一次模型调用而让主 Agent 同时产出结构化结果，也应在 router 边界立即拆成 `text` 与 `metadata.display_plan`，不要把 JSON envelope 当作对话正文长期存入记忆。

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

#### 9.3.1 统一 TTS Provider 与音频分发边界

高质量 TTS 通常依赖 GPT-SoVITS、IndexTTS 或云厂商 Few Shot TTS。这些 Provider 的鉴权、音色管理、请求参数、重试、格式转换和错误处理不应在 Python backend 与 Desktop 各实现一遍。统一边界如下：

```text
DisplayPlan / Channel voice request
              │
              ▼
Python SpeechService
              │
              ├── TtsProvider registry
              │     ├── GPT-SoVITS
              │     ├── IndexTTS
              │     └── cloud TTS providers
              │
              ▼
SpeechArtifactStore
              │
              ├── Channel 读取本地 artifact 并发送语音
              └── Gateway Media API 暴露受鉴权的 HTTP 下载地址
                                    │
                                    ▼
                         Desktop AudioPlaybackAdapter
```

设计决策：

- 高质量 `TtsProvider` 只在 Python backend 适配一次，Desktop 不直接持有 Provider API key，也不理解 GPT-SoVITS、IndexTTS 或云厂商私有协议。
- Desktop 负责下载、播放、停止、打断、字幕同步和 lip-sync；系统语音或 Web Speech 只作为本地开发与离线 fallback。
- Gateway-Node WebSocket 只传 `SpeechArtifactRef` 和播放控制消息，不传 base64 或原始音频二进制。
- `SpeechArtifactRef.download_url` 必须指向 Desktop 当前连接的 Gateway Media API，不能直接暴露内部 TTS Provider URL。
- GPU TTS 服务可以位于无公网 IP 的机器上。Gateway 通过 SSH tunnel、FRP 或其他私网链路调用它，生成后把音频拉取并缓存到 Gateway 可管理的 artifact store。
- Desktop 和 Channel 复用同一个合成 artifact，避免对相同文本、音色和参数重复合成。
- Gateway 下载接口使用流式文件响应，不把完整音频读入内存；缓存具有 TTL、容量上限和清理策略。
- 如果后续规模增大，可以把 artifact store 替换为 S3、R2、OSS 等对象存储，并由 Gateway 签发短期下载地址；首版使用 Gateway 本地磁盘缓存即可。

建议的统一请求与引用：

```json
{
  "text": "今天的计划已经整理好了。",
  "profile": "nahida-default",
  "style": "bright",
  "speed": 1.0,
  "pitch": 0,
  "output_formats": ["audio/ogg", "audio/mpeg"]
}
```

```json
{
  "artifact_id": "speech_abc123",
  "download_url": "/api/media/speech/speech_abc123",
  "mime_type": "audio/ogg",
  "duration_ms": 8420,
  "size_bytes": 126400,
  "expires_at": "2026-06-13T00:00:00Z",
  "alignment": null
}
```

缓存键至少包含规范化文本、Provider、voice/profile、参考音频版本、style、speed、pitch、输出 codec 和 Provider 配置版本。`artifact_id` 是不透明 ID，不能包含服务器文件路径。

首版允许等待完整音频生成后再播放。当前产品可以接受秒级到几十秒的 TTS 延迟，因此不需要为了流式首包延迟过早引入音频 chunk 协议。文本和气泡应立即显示，音频准备完成后再进入 speaking 状态；合成失败只降级为字幕。

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

- Python SpeechService 生成音频，或 Desktop 使用本地 fallback 语音。
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

用户自定义模型应作为可选扩展能力，而不是首版主体验的前置条件。首版建议先固定默认模型，把贴边隐藏、唤出、通知、气泡、TTS 和输入框体验做完整；用户导入模型可以在后续版本加入。

兼容性边界必须明确：Desktop App 可以帮助用户加载模型、预览表情和动作、配置 expression/motion/lip-sync 映射，但不能让模型拥有作者没有制作的贴图、ArtMesh、参数或 motion。第三方模型如果要完整适配 Nahida Desktop，需要模型作者或用户自行提供相应动作、表情、参数命名或映射配置。

因此用户模型支持采取 **best-effort** 策略：

| 能力 | 策略 |
|------|------|
| 加载模型 | 支持标准 Cubism `.model3.json` 入口 |
| 表情 | 扫描 `.exp3.json`，用户通过 Expression Map 映射语义关键词 |
| 动作 | 扫描 `.motion3.json`，用户通过 Motion Map 映射语义动作 |
| Base 动作 | 使用常见参数做轻量降级，例如点头、提示、说话姿态 |
| 口型 | 优先读 lip-sync 声明，再尝试 `ParamMouthOpenY` 等常见参数 |
| 缺失能力 | 明确提示，不自动生成缺失贴图或复杂动作 |

不应把参数曲线编辑做成逐帧编辑器。可接受的校准粒度是：参数选择、强度、方向、时长、默认缩放和位置。逐帧曲线、复杂部件变形和贴图制作仍应交给 Cubism Editor 或模型作者处理。

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
    "idle": { "source": "model", "group": "Idle", "index": 0 },
    "wave": { "source": "model", "group": "Gesture", "index": 0 },
    "nod": { "source": "procedural", "motion": "nod" },
    "point": { "source": "none" }
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
- 配置 DisplayPlan keyword 到模型 expression 的映射。
- 配置 DisplayPlan motion 到模型原生 motion、Base 动作或 None 的映射。
- 校准口型参数、默认缩放、默认位置和贴边露出区域。
- 检测并提示缺失的动作、表情、贴图或 lip-sync 参数。

### 9.8 性能预算与渲染策略

Desktop App 是常驻应用，Live2D/WebGL 渲染必须按低功耗目标设计。Tauri 本身不是主要性能风险，主要风险是 WebGL/Live2D 常驻 60fps、复杂模型和透明窗口合成。

首版建议设定性能目标：

| 状态 | 目标 |
|------|------|
| idle CPU | 尽量低于 1-3% |
| idle GPU | 接近 0，只做低频刷新 |
| 常驻内存 | 先控制在 150-250MB 内 |
| speaking/active | 允许短时升高 |
| hidden/minimized/locked | 暂停渲染 |

渲染频率策略：

| 模式 | 目标帧率 | 触发条件 |
|------|----------|----------|
| `suspended` | 0fps | 窗口隐藏、最小化、锁屏、用户暂停 |
| `idle` | 10-15fps | 桌宠空闲、无 TTS、无动作 |
| `speaking` | 24-30fps | TTS 播放、口型同步 |
| `active` | 60fps | 拖拽、交互、短动作播放 |

实现约束：

- 不要永久无条件 `requestAnimationFrame`。
- idle 状态降频或仅在物理/动作需要更新时渲染。
- TTS 停止后回到 `idle`。
- canvas 分辨率限制 `devicePixelRatio`，建议首版 cap 到 `1.5`。
- WebGL context 默认使用低功耗配置：

```ts
{
  alpha: true,
  antialias: false,
  powerPreference: "low-power"
}
```

- 用户导入模型时限制贴图尺寸、文件数量、模型总大小。
- 模型切换时释放旧 texture、motion、audio analyzer 和 WebGL resource。
- 透明窗口避免大面积 blur、动态阴影和复杂 CSS filter。
- 提供省电模式：禁用物理、降低 fps、禁用高频 idle 动作。

### 9.9 Renderer 技术路线

首版主线仍然使用 **Tauri + WebView + WebGL Live2D**。

不建议现在切换到全 C++ app，原因：

- 非桌宠 UI 会失去 Web 前端复用能力。
- 跨平台 UI、设置页、模型管理、登录配对会显著增加成本。
- Native renderer 的窗口、透明、DPI、点击穿透和打包复杂度更高。
- 当前还没有真实性能数据证明 WebGL 路线不可接受。

也不建议在首版通过 C++ FFI 让 WebView 直接接 Live2D Native SDK。Tauri 前端不能直接 FFI C++；可行路径是 Rust side 链接 Native SDK，再通过 Tauri IPC 暴露能力。真正困难的是 native renderer 的绘制 surface：如果 C++ Live2D 渲染到独立 OpenGL/DirectX/Metal 上下文，要和 WebView 共享窗口或纹理并不自然。

推荐保留可替换 renderer 接口：

```ts
interface Live2DRenderer {
  loadModel(manifest: Live2DModelManifest): Promise<void>;
  setExpression(emotion: DisplayEmotion): void;
  playMotion(motion: DisplayMotion): void;
  setLipSync(value: number): void;
  setFpsMode(mode: "suspended" | "idle" | "speaking" | "active"): void;
  dispose(): void;
}
```

首版实现：

```text
WebView UI
  └── WebLive2DRenderer
        └── Live2D Web SDK / WebGL
```

性能不达标时再实验：

```text
WebView UI
  └── Tauri IPC
        └── Rust native renderer bridge
              └── Live2D Native SDK / C API
```

Native/C++ 路线只作为后续性能兜底，不作为首版主线。

### 9.10 透明 pet window 与点击穿透

Desktop App 应拆成两类窗口：

| 窗口 | 说明 |
|------|------|
| `main` | 普通设置/聊天/模型管理 UI，可不透明 |
| `pet` | 透明、无边框、置顶，只渲染 Live2D 桌宠 |

`pet` window 的目标配置：

```json
{
  "label": "pet",
  "transparent": true,
  "decorations": false,
  "alwaysOnTop": true,
  "skipTaskbar": true,
  "resizable": false
}
```

透明视觉比较容易：Tauri window 透明，WebView 背景透明，WebGL canvas 使用 alpha channel，renderer 清屏 alpha 为 0。

点击穿透需要 native window 能力。CSS `pointer-events: none` 只能影响 WebView 内部事件，不能让点击穿透到操作系统下面的窗口。Tauri 的 `setIgnoreCursorEvents(true)` 可以让整个窗口忽略鼠标事件，但它不是按透明像素做自动 hit-test。

`pet` window 的默认产品状态不是完整显示，而是贴边隐藏：

```text
hidden
  - 窗口贴在屏幕右下角或用户选择的边缘
  - 只露出小叶子、发饰、气泡点或其它轻量提示部件
  - 低 fps 或暂停 Live2D 主渲染

peek
  - 鼠标靠近边缘、托盘点击、快捷键或事件触发
  - 短动画露出提示部件和小气泡

emerged
  - Live2D 主体从边缘滑出
  - 显示气泡文本、TTS/口型、DisplayPlan 表情和动作

chat
  - 用户点击气泡或输入框后进入交互模式
  - 当前消息发送到关联 session
  - 暂时关闭 click-through，允许输入和拖拽

retreat
  - TTS 结束、气泡超时、失焦或用户关闭后收回边缘
  - 回到 hidden 或 peek
```

通知 pipeline 统一处理 Gateway 事件、本地番茄钟和后续其它本地提醒：

```text
DesktopEvent
  ├── gateway message / CRON delivery（agent.message.* 事件）
  ├── scheduler notification（notification.reminder / notification.error 事件）
  ├── local pomodoro timer（notification.reminder，带 dedupeKey）
  └── future posture reminder
        │
        ▼
Notification presentation plan
        │
        ├── bubble text
        ├── optional TTS
        ├── display_plan
        └── target session
        │
        ▼
pet window emerge + speak + compact reply box
```

已实现的通知通路：

- **Scheduler → Node**：`SchedulerService` 发布 `SchedulerNotification`
  （`level: "reminder"|"error"`），`NodeEventBridge` 映射为 node 协议事件
  `notification.reminder` / `notification.error`，只路由到绑定该会话的节点。
- **CRON 投送**：Agent 在 CRON 运行中可调用 `desktop_announce` 工具直接
  在桌宠上播报（见 10.3）。
- **本地番茄钟**：`pomodoroService` 完全本地运行（work/break 阶段），到点
  触发带 `dedupeKey` 的 `notification.reminder` DesktopEvent，进入同一展示
  pipeline；设置持久化到本地配置，UI 在 `PomodoroSettingsPanel`。
- **通知队列**：`speechPlaybackCoordinator` 支持 replace/queue 打断语义，
  桌面 store 维护 `pendingAfterEmerge` 动作队列；TTS 预载由
  `preloadSegments` + Gateway blob 缓存完成。TTS 来源可在
  `system | gateway | auto` 之间选择。

首版采用 **方案 A：整窗穿透 + 手动交互模式**：

```text
默认状态：
  pet window setIgnoreCursorEvents(true)
  用户可以点击桌宠背后的窗口

进入交互模式：
  通过托盘、快捷键、设置页或显式命令触发
  pet window setIgnoreCursorEvents(false)
  用户可以拖拽桌宠、点击模型、打开快捷菜单

退出交互模式：
  超时、快捷键、菜单操作或窗口失焦
  pet window 回到 click-through
```

首版不做 per-pixel hit-test。后续可选升级：

| 方案 | 说明 | 复杂度 |
|------|------|--------|
| B | 模型外接矩形命中，鼠标进入大概范围时关闭穿透 | 中 |
| C | 基于 Live2D drawable/hit area/alpha mask 的不规则命中 | 高 |

方案 C 需要 Rust/native 层追踪全局鼠标位置，因为窗口处于 click-through 后 WebView 收不到 `mousemove`。该方案后置。

### 9.11 本地优先运行时边界

为了避免 Desktop 与 Gateway-Node 分支互相阻塞，Desktop 前几轮实现应把 Gateway 看成可插拔事件源，而不是核心依赖。推荐内部先形成下面这条本地 pipeline：

```text
Local trigger / mock DisplayPlan / pomodoro
      │
      ▼
DesktopEvent
      │
      ▼
PresentationPlanner
      │
      ▼
PetRuntime state machine
      │
      ├── pet window position / hidden / peek / emerged / retreat
      ├── bubble text / compact input
      ├── TTS playback / lip-sync
      └── Live2D expression / motion / render mode
```

核心模块边界建议如下：

| 模块 | 职责 |
|------|------|
| `DesktopEventBus` | 接收本地 mock、番茄钟、用户输入和后续 Gateway 事件 |
| `PresentationPlanner` | 把事件转换成气泡、TTS、DisplayPlan、目标 session 和打断策略 |
| `PetRuntime` | 管理 hidden、peek、emerging、speaking、chat、retreating 等状态 |
| `Live2DPresentationController` | 统一调度 expression、motion、lip-sync、idle 和渲染模式 |
| `ModelMappingStore` | 保存当前模型的 expression map、motion map、口型参数、缩放和贴边露出配置 |
| `SpeechPlaybackCoordinator` | 串行执行 segment、pause、replace/queue、停止与失败降级 |
| `AudioPlaybackAdapter` | 播放 Gateway `SpeechArtifactRef`；后续可接 Web Audio 或 Tauri native audio |
| `SystemSpeechAdapter` | Phase 3 本地开发与离线 fallback，可使用 Web Speech，失败时只显示字幕 |
| `GatewayEventAdapter` | 后续把 Gateway REST/SSE/WebSocket 事件转换成同一种 `DesktopEvent` |

这样可以先在纯本地环境完成可视表现、窗口行为和调试工具，再把真实 Gateway 消息接入同一入口。Gateway 接入不应修改 Live2D renderer 的核心逻辑，也不应让 pet window 状态机直接读取 Gateway store。

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
| `/api/speech/jobs` | 提交统一 TTS 合成任务并获取 job/artifact 状态 |
| `/api/media/speech/{artifact_id}` | 从 Gateway 下载受鉴权的缓存音频 |
| `/api/pomodoro/reminders` | 用任务模型生成一句番茄钟提醒文案，并可选预热 TTS 缓存 |

`/api/speech/jobs` 与 `/api/media/speech/{artifact_id}` 已实现：

- `POST /api/speech/jobs`：请求体 `{text`（必填）`, voice, text_lang, style,
  speed, pitch, output_format}`；同步合成，缓存命中时幂等返回同一 artifact。
  响应 `{artifact_id, download_url, mime_type, size_bytes, duration_ms, voice,
  provider, expires_at}`。文本超 `max_text_length` 返回 422，`TtsError`
  返回 502，`webapi.speech` 未启用返回 503。
- `GET /api/media/speech/{artifact_id}`：流式返回音频，`artifact_id` 含 `/`
  或 `\` 时拒绝；缺失/过期/被淘汰返回 404，响应头带 `X-Artifact-Expires`。
- `SpeechArtifactStore` 按 SHA-256（text/voice/provider/style/speed/pitch/
  format/config_version）内容寻址缓存，TTL + 按字节 LRU 淘汰，`artifact_id`
  不透明。

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

已实现（Desktop 设置页「Gateway 连接」自服务配对，替代早期复用 WebUI
session 的方案）：

- 路径 A：直接粘贴长期 node token（`nt_...` 前缀）。
- 路径 B：输入 admin bearer → `POST /api/nodes/pairing/start` 签发一次性
  `np_...` 配对 token（单次使用、默认 600s 过期）→ Desktop 调用**公开的**
  `POST /api/nodes/pairing/complete`（凭据就是配对 token 本身，无需 admin
  认证）兑换为 node token。
- node token 由 SQLite 仓库持久化（另有 in-memory 兜底实现），可通过
  `GET /api/nodes`、`GET /api/nodes/{id}` 查看，`POST /api/nodes/{id}/revoke`
  撤销。
- 连接模式 `mock | gateway`，WS 默认 `ws://127.0.0.1:6185/api/nodes/ws`；
  Desktop 会话 ID 稳定为 `desktop:private:{nodeId}`。
- 服务端参数在 `webapi.nodes`（见 [配置参考](../guide/configuration.md#desktop--gateway-node-协议)）。

### 10.3 远程控制模式

Desktop 设置页提供「远程控制」模式下拉，本地 Rust 侧按策略文件裁决
（策略存于 Tauri app config 目录；旧版 `enabled: bool` 自动迁移为
`scoped`）：

| 模式 | 行为 |
|------|------|
| `disabled` | 禁止 `desktop.process.exec` / `desktop.fs.read_text`，默认 |
| `scoped` | 仅允许 `exec_profiles[].id` 指定的程序；`cwd` 必须在配置的 root 内；文件读取要求 `rootId` + 相对路径，越出 root 即拒绝 |
| `full_access` | 任意程序/路径，仅做 NUL 与 canonicalize 校验；UI 明确标注危险并要求确认 |

授权要求：`allowed_actor_account_keys` 必须包含 Gateway 注入的
`actorAccountKey`（服务端校验该 key 确为调用方唯一账号）。

服务端路由：`DesktopControlService` 通过 NodeRegistry 找到当前在线且绑定到
该 actor 的唯一 Desktop node，多个匹配时拒绝调用。Agent 侧对应工具
`desktop_exec`、`desktop_file_read` 标记为 `requires_admin`（仅在
`identity.admins` 中的账号可调用），对 subagent 不可用。

CRON 专用提醒工具：`desktop_announce`（仅 `origin == "cron_trigger"` 时可用，
消息最长 300 字符），由 `DesktopAnnouncementService.announce` 投递到绑定
节点。

### 10.4 纯视觉 Computer Use（Windows MVP）

首版采用 observe → act 循环，不读取 UI Automation 节点树或 WebView DOM：

1. `desktop_screenshot_capture()` 调用 `desktop.computer.screenshot`，Rust 使用
   GDI 抓取整个虚拟桌面，在内存中缩放到最长边 1600px 并编码为 JPEG。Gateway
   将解码后的图片写入带 TTL 的 `MediaStore`，返回绑定当前 actor 的 `media_id`。
2. `desktop_screen_observe(question, media_id?)` 可复用已有截图；省略 `media_id`
   时先抓取新图。Gateway 不把 base64 写入普通 tool transcript，而是按普通图片
   相同的路由顺序使用 `multimodal.image_fallback_model`，未配置时再尝试 `vision`
   tag，向视觉 provider 发起临时多模态请求并把文字观察结果交还主模型。因此主
   模型本身可以没有视觉能力。
3. `desktop_screenshot_send(media_id?, caption?, attachment_type?)` 可把同一缓存
   截图作为 photo 或 document 发送到当前聊天；它不能选择其他聊天或 Desktop。
4. 坐标统一为左上角原点的 0–1000 归一化空间；Agent 调用
   `desktop_input` 执行一次 move/click/scroll/type/key 后重新观察。
5. Rust 将归一化坐标映射到 Windows 虚拟桌面（支持副屏负坐标），鼠标和键盘
   通过 `SendInput` 注入。非 Windows 平台当前返回 `platform_unsupported`。

隐私与权限边界：Desktop 端不持久化截图；Gateway 只在统一媒体缓存中短期落盘，
到期由现有清理任务删除。tool transcript 不含缓存路径或 base64；视觉调用失败时
也不会把原始图像降级为文本 base64。`media_id` 含 actor scope，不能被另一个
actor 复用。
`computerUse.allowScreenCapture` 与 `computerUse.allowInput` 默认都为 `false`，
设置页首次开启时分别确认。两项能力仍要求远控模式非 disabled、actor 在本地
allowlist 中、Gateway 唯一绑定 Desktop、调用工具的账号具备 admin 权限，且
subagent 永远不可调用。首版不自动处理验证码、登录、支付、发送消息等高影响
动作的逐步确认；这些场景应保持关闭输入或由上层产品确认策略拦截。

## 11. Capability 模型

Desktop Node 的能力应显式声明。

### 11.1 首批能力

| capability | 方向 | 说明 |
|------------|------|------|
| `desktop.live2d.set_expression` | Gateway -> Desktop | 设置表情 |
| `desktop.live2d.play_motion` | Gateway -> Desktop | 播放动作 |
| `desktop.live2d.set_visibility` | Gateway -> Desktop | 显示/隐藏桌宠 |
| `desktop.live2d.load_model` | Gateway -> Desktop | 切换到已授权的本地模型 |
| `desktop.audio.play` | Gateway -> Desktop | 播放经过校验的 `SpeechArtifactRef` 或分段播放计划 |
| `desktop.audio.stop` | Gateway -> Desktop | 停止当前音频播放 |
| `desktop.notification.show` | Gateway -> Desktop | 展示系统通知 |
| `desktop.notification.announce` | Gateway -> Desktop | 在桌宠上排队并语音播报提醒 |
| `desktop.pomodoro.control` | Gateway -> Desktop | start/stop/toggle/status/configure 本地番茄钟；`workMinutes`/`breakMinutes`/`totalRounds`/提醒文案/`enabled`/`speakReminders`/`dynamicText` 可选，configure 先落盘再生效。agent 工具 `desktop_pomodoro` 走此能力 |
| `desktop.computer.screenshot` | Gateway -> Desktop | 抓取虚拟桌面像素并返回单帧 JPEG；Gateway 可写入 TTL 媒体缓存 |
| `desktop.computer.input` | Gateway -> Desktop | 注入归一化鼠标与受限键盘动作 |
| `desktop.window.focus` | Gateway -> Desktop | 聚焦窗口 |
| `desktop.window.set_interaction_mode` | Gateway -> Desktop | 切换 pet window 整窗点击穿透/交互模式 |
| `desktop.window.set_render_mode` | Gateway -> Desktop | 切换 `suspended` / `idle` / `speaking` / `active` 渲染模式 |
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

其中**读取本机文件**（`desktop.fs.read_text`）、**执行命令**
（`desktop.process.exec`）、**截屏**（`desktop.computer.screenshot`）与
**鼠标/键盘输入**（`desktop.computer.input`）已通过
[10.3 远程控制模式](#103-远程控制模式)
与 [10.4 纯视觉 Computer Use](#104-纯视觉-computer-usewindows-mvp) 实现：
默认关闭，`scoped`/`full_access` 两种模式按策略裁决，`allowed_actor` 必须
显式授权，每次调用由服务端路由到唯一绑定节点并受 `AuthorizationGate`
管辖（工具标记 `requires_admin`）。写入本机文件、录音、摄像头与全局键盘
监听仍不开放。

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

### 12.6.1 SpeechArtifact 安全

- Desktop 只接受 Gateway 签发或事件中提供的 `artifact_id` 和 Gateway-relative `download_url`。
- Gateway Media API 必须复用 Desktop 登录或 node token 鉴权，并校验 artifact 的访问范围、TTL 和 MIME。
- `artifact_id` 不得直接映射用户输入路径；下载端点需要防止路径穿越和任意文件读取。
- Provider 内部 URL、SSH/FRP 地址、参考音频路径和 API key 不进入 Desktop event、日志或公开 metadata。
- Desktop 下载前校验允许的 scheme、Gateway origin、Content-Type 和文件大小上限。

### 12.7 透明窗口与点击穿透安全

- pet window 默认 click-through，避免长期拦截用户桌面操作。
- 进入交互模式必须有明确触发源，例如托盘、快捷键、设置页或用户命令。
- 交互模式应有自动超时，避免用户忘记退出后透明窗口持续拦截点击。
- 不在首版实现全局鼠标 hook 或 per-pixel hit-test，降低权限和平台差异风险。
- Linux/Wayland 等平台如果透明或点击穿透能力不稳定，应降级为普通窗口或手动交互模式。

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
| `tts_settings` | 远程 voice/profile、speed、pitch、音量、本地 fallback 和自动播放偏好 |
| `window_state` | 位置、大小、置顶、透明度 |
| `pet_window_state` | pet window 位置、大小、贴边方向、露出尺寸、click-through、交互模式 |
| `render_mode` | `suspended` / `idle` / `speaking` / `active` |
| `performance_mode` | 省电、平衡、活跃 |
| `notification_preferences` | 是否自动唤出、是否自动 TTS、气泡停留时长 |
| `pomodoro_settings` | 番茄钟时长、休息时长、提醒文案和启用状态 |
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
| `performance_state` | Desktop 上报的渲染模式、fps、资源摘要 |

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
| 用户模型不具备某动作所需贴图或 ArtMesh | 明确标记该动作不可用或降级为 Base 动作 |
| DisplayPlan 解析失败 | 降级为纯文本回复，不执行动作 |
| TTS 合成失败 | 显示文本并播放默认表情，不阻塞消息 |
| Gateway 音频下载失败或 artifact 过期 | 尝试本地 fallback；不可用时只显示字幕 |
| GPU TTS 节点或 SSH/FRP 链路不可用 | Speech job 标记失败，不向 Desktop 暴露内部网络信息 |
| 音频 codec 不受 Desktop 支持 | 请求兼容 rendition；仍失败时使用本地 fallback 或字幕 |
| WebGL context 丢失 | 释放并重建 renderer，失败则降级为静态状态 |
| 透明窗口不受平台支持 | 降级为普通 pet window，提示用户 |
| click-through 切换失败 | 保持普通窗口模式，不隐藏控制入口 |
| idle 性能超预算 | 自动进入省电模式，降低 fps 或暂停物理 |
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
- render mode 能正确切换 `suspended` / `idle` / `speaking` / `active`。
- pet window 交互模式能在 click-through 与可交互之间切换。

### 15.4 视觉与交互测试

- 桌宠透明窗口不遮挡核心 UI。
- 不同分辨率和缩放比例下 Live2D 位置正确。
- 断线、思考、回复、错误状态都有可见反馈。
- WebGL canvas 非空，模型加载失败时有降级 UI。
- 用户导入模型后能预览表情和动作。
- 缺失 lip-sync 参数的模型不会导致播放崩溃。
- TTS 播放时口型参数有可见变化。
- hidden / peek / emerged / chat / retreat 状态切换符合预期。
- Gateway 通知、CRON 投送和番茄钟提醒能进入同一气泡展示 pipeline。
- 气泡输入框能向当前 session 发送回复。
- click-through 默认不拦截桌面点击。
- 交互模式下可以拖拽或点击桌宠。
- idle / speaking / active 模式下 fps 符合预算。

## 16. 实施路线

当前实施顺序调整为 **Local-first**。Phase 1 到 Phase 4 不依赖 Gateway，也不修改 Gateway-Node 协议；它们只在 `desktop/` 内把 Live2D 桌宠体验做成可本地验证的闭环。Gateway Client 和 Gateway-Node 放到后续阶段，以减少与协议分支并行开发时的冲突。

### Phase 0：当前分支收敛

- [x] 保留已有 Live2D renderer、Debug Panel、Expression Map、Motion Map 和 DisplayPlan mock 能力。
- [x] 把现有工作台定位为开发/设置界面，不再作为主产品第一屏。
- [x] 明确 Desktop 内部事件模型：`DesktopEvent`、`PresentationPlan`、`PetRuntimeState`。
- [x] 明确本地配置边界：模型 manifest、expression map、motion map、lip-sync、窗口位置和性能模式。
- [x] 将后续 Gateway 接入定义为 `GatewayEventAdapter`，不让 renderer 或 pet state machine 直接依赖 Gateway API。

验收口径：不连接 Gateway 时，开发者仍可通过本地 mock 面板完整预览文本、DisplayPlan、表情、动作和口型。

### Phase 1：纯本地 Live2D 表现层

- [x] 收敛 `Live2DPresentationController`，统一管理 expression、motion、lip-sync 和 render mode。
- [ ] 继续调优 Base 参数动作，例如 `nod`、`notify`、`speaking`、`peek`、`emerge`、`retreat`、`bounce`。
- [x] 明确 Base 动作边界：只用常见参数制造头部、身体、视线、眉毛、口型和物理惯性；不承诺生成模型没有的手臂或贴图动作。
- [x] 支持 motion fallback：优先模型原生 `.motion3.json`，其次 Base 参数动作，最后 None。
- [x] 支持 expression fallback：优先 DisplayPlan keyword 映射，其次 emotion 映射，最后 neutral 或保持当前表情。
- [x] 让 Debug Panel 同时展示模型原生 motion、Base motion、expression 和关键参数，便于调试当前模型。
- [x] 将动作参数从 renderer 中进一步整理成可维护 profile，避免在渲染器里堆大量临时常量。

验收口径：输入一段本地 DisplayPlan 后，模型能稳定完成表情、动作、说话口型和 idle 恢复；动作速度、幅度和身体/头部比例可继续调参。

### Phase 2：本地 Pet Window 与状态机

- [x] 新增或完善独立 `pet` transparent window。
- [x] 实现 `hidden`、`peek`、`emerging`、`emerged`、`speaking`、`chat`、`retreating`、`error` 状态机。
- [x] 将“从屏幕边缘爬出来”拆成两层：窗口/容器滑出动画负责位移，Live2D Base motion 负责头发、身体、头部和视线的惯性表现。
- [x] 实现贴边隐藏、露出部件、mock/主窗口唤出和自动收回。
- [x] 增加鼠标靠近唤出；整窗 click-through 模式下使用 Tauri `cursorPosition()` 全局指针轮询，不依赖自定义 Rust hook。
- [x] 实现整窗 click-through 与手动交互模式，首版不做 per-pixel hit-test。
- [x] 实现主体验气泡文本和紧凑输入框；本阶段输入先接本地 mock session。
- [x] 实现渲染模式切换：`suspended`、`idle`、`speaking`、`active`。

实现备注：

- 鼠标靠近唤出的状态流：cursor 接近贴边露出区域 → `peek`；cursor 触碰露出部件 → `emerge`；cursor 远离 → 收回。`emerged`/`chat` 状态下 cursor 悬停会重置自动收回/聊天空闲计时。
- 交互模式有自动超时（chat 空闲超时后回到 click-through），满足 12.7 节“交互模式应有自动超时”的要求。
- 断线（`fail`）不会把隐藏中的桌宠弹出；error 状态有独立自动收回计时。
- 省电性能模式映射到 `idle` 渲染档（低帧率），`suspended` 仅保留给隐藏/最小化/锁屏。
- WebView `visibilitychange` 时渲染自动挂起，主窗口最小化后不再消耗帧预算。

验收口径：不连接 Gateway 时，桌宠可以贴边隐藏、被 hover/mock 事件唤出、展示气泡、进入输入模式、超时收回，并且默认不长期拦截桌面点击。

### Phase 3：本地 DisplayPlan、TTS 与提醒 pipeline

- [x] 定义 Desktop 本地可消费的 `DisplayPlan` schema 和 parser。
- [x] 支持纯文本、完整 DisplayPlan、`metadata.display_plan` 包装结构和 provider envelope 的本地输入。
- [x] 实现 `PresentationPlanner`，把本地事件转换为气泡、TTS、DisplayPlan、目标 session 和打断策略。
- [x] 定义 `AudioPlaybackAdapter` 与 `SpeechPlaybackCoordinator`，不耦合具体高质量 TTS Provider。
- [x] 接入首版 `SystemSpeechAdapter` 作为本地开发/离线 fallback；失败时降级为字幕。
- [x] 按 segment 串行播放，支持 `pause_after_ms`、replace/queue 打断和停止。
- [x] 首版使用真实播放状态驱动 speaking/lip-sync；远程 artifact 接入后再升级为音量 envelope。
- [x] 实现通知队列、打断、合并和自动收回策略，避免多个提醒互相覆盖。
- [x] 实现本地番茄钟设置与提醒，让它进入同一 DesktopEvent pipeline。

当前实现备注：

- `SpeechPlaybackCoordinator` 运行在 main window，pet window 继续只消费 `DesktopRuntimeSnapshot`，避免两个窗口同时播放。
- 有 `voice` 的 segment 在 Web Speech 可用时进入真实 speaking 状态；无 voice 或播放失败时按字幕估算时长推进，不启用口型。
- 新的 replace presentation 会取消当前系统语音；queue 顺序、segment pause 和失败降级已有单元测试。
- Workbench 提供系统语言、具体 voice、女声自动偏好、语速、音高、音量和中文试听；默认语言为 `zh-CN`，选择持久化到本地配置。
- Web Speech 不提供可靠的 gender metadata，女声自动模式只对常见 voice 名称做启发式优先；用户明确选择的 voice 始终优先。
- `PresentationPlanner` 已覆盖 message completed / error 基线、通知合并优先级（`notification.reminder` / `notification.error`）与番茄钟事件；Gateway 音频走 `/api/speech/jobs` + `/api/media/speech/{artifact_id}`（见 10.1）。
- 番茄钟运行状态（phase/expiresAt/剩余秒数）由 `PomodoroService.onStateChange` 推送到 desktop store；Workbench 与 Settings 面板按实际运行状态显示 Start/Stop、阶段徽标和倒计时，`enabled` 只作为提醒总开关（关闭时同时停表）。提醒 dedupeKey 按提醒类型区分（`work-start` / `break-start` / `break-end`），避免 break 提醒与 work 提醒撞 key 被播放协调器去重吞掉。
- 番茄钟提醒默认带 `ttsEnabled`（`speakReminders` 设置，默认开）：planner 给 reminder segment 加 voice，按 TTS 来源设置走系统语音或 Gateway `/api/speech/jobs` 合成。Agent 可经 `desktop_pomodoro` 工具 → `desktop.pomodoro.control` capability 控制番茄钟（低风险、无需审批）。
- 动态文案（`dynamicText` 设置，默认关）：开启后每个阶段开始时 Desktop 在 runway 内调 `POST /api/pomodoro/reminders`（带最近用过的 `avoid` 列表防重复）预取下阶段文案；Gateway 用 `pomodoro_reminder` 任务模型生成一句 ≤40 字提醒，并按与播放侧完全一致的参数（style=neutral）预合成 TTS，触发时刻 `/api/speech/jobs` 直接缓存命中，无需实时合成。生成失败、未配置模型或未连接 Gateway 时回退固定文案。阶段含 `rounds_done`（全部轮次完成）。
- 轮数（`totalRounds` 设置，1–16，默认 1）：一次 start = `totalRounds` 个 work+break 轮，break 结束后自动进入下一轮，最后一轮结束用 `roundsDoneText` 提醒并自动停表；`PomodoroState` 暴露 `round`/`totalRounds`，运行中改设置不影响本次运行的轮数。
- TTS 来源可选 `system | gateway | auto`：gateway 模式经 Gateway 合成并缓存到本地 blob。

验收口径：本地番茄钟或 mock 通知能触发桌宠唤出、气泡、可选 TTS、口型、表情和动作；TTS 失败不会影响文本展示。

### Phase 4：模型配置与导入

- [ ] 固化默认模型 manifest，包含 expression map、motion map、lip-sync、缩放、位置和贴边露出配置。
- [ ] 将 Expression Map、Motion Map、lip-sync 参数、默认缩放和默认位置迁移到 Tauri app data 配置。
- [ ] 增加模型校准 UI：表情预览、动作预览、口型参数测试、Base motion 强度/方向/时长校准。
- [ ] 支持导出/导入当前模型的本地 manifest，但 manifest 不包含模型资源本体。
- [ ] 实现 best-effort 用户模型导入：扫描 `.model3.json`、校验路径安全、复制到受控目录、生成 manifest。
- [ ] 对缺失动作、缺失表情、缺失 lip-sync 参数或缺失贴图的模型给出明确提示。

验收口径：默认模型体验完整；第三方模型可以加载、预览、配置映射，并在能力缺失时清晰降级。

### Phase 5：Gateway Client 集成

- [x] 连接现有 Gateway REST API。
- [x] 接入 `/api/events/stream` 或临时 WebSocket event bridge。
- [x] 实现 Gateway 地址配置、登录/token 保存和断线状态展示。
- [x] 将 Gateway message、CRON delivery、agent started/completed/error 等事件转换为 `DesktopEvent`。
- [x] 将气泡输入框里的用户回复发送到当前 Gateway session。
- [x] 保持本地 mock event source 可用，作为离线调试入口。
- [x] 确认普通 Channel 只收到干净文本，Desktop 只消费 `metadata.display_plan`。
- [x] 接入 Speech job 和 Gateway Media API，Desktop 通过 Gateway URL 下载 `SpeechArtifactRef`。

验收口径：Gateway 未启动时本地桌宠仍可用；Gateway 启动后，真实消息和本地提醒进入同一展示 pipeline。

### Phase 6：Gateway-Node WebSocket 基线

- [x] 新增 `docs/architecture/gateway-node-protocol.md`。
- [x] 定义 envelope、错误码、认证、心跳、注册、capability 调用。
- [x] 增加 `tests/fixtures/gateway_node/*.json`。
- [x] Python 侧 Pydantic models 能 parse fixtures。
- [x] 新增 `/api/nodes/ws`。
- [x] 实现 node auth、node.register、heartbeat。
- [x] Gateway 维护在线节点 registry。
- [x] Desktop Rust side 实现 WebSocket client。
- [x] Rust side 通过 fixtures 与 Python 协议对齐。

验收口径：Desktop 可以作为 node 连接、鉴权、注册 capability、心跳重连，但 capability 调用仍可先只做 no-op 或日志记录。

### Phase 7：Desktop Capability 与远程表现控制

- [ ] Desktop 注册 Live2D capability。
- [ ] Gateway 调用 `desktop.live2d.set_expression`。
- [ ] Gateway 调用 `desktop.live2d.play_motion`。
- [ ] Desktop 注册 `desktop.audio.play` / `desktop.audio.stop` capability。
- [ ] Python SpeechService 统一适配高质量 TTS Provider，并生成可供 Channel/Desktop 复用的 artifact。
- [ ] Gateway 或 Desktop pipeline 能提交分段音频播放计划。
- [ ] Desktop 注册 window interaction/render mode capability。
- [ ] 调用链路具备超时、错误、审计。
- [ ] Desktop 本地允许列表生效。
- [ ] Agent 输出或后处理器能生成 `DisplayPlan`。
- [ ] Router 将 `DisplayPlan` 拆为 `OutboundMessage.text` 与 `metadata.display_plan`。

验收口径：Gateway 能通过 capability 控制桌宠表现，但常规长回复仍优先走 DisplayPlan pipeline，而不是让 LLM 逐句直接调用底层能力。

### Phase 8：产品化与发布

- [ ] 托盘、透明窗口、置顶、拖拽和快捷入口。
- [ ] 设置生产 CSP，替换当前开发期的 `csp: null`（见 12.4 节）。
- [x] 配对流程（自服务配对已实现，见 10.2）。
- [x] token 撤销与重新登录。
- [ ] TTS voice 管理。
- [ ] 性能模式和省电模式。
- [ ] Windows/macOS 透明 pet window 验证。
- [ ] Linux 透明/click-through 降级策略。
- [ ] Windows/macOS/Linux 打包验证。
- [ ] 隐私与权限提示：摄像头/OpenCV 久坐提醒作为实验能力，默认关闭。

### Phase 9：Native Renderer 实验（可选）

仅当 WebGL Live2D 真实 profiling 不达标时进入本阶段。

- [ ] 定义 `Live2DRenderer` 抽象接口的稳定边界。
- [ ] 建立 `native-live2d-experiment` 分支或独立 proof-of-concept。
- [ ] 验证 Rust side 链接 Live2D Native SDK 的构建和授权路径。
- [ ] 验证 native renderer 与 Tauri pet window 的 surface/window 集成。
- [ ] 比较 Web renderer 与 Native renderer 的 idle CPU/GPU/内存。
- [ ] 只有明显收益且维护成本可接受时，才合入主线。

## 17. 拆仓库时机

当前不建议拆仓库。满足以下条件后可重新评估：

- Gateway-Node 协议已稳定并承诺向后兼容。
- Desktop App 有独立发布周期和独立维护团队。
- Live2D 模型、二进制产物或平台打包资源显著膨胀。
- 需要为 Desktop App 建立独立 issue、release、签名和分发流程。
- 外部项目需要只依赖 desktop/node SDK，而不关心 Nahida Bot core。

即使拆仓库，也不应通过 FFI 共享 Python 实现。拆分后仍应通过 Gateway-Node 协议、schema fixtures 和版本号维护兼容性。

## 18. 待决问题

- 首版气泡输入框是否只回复当前 active session，还是允许切换 session。
- Gateway 是否需要为 Desktop 单独提供 WebSocket event stream，替代 SSE 的鉴权限制。
- Live2D 情绪应该由 Agent 显式输出 DisplayPlan，还是由 Desktop 本地规则推导。
- DisplayPlan 应由主 LLM 直接输出，还是由回复后处理器二次生成。
- TTS 时间戳、phoneme、viseme 数据是否需要纳入统一 schema。
- SpeechArtifact 首版统一使用 Opus/Ogg、MP3，还是按 Channel/Desktop 生成多个 rendition。
- pet window 的交互模式触发源：托盘、快捷键、悬停按钮还是 Gateway 命令。
- 贴边隐藏默认位置、露出部件和唤出距离如何配置。
- 番茄钟属于 Desktop 本地设置，还是需要同步到 Gateway scheduler。
- 摄像头/OpenCV 久坐提醒是否进入首个公开版本，还是作为实验 capability。
- click-through 在 Linux/Wayland 上的支持边界和降级策略。
- WebGL renderer 的真实性能预算是否满足常驻桌宠需求。
- Native/C++ renderer 是否需要长期保留为实验分支。
- 用户模型是否允许从远程 URL 安装，还是只允许本地文件导入。
- 用户模型 manifest 是否需要支持分享，但不包含模型资源本体。
- Desktop Node capability 是否允许被插件调用，还是只允许 core/system 调用。
- 是否需要支持一个 Gateway 同时连接多个 Desktop Node。
- 是否需要支持 Desktop App 启动、停止或管理本地 Nahida Bot 进程。

## 19. 参考资料

- [Live2D Cubism Editor Manual: File Types and Extensions](https://docs.live2d.com/en/cubism-editor-manual/file-type-and-extension/)：说明 `.model3.json`、`.moc3`、`.motion3.json`、`.exp3.json` 等文件角色。
- [Live2D Cubism SDK Manual: About Models (Web)](https://docs.live2d.com/en/cubism-sdk-manual/model-web/)：说明 Web 场景从 `.model3.json` 加载模型、贴图和 renderer 的基本流程。
- [Live2D Cubism SDK Manual: Lip-sync](https://docs.live2d.com/en/cubism-sdk-manual/lipsync/)：说明 lip-sync 参数如何从 `.model3.json` 获取，以及通过音量值或 motion 驱动嘴部开合。
- [Live2D Cubism SDK: About SDK](https://www.live2d.com/en/sdk/about/)：说明 Native/Web 等 SDK 方向。
- [Live2D Cubism Core API Reference](https://docs.live2d.com/en/cubism-sdk-manual/cubism-core-api-reference/)：说明 Native Core API 边界。
- [Tauri v2 Window API](https://v2.tauri.app/reference/javascript/api/namespacewindow/)：透明窗口、窗口控制和 `setIgnoreCursorEvents` 等 API 参考。
- [Tauri v2 Core Permissions](https://v2.tauri.app/reference/acl/core-permissions/)：window API 权限配置参考。

## 20. 结论

Desktop App 应先作为当前 monorepo 下的独立 Tauri 应用存在，通过公开 Gateway API 接入 Nahida Bot。首版产品形态应聚焦边缘隐藏式桌面助手：平时低打扰地贴在屏幕边缘，事件到达时唤出 Live2D、气泡、TTS 和紧凑输入框，而不是把 Live2D 调试器或 WebUI 控制台作为主界面。

Rust/Tauri 端不应通过 FFI 调 Python。Desktop App 与 Python core 的边界就是 Gateway 协议。这样可以保留分布式架构的清晰边界，也能为未来 Python node、Rust desktop node 和其他语言 node 提供一致扩展路径。

长消息、TTS 和 Live2D 表现不应依赖 LLM 逐句调用工具。更合适的方式是让 LLM 或后处理器生成 DisplayPlan，由 Gateway/Router 保留干净文本，并将表现 metadata 交给 Desktop pipeline 消费。Live2D capability 继续作为底层执行能力存在，但它的调用通常来自解析后的表现流水线，而不是直接来自 LLM。

高质量 TTS Provider 统一由 Python SpeechService 适配。GPU TTS 可以运行在通过 SSH/FRP 与 Gateway 连通的私网机器上；Gateway 负责拉取、缓存并通过受鉴权的 Media API 暴露音频，Desktop 和 Channel 复用同一个 SpeechArtifact。Gateway-Node WebSocket 只传 artifact 引用和播放控制，不承载音频二进制。Desktop 保留系统语音作为本地开发和离线 fallback。

性能和桌面体验方面，首版继续采用 Tauri + WebView + WebGL Live2D。Native/C++ renderer 只作为后续性能兜底实验，不作为主线。桌宠窗口采用独立透明 `pet` window，首版使用整窗 click-through + 手动交互模式，不做 per-pixel hit-test。

用户模型支持采用 best-effort 策略。Desktop 提供导入、安全校验、Expression Map、Motion Map、口型参数和位置校准工具，但不承诺让任意模型自动拥有作者未制作的贴图、ArtMesh、参数或动作。完整适配应由模型作者或用户通过模型资源和本地 manifest 配置完成。
