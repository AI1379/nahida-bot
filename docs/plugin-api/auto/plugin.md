---
title: "Plugin 基类"
description: 从 nahida_bot_sdk.plugin 自动生成的 API 参考。
---

# Plugin 基类

> **源码路径:** `nahida_bot_sdk.plugin`

Plugin base class, registration decorators, SessionInfo, and MemoryRef.

---


## 类


### SessionInfo

Snapshot of an active session.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `session_id` | `str` | `—` |  |
| `channel` | `str` | `—` |  |
| `chat_id` | `str` | `—` |  |
| `user_id` | `str` | `—` |  |
| `workspace_id` | `str` | ``''`` |  |


### MemoryRef

A retrieved memory record.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `key` | `str` | `—` |  |
| `content` | `str` | `—` |  |
| `score` | `float` | ``0.0`` |  |
| `metadata` | `dict[str, Any]` | ``{}`` |  |


### Plugin

Base class for all nahida-bot plugins.

Two styles are supported for registering handlers:

1. **Decorator style** (recommended)::

       class MyPlugin(Plugin):
           @register_command("hello", description="Say hello")
           async def _cmd_hello(self, *, args, inbound, session_id) -> CommandResult:
               return CommandResult(OutboundMessage(text=f"Hello, {args}"))

   Decorated handlers are activated by the runtime when the plugin is enabled.

2. **Imperative style** (backward compatible)::

       class MyPlugin(Plugin):
           async def on_load(self) -> None:
               self.api.register_command("hello", self._cmd_hello, ...)

Both styles coexist — a plugin can use decorators for its own handlers
while also calling `self.api.register_*` in `on_load` for dynamic
registration.

- **基类:** `ABC`

**属性 (Properties):**

#### api

- **返回类型:** `BotAPI`

Bot capabilities available to this plugin.

#### manifest

- **返回类型:** `PluginManifest`

This plugin's manifest metadata.

**方法:**

#### `on_load()`

Called once before the plugin is enabled for the first time.

Override for one-time setup or imperative `self.api.register_*` calls.
Decorator registrations are handled by the runtime, not by this hook.

#### `on_unload()`

Called when the plugin is being unloaded. Clean up resources.

#### `on_enable()`

Called when the plugin is enabled after loading.

#### `on_disable()`

Called when the plugin is being disabled.


## 函数


### `register_command(name: str, description: str = '', aliases: list[str] | None = None)`

Decorator to mark a method as a slash-command handler.

The decorated method must accept keyword arguments
`args: str`, `inbound: InboundMessage`, `session_id: str`
and return a `CommandResult`.

Usage::

    class MyPlugin(Plugin):

        @register_command("hello", description="Say hello")
        async def _cmd_hello(
            self, *, args: str, inbound: InboundMessage, session_id: str
        ) -> CommandResult:
            return CommandResult.text(f"Hello, {args}")

### `register_tool(name: str, description: str = '', parameters: dict[str, Any] | None = None)`

Decorator to mark a method as an LLM-callable tool handler.

The decorated method should accept `**kwargs` and return `str`.

Usage::

    class MyPlugin(Plugin):

        @register_tool("my_tool", description="Does something useful")
        async def _handle_my_tool(self, **kwargs: object) -> str:
            return "done"

### `subscribe(event_type: type)`

Decorator to mark a method as an event subscriber.

The decorated method should accept a single `event` argument
and return `None`.

Usage::

    class MyPlugin(Plugin):

        @subscribe(MessageReceived)
        async def _on_message(self, event: MessageReceived) -> None:
            pass

### `bind_decorated_registrations(plugin: Any, api: BotAPI | None = None)`

Bind decorator declarations from *plugin* into *api*.

This is used by the runtime and SDK testing helpers. Plugin authors usually
do not need to call it directly.
