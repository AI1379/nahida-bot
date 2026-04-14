# Plugin 系统完整设计

## 1. 设计目标与核心原则

### 1.1 设计目标

1. **解耦开发**：插件开发者只需依赖一个轻量 SDK，无需引入整个 nahida-bot。
2. **类型安全**：插件接口、事件类型、配置项全部可被 pyright 检查。
3. **声明式权限**：权限在 manifest 中声明，运行时强制执行，不可绕过。
4. **异常隔离**：单个插件的崩溃不影响核心和其他插件。
5. **可测试**：插件可以在不启动 bot 的情况下完成单元测试。

### 1.2 核心原则

- **SDK 分离**：插件只依赖 `nahida-bot-sdk`（纯接口包，无重运行时依赖），不依赖 `nahida-bot` 本体。
- **契约优于实现**：插件面向接口编程，bot 在运行时注入具体实现。
- **最小权限**：插件只能访问 manifest 中声明的资源和 API。
- **显式优于隐式**：所有事件监听、工具注册、钩子挂载都通过显式声明完成。

## 2. 整体架构

```text
┌──────────────────────────────────────────────────────┐
│                    nahida-bot 主进程                    │
│                                                      │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────────┐ │
│  │  Core   │  │  Agent   │  │    EventBus (增强)    │ │
│  │ App/Config│ │ Loop/Ctx │  │  类型安全 + 优先级    │ │
│  └────┬────┘  └────┬─────┘  └──────────┬───────────┘ │
│       │            │                    │             │
│  ┌────┴────────────┴────────────────────┴───────────┐ │
│  │              Plugin Host (插件宿主)                │ │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │ │
│  │  │ Loader   │  │ Manager  │  │ Permission    │  │ │
│  │  │ 发现/加载 │  │ 生命周期  │  │ Checker       │  │ │
│  │  └──────────┘  └──────────┘  └───────────────┘  │ │
│  │  ┌──────────┐  ┌──────────────────────────────┐ │ │
│  │  │ Registry │  │    API Bridge (运行时注入)    │ │ │
│  │  │ 工具/事件 │  │  将 SDK 接口桥接到真实实现    │ │ │
│  │  └──────────┘  └──────────────────────────────┘ │ │
│  └──────────────────────────────────────────────────┘ │
│       │              │              │                 │
│  ┌────┴────┐  ┌──────┴──────┐  ┌───┴────────────┐   │
│  │ Plugin A │  │  Plugin B   │  │  Plugin C      │   │
│  │ (Channel)│  │  (Tool)     │  │  (Hook)        │   │
│  └─────────┘  └─────────────┘  └────────────────┘   │
└──────────────────────────────────────────────────────┘

每个插件只依赖：
  ┌──────────────────┐
  │  nahida-bot-sdk  │  ← 纯接口 + 类型 + 测试工具
  │  (PyPI 包)       │
  └──────────────────┘
```

## 3. SDK 层设计（nahida-bot-sdk）

### 3.1 为什么需要独立 SDK

**问题**：插件开发者需要 `import nahida_bot` 来获取接口定义，但 `nahida_bot` 拉入了 `aiosqlite`、`httpx`、`fastapi`、`structlog` 等大量运行时依赖。这导致：

- 开发环境搭建成本高。
- CI 中跑插件测试需要安装整个 bot。
- 版本耦合严重——bot 的内部重构会破坏插件编译。

**解决方案**：将插件所需的全部接口抽入独立包 `nahida-bot-sdk`。

```text
nahida-bot-sdk/
  __init__.py
  types.py              # Event, Payload, ToolDefinition 等核心类型
  plugin_base.py        # Plugin 基类
  channel_plugin.py     # ChannelPlugin 基类
  manifest.py           # PluginManifest 数据模型 (Pydantic)
  permissions.py        # Permission 声明类型
  hooks.py              # 钩子注册装饰器
  api/
    __init__.py
    interfaces.py       # BotAPI 协议定义 (插件可调用的 bot 能力)
    messaging.py        # InboundMessage, OutboundMessage
    session.py          # Session 相关接口
    memory.py           # Memory 相关接口
  testing/
    __init__.py
    mocks.py            # MockBotAPI, MockEventBus 等
    fixtures.py         # pytest 插件和常用 fixture
```

**依赖要求**：`nahida-bot-sdk` 只允许依赖 `pydantic >= 2.0` 和 `typing_extensions`，不引入任何运行时框架。

### 3.2 Plugin 基类

```python
# nahida_bot_sdk/plugin_base.py

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nahida_bot_sdk.api.interfaces import BotAPI
    from nahida_bot_sdk.manifest import PluginManifest


class Plugin(ABC):
    """所有插件的基类。

    插件开发者在子类中：
    1. 实现 ``on_load`` 完成初始化（注册事件处理器、工具等）。
    2. 可选实现 ``on_unload`` 清理资源。
    3. 通过 ``self.api`` 调用 bot 提供的能力。
    """

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        self._api = api
        self._manifest = manifest

    @property
    def api(self) -> BotAPI:
        """插件可调用的 bot 能力。运行时由主进程注入真实实现；测试时注入 mock。"""
        return self._api

    @property
    def manifest(self) -> PluginManifest:
        """本插件的 manifest 元数据。"""
        return self._manifest

    @abstractmethod
    async def on_load(self) -> None:
        """插件加载时调用。在此注册事件处理器、工具、钩子等。"""
        ...

    async def on_unload(self) -> None:
        """插件卸载时调用（可选覆写）。清理资源、断开连接等。"""
        pass

    async def on_enable(self) -> None:
        """插件启用时调用（可选覆写）。"""
        pass

    async def on_disable(self) -> None:
        """插件禁用时调用（可选覆写）。"""
        pass
```

### 3.3 BotAPI 协议——插件可调用的全部能力

这是解决「测试困难」的关键：插件只依赖这个协议接口，不依赖 bot 内部的具体类。

```python
# nahida_bot_sdk/api/interfaces.py

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Awaitable

    from nahida_bot_sdk.api.messaging import InboundMessage, OutboundMessage
    from nahida_bot_sdk.api.session import SessionInfo
    from nahida_bot_sdk.api.memory import MemoryRef
    from nahida_bot_sdk.types import EventT


@runtime_checkable
class BotAPI(Protocol):
    """插件可调用的 bot 能力接口。

    运行时由 Plugin Host 注入真实实现。
    测试时由 MockBotAPI 或自定义 mock 注入。
    """

    # ── 消息 ──────────────────────────────────────────

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        """通过指定 channel 向目标发送消息。返回平台消息 ID。

        如果 ``channel`` 为空，发送到产生当前会话的 channel。
        """
        ...

    # ── 事件系统 ──────────────────────────────────────

    def on_event(self, event_type: type[EventT]) -> Callable:
        """装饰器：注册事件处理器。

        用法::

            @api.on_event(MessageReceived)
            async def handle_message(event: MessageReceived) -> None:
                ...
        """
        ...

    def subscribe(
        self, event_type: type[EventT], handler: Callable[[EventT], Awaitable[None]]
    ) -> SubscriptionHandle:
        """编程式注册事件处理器。返回可用于取消订阅的句柄。"""
        ...

    # ── 工具注册 ──────────────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],  # JSON Schema
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        """向 Agent 注册一个可用工具。LLM 可在对话中调用此工具。"""
        ...

    # ── 会话 ──────────────────────────────────────────

    async def get_session(self, session_id: str) -> SessionInfo | None:
        """获取会话信息。"""
        ...

    # ── 记忆 ──────────────────────────────────────────

    async def memory_search(self, query: str, *, limit: int = 5) -> list[MemoryRef]:
        """在记忆中搜索相关内容。"""
        ...

    async def memory_store(self, key: str, content: str, *, metadata: dict | None = None) -> None:
        """向记忆中存储一条记录。"""
        ...

    # ── 工作空间 ──────────────────────────────────────

    async def workspace_read(self, path: str) -> str:
        """读取工作空间中的文件内容。受权限检查约束。"""
        ...

    async def workspace_write(self, path: str, content: str) -> None:
        """向工作空间写入文件。受权限检查约束。"""
        ...

    # ── 日志 ──────────────────────────────────────────

    @property
    def logger(self) -> PluginLogger:
        """获取带插件标识的结构化日志器。"""
        ...


class SubscriptionHandle(Protocol):
    """事件订阅句柄，可用于取消订阅。"""

    def unsubscribe(self) -> None: ...


class PluginLogger(Protocol):
    """插件专用日志器。自动附带 plugin_id 字段。"""

    def debug(self, msg: str, **kwargs: object) -> None: ...
    def info(self, msg: str, **kwargs: object) -> None: ...
    def warning(self, msg: str, **kwargs: object) -> None: ...
    def error(self, msg: str, **kwargs: object) -> None: ...
    def exception(self, msg: str, **kwargs: object) -> None: ...
```

### 3.4 消息类型

```python
# nahida_bot_sdk/api/messaging.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class InboundMessage:
    """从外部平台收到的标准化消息。"""

    message_id: str
    platform: str              # 来源平台标识，如 "telegram"、"qq"
    chat_id: str               # 平台会话 ID
    user_id: str               # 发送者平台 ID
    text: str                  # 消息正文
    raw_event: dict[str, Any]  # 平台原生事件（供插件自行解析扩展字段）
    is_group: bool = False
    reply_to: str = ""         # 被回复的消息 ID（如有）
    timestamp: float = 0.0


@dataclass(slots=True, frozen=True)
class OutboundMessage:
    """向外部平台发送的标准化消息。"""

    text: str
    reply_to: str = ""         # 回复指定消息
    extra: dict[str, Any] = field(default_factory=dict)  # 平台特定参数
```

### 3.5 测试支持

这是解决「插件测试困难」的核心：

```python
# nahida_bot_sdk/testing/mocks.py

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Awaitable
from unittest.mock import AsyncMock

from nahida_bot_sdk.api.interfaces import BotAPI, SubscriptionHandle
from nahida_bot_sdk.api.messaging import OutboundMessage


class MockBotAPI:
    """轻量 BotAPI 实现，用于插件单元测试。

    无需启动 bot、数据库或任何外部服务。

    用法::

        api = MockBotAPI()
        plugin = MyPlugin(api=api, manifest=my_manifest)
        await plugin.on_load()

        # 验证插件注册了事件处理器
        assert api.has_event_handler(MessageReceived)

        # 验证插件注册了工具
        assert "my_tool" in api.registered_tools

        # 模拟发送消息
        await api.trigger_event(MessageReceived(payload=...))
        assert api.sent_messages == [...]
    """

    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, OutboundMessage]] = []
        self.event_handlers: dict[type, list[Callable]] = defaultdict(list)
        self.registered_tools: dict[str, dict[str, Any]] = {}
        self._tool_handlers: dict[str, Callable] = {}
        self._stored_memories: list[tuple[str, str, dict | None]] = []
        self._workspace_files: dict[str, str] = {}

    # ── 实现 BotAPI 接口 ──────────────────────────────

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        msg_id = f"mock_msg_{len(self.sent_messages)}"
        self.sent_messages.append((target, message))
        return msg_id

    def on_event(self, event_type: type) -> Callable:
        def decorator(handler: Callable) -> Callable:
            self.event_handlers[event_type].append(handler)
            return handler
        return decorator

    def subscribe(
        self, event_type: type, handler: Callable[..., Awaitable[None]]
    ) -> SubscriptionHandle:
        self.event_handlers[event_type].append(handler)
        return _MockSubscriptionHandle(self.event_handlers, event_type, handler)

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        self.registered_tools[name] = {
            "description": description,
            "parameters": parameters,
        }
        self._tool_handlers[name] = handler

    async def get_session(self, session_id: str) -> None:
        return None

    async def memory_search(self, query: str, *, limit: int = 5) -> list:
        return []

    async def memory_store(
        self, key: str, content: str, *, metadata: dict | None = None
    ) -> None:
        self._stored_memories.append((key, content, metadata))

    async def workspace_read(self, path: str) -> str:
        return self._workspace_files.get(path, "")

    async def workspace_write(self, path: str, content: str) -> None:
        self._workspace_files[path] = content

    @property
    def logger(self) -> Any:
        return _MockLogger()

    # ── 测试辅助方法 ──────────────────────────────────

    def has_event_handler(self, event_type: type) -> bool:
        return event_type in self.event_handlers

    async def trigger_event(self, event: Any) -> None:
        """模拟触发一个事件，调用所有已注册的处理器。"""
        for handler in self.event_handlers.get(type(event), []):
            await handler(event)

    async def call_tool(self, name: str, **kwargs: Any) -> str:
        """模拟调用一个已注册的工具。"""
        return await self._tool_handlers[name](**kwargs)


class _MockSubscriptionHandle:
    def __init__(self, handlers: dict, event_type: type, handler: Callable) -> None:
        self._handlers = handlers
        self._event_type = event_type
        self._handler = handler

    def unsubscribe(self) -> None:
        handlers = self._handlers.get(self._event_type, [])
        if self._handler in handlers:
            handlers.remove(self._handler)


class _MockLogger:
    def debug(self, msg: str, **kw: object) -> None: pass
    def info(self, msg: str, **kw: object) -> None: pass
    def warning(self, msg: str, **kw: object) -> None: pass
    def error(self, msg: str, **kw: object) -> None: pass
    def exception(self, msg: str, **kw: object) -> None: pass
```

插件开发者的测试用例示例：

```python
# tests/test_my_plugin.py

import pytest
from nahida_bot_sdk.testing.mocks import MockBotAPI
from nahida_bot_sdk.types import MessageReceived
from my_plugin import MyPlugin, MANIFEST


@pytest.fixture
def api():
    return MockBotAPI()


@pytest.fixture
def plugin(api):
    return MyPlugin(api=api, manifest=MANIFEST)


async def test_plugin_registers_event_handler(plugin):
    await plugin.on_load()
    assert plugin.api.has_event_handler(MessageReceived)


async def test_plugin_responds_to_message(plugin):
    await plugin.on_load()
    event = MessageReceived(payload=InboundMessage(
        message_id="test",
        platform="test",
        chat_id="chat_1",
        user_id="user_1",
        text="hello",
        raw_event={},
    ))
    await plugin.api.trigger_event(event)

    assert len(plugin.api.sent_messages) == 1
    assert "response" in plugin.api.sent_messages[0][1].text
```

## 4. Manifest 设计（plugin.yaml）

### 4.1 Manifest 完整字段

```yaml
# plugin.yaml — 插件清单

id: "com.example.my_plugin"     # 反转域名格式，全局唯一
name: "My Awesome Plugin"        # 人类可读名称
version: "1.0.0"                 # 语义版本
description: "做某件很酷的事情"

# 入口点：Plugin 子类的完全限定名
entrypoint: "my_plugin:MyPlugin"

# 兼容性声明
nahida_bot_version: ">=0.1.0,<1.0.0"   # 兼容的 bot 版本范围
sdk_version: ">=0.1.0,<1.0.0"          # 兼容的 SDK 版本范围

# 类型标签（用于分类和检索）
type: "tool"  # channel | tool | hook | integration | theme

# ── 权限声明（最小权限原则） ──
permissions:
  network:
    outbound:                           # 允许的外部网络访问
      - "https://api.example.com/*"
    inbound: false                      # 是否需要接收外部请求

  filesystem:
    read: ["workspace"]                 # 可读区域: workspace | data | temp
    write: ["workspace"]                # 可写区域

  memory:
    read: true                          # 可读记忆
    write: true                         # 可写记忆

  system:
    env_vars: ["MY_PLUGIN_*"]           # 可读取的环境变量（前缀匹配）
    subprocess: false                   # 是否允许执行子进程
    signal_handlers: false              # 是否允许注册信号处理

# ── 能力声明 ──
capabilities:
  # 如果 type 是 channel，声明通信协议
  # (仅 ChannelPlugin 需要)
  channel_protocols: []  # http_server, http_client, ws_server, ws_client, sse

  # 插件提供的工具（供 LLM 调用）
  tools: []
  #  - name: "web_search"
  #    description: "搜索互联网"

  # 插件监听的事件类型
  subscribes_to: []
  #  - "MessageReceived"
  #  - "AppStarted"

# ── 配置项定义（JSON Schema 格式） ──
config:
  type: "object"
  properties:
    api_key:
      type: "string"
      description: "第三方 API 密钥"
      secret: true                       # 标记为敏感，日志中自动脱敏
    max_retries:
      type: "integer"
      description: "最大重试次数"
      default: 3
      minimum: 1
      maximum: 10
    response_style:
      type: "string"
      description: "回复风格"
      default: "casual"
      enum: ["casual", "formal", "concise"]
  required: ["api_key"]
```

### 4.2 Manifest 数据模型

```python
# nahida_bot_sdk/manifest.py

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NetworkPermission(BaseModel):
    outbound: list[str] = Field(default_factory=list)
    inbound: bool = False


class FilesystemPermission(BaseModel):
    read: list[str] = Field(default_factory=lambda: ["workspace"])
    write: list[str] = Field(default_factory=list)


class MemoryPermission(BaseModel):
    read: bool = False
    write: bool = False


class SystemPermission(BaseModel):
    env_vars: list[str] = Field(default_factory=list)
    subprocess: bool = False
    signal_handlers: bool = False


class Permissions(BaseModel):
    network: NetworkPermission = Field(default_factory=NetworkPermission)
    filesystem: FilesystemPermission = Field(default_factory=FilesystemPermission)
    memory: MemoryPermission = Field(default_factory=MemoryPermission)
    system: SystemPermission = Field(default_factory=SystemPermission)


class Capabilities(BaseModel):
    channel_protocols: list[str] = Field(default_factory=list)
    tools: list[dict[str, str]] = Field(default_factory=list)
    subscribes_to: list[str] = Field(default_factory=list)


class PluginManifest(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    entrypoint: str                      # "module_path:ClassName"
    nahida_bot_version: str = ""
    sdk_version: str = ""
    type: str = "tool"                   # channel | tool | hook | integration | theme
    permissions: Permissions = Field(default_factory=Permissions)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    config: dict[str, Any] = Field(default_factory=dict)
```

### 4.3 敏感配置项的处理

`plugin.yaml` 中的 `config` 定义配置 schema，但 **不在 yaml 中存放实际值**。实际值通过以下途径提供：

1. **环境变量**：`NAHIDA_PLUGIN_{PLUGIN_ID}_{KEY}`（自动转换为大写，非字母替换为下划线）。
2. **bot 配置文件**：`config/plugins/{plugin_id}.yaml`，由 bot 管理员维护。
3. **Secrets 管理**：标记了 `secret: true` 的字段，推荐从环境变量或 vault 读取，bot 的日志系统自动脱敏。

运行时，Plugin Host 解析这些来源并合并为一个 `dict[str, Any]`，在调用 `Plugin.__init__` 之前校验 schema 合法性，然后通过 `api` 提供给插件。

## 5. 插件生命周期

### 5.1 状态机

```text
         discover
            │
            ▼
        ┌────────┐
        │Found   │  plugin.yaml 被扫描到
        └───┬────┘
    load │
            ▼
        ┌────────┐
        │Loaded  │  Python 包被导入，Plugin 类被实例化
        └───┬────┘
   enable │
            ▼
        ┌─────────┐
        │Enabled  │  on_load() + on_enable() 被调用，事件处理器和工具生效
        └──┬──┬───┘
  disable │  │ reload
           │  │
           ▼  ▼
    ┌──────────┐  ┌──────────┐
    │Disabled  │  │Reloading │  on_disable() → on_unload() → 重新 load → enable
    └─────┬────┘  └──────────┘
 unload │
           ▼
        ┌──────────┐
        │Unloaded  │  模块从 sys.modules 移除（如可安全移除）
        └──────────┘
```

### 5.2 PluginManager 接口

```python
# nahida_bot/plugins/manager.py (伪代码)

class PluginManager:
    """管理所有插件的生命周期。"""

    def __init__(self, event_bus: EventBus, api_bridge: APIBridge) -> None: ...

    async def discover(self, paths: list[Path]) -> list[PluginManifest]:
        """在给定路径中扫描 plugin.yaml，返回所有发现的 manifest。"""
        ...

    async def load(self, plugin_id: str) -> None:
        """加载指定插件：导入模块、校验 manifest、实例化 Plugin。"""
        ...

    async def enable(self, plugin_id: str) -> None:
        """启用插件：调用 on_load() + on_enable()，注册事件和工具。"""
        ...

    async def disable(self, plugin_id: str) -> None:
        """禁用插件：调用 on_disable()，取消所有事件订阅和工具注册。"""
        ...

    async def reload(self, plugin_id: str) -> None:
        """热重载插件：disable → unload → load → enable。"""
        ...

    async def unload(self, plugin_id: str) -> None:
        """卸载插件：调用 on_unload()，释放资源。"""
        ...

    async def shutdown_all(self) -> None:
        """按依赖逆序关闭所有已启用的插件。"""
        ...
```

### 5.3 加载顺序与依赖

插件的 `plugin.yaml` 中可选声明依赖：

```yaml
depends_on:
  - id: "nahida_bot.builtin.file_reader"
    version: ">=0.1.0"
```

Plugin Host 按拓扑排序加载插件。循环依赖视为加载错误。

## 6. 事件系统集成

### 6.1 当前状态与问题

当前 `core/events.py` 实现了 Core API（subscribe/unsubscribe/publish/publish_nowait/shutdown），但存在以下不足：

1. **事件类型不足**：只有 4 个生命周期事件（AppInitializing/AppStarted/AppStopping/AppStopped），缺少消息事件、工具事件、插件事件等。
2. **Handler 执行模型**：当前是同类型内严格串行，一个慢 handler 会阻塞后续所有 handler。
3. **无优先级**：无法保证核心 handler 先于插件 handler 执行。
4. **Facade API 未实现**：`event-system.md` 中规划的装饰器式注册和 Depends 注入尚未落地。

### 6.2 需要新增的事件类型

插件系统需要以下事件类型（定义在 `core/events.py` 或拆分后的 `core/events/types.py`）：

```python
# ── 消息事件 ──

@dataclass(slots=True, frozen=True)
class MessagePayload:
    message: InboundMessage
    session_id: str

class MessageReceived(Event[MessagePayload]):
    """收到外部平台消息（经 ChannelPlugin 标准化后触发）。"""

class MessageSending(Event[MessagePayload]):
    """即将发送消息（插件可拦截/修改）。"""

class MessageSent(Event[MessagePayload]):
    """消息已成功发送。"""


# ── 工具事件 ──

@dataclass(slots=True, frozen=True)
class ToolCallPayload:
    tool_name: str
    arguments: dict[str, Any]
    session_id: str
    plugin_id: str  # 注册该工具的插件

class ToolCalled(Event[ToolCallPayload]):
    """工具被调用前（可用于审计、限流）。"""

class ToolCompleted(Event[ToolCallPayload]):
    """工具执行完成。"""


# ── 插件事件 ──

@dataclass(slots=True, frozen=True)
class PluginPayload:
    plugin_id: str
    plugin_name: str
    plugin_version: str

class PluginLoaded(Event[PluginPayload]):
    """插件已加载。"""

class PluginEnabled(Event[PluginPayload]):
    """插件已启用。"""

class PluginDisabled(Event[PluginPayload]):
    """插件已禁用。"""

class PluginError(Event[PluginErrorPayload]):
    """插件运行时出错。"""
```

### 6.3 Handler 执行策略改进

解决 `core/events.py` 中 FIXME 指出的问题：

```python
# nahida_bot/core/events.py (增强后的 publish 方法)

class EventBus:
    # ...

    async def publish(self, event: Event[Any]) -> PublishResult:
        if self._closed:
            raise EventBusClosedError("EventBus is already closed")

        entries = self._handlers.get(type(event), [])

        # 按优先级排序（数值越小越优先）
        sorted_entries = sorted(entries, key=lambda e: e.priority)

        # 分两阶段执行
        # Phase 1: 同步阶段（priority <= 0）—— 串行执行，保证顺序
        #   用于核心逻辑：消息路由、权限检查等
        # Phase 2: 异步阶段（priority > 0）—— 并发执行，per-handler 超时
        #   用于插件逻辑：日志、通知、第三方集成等

        sync_handlers = [e for e in sorted_entries if e.priority <= 0]
        async_handlers = [e for e in sorted_entries if e.priority > 0]

        failures: list[HandlerFailure] = []

        # Phase 1: 串行
        for entry in sync_handlers:
            try:
                result = entry.handler(event, self._context)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                failures.append(HandlerFailure(
                    handler_name=entry.name, error=str(exc)
                ))
                self._context.logger.exception("Sync handler failed", exc_info=exc)

        # Phase 2: 并发（带 per-handler 超时）
        if async_handlers:
            async def _run_with_timeout(entry: HandlerEntry) -> None:
                try:
                    result = entry.handler(event, self._context)
                    if inspect.isawaitable(result):
                        await asyncio.wait_for(result, timeout=entry.timeout)
                except TimeoutError:
                    failures.append(HandlerFailure(
                        handler_name=entry.name,
                        error=f"Handler timed out after {entry.timeout}s",
                    ))
                except Exception as exc:
                    failures.append(HandlerFailure(
                        handler_name=entry.name, error=str(exc)
                    ))
                    self._context.logger.exception("Async handler failed", exc_info=exc)

            await asyncio.gather(
                *[_run_with_timeout(e) for e in async_handlers],
                return_exceptions=False,
            )

        return PublishResult(
            dispatched=len(sync_handlers) + len(async_handlers),
            failures=tuple(failures),
        )
```

**核心 Handler 注册**（priority ≤ 0）：

- 消息路由器 (priority = -100)
- 权限检查器 (priority = -50)
- Session 解析器 (priority = -20)

**插件 Handler 注册**（priority > 0）：

- 默认 priority = 100，可由插件自行指定
- 有 per-handler 超时保护（默认 30 秒）

### 6.4 插件如何注册事件处理器

两种方式：

**方式 A：装饰器式（推荐）**

```python
class MyPlugin(Plugin):
    async def on_load(self) -> None:
        pass  # 装饰器在类定义时就声明了注册关系

    @api.on_event(MessageReceived)
    async def handle_message(self, event: MessageReceived) -> None:
        await self.api.send_message(
            event.payload.message.chat_id,
            OutboundMessage(text="收到！"),
        )
```

**方式 B：编程式（在 on_load 中注册）**

```python
class MyPlugin(Plugin):
    async def on_load(self) -> None:
        self._sub = self.api.subscribe(MessageReceived, self._on_message)

    async def _on_message(self, event: MessageReceived) -> None:
        ...

    async def on_unload(self) -> None:
        self._sub.unsubscribe()
```

## 7. 权限系统

### 7.1 运行时权限检查

Plugin Host 在插件调用 `BotAPI` 方法时，根据 manifest 中声明的权限进行拦截：

```python
# nahida_bot/plugins/permissions.py (伪代码)

class PermissionChecker:
    """根据 manifest 权限声明拦截 API 调用。"""

    def __init__(self, manifest: PluginManifest) -> None:
        self._manifest = manifest

    def check_network(self, url: str) -> None:
        """检查插件是否被允许访问目标 URL。"""
        if not self._match_patterns(url, self._manifest.permissions.network.outbound):
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' cannot access {url} "
                f"(no matching outbound network permission)"
            )

    def check_filesystem_read(self, zone: str) -> None:
        """检查插件是否被允许读取指定区域。"""
        if zone not in self._manifest.permissions.filesystem.read:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' cannot read from {zone}"
            )

    # ... 其他 check 方法

    @staticmethod
    def _match_patterns(value: str, patterns: list[str]) -> bool:
        """用 glob 风格的模式匹配检查值是否在允许列表中。"""
        import fnmatch
        return any(fnmatch.fnmatch(value, p) for p in patterns)
```

权限检查发生在 `APIBridge` 层——这是连接 SDK 接口和真实 bot 实现的中间层：

```text
Plugin → BotAPI (SDK 接口) → APIBridge (权限检查 + 真实调用) → Core/Agent/Workspace
```

### 7.2 审计日志

所有权限拒绝和敏感操作都通过结构化日志记录：

```python
logger.info(
    "permission_denied",
    plugin_id=plugin_id,
    resource="network",
    action="outbound",
    target=url,
)
```

## 8. 插件目录结构（磁盘布局）

### 8.1 单文件插件（简单场景）

```text
plugins/
  my_tool/
    plugin.yaml
    my_tool.py        # 入口：class MyTool(Plugin): ...
```

### 8.2 包插件（推荐）

```text
plugins/
  qq_channel/
    plugin.yaml
    qq_channel/
      __init__.py     # from .plugin import QQChannel
      plugin.py       # class QQChannel(ChannelPlugin): ...
      handlers.py
      api_client.py
    tests/
      test_plugin.py  # 依赖 nahida-bot-sdk，不需要 nahida-bot
```

### 8.3 内置插件

```text
nahida_bot/plugins/builtin/
  __init__.py
  file_reader/
    plugin.yaml
    file_reader.py
  command_executor/
    plugin.yaml
    command_executor.py
  web_fetcher/
    plugin.yaml
    web_fetcher.py
  memory_retrieval/
    plugin.yaml
    memory_retrieval.py
```

## 9. APIBridge——SDK 接口到真实实现的桥接

```python
# nahida_bot/plugins/api_bridge.py (伪代码)

class RealBotAPI:
    """BotAPI 的真实实现，注入到插件中。

    每个插件实例获得独立的 RealBotAPI，内置该插件的权限检查器。
    """

    def __init__(
        self,
        plugin_id: str,
        manifest: PluginManifest,
        event_bus: EventBus,
        agent_loop: AgentLoop,
        workspace_manager: WorkspaceManager,
        memory_store: MemoryStore,
        permission_checker: PermissionChecker,
        logger: PluginLogger,
    ) -> None:
        self._plugin_id = plugin_id
        self._manifest = manifest
        self._event_bus = event_bus
        self._agent_loop = agent_loop
        self._workspace = workspace_manager
        self._memory = memory_store
        self._permissions = permission_checker
        self._logger = logger

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        self._permissions.check_network(target if target.startswith("http") else "*")
        # 实际发送逻辑...
        ...

    async def workspace_read(self, path: str) -> str:
        self._permissions.check_filesystem_read("workspace")
        return await self._workspace.read_file(path)

    # ... 其他方法实现
```

## 10. 异常隔离与降级

### 10.1 隔离策略

1. **Handler 级隔离**：EventBus 已实现 per-handler 错误隔离。增强版加入超时保护。
2. **工具调用隔离**：工具执行在 `asyncio.create_task` 中运行，有超时和异常捕获。
3. **插件级隔离**：PluginManager 捕获所有 `on_load`/`on_enable`/`on_disable`/`on_unload` 中的异常，记录日志，标记插件为 `Error` 状态，不影响其他插件。

```python
async def _safe_call(self, plugin: Plugin, method_name: str) -> None:
    """安全调用插件方法，捕获所有异常。"""
    try:
        method = getattr(plugin, method_name)
        await asyncio.wait_for(method(), timeout=60.0)
    except TimeoutError:
        self.logger.error(
            "plugin_method_timeout",
            plugin_id=plugin.manifest.id,
            method=method_name,
        )
        self._mark_error(plugin.manifest.id, "timeout")
    except Exception as exc:
        self.logger.exception(
            "plugin_method_error",
            plugin_id=plugin.manifest.id,
            method=method_name,
        )
        self._mark_error(plugin.manifest.id, str(exc))
        # 触发 PluginError 事件，通知其他插件和管理系统
        await self.event_bus.publish(PluginError(payload=PluginErrorPayload(
            plugin_id=plugin.manifest.id,
            method=method_name,
            error=str(exc),
            ...
        )))
```

### 10.2 降级策略

当插件出错时：

1. **标记为 Error 状态**：不再向该插件分发事件。
2. **取消已注册的处理器和工具**：从 EventBus 和 ToolRegistry 中移除。
3. **通知管理接口**：通过 `PluginError` 事件通知 WebUI / CLI。
4. **可选自动重启**：管理员可配置自动重试策略（最大次数 + 冷却时间）。

## 11. 与现有设计的关系

### 11.1 已有设计整合

| 已有设计 | 本文档整合方式 |
|---------|-------------|
| `channel-plugin.md` | ChannelPlugin 作为 Plugin 子类，复用完整的 manifest、权限、生命周期机制。通信方式声明在 `capabilities.channel_protocols` 中。 |
| `event-system.md` | Core API 保持不变，增强 publish 为双阶段执行。Facade API（装饰器 + Depends）在插件层实现为 `api.on_event()` 装饰器。 |
| `directory-structure.md` | `plugins/` 目录结构新增 `api_bridge.py`，`builtin/` 下每个内置插件独立子目录。 |
| `runtime-flows.md` | 消息主流程不变。新增「插件注册工具 → LLM 调用 → 权限检查 → 执行」的完整链路。 |
| `priorities-and-strategy.md` | Plugin 系统仍为 P1，但新增 SDK 分离和测试基础设施作为 P1 前置。 |
| `data-and-state.md` | 插件配置存储在 `data/plugins/{plugin_id}/`，插件状态纳入 transient/session 层。 |
| `security-observability.md` | 插件最小权限原则、审计日志、降级告警与安全文档对齐。 |

### 11.2 ChannelPlugin 在本设计中的位置

ChannelPlugin 是 Plugin 的子类，额外提供：

```python
# nahida_bot_sdk/channel_plugin.py

class ChannelPlugin(Plugin):
    """接入外部消息平台的插件基类。"""

    # 在 capabilities.channel_protocols 中声明

    async def handle_inbound_event(self, event: dict) -> None:
        """处理来自外部系统的原生事件，转换为 InboundMessage 并触发 Agent。"""
        ...

    async def send_message(
        self, target: str, message: OutboundMessage
    ) -> str:
        """向外部平台发送消息。"""
        ...

    async def get_user_info(self, user_id: str) -> dict:
        """获取用户信息（可选）。"""
        return {}

    async def get_group_info(self, group_id: str) -> dict:
        """获取群组信息（可选）。"""
        return {}
```

ChannelPlugin 的 `plugin.yaml` 中 `type: "channel"` 且 `capabilities.channel_protocols` 非空。Plugin Host 对 ChannelPlugin 有特殊处理：在 `on_enable()` 后自动注册消息路由和 webhook 端点（如果声明了 `http_server` 协议）。

## 12. 实施计划

### Phase 3.0 — SDK 基线（前置）

1. 创建 `nahida-bot-sdk` 包，实现 `Plugin` 基类、`BotAPI` 协议、`PluginManifest` 模型、消息类型。
2. 实现 `MockBotAPI` 和测试 fixture。
3. 发布到 PyPI（或本地 `uv` 可安装）。
4. 验证：一个不依赖 `nahida-bot` 的插件可以安装 SDK 并完成编译 + 单元测试。

### Phase 3.1 — Manifest 与 Loader

1. 实现 `plugin.yaml` 解析和校验（Pydantic 模型）。
2. 实现插件发现（扫描指定目录）和加载（动态导入 + 入口点解析）。
3. 实现基础生命周期（load → enable → disable → unload）。
4. 验证：可以加载一个最小插件并调用 `on_load()`。

### Phase 3.2 — 事件系统增强

1. 新增消息事件、工具事件、插件事件等事件类型。
2. 改进 `EventBus.publish()` 为双阶段执行模型（同步核心 + 异步插件）。
3. 实现优先级和 per-handler 超时。
4. 验证：核心 handler 先于插件 handler 执行，慢插件不阻塞核心。

### Phase 3.3 — APIBridge 与权限

1. 实现 `RealBotAPI`，桥接 SDK 接口到真实 bot 实现。
2. 实现 `PermissionChecker`，根据 manifest 拦截越权调用。
3. 实现审计日志。
4. 验证：插件调用越权 API 时抛出 `PermissionDenied` 并记录审计日志。

### Phase 3.4 — 异常隔离与内置插件

1. 实现 PluginManager 的异常隔离和安全调用机制。
2. 实现降级策略和 `PluginError` 事件。
3. 实现 1-2 个内置插件（如 file_reader、web_fetcher）。
4. 验证：插件崩溃不影响核心和其他插件。

### Phase 3.5 — ChannelPlugin 接口

1. 实现 `ChannelPlugin` 基类。
2. 实现 HTTP Server 模式的 webhook 端点自动注册。
3. 实现消息标准化流程（平台事件 → InboundMessage → Agent → OutboundMessage → 平台回复）。
4. 验证：ChannelPlugin 可以接收外部事件并触发 Agent 回复。

## 13. 设计约束与注意事项

1. **SDK 依赖极简**：`nahida-bot-sdk` 不允许引入任何框架级依赖（仅 `pydantic` + `typing_extensions`）。
2. **单进程模型**：当前设计为单进程内多插件，通过 asyncio 并发。不引入进程级隔离（如 subprocess/multiprocessing），但架构上预留了未来扩展的可能。
3. **热加载限制**：Python 模块的 `sys.modules` 缓存使得完全卸载困难。对于需要热重载的场景，采用「禁用旧实例 → 创建新实例」策略，不保证模块级完全卸载。
4. **配置 schema 兼容性**：manifest 中的 `config` 字段使用 JSON Schema 描述，未来新增字段时必须向后兼容（新字段有默认值）。
5. **事件类型注册**：所有事件类型定义在 `core/events.py` 或 `core/events/types.py` 中集中管理。插件不允许自定义新事件类型，只能使用 bot 定义的事件类型。这一约束是为了防止事件类型碎片化——如果未来有明确的生态需求，可以考虑开放插件自定义事件，但需要额外的注册机制和命名空间隔离。
