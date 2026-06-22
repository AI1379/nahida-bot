---
title: "事件系统"
description: 从 nahida_bot_sdk.events 自动生成的 API 参考。
---

# 事件系统

> **源码路径:** `nahida_bot_sdk.events`

Event types and payloads for nahida-bot plugin communication.

---


## 类


### Event

Base typed event model.

- **基类:** `Generic[PayloadT]`

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `payload` | `PayloadT` | `—` |  |
| `event_id` | `UUID` | ``{}`` |  |
| `trace_id` | `str` | ``''`` |  |
| `source` | `str` | ``''`` |  |
| `occurred_at` | `datetime` | ``{}`` |  |


### AppLifecyclePayload

Payload used by application lifecycle events.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `app_name` | `str` | `—` |  |
| `debug` | `bool` | `—` |  |


### AppInitializing

Raised before application initialization starts.

- **基类:** `Event[AppLifecyclePayload]`


### AppStarted

Raised after application startup completes.

- **基类:** `Event[AppLifecyclePayload]`


### AppStopping

Raised before application shutdown starts.

- **基类:** `Event[AppLifecyclePayload]`


### AppStopped

Raised after application shutdown completes.

- **基类:** `Event[AppLifecyclePayload]`


### PluginPayload

Payload for plugin lifecycle events.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `plugin_id` | `str` | `—` |  |
| `plugin_name` | `str` | `—` |  |
| `plugin_version` | `str` | `—` |  |


### PluginLoaded

Raised after a plugin has been loaded.

- **基类:** `Event[PluginPayload]`


### PluginEnabled

Raised after a plugin has been enabled.

- **基类:** `Event[PluginPayload]`


### PluginDisabled

Raised after a plugin has been disabled.

- **基类:** `Event[PluginPayload]`


### PluginUnloaded

Raised after a plugin has been fully unloaded.

- **基类:** `Event[PluginPayload]`


### PluginErrorPayload

Payload for plugin error events.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `plugin_id` | `str` | `—` |  |
| `plugin_name` | `str` | `—` |  |
| `method` | `str` | `—` |  |
| `error` | `str` | `—` |  |


### PluginErrorOccurred

Raised when a plugin method raises an unhandled exception.

- **基类:** `Event[PluginErrorPayload]`


### MessagePayload

Payload for message lifecycle events.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `message` | `Any` | `—` |  |
| `session_id` | `str` | `—` |  |


### MessageReceived

Raised after a channel service plugin normalizes an inbound event.

- **基类:** `Event[MessagePayload]`


### MessageObserved

Raised for inbound messages recorded as context but not handled by agent.

- **基类:** `Event[MessagePayload]`


### AgentResponseRequestPayload

Payload for a plugin-initiated request to run the main agent.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `message` | `Any` | `—` |  |
| `session_id` | `str` | `—` |  |
| `chat_address` | `ChatAddress` | `—` |  |
| `requester_plugin_id` | `str` | `—` |  |
| `reason` | `str` | `—` |  |
| `instruction` | `str` | ``''`` |  |
| `synthetic` | `bool` | ``False`` |  |
| `observed_messages` | `tuple[Any, ...]` | ``()`` |  |
| `reply_to_message_id` | `str | None` | ``None`` |  |


### AgentResponseRequested

Raised when a plugin asks the router to let the agent join a chat.

- **基类:** `Event[AgentResponseRequestPayload]`


### MessageSending

Raised before sending a message for observation and audit hooks.

- **基类:** `Event[MessagePayload]`


### MessageSent

Raised after a message has been successfully sent.

- **基类:** `Event[MessagePayload]`
