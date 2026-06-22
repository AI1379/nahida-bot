---
title: "命令相关"
description: 从 nahida_bot_sdk.commands 自动生成的 API 参考。
---

# 命令相关

> **源码路径:** `nahida_bot_sdk.commands`

Command result types and command metadata for nahida-bot plugins.

---


## 类


### CommandResult

Structured result returned by a command handler.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `message` | `OutboundMessage | None` | ``None`` |  |
| `suppress_response` | `bool` | ``False`` |  |

**方法:**

#### `text(text: str)`

Create a result that sends a plain text response.

#### `outbound(message: OutboundMessage)`

Create a result that sends a full outbound message.

#### `none()`

Create a result that intentionally sends no response.


### CommandInfo

Public metadata about a registered command.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `name` | `str` | `—` |  |
| `description` | `str` | `—` |  |
| `aliases` | `tuple[str, ...]` | `—` |  |
| `plugin_id` | `str` | `—` |  |


### CommandMatch

Result of attempting to match a command from a message.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `matched` | `bool` | `—` |  |
| `name` | `str` | ``''`` |  |
| `args` | `str` | ``''`` |  |
