# Plugin API 参考

> 本页面是 nahida-bot 插件开发 API 的完整参考。面向第三方插件开发者，无需阅读内部源码。

## 目录结构

一个完整的 nahida-bot 插件由两个文件组成：

```
my-plugin/
├── pyproject.toml       ← Python 包元数据与依赖声明
├── plugin.yaml          ← nahida-bot 特有元数据（权限、入口点、配置）
└── my_plugin/
    ├── __init__.py
    └── plugin.py        ← Plugin 子类实现
```

`pyproject.toml` 负责标准的 Python 打包：

```toml
[project]
name = "nahida-plugin-image-gen"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "nahida-bot-sdk>=0.1.0",  # SDK 版本约束
    "pillow>=10.0",           # 你需要的任何第三方库
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["my_plugin"]
```

安装：`uv pip install ./my-plugin` 会同时安装所有 Python 依赖。

---

## 1. plugin.yaml — 插件清单

```yaml
id: "com.example.my-plugin"    # 必填，全局唯一标识
name: "My Plugin"              # 必填，人类可读名称
version: "0.1.0"              # 必填，语义版本
description: "做了什么"        # 可选，供 WebUI 展示
entrypoint: "my_plugin.plugin:MyPluginClass"  # 必填，"module.path:ClassName"

# nahida-bot 兼容性（可选，默认 warn 不 block）
nahida_bot_version: ">=0.1.0"
sdk_version: ">=0.1.0"

# 加载阶段（可选，默认 "post-agent"）
#   "pre-agent"  — 在 AgentLoop 之前加载（用于注册 Provider 类型）
#   "post-agent" — 在 AgentLoop 之后加载（常规插件）
load_phase: "post-agent"

# 依赖其他插件（可选）
depends_on:
  - id: "builtin-commands"
    version: ">=0.1.0"
```

### 1.1 权限声明

所有权限声明在运行时由 `PermissionChecker` 强制执行。越权调用会抛出 `PermissionDenied`。

```yaml
permissions:
  network:
    outbound: ["https://api.example.com/*"]  # 允许访问的外部 URL（glob 匹配）
    inbound: false                            # 是否需要接收外部 HTTP 请求

  filesystem:
    read: ["workspace"]                       # 可读取的沙箱区域（当前仅 "workspace"）
    write: ["workspace"]                      # 可写入的沙箱区域

  memory:
    read: true                                # 可读取持久化记忆
    write: true                               # 可写入持久化记忆

  plugin_data:
    read: true                                # 可读取插件数据存储
    write: true                               # 可写入插件数据存储

  plugin_secrets:
    read: true                                # 可读取本插件的不透明 secret
    write: true                               # 可创建或删除本插件的 secret

  system:
    env_vars: ["MY_PLUGIN_*"]                 # 可读取的环境变量（前缀匹配）
    subprocess: false                          # 是否允许执行子进程
    signal_handlers: false                     # 是否允许注册信号处理
```

### 1.2 能力声明

静态声明插件提供的能力，供 WebUI 和冲突检测使用：

```yaml
capabilities:
  tools:
    - name: "generate_image"
      description: "根据文本提示生成图片"
  subscribes_to:
    - "MessageReceived"
```

### 1.3 插件配置

```yaml
# 框架级生命周期默认值；不属于插件业务配置
enabled: false

# 默认配置值
config:
  api_key: ""
  max_retries: 3
  response_style: "casual"

# JSON Schema 格式的配置校验（可选，有定义时 WebUI 会生成表单）
config_schema:
  type: "object"
  properties:
    api_key:
      type: "string"
      description: "第三方 API 密钥"
      secret: true          # 标记为敏感，日志自动脱敏
    max_retries:
      type: "integer"
      default: 3
      minimum: 1
      maximum: 10
    response_style:
      type: "string"
      default: "casual"
      enum: ["casual", "formal", "concise"]
  required: ["api_key"]
```

`enabled` 是 Plugin Host 保留字段。主配置中可通过
`<plugin-id>.enabled` 覆盖它；框架会在实例化前消费该字段，不会把它放入
`self.manifest.config`。其余配置值会自动合并，并可在运行时通过
`self.manifest.config` 访问。

---

## 2. Plugin 基类

```python
from nahida_bot_sdk import Plugin


class MyPlugin(Plugin):
    """我的插件。"""

    async def on_load(self) -> None:
        """每次启用新实例时调用。可在此注册动态命令、工具、事件处理器。"""
        ...

    async def on_unload(self) -> None:
        """插件卸载时调用（可选）。清理资源、断开连接。"""
        ...

    async def on_enable(self) -> None:
        """插件启用时调用（可选）。"""
        ...

    async def on_disable(self) -> None:
        """插件禁用时调用（可选）。"""
        ...
```

| 方法 | 调用时机 | 是否必须 |
|------|---------|:------:|
| `on_load()` | 每次启用新实例时（装饰器声明激活后） | 否 |
| `on_enable()` | `on_load()` 成功后 | 否 |
| `on_disable()` | 禁用已启用实例时 | 否 |
| `on_unload()` | 禁用、显式卸载或热重载时 | 否 |

插件实例持有两个属性：
- `self.api` — [`BotAPI`](#3-botapi-协议) 实例，插件的全部能力入口
- `self.manifest` — [`PluginManifest`](#13-插件配置) 对象，包含 `plugin.yaml` 解析后的全部字段

---

## 3. BotAPI 协议

插件通过 `self.api` 与 bot 运行时交互。访问外部网络、工作空间、记忆等受控能力的方法会执行权限检查，越权调用会抛出 `PermissionDenied`。

### 3.1 消息

#### send_message

```python
async def send_message(
    self, target: str, message: OutboundMessage, *, channel: str = ""
) -> str:
```

向外部平台发送消息。返回平台消息 ID。

```python
from nahida_bot_sdk.messaging import OutboundMessage

await self.api.send_message(
    "group:12345",
    OutboundMessage(text="Hello!"),
)
```

`OutboundMessage.attachments` 可携带图片、文件等出站附件。插件如果需要先生成或下载一个本地文件再作为附件发送，推荐使用下面的托管临时文件 API，而不是自行写入系统临时目录。

#### 托管临时文件附件

```python
async def create_temp_file(
    self,
    *,
    suffix: str = "",
    prefix: str = "",
    purpose: str = "",
    ttl_seconds: int = 3600,
) -> ManagedTempFile

async def cleanup_temp_files(self, *, expired_only: bool = True) -> int
async def cleanup_temp_attachment(self, attachment: Attachment) -> bool
```

`create_temp_file()` 会为当前插件分配一个插件隔离的临时文件路径，并由核心记录清理元数据。核心会周期性 GC 过期文件；通过 `send_message()` 或命令返回发送成功的托管附件，默认也会在发送后自动清理。

```python
from pathlib import Path

from nahida_bot_sdk import CommandResult, OutboundMessage


temp_file = await self.api.create_temp_file(
    suffix=".png",
    prefix="preview",
    purpose="preview-image",
    ttl_seconds=3600,
)
Path(temp_file.path).write_bytes(image_bytes)

return CommandResult.outbound(
    OutboundMessage(
        text="Preview ready.",
        attachments=[
            temp_file.as_attachment(
                type="photo",
                filename="preview.png",
                mime_type="image/png",
            )
        ],
    )
)
```

如果同一个附件要推送给多个目标，不要让第一条发送后立刻清理文件。应设置 `cleanup_after_send=False`，全部目标发送结束后在 `finally` 中调用 `cleanup_temp_attachment()`：

```python
attachment = temp_file.as_attachment(
    type="photo",
    mime_type="image/png",
    cleanup_after_send=False,
)
try:
    for target in targets:
        await self.api.send_message(target, OutboundMessage(text="...", attachments=[attachment]))
finally:
    await self.api.cleanup_temp_attachment(attachment)
```

#### record_session_event

```python
async def record_session_event(
    self,
    session_id: str,
    content: str,
    *,
    source: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
```

向会话历史写入一条系统 turn，不触发 Agent 运行。

### 3.2 命令注册

```python
def register_command(
    self,
    name: str,
    handler: Callable[..., Awaitable[CommandHandlerResult]],
    *,
    description: str = "",
    aliases: list[str] | None = None,
    arguments: Sequence[CommandArgument] | None = None,
) -> None:
```

注册一个可由用户通过 `/<name>` 调用的命令。handler 使用关键字参数：

```python
async def handler(
    *, args: str, inbound: InboundMessage, session_id: str
) -> CommandHandlerResult:
    ...
```

```python
from nahida_bot_sdk import CommandArgument, CommandResult

async def on_load(self) -> None:
    self.api.register_command(
        "hello", self._hello,
        description="Say hello",
        aliases=["hi", "greet"],
        arguments=[
            CommandArgument(
                name="person",
                description="Who to greet",
                required=True,
            )
        ],
    )

async def _hello(
    self, *, args: str, inbound: InboundMessage, session_id: str
) -> CommandResult:
    return CommandResult.text(f"Hello, {inbound.user_id}!")
```

`arguments` 为 Discord 等原生命令界面提供参数类型、必填状态、静态选项和动态补全；
文本命令仍由 handler 从 `args` 字符串自行解析。

命令需要保存用户级状态时，使用 `inbound.sender_account_key`。它返回身份系统统一的
`{channel}:user:{platform_user_id}`，标识发送者账号而非群聊/私聊地址；渠道无法提供
稳定用户 ID 时返回空字符串。

**返回值类型**（`CommandHandlerResult`）：

| 值 | 效果 |
|----|------|
| `str` | 作为纯文本回复发送 |
| `OutboundMessage` | 结构化出站消息（可带附件） |
| `CommandResult.text("...")` | 显式文本回复 |
| `CommandResult.outbound(OutboundMessage(...))` | 显式结构化回复（可带附件） |
| `CommandResult.none()` / `None` | 不发送任何回复 |

### 3.3 工具注册

```python
def register_tool(
    self,
    name: str,
    description: str,
    parameters: dict[str, Any],  # JSON Schema
    handler: Callable[..., Awaitable[str]],
    *,
    requires_admin: bool = False,
) -> None:
```

注册一个 LLM 可调用的工具。参数 schema 遵循 [JSON Schema](https://json-schema.org/) 规范：

```python
self.api.register_tool(
    "add_numbers",
    "Add two integers",
    {
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "First number"},
            "b": {"type": "integer", "description": "Second number"},
        },
        "required": ["a", "b"],
    },
    self._add_numbers,
)

async def _add_numbers(self, a: int, b: int) -> str:
    return str(a + b)
```

`@register_tool` 装饰器同样支持 `requires_admin` 关键字参数。

`requires_admin=True` 把工具标记为特权工具：非管理员调用会被
`AuthorizationGate` 拒绝（fail-closed）。管理员判定来自 `identity.admins`
中的平台账号（见 [配置参考](../guide/configuration.md#身份与管理员授权)）；
`identity` 未启用时放行。内置的 `exec`、`message`、`workspace_write`、
`identity_manage` 和桌面远控工具（`desktop_exec`、`desktop_file_read`）
都是如此标记的。

LLM 调用工具时，参数名会映射到 handler 的关键字参数，返回值作为工具结果返回给 LLM。

### 3.4 事件系统

#### subscribe（编程式）

```python
def subscribe(
    self,
    event_type: type,
    handler: Callable[..., Awaitable[None]],
) -> SubscriptionHandle:
```

订阅某个事件类型。返回可用于取消订阅的句柄。

```python
from nahida_bot_sdk.events import MessageReceived

async def on_load(self) -> None:
    self.api.subscribe(MessageReceived, self._on_message)

async def _on_message(self, event: MessageReceived) -> None:
    inbound = event.payload.message
    if isinstance(inbound, InboundMessage) and "hello" in inbound.text.lower():
        await self.api.send_message(
            "group:12345",
            OutboundMessage(text="Hi there!"),
        )
```

#### on_event（装饰器式）

```python
def on_event(self, event_type: type) -> Callable:
```

```python
@self.api.on_event(MessageReceived)
async def handle_message(self, event: MessageReceived) -> None:
    ...
```

#### publish_event

```python
async def publish_event(self, event: Any) -> None:
```

向事件总线发布事件，触发其他插件的订阅。

### 3.5 工作空间

```python
async def workspace_read(self, path: str) -> str
async def workspace_write(self, path: str, content: str) -> None
def resolve_workspace_path(self, path: str) -> str  # 解析为绝对本地路径
```

需要 `permissions.filesystem.read: ["workspace"]` / `write: ["workspace"]`。

### 3.6 记忆

```python
async def memory_search(self, query: str, *, limit: int = 5) -> list[MemoryRef]
async def memory_store(self, key: str, content: str, *, metadata: dict | None = None) -> None
```

需要 `permissions.memory.read: true` / `write: true`。

`MemoryRef` 包含 `key`、`content`、`score`、`metadata` 字段。

### 3.7 插件数据存储

插件专属的持久化 KV 存储，适合保存运行时配置和状态（不需要 config 热更新）。每个插件的数据自动隔离，无法访问其他插件的数据。

```python
async def plugin_data_get(self, key: str) -> Any | None
async def plugin_data_set(self, key: str, value: Any) -> None
async def plugin_data_delete(self, key: str) -> bool
async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]
```

需要 `permissions.plugin_data.read: true` / `write: true`。

**用法示例：**

```python
# 存储
await self.api.plugin_data_set("server:my-server", {
    "transport": "sse",
    "url": "http://localhost:3000/sse",
})

# 读取
config = await self.api.plugin_data_get("server:my-server")

# 按前缀列出
servers = await self.api.plugin_data_list(prefix="server:")
# => {"server:my-server": {...}, "server:other": {...}}

# 删除
deleted = await self.api.plugin_data_delete("server:my-server")
```

**适用场景：**

- 动态插件配置（如 MCP 动态服务器）
- 运行时状态持久化
- 跨会话共享的结构化数据

**不适用：** 凭据等秘密（用下方 `plugin_secret_*`）、语义记忆（用 `memory_search`/`memory_store`）、文件存储（用 `workspace_read`/`workspace_write`）。

### 3.8 插件 Secret 存储

插件专属的不透明字符串存储，适合动态用户凭据。API 自动注入当前 `plugin_id`，且刻意
不提供 list 操作，以减少批量误暴露的风险。

```python
async def plugin_secret_get(self, key: str) -> str | None
async def plugin_secret_set(self, key: str, secret: str) -> None
async def plugin_secret_delete(self, key: str) -> bool
```

需要 `permissions.plugin_secrets.read: true` / `write: true`。不要把 secret 放入 key、
日志或普通 `plugin_data`。当前 SQLite 后端的安全边界与 Provider 凭据一致：数据库内容
未做应用层加密，部署者必须保护数据库文件及备份的访问权限。

### 3.9 会话管理

```python
async def get_session_info(self, session_id: str) -> dict[str, Any]
async def clear_session(self, session_id: str) -> int
async def start_new_session(self, address: ChatAddress) -> str | None
async def set_session_model(self, session_id: str, model_name: str) -> str | None
async def update_runtime_settings(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]
def get_session_run_status(self, session_id: str) -> dict[str, Any]
def list_commands(self) -> list[CommandInfo]
def list_models(self) -> list[dict[str, str]]
```

### 3.10 服务注册

```python
def register_channel(self, channel: ChannelService) -> None
def register_provider_type(
    self, type_key: str, factory: Callable[[dict[str, Any]], Any],
    *, config_schema: dict[str, Any] | None = None, description: str = "",
) -> None
```

- `register_channel` 需要 `permissions.network.inbound: true`
- `register_provider_type` 仅允许在 `load_phase: "pre-agent"` 的插件中调用

### 3.11 日志

```python
@property
def logger(self) -> PluginLogger:
```

返回插件专属的结构化日志器：

```python
self.api.logger.info("something_happened", key="value")
self.api.logger.error("something_failed", error=str(exc))
self.api.logger.exception("unexpected", exc_info=exc)
```

---

## 4. 消息类型

### InboundMessage

从外部平台收到的标准化消息：

```python
@dataclass(slots=True, frozen=True)
class InboundMessage:
    message_id: str          # 平台消息 ID
    platform: str            # "telegram", "qq" 等
    chat_id: str             # 平台会话 ID
    user_id: str             # 发送者平台 ID
    text: str                # 消息正文
    raw_event: dict          # 平台原生事件（供扩展解析）
    is_group: bool           # 是否群聊
    reply_to: str            # 被回复的消息 ID
    timestamp: float         # Unix 时间戳
    command_prefix: str      # 命令前缀，默认 "/"
    attachments: list[InboundAttachment]  # 附件列表
    sender_context: SenderContext | None  # 发送者信息
    chat_context: ChatContext | None      # 聊天信息
    message_context: MessageContext | None  # LLM context block 渲染
    mentions_bot: bool       # 是否 @了 bot
    mentioned_user_ids: tuple[str, ...]   # 被 @的用户 ID 列表
```

### OutboundMessage

向外发送的标准化消息：

```python
@dataclass(slots=True, frozen=True)
class OutboundMessage:
    text: str                # 消息正文
    reply_to: str = ""       # 回复指定消息
    reasoning: str = ""      # 思考过程（部分模型支持）
    extra: dict = {}         # 平台扩展参数
    attachments: list[Attachment] = []  # 要发送的附件
```

### Attachment（出站附件）

```python
@dataclass(slots=True, frozen=True)
class Attachment:
    type: str      # "photo", "document", "audio", "video"
    path: str      # 本地文件路径
    filename: str
    mime_type: str
    caption: str
    extra: dict    # 平台扩展参数；托管临时文件会在这里写入清理标记
```

普通插件通常不需要手写托管临时文件的 `extra` 字段；使用 `ManagedTempFile.as_attachment(...)` 会自动填入 `managed_temp_file`、`cleanup_token`、`cleanup_after_send`。

### InboundAttachment（入站附件）

```python
@dataclass(slots=True, frozen=True)
class InboundAttachment:
    kind: str        # image, audio, video, file
    platform_id: str # 平台资源 ID
    url: str         # 临时 URL（可能过期）
    path: str        # 本地缓存路径
    mime_type: str
    file_size: int
    width: int
    height: int
    alt_text: str    # 平台摘要或生成描述
```

### ChatAddress

聊天的类型化地址标识：

```python
@dataclass(slots=True, frozen=True)
class ChatAddress:
    channel: str          # 平台标识
    target_type: TargetType  # "private" | "group" | "channel" | "thread" | "unknown"
    target_id: str        # 目标 ID
    thread_id: str = ""   # 子话题 ID（可选）

    @classmethod
    def parse(cls, value: str) -> ChatAddress: ...
    @classmethod
    def from_inbound(cls, platform: str, chat_id: str, *, is_group: bool, chat_type: str): ...
```

字符串形式：`"channel:target_type:target_id[:thread_id]"`，如 `"milky:group:20001"`。

---

## 5. 事件类型

所有事件继承自 `Event[PayloadT]`。通过 `event.payload` 访问载荷。

### 应用生命周期

| 事件 | 载荷 | 触发时机 |
|------|------|---------|
| `AppInitializing` | `AppLifecyclePayload(app_name, debug)` | 初始化前 |
| `AppStarted` | 同上 | 启动完成后 |
| `AppStopping` | 同上 | 关闭前 |
| `AppStopped` | 同上 | 关闭完成后 |

### 插件生命周期

| 事件 | 载荷 | 触发时机 |
|------|------|---------|
| `PluginLoaded` | `PluginPayload(plugin_id, plugin_name, plugin_version)` | 模块导入后 |
| `PluginEnabled` | 同上 | on_load + on_enable 后 |
| `PluginDisabled` | 同上 | on_disable + on_unload 并释放实例后 |
| `PluginUnloaded` | 同上 | 完全卸载后 |
| `PluginErrorOccurred` | `PluginErrorPayload(plugin_id, plugin_name, method, error)` | 方法抛出异常 |

### 消息生命周期

| 事件 | 载荷 | 触发时机 |
|------|------|---------|
| `MessageReceived` | `MessagePayload(message: InboundMessage, session_id)` | 收到入站消息（Agent 处理前） |
| `MessageObserved` | 同上 | 入站消息仅记录不处理 |
| `MessageSending` | 同上 | 出站消息发送前 |
| `MessageSent` | 同上 | 出站消息发送后 |

---

## 6. 命令类型

```python
@dataclass(slots=True, frozen=True)
class CommandResult:
    message: OutboundMessage | None   # 要发送的消息
    suppress_response: bool           # 是否抑制回复

    @classmethod
    def text(cls, text: str) -> "CommandResult": ...
    @classmethod
    def outbound(cls, message: OutboundMessage) -> "CommandResult": ...
    @classmethod
    def none(cls) -> "CommandResult": ...


# 命令 handler 的合法返回值
CommandHandlerResult = str | OutboundMessage | CommandResult | None


@dataclass(slots=True, frozen=True)
class CommandInfo:
    """命令的公开元数据。"""
    name: str
    description: str
    aliases: tuple[str, ...]
    plugin_id: str
    arguments: tuple[CommandArgument, ...]
```

---

## 7. 测试

### 7.1 MockBotAPI

用于单元测试，无需启动 bot：

```python
from nahida_bot_sdk.testing import MockBotAPI, RecordingMockBotAPI, load_plugin_for_test

api = RecordingMockBotAPI()
plugin = MyPlugin(api=api, manifest=my_manifest)
await load_plugin_for_test(plugin)

# 验证工具已注册
assert "add_numbers" in api.registered_tools

# 验证命令已注册
# 验证事件已发布
assert len(api.published_events) > 0
```

`RecordingMockBotAPI` 跟踪：
- `registered_tools` — `dict[name, {description, parameters, handler}]`
- `registered_commands` — `dict[name_or_alias, {name, description, aliases, arguments, handler}]`
- `registered_event_handlers` — `dict[event_type, list[handler]]`
- `registered_provider_types` — `dict[type_key, {factory, config_schema, description}]`
- `published_events` — `list[event]`
- `registered_channels` — `list[channel]`

### 7.2 ConsoleMockBotAPI

交互式测试，支持命令/工具/事件调度：

```python
from nahida_bot_sdk import InboundMessage
from nahida_bot_sdk.testing import ConsoleMockBotAPI, load_plugin_for_test

api = ConsoleMockBotAPI()
plugin = MyPlugin(api=api, manifest=manifest)
await load_plugin_for_test(plugin)

# 调用命令
result = await api.invoke_command("hello", "arg")
# 调用工具
result = await api.invoke_tool("add_numbers", {"a": 3, "b": 5})
# 触发事件
inbound = InboundMessage(
    message_id="m1",
    platform="console",
    chat_id="test",
    user_id="u1",
    text="hi",
    raw_event={},
)
await api._trigger_event(
    MessageReceived(payload=MessagePayload(message=inbound, session_id="test"))
)
# 检查响应
print(api.sent_messages)  # list[(target, OutboundMessage)]
```

### 7.3 测试控制台（REPL）

```bash
uv run python -m nahida_bot_sdk.testing.console ./my-plugin
```

| 输入 | 效果 |
|------|------|
| `/cmd args` | 调用插件命令 |
| `#call tool {json}` | 调用工具 |
| `#fire Type {json}` | 触发事件 |
| `#tools` / `#commands` / `#events` | 列出注册内容 |
| 普通文本 | 模拟入站消息 |
| `#exit` | 退出 |

---

## 8. 示例：完整插件

一个注册了命令、工具和事件处理器的完整示例：

```python
from nahida_bot_sdk import Plugin
from nahida_bot_sdk.commands import CommandResult
from nahida_bot_sdk.events import MessageReceived, MessagePayload
from nahida_bot_sdk.messaging import InboundMessage, OutboundMessage


class MyPlugin(Plugin):

    async def on_load(self) -> None:
        # 注册命令
        self.api.register_command("hello", self._hello, description="Say hello")

        # 注册工具
        self.api.register_tool(
            "uppercase",
            "Convert text to uppercase",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            self._uppercase,
        )

        # 订阅消息事件
        self.api.subscribe(MessageReceived, self._on_message)

    async def _hello(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return CommandResult.text(f"Hello, {inbound.user_id}!")

    async def _uppercase(self, text: str) -> str:
        return text.upper()

    async def _on_message(self, event: MessageReceived) -> None:
        inbound = event.payload.message
        if isinstance(inbound, InboundMessage):
            await self.api.send_message(
                "console:private:test",
                OutboundMessage(text=f"Echo: {inbound.text}"),
            )
```

```yaml
# plugin.yaml
id: com.example.my-plugin
name: "My Plugin"
version: "0.1.0"
entrypoint: "my_plugin.plugin:MyPlugin"

permissions:
  network:
    outbound: ["*"]
```

```toml
# pyproject.toml
[project]
name = "my-cool-plugin"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["nahida-bot-sdk>=0.1.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```
