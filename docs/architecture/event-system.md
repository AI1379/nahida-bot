# 类型安全事件系统（面向开发者与 AI 约束）

## 设计目标

事件系统不是"字符串 + dict"的通知器，而是核心契约层。目标：

- **类型安全**：事件类型、载荷结构、处理器签名可被 pyright 严格检查。
- **行为可预测**：发布语义、错误策略、并发模型固定。
- **可注入**：处理器依赖通过统一 DI 注入，避免隐式全局状态。
- **可治理**：对开发者与 AI 都有明确边界，减少"随手加事件名"和"任意 payload"。

## Python 是否有统一规范

Python 对"进程内事件总线"没有单一官方规范（标准库未给出统一 EventBus 抽象）。实践上通常是：

- 用 `asyncio` 自研轻量总线并固定项目契约。
- 或选用轻量库（如 blinker/pyee）再叠加类型和并发约束。

对 nahida-bot，推荐第一条：自研轻量总线 + 强类型事件模型。

## 参考 Rust/C++ 项目的可迁移思路

可借鉴的"机制"而不是语法：

- Rust Tokio：`broadcast`/`mpsc` 的边界清晰，强调背压和关闭语义。
- Rust Bevy：事件是显式类型，系统按类型消费事件。
- C++ Boost.Signals2：订阅关系可管理、连接可断开、生命周期安全。

映射到 Python：

- 借鉴 Tokio：引入 `max_queue_size`、`publish` 超时、`shutdown` 明确语义。
- 借鉴 Bevy：事件用独立类建模，按事件类订阅，不用裸字符串驱动主逻辑。
- 借鉴 Signals2：`subscribe` 返回可取消句柄，避免遗忘反注册导致泄漏。

## 核心类型契约（建议固定）

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

## EventBus 语义（建议作为硬约束）

事件系统采用双层 API：

1. Core API（底层执行层，稳定、可预测、低魔法）

- `subscribe(event_type, handler) -> Subscription`
- `unsubscribe(event_type, handler) -> None`
- `publish(event) -> PublishResult`
- `publish_nowait(event) -> bool`
- `shutdown(timeout: float | None = None) -> None`

1. Facade API（上层开发体验层，Pythonic、声明式）

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

## 与 FastAPI/NoneBot2 风格 DI 的结合

可借鉴 FastAPI 的思路：

- handler 不直接读取全局单例，而是显式声明依赖。
- 依赖由容器/解析器提供，便于测试替身注入。

可借鉴 NoneBot2 的思路：

- 将处理器参数视作"可解析依赖"，由框架在调用前构建。
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

## 约束开发者与 AI 的规则

为减少错误扩展，建议把以下规则写入实现注释与测试：

- 禁止在核心事件总线里传裸 `dict` payload（仅测试夹具可例外）。
- 新事件必须定义 payload 类型和事件类，并添加至少一条类型检查测试。
- 新 handler 必须显式声明 `event` 和 `ctx` 参数，不允许 `*args/**kwargs`。
- 事件类型命名固定在 `core.events.types`，禁止分散到任意模块。
- 事件处理副作用需可测试，不允许只写日志不暴露可断言行为。

## 渐进实施计划

Phase 1 建议拆成三步：

1. 建立 `core/events` 目录与类型契约（Event、EventHandler、Subscription）。
2. 在 `Application.initialize/start/stop` 中接入四个生命周期事件：
   - `AppInitializing`
   - `AppStarted`
   - `AppStopping`
   - `AppStopped`
3. 补齐测试：类型检查、订阅行为、异常隔离、shutdown 语义、生命周期事件触发。
