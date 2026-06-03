---
title: "Plugin 基类"
description: 从 nahida_bot_sdk.plugin 自动生成的 API 参考。
---

# Plugin 基类

> **源码路径:** `nahida_bot_sdk.plugin`

Plugin base class, SessionInfo, and MemoryRef.

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

Subclass and implement `on_load` to register event handlers and tools.
Optionally override `on_unload`, `on_enable`, and `on_disable`
for lifecycle management.

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

Called when the plugin is loaded. Register handlers/tools here.

#### `on_unload()`

Called when the plugin is being unloaded. Clean up resources.

#### `on_enable()`

Called when the plugin is enabled after loading.

#### `on_disable()`

Called when the plugin is being disabled.
