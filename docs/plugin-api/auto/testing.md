---
title: "测试工具"
description: 从 nahida_bot_sdk.testing._mocks 自动生成的 API 参考。
---

# 测试工具

> **源码路径:** `nahida_bot_sdk.testing._mocks`

Testing utilities for nahida-bot plugin development.

---


## 类


### MockBotAPI

Minimal no-op BotAPI stub for testing.

All methods are no-ops.  For stateful tracking (recording calls),
use `RecordingMockBotAPI` instead.

**属性 (Properties):**

#### scheduler_service

- **返回类型:** `Any | None`

#### logger

- **返回类型:** `Any`

**方法:**

#### `send_message(target: str, message: Any, channel: str = '')`

#### `record_session_event(session_id: str, content: str, source: str = '', metadata: dict[str, Any] | None = None)`

#### `on_event(event_type: type)`

#### `subscribe(event_type: type, handler: Callable[..., Awaitable[None]])`

#### `register_tool(name: str, description: str, parameters: dict[str, Any], handler: Callable[..., Awaitable[str]])`

#### `unregister_tool(name: str)`

#### `register_channel(channel: Any)`

#### `register_provider_type(type_key: str, factory: Any, config_schema: dict[str, Any] | None = None, description: str = '')`

#### `register_prompt_supplement(key: str, instruction: str, channel: str | None = None, filter: Any | None = None)`

#### `unregister_prompt_supplement(key: str)`

#### `register_command(name: str, handler: Callable[..., Awaitable[Any]], description: str = '', aliases: list[str] | None = None)`

#### `get_session(session_id: str)`

#### `record_message_delivery(target: ChatAddress | str, text: str, source: str, delivery_mode: str = '', status: str = 'sent', message_id: str = '', error: str = '', metadata: dict[str, Any] | None = None, source_session_id: str = '', source_chat_address: str = '', source_user_id: str = '')`

#### `clear_session(session_id: str)`

#### `start_new_session(address: ChatAddress)`

#### `get_session_info(session_id: str)`

#### `get_session_run_status(session_id: str)`

#### `list_commands()`

#### `list_models()`

#### `set_session_model(session_id: str, model_name: str)`

#### `llm_chat(messages: list[dict[str, str]], model: str = '', temperature: float | None = None, max_tokens: int | None = None, tools: list[dict[str, Any]] | None = None)`

#### `run_subagent(prompt: str, model: str = '', system_prompt: str = '', tools: list[str] | None = None, max_steps: int = 10, timeout_seconds: int = 300)`

#### `update_runtime_settings(session_id: str, updates: dict[str, Any])`

#### `memory_search(query: str, limit: int = 5)`

#### `memory_store(key: str, content: str, metadata: dict[str, Any] | None = None)`

#### `plugin_data_get(key: str)`

#### `plugin_data_set(key: str, value: Any)`

#### `plugin_data_delete(key: str)`

#### `plugin_data_list(prefix: str = '')`

#### `workspace_read(path: str)`

#### `workspace_write(path: str, content: str)`

#### `resolve_workspace_path(path: str)`

#### `publish_event(event: Any)`


### RecordingMockBotAPI

Stateful BotAPI mock that records calls for assertion.

Tracks: published events, registered tools, commands, handlers, services.

- **基类:** `MockBotAPI`

**属性 (Properties):**

#### published_events

- **返回类型:** `list[Any]`

#### registered_tools

- **返回类型:** `dict[str, dict[str, Any]]`

#### registered_commands

- **返回类型:** `dict[str, dict[str, Any]]`

#### registered_event_handlers

- **返回类型:** `dict[type, list[Callable[..., Awaitable[None]]]]`

#### registered_channels

- **返回类型:** `list[Any]`

#### registered_provider_types

- **返回类型:** `dict[str, dict[str, Any]]`

#### registered_prompt_supplements

- **返回类型:** `dict[str, dict[str, Any]]`

**方法:**

#### `on_event(event_type: type)`

#### `subscribe(event_type: type, handler: Callable[..., Awaitable[None]])`

#### `register_tool(name: str, description: str, parameters: dict[str, Any], handler: Any)`

#### `unregister_tool(name: str)`

#### `register_command(name: str, handler: Callable[..., Awaitable[Any]], description: str = '', aliases: list[str] | None = None)`

#### `register_channel(channel: Any)`

#### `register_provider_type(type_key: str, factory: Any, config_schema: dict[str, Any] | None = None, description: str = '')`

#### `register_prompt_supplement(key: str, instruction: str, channel: str | None = None, filter: Any | None = None)`

#### `unregister_prompt_supplement(key: str)`

#### `publish_event(event: Any)`

#### `plugin_data_get(key: str)`

#### `plugin_data_set(key: str, value: Any)`

#### `plugin_data_delete(key: str)`

#### `plugin_data_list(prefix: str = '')`


### StubChannelService

Plain object satisfying the ChannelService protocol (not a Plugin).

Use when you only need a channel-shaped object without Plugin machinery.
For tests that need a real Plugin, extend Plugin directly in the test.

**属性 (Properties):**

#### channel_id

- **返回类型:** `str`

**方法:**

#### `handle_inbound_event(event: dict[str, Any])`

#### `send_message(target: str, message: Any)`


### ConsoleMockBotAPI

Interactive-capable BotAPI mock for the plugin testing console.

Tracks all registrations and supports dispatching commands, tools,
and events interactively.  Also records sent messages so the console
can print responses.

**属性 (Properties):**

#### sent_messages

- **返回类型:** `list[tuple[str, OutboundMessage]]`

#### scheduler_service

- **返回类型:** `Any | None`

#### logger

- **返回类型:** `Any`

#### tool_names

- **返回类型:** `list[str]`

#### event_handler_types

- **返回类型:** `list[type]`

#### channel_count

- **返回类型:** `int`

**方法:**

#### `send_message(target: str, message: OutboundMessage, channel: str = '')`

#### `record_session_event(session_id: str, content: str, source: str = '', metadata: dict[str, Any] | None = None)`

#### `record_message_delivery(kw: Any = {})`

#### `on_event(event_type: type)`

#### `subscribe(event_type: type, handler: Callable[..., Awaitable[None]])`

#### `register_tool(name: str, description: str, parameters: dict[str, Any], handler: Callable[..., Awaitable[str]])`

#### `unregister_tool(name: str)`

#### `list_tools()`

#### `invoke_tool(name: str, arguments: dict[str, Any])`

Invoke a registered tool by name with JSON-decoded arguments.

#### `register_command(name: str, handler: Callable[..., Awaitable[Any]], description: str = '', aliases: list[str] | None = None)`

#### `list_commands()`

#### `invoke_command(name: str, args: str = '')`

Dispatch a command by name and return the result text.

#### `register_channel(channel: Any)`

#### `register_provider_type(type_key: str, factory: Any, config_schema: dict[str, Any] | None = None, description: str = '')`

#### `register_prompt_supplement(key: str, instruction: str, channel: str | None = None, filter: Any | None = None)`

#### `unregister_prompt_supplement(key: str)`

#### `get_session(session_id: str)`

#### `clear_session(session_id: str)`

#### `start_new_session(address: ChatAddress)`

#### `get_session_info(session_id: str)`

#### `get_session_run_status(session_id: str)`

#### `list_models()`

#### `set_session_model(session_id: str, model_name: str)`

#### `llm_chat(messages: list[dict[str, str]], model: str = '', temperature: float | None = None, max_tokens: int | None = None, tools: list[dict[str, Any]] | None = None)`

#### `run_subagent(prompt: str, model: str = '', system_prompt: str = '', tools: list[str] | None = None, max_steps: int = 10, timeout_seconds: int = 300)`

#### `update_runtime_settings(session_id: str, updates: dict[str, Any])`

#### `memory_search(query: str, limit: int = 5)`

#### `memory_store(key: str, content: str, metadata: dict[str, Any] | None = None)`

#### `plugin_data_get(key: str)`

#### `plugin_data_set(key: str, value: Any)`

#### `plugin_data_delete(key: str)`

#### `plugin_data_list(prefix: str = '')`

#### `workspace_read(path: str)`

#### `workspace_write(path: str, content: str)`

#### `resolve_workspace_path(path: str)`

#### `publish_event(event: Any)`


## 函数


### `load_plugin_for_test(plugin: Any)`

Register decorated handlers on a test BotAPI, then call `on_load`.
