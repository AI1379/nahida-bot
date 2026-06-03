---
title: "BotAPI 协议"
description: 从 nahida_bot_sdk.api 自动生成的 API 参考。
---

# BotAPI 协议

> **源码路径:** `nahida_bot_sdk.api`

BotAPI protocol, ChannelService protocol, and related interfaces.

---


## 类


### PluginLogger

Structured logger automatically scoped to a plugin.

- **基类:** `Protocol`

**方法:**

#### `debug(msg: str, kwargs: object = {})`

#### `info(msg: str, kwargs: object = {})`

#### `warning(msg: str, kwargs: object = {})`

#### `error(msg: str, kwargs: object = {})`

#### `exception(msg: str, kwargs: object = {})`


### SubscriptionHandle

Handle returned by subscribe(); call unsubscribe() to detach.

- **基类:** `Protocol`

**方法:**

#### `unsubscribe()`


### ChannelService

Runtime contract for a channel service exposed by a plugin.

Channel services are ordinary plugins that explicitly register themselves
with `BotAPI.register_channel()`.

- **基类:** `Protocol`

**属性 (Properties):**

#### channel_id

- **返回类型:** `str`

Unique channel/platform identifier.

**方法:**

#### `handle_inbound_event(event: dict[str, Any])`

Normalize one platform-native event and publish a bot event.

#### `send_message(target: str, message: OutboundMessage)`

Send one normalized outbound message to the channel.


### BotAPI

Interface that plugins use to interact with the bot runtime.

The concrete implementation is injected at load time; tests inject a mock.

- **基类:** `Protocol`

**属性 (Properties):**

#### scheduler_service

- **返回类型:** `Any | None`

Scheduler service exposed to plugins that provide scheduler tools.

#### logger

- **返回类型:** `PluginLogger`

Structured logger scoped to this plugin.

**方法:**

#### `send_message(target: str, message: OutboundMessage, channel: str = '')`

Send a message to an external target. Returns platform message ID.

#### `record_session_event(session_id: str, content: str, source: str = '', metadata: dict[str, Any] | None = None)`

Write a system turn into a session's history without triggering a run.

#### `record_message_delivery(target: ChatAddress | str, text: str, source: str, delivery_mode: str = '', status: str = 'sent', message_id: str = '', error: str = '', metadata: dict[str, Any] | None = None, source_session_id: str = '', source_chat_address: str = '', source_user_id: str = '')`

Write an outbound delivery audit record without affecting memory.

#### `on_event(event_type: type)`

Decorator: register an event handler.

#### `subscribe(event_type: type, handler: Callable[..., Awaitable[None]])`

Programmatic event subscription. Returns an unsubscribe handle.

#### `register_tool(name: str, description: str, parameters: dict[str, Any], handler: Callable[..., Awaitable[str]])`

Register a tool that the LLM can call during conversations.

#### `register_channel(channel: ChannelService)`

Register a channel service implemented by this plugin.

#### `register_provider_type(type_key: str, factory: Callable[[dict[str, Any]], Any], config_schema: dict[str, Any] | None = None, description: str = '')`

Register a provider type that can be used from YAML config.

#### `register_command(name: str, handler: Callable[..., Awaitable[CommandHandlerResult]], description: str = '', aliases: list[str] | None = None)`

Register a /command that is matched from incoming messages.

#### `get_session(session_id: str)`

Look up session metadata.

#### `clear_session(session_id: str)`

Delete all turns for a session and return the number removed.

#### `start_new_session(address: ChatAddress)`

Switch the active chat to a new session and return its id.

#### `get_session_info(session_id: str)`

Return command-facing session metadata.

#### `get_session_run_status(session_id: str)`

Return command-facing agent run status for a session.

#### `list_commands()`

List registered commands.

#### `list_models()`

List available provider/model pairs.

#### `set_session_model(session_id: str, model_name: str)`

Switch the session to a model and return provider id if found.

#### `update_runtime_settings(session_id: str, updates: dict[str, Any])`

Merge runtime settings into session metadata and return the result.

#### `memory_search(query: str, limit: int = 5)`

Search the memory store for relevant records.

#### `memory_store(key: str, content: str, metadata: dict[str, Any] | None = None)`

Persist a record to the memory store.

#### `workspace_read(path: str)`

Read a file from the workspace. Subject to permission checks.

#### `workspace_write(path: str, content: str)`

Write a file to the workspace. Subject to permission checks.

#### `resolve_workspace_path(path: str)`

Resolve a workspace-relative path to an absolute local path.

#### `publish_event(event: Any)`

Publish an event on the event bus.
