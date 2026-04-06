# Nahida Bot ARCHITECTURE

> 本文档只描述 Python 方案架构，不包含 Rust 方案。

## 1. 架构目标

Nahida Bot 的核心目标：

- Agent-first：以 Agent Loop 为中枢，而不是把 LLM 当外挂。
- Workspace-native：文件即上下文，工作空间是一等对象。
- Plugin-driven：能力扩展通过插件，不通过核心硬编码。
- Gateway-Node-ready：天然支持远程节点和分布式执行。
- Ops-friendly：可观测、可诊断、可发布。

## 2. 总体分层

推荐的逻辑分层如下：

1. **Core Layer** — 应用容器、生命周期、配置、事件、异常
2. **Workspace Layer** — 工作空间、文件沙盒、上下文注入
3. **Agent Layer** — LLM 推理循环、上下文管理、记忆、Provider 抽象
4. **Plugin Layer** — 插件加载、权限管理、工具注册、**Channel 接口** ⚠️
5. **Gateway-Node Layer** — 远程节点通信、分布式执行
6. **Interface Layer** — CLI / WebUI / API

> **关键改进：Channel 不再是独立层，而是作为 Plugin Layer 的标准接口之一。** 参考 OneBot/NapCat 设计，定义 ChannelPlugin 基类，支持多种通信协议（HTTP Server/Client、WebSocket、SSE）。这样可以：
>
> - 复用插件的权限系统、生命周期管理
> - 灵活支持多种平台接入方式
> - 允许第三方开发 Channel 插件

依赖方向约束：

- 上层可依赖下层，下层不可反向依赖上层。
- `core` 不依赖任何具体平台实现。
- `agent` 不依赖具体 `plugin` 或 `channel` 实现。
- `plugins` （包括 ChannelPlugin）通过协议接入，不直接侵入 `core` 内部状态。
- **ChannelPlugin** 的具体实现（如 Telegram、QQ）通过标准 Plugin 接口加载，无需核心改动。

## 3. 建议目录与模块边界

建议使用如下目录边界（按当前仓库渐进演化）：

```text
nahida_bot/
  core/
    app.py
    config.py
    events.py
    logging.py
    exceptions.py
  workspace/
    manager.py
    sandbox.py
    templates/
  agent/
    loop.py
    context.py
    memory.py
    tools.py
    providers/
      base.py
      openai.py
      registry.py
  plugins/
    manager.py
    loader.py
    manifest.py
    permissions.py
    registry.py
    base.py                      # Channel、Tool、Hook 基类
    builtin/
      __init__.py
      channel.py                 # ChannelPlugin 基类 (Abstract)
      # 具体 Channel 实现（内置插件示例）：
      # - telegram_channel.py
      # - qq_channel.py (via NapCat)
      # - matrix_channel.py
      builtin_tools/
        file_reader.py
        command_executor.py
        web_fetcher.py
        memory_retrieval.py
  gateway/
    server.py
    router.py
    node_manager.py
    protocol.py
  node/
    client.py
    connector.py
    executor.py
  db/
    engine.py
    models.py
    repositories/
  cli/
    main.py
```

### 重点说明

1. **ChannelPlugin 基类** 在 `plugins/base.py` 或专属 `plugins/channel_base.py`
   - 定义标准接口（`handle_inbound_event`、`send_message`、`get_user_info` 等）
   - 声明支持的通信方式（HTTP Server/Client、WebSocket、SSE）
   - 嵌入权限声明和生命周期挂钩

2. **内置 Channel 实现** 在 `plugins/builtin/` 下，作为 ChannelPlugin 实例
   - 每个 Channel 是一个标准 Plugin，有 `plugin.yaml` 和实现代码
   - 通过 Plugin Manager 加载，享受权限隔离和热加载机制

3. **第三方 Channel 插件** 结构相同，可外部贡献
   - 遵循同一的 Plugin 接口契约
   - 无须修改核心代码

## 4. 核心运行流程

### 4.1 消息主流程（通过 ChannelPlugin）

```text
外部平台 (QQ/Telegram/Matrix/etc)
  ↓
ChannelPlugin 接收事件
  ├─ HTTP Server: webhook 推送
  ├─ WebSocket: 双向连接
  ├─ HTTP Client: 轮询或长连接
  └─ SSE: 单向推送
  ↓
InboundMessage 标准化
  ↓
Session Resolver （映射平台用户 -> Bot 会话）
  ↓
Context Builder (workspace 文件注入 + 历史记录)
  ↓
Agent Loop
  ├─ 消息入 LLM
  ├─ Tool calls (optional)
  └─ 最终回复组装
  ↓
OutboundMessage
  ↓
ChannelPlugin 发送
  ├─ 调用外部 API
  └─ 或通过 WebSocket 回复
  ↓
持久化历史记录到 SQLite
```

### 4.2 工具调用流程

```text
LLM tool_call
  → Tool Registry lookup （所有 Plugin 注册的 Tool）
  → Permission check （权限系统）
  → Tool execute
  → Tool result message
  → Loop continues
```

### 4.3 Gateway-Node 流程

```text
Node connect
  → auth challenge/response
  → heartbeat
  → command dispatch
  → result return
  → health update
```

## 5. 模块契约（建议先固定）

优先稳定以下契约，后续模块都基于这些契约展开：

- **Message Contract**：`InboundMessage` / `OutboundMessage`
  - 所有 ChannelPlugin 都基于这个统一结构转换平台原生消息
- **ChannelPlugin Contract**（参考 OpenClaw、OneBot）
  - `handle_inbound_event(event: dict) -> None`
  - `send_message(target: str, message: OutboundMessage) -> str`
  - 支持的通信方式声明（HTTP Server/Client、WebSocket、SSE）
  - 权限和生命周期钩子
- **Agent Contract**：`AgentLoop.run()` 输入输出与中断语义
- **Tool Contract**：tool definition、参数校验、执行结果结构
  - 由 Plugin 通过权限系统注册
- **Plugin Manifest Contract**：`plugin.yaml` 字段与版本兼容策略
  - ChannelPlugin 作为标准 Plugin 的一种，需遵循同一 manifest 规范
- **Gateway Protocol Contract**：消息类型、错误码、版本字段

**设计原则**：

- 契约一旦开放给插件或外部节点使用，默认只做向后兼容改动。
- ChannelPlugin 的多通信协议支持（HTTP/WebSocket）需在 manifest 中明确声明，确保 Bot 和外部系统能协商使用哪种方式。
- 参考 NapCat/OneBot 的做法：允许外部系统通过注册 webhook 向 Bot 推送，同时 Bot 也能通过 HTTP/WebSocket 主动向外部系统发送消息。

## 6. 数据与状态边界

状态分层建议：

- 瞬时态：请求上下文、流式响应缓冲、工具调用中间态
- 会话态：聊天历史、当前会话配置、会话级变量
- 长期态：workspace 文件、长期记忆、插件配置、节点信息

存储策略建议：

- 先使用 SQLite 统一落地会话与配置。
- 对外统一通过 repository 接口，不让业务代码直接拼 SQL。
- 文件系统读写全部经过 workspace/sandbox 统一入口。

## 7. ChannelPlugin 设计细节（Plugin 系统的扩展）

### 7.1 背景与设计目标

**为什么 Channel 是 Plugin？**

- 复用插件权限系统、生命周期管理和能力注册机制
- 支持第三方 Channel 插件无须修改核心代码
- 灵活支持多种通信协议（HTTP、WebSocket、SSE）组合

**参考项目**：OpenClaw Gateway-Channel 模型、OneBot 协议、NapCat 的多通道设计

### 7.2 ChannelPlugin 核心接口

```python
class ChannelPlugin(Plugin):
    """所有 Channel 插件的基类。"""

    # 通信方式支持声明（在 plugin.yaml 中对应 capabilities）
    SUPPORT_HTTP_SERVER: bool = False      # Bot 提供 HTTP 端点，外部推送
    SUPPORT_HTTP_CLIENT: bool = False      # Bot 主动 HTTP 请求外部系统
    SUPPORT_WEBSOCKET_SERVER: bool = False # Bot 提供 WebSocket，外部连接
    SUPPORT_WEBSOCKET_CLIENT: bool = False # Bot 连接到外部 WebSocket
    SUPPORT_SSE: bool = False              # Bot 通过 SSE 单向推送

    async def handle_inbound_event(self, event: dict) -> None:
        """处理来自外部系统的事件（webhook、WebSocket 消息等）。

        将平台原生事件格式转换为 InboundMessage，并触发 Agent Loop。
        """
        ...

    async def send_message(
        self,
        target: str,  # 平台用户/群组 ID
        message: OutboundMessage
    ) -> str:
        """向外部系统发送消息，返回平台消息 ID。

        支持流式响应分片、速率限制等。
        """
        ...

    async def get_user_info(self, user_id: str) -> dict:
        """获取用户信息（可选）。"""
        ...

    async def get_group_info(self, group_id: str) -> dict:
        """获取群组信息（可选）。"""
        ...
```

### 7.3 通信方式详解

各 Channel 可选择一种或多种组合：

| 方式 | 说明 | 场景 | 示例 |
| ---- | ---- | ---- | ---- |
| **HTTP Server** | Bot 提供 HTTP POST 端点接收 webhook | 外部系统主动推送事件 | NapCat `POST /channels/qq/webhook` |
| **HTTP Client** | Bot 主动轮询或通过 HTTP 请求向外部发送消息 | Bot 需主动控制消息流向 | Telegram Polling API、HTTP 心跳 |
| **WebSocket Server** | Bot 监听 WebSocket 端口，外部系统连接 | 需要持久连接、双向实时通信 | Web 端、自定义客户端 |
| **WebSocket Client** | Bot 连接到外部 WebSocket 端点 | Bot 作为客户端和中心网关通信 | 云服务集中管理 |
| **SSE** | Bot 通过 HTTP SSE 单向推送事件 | 只需事件流，不需请求-响应模式 | 浏览器、Node.js 客户端 |

### 7.4 典型设计模式（参考 NapCat/OneBot）

**模式 A：HTTP Server + HTTP Client**（推荐开始）

```text
外部系统 ──webhook──> Bot HTTP Server ──处理──> InboundMessage
        <────HTTP────                          OutboundMessage
```

优点：

- 无需长连接，可跨域
- 支持负载均衡和水平扩展
- 外部系统可异步推送，Bot 可异步发送

缺点：

- 需要鉴权机制（防 webhook 伪造）
- 频率限制和重试需自行管理

NapCat 示例配置：

```yaml
channels:
  qq:
    type: qq
    plugin: qq_channel
    config:
      webhook_url: "http://bot.local:8888/channels/qq/webhook"
      api_endpoint: "http://napcat.local:3000"
      token: "secret_token"
```

**模式 B：WebSocket 双向**（长连接、实时）

```text
外部系统 ◄──► Bot WebSocket Server
```

优点：

- 真正双向、低延迟
- 一个连接复用，性能好

缺点：

- 需要连接管理和重连机制
- 难以水平扩展（需要 session 亲和性）

**模式 C：混合模式**（最灵活）

```text
接收事件用 WebSocket Server
发送消息用 HTTP Client
```

或其他组合，根据平台特性选择。

### 7.5 plugin.yaml 示例

```yaml
id: qq_channel
name: "QQ Channel (via NapCat)"
version: "0.1.0"
description: "QQ 平台接入（通过 NapCat OneBot 协议）"

# 权限声明
permissions:
  - resource: "network"
    action: ["http_post", "http_get", "websocket"]
    targets:
      - "http://napcat.local:*"
      - description: "NapCat 本地服务"

# 能力声明
capabilities:
  - type: "channel"
    name: "qq_channel"
    protocols:
      - "http_server"    # Bot 提供 webhook 端点
      - "http_client"    # Bot 向 NapCat 发送消息

# 配置架构
configSchema:
  type: "object"
  properties:
    webhook_secret:
      type: "string"
      description: "Webhook 鉴权密钥（玄性）"
    napcat_endpoint:
      type: "string"
      description: "NapCat API 端点"
      default: "http://localhost:3000"
    bot_qq:
      type: "string"
      description: "Bot 的 QQ 号"
```

### 7.6 权限与安全

- ChannelPlugin 需声明 network 权限（HTTP、WebSocket）
- Webhook 端点需要鉴权（HMAC 签名或 Token）
- 频率限制由 Plugin 或 Gateway 负责
- 敏感信息（Token、密钥）走系统级 Secret 管理

### 7.7 与 Phase 的关系

- **Phase 3**：定义 ChannelPlugin 基类、接口规范、manifest 契约
- **Phase 4**：实现第一个 ChannelPlugin（如 Telegram 或 QQ/NapCat）
- **Phase 5+**：第三方 Channel 插件生态，无需核心改动

## 8. 安全与可观测性基线

安全基线：

- 路径穿越防护。
- 插件最小权限。
- 节点认证和命令白名单。
- Webhook 鉴权（HMAC 签名或 Token）。
- 敏感信息脱敏日志。

可观测性基线：

- 结构化日志（请求 ID、会话 ID、节点 ID）。
- 基础健康检查（服务、数据库、节点连接）。
- 关键链路指标（响应时延、错误率、工具调用成功率）。
- Channel 级指标（消息发送成功率、延迟）。

## 9. 模块优先级

不严格按时间，而按“依赖风险 + 价值密度”排序。

### P0 - 必须先做

- `core`：应用容器、配置、日志、异常、事件
- `workspace`：安全文件边界、模板、上下文读取
- `agent.loop`：基础回路和终止条件
- `agent.providers.base + openai`：首个可用 Provider
- `agent.tools`：工具协议与执行器

原因：这些模块决定系统是否能形成最小闭环。

### P1 - MVP 关键能力

- `plugins.base + plugins.manifest/loader/manager`：插件发现、加载和生命周期管理
  - 包括 ChannelPlugin 基类定义（参考 OneBot/NapCat 多协议设计）
  - Plugin manifest 要明确支持的通信方式
- `plugins.permissions`：声明式权限系统（文件、网络、环境变量等）
- 首个具体 ChannelPlugin 实现（内置或内置示例）
  - 选择 **Telegram**（推荐：API 简单、建议 aiogram 库参考）
  - 或 **NapCat/QQ**（参考 OneBot webhook + HTTP 双向通信）
  - 设计重点：支持至少两种通信方式（如 HTTP Server + HTTP Client）
- `agent.memory + db.engine + repositories`：会话与记忆持久化

原因：把"单机可跑"推进到"多平台真实可用"，且 **Channel 通过插件系统接入，无需改动核心代码**。

### P2 - 可扩展与分布式

- `gateway.server/router/node_manager`：Gateway 消息路由和节点管理
- `node.client/connector/executor`：Node 连接器和远程执行
- `gateway.protocol`（版本化）：WebSocket 协议定版
- `plugins.hook` 和热加载：Hook 系统和插件动态加载
- 更多 ChannelPlugin 实现（第二、第三个平台）：
  - 参考第一个 Channel 的设计，复用 ChannelPlugin 基类
  - 可通过开发文档指导第三方贡献 Channel 插件

原因：进入多节点和复杂扩展场景，同时 Channel 生态可独立扩展。

### P3 - 体验与生态

- `cli` 完整命令集
- WebUI 管理与可视化
- 文档体系、示例插件、发布流水线

原因：提升可维护性、可交付性和社区可接入性。

## 10. 开发策略建议

- 先契约后实现：先固定输入输出模型，再写具体逻辑。
- 每完成一个优先级层级，进行一次接口冻结。
- 每个模块至少同时交付：代码、测试、文档最小集。
- 避免跨层捷径调用，宁可加一个小的协议层。

## 11. 类型安全事件系统（面向开发者与 AI 约束）

### 11.1 设计目标

事件系统不是“字符串 + dict”的通知器，而是核心契约层。目标：

- 类型安全：事件类型、载荷结构、处理器签名可被 pyright 严格检查。
- 行为可预测：发布语义、错误策略、并发模型固定。
- 可注入：处理器依赖通过统一 DI 注入，避免隐式全局状态。
- 可治理：对开发者与 AI 都有明确边界，减少“随手加事件名”和“任意 payload”。

### 11.2 Python 是否有统一规范

Python 对“进程内事件总线”没有单一官方规范（标准库未给出统一 EventBus 抽象）。实践上通常是：

- 用 `asyncio` 自研轻量总线并固定项目契约。
- 或选用轻量库（如 blinker/pyee）再叠加类型和并发约束。

对 nahida-bot，推荐第一条：自研轻量总线 + 强类型事件模型。

### 11.3 参考 Rust/C++ 项目的可迁移思路

可借鉴的“机制”而不是语法：

- Rust Tokio：`broadcast`/`mpsc` 的边界清晰，强调背压和关闭语义。
- Rust Bevy：事件是显式类型，系统按类型消费事件。
- C++ Boost.Signals2：订阅关系可管理、连接可断开、生命周期安全。

映射到 Python：

- 借鉴 Tokio：引入 `max_queue_size`、`publish` 超时、`shutdown` 明确语义。
- 借鉴 Bevy：事件用独立类建模，按事件类订阅，不用裸字符串驱动主逻辑。
- 借鉴 Signals2：`subscribe` 返回可取消句柄，避免遗忘反注册导致泄漏。

### 11.4 核心类型契约（建议固定）

建议目录：

```text
nahida_bot/core/events/
  __init__.py
  bus.py               # EventBus 实现
  types.py             # 事件基类与类型定义
  registry.py          # 允许事件类型白名单（可选）
  errors.py            # 事件系统错误定义
```

建议类型模型：

```python
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Generic, TypeVar
from uuid import UUID, uuid4

PayloadT = TypeVar("PayloadT")

@dataclass(slots=True, frozen=True)
class Event(Generic[PayloadT]):
  event_id: UUID = field(default_factory=uuid4)
  trace_id: str = ""
  source: str = ""
  occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  payload: PayloadT = field()
```

业务事件建议用独立类型，不建议用 `dict[str, Any]`：

```python
@dataclass(slots=True, frozen=True)
class AppStartedPayload:
  app_name: str
  debug: bool

@dataclass(slots=True, frozen=True)
class AppStarted(Event[AppStartedPayload]):
  ...
```

处理器签名建议：

```python
from typing import Protocol, TypeVar, Awaitable

EventT = TypeVar("EventT", bound=Event[object])

class EventHandler(Protocol[EventT]):
  async def __call__(self, event: EventT) -> None: ...
```

### 11.5 EventBus 语义（建议作为硬约束）

事件系统采用双层 API：

1. Core API（底层执行层，稳定、可预测、低魔法）

- `subscribe(event_type, handler) -> Subscription`
- `unsubscribe(event_type, handler) -> None`
- `publish(event) -> PublishResult`
- `publish_nowait(event) -> bool`
- `shutdown(timeout: float | None = None) -> None`

2. Facade API（上层开发体验层，Pythonic、声明式）

- `@events.on(EventType)` 装饰器式注册。
- `@events.on(EventType, depends=[Depends(check_xxx)])` 前置依赖。
- `events.emit(EventType(...))` 语义别名（内部仍委派到 `publish`）。
- `events.listener(EventType)` 上下文管理器（自动注册/解绑）。

语义约束：

- 订阅按 `event_type`（类）匹配，不按字符串名匹配核心逻辑。
- 默认策略：同一事件内按订阅顺序串行执行，事件与事件之间可并发。
- 单个 handler 失败不阻断同事件其他 handler；失败收敛到 `PublishResult.errors`。
- `publish` 可配置超时；超时和取消需要可观测（日志 + 指标）。
- `shutdown` 后拒绝新事件，并等待在途处理完成。

### 11.6 与 FastAPI/NoneBot2 风格 DI 的结合

可借鉴 FastAPI 的思路：

- handler 不直接读取全局单例，而是显式声明依赖。
- 依赖由容器/解析器提供，便于测试替身注入。

可借鉴 NoneBot2 的思路：

- 将处理器参数视作“可解析依赖”，由框架在调用前构建。
- 把上下文对象（session、bot、state）作为受控注入项，而非任意获取。
- 依赖求值采用 parse/check/solve 三段式，而非直接反射调用。

建议在事件系统中引入 `EventContext`：

```python
@dataclass(slots=True)
class EventContext:
  settings: Settings
  logger: logging.Logger
  app: Application
```

处理器签名建议统一为：

```python
async def handle_app_started(event: AppStarted, ctx: EventContext) -> None:
  ...
```

由 EventBus 在 dispatch 前注入 `ctx`，避免 handler 到处访问全局对象。

建议补充 `Depends` 风格语义（用于 Facade API）：

```python
from typing import Annotated

async def get_repo(ctx: EventContext) -> Repo: ...
async def guard_feature(ctx: EventContext) -> None: ...

@events.on(AppStarted, depends=[Depends(guard_feature)])
async def bootstrap(
  event: AppStarted,
  repo: Annotated[Repo, Depends(get_repo)],
) -> None:
  ...
```

依赖解析流程建议固定为：

1. parse：解析函数签名和 `Depends` 元信息。
2. check：执行 parameterless/pre-check 依赖（返回值忽略）。
3. solve：解析事件参数与子依赖值（支持缓存和校验）。
4. call：调用 handler。
5. teardown：清理生成器依赖/上下文资源。

### 11.7 约束开发者与 AI 的规则

为减少错误扩展，建议把以下规则写入实现注释与测试：

- 禁止在核心事件总线里传裸 `dict` payload（仅测试夹具可例外）。
- 新事件必须定义 payload 类型和事件类，并添加至少一条类型检查测试。
- 新 handler 必须显式声明 `event` 和 `ctx` 参数，不允许 `*args/**kwargs`。
- 事件类型命名固定在 `core.events.types`，禁止分散到任意模块。
- 事件处理副作用需可测试，不允许只写日志不暴露可断言行为。

### 11.8 渐进实施计划

Phase 1 建议拆成三步：

1. 建立 `core/events` 目录与类型契约（Event、EventHandler、Subscription）。
2. 在 `Application.initialize/start/stop` 中接入四个生命周期事件：
   - `AppInitializing`
   - `AppStarted`
   - `AppStopping`
   - `AppStopped`
3. 补齐测试：类型检查、订阅行为、异常隔离、shutdown 语义、生命周期事件触发。

## 12. 与 ROADMAP 的关系

- [docs/ROADMAP.md](docs/ROADMAP.md)：回答"做什么、做到什么程度"。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：回答"怎么分层、模块如何协作、先做哪些模块、Channel 作为 Plugin 的设计"。

两份文档应同步更新，不允许只改其中一份。
