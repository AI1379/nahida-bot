---
title: "消息类型"
description: 从 nahida_bot_sdk.messaging 自动生成的 API 参考。
---

# 消息类型

> **源码路径:** `nahida_bot_sdk.messaging`

Normalized message types for nahida-bot plugin communication.

---


## 类


### InboundAttachment

First-class inbound media object from an external platform.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `kind` | `str` | `—` |  |
| `platform_id` | `str` | `—` |  |
| `url` | `str` | ``''`` |  |
| `path` | `str` | ``''`` |  |
| `mime_type` | `str` | ``''`` |  |
| `file_size` | `int` | ``0`` |  |
| `width` | `int` | ``0`` |  |
| `height` | `int` | ``0`` |  |
| `alt_text` | `str` | ``''`` |  |
| `metadata` | `dict[str, object]` | ``{}`` |  |


### SenderContext

Short, provider-safe facts about the sender of one inbound message.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `display_name` | `str` | ``''`` |  |
| `platform_user_id` | `str` | ``''`` |  |
| `role_tags` | `tuple[str, ...]` | ``()`` |  |
| `is_bot` | `bool` | ``False`` |  |
| `is_self` | `bool` | ``False`` |  |


### ChatContext

Short, provider-safe facts about the chat that produced one message.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `platform` | `str` | ``''`` |  |
| `chat_type` | `str` | ``'unknown'`` |  |
| `platform_chat_id` | `str` | ``''`` |  |
| `display_name` | `str` | ``''`` |  |


### MessageContext

Per-turn facts rendered as structured LLM-visible context data.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `timestamp` | `float` | ``0.0`` |  |
| `channel` | `str` | ``''`` |  |
| `chat_type` | `str` | ``'unknown'`` |  |
| `chat_id` | `str` | ``''`` |  |
| `chat_display_name` | `str` | ``''`` |  |
| `sender_id` | `str` | ``''`` |  |
| `sender_display_name` | `str` | ``''`` |  |
| `sender_role_tags` | `tuple[str, ...]` | ``()`` |  |
| `extra_tags` | `tuple[str, ...]` | ``()`` |  |


### InboundMessage

Normalized message received from an external platform.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `message_id` | `str` | `—` |  |
| `platform` | `str` | `—` |  |
| `chat_id` | `str` | `—` |  |
| `user_id` | `str` | `—` |  |
| `text` | `str` | `—` |  |
| `raw_event` | `dict[str, Any]` | `—` |  |
| `is_group` | `bool` | ``False`` |  |
| `reply_to` | `str` | ``''`` |  |
| `timestamp` | `float` | ``0.0`` |  |
| `command_prefix` | `str` | ``'/'`` |  |
| `attachments` | `list[InboundAttachment]` | ``[]`` |  |
| `sender_context` | `SenderContext | None` | ``None`` |  |
| `chat_context` | `ChatContext | None` | ``None`` |  |
| `message_context` | `MessageContext | None` | ``None`` |  |
| `mentions_bot` | `bool` | ``False`` |  |
| `mentioned_user_ids` | `tuple[str, ...]` | ``()`` |  |


### MediaDownloadResult

Result of downloading a media file from a platform.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `path` | `str` | `—` |  |
| `file_name` | `str` | ``''`` |  |
| `mime_type` | `str` | ``''`` |  |
| `file_size` | `int` | ``0`` |  |


### Attachment

A file attachment for an outbound message.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `type` | `str` | `—` |  |
| `path` | `str` | `—` |  |
| `filename` | `str` | ``''`` |  |
| `mime_type` | `str` | ``''`` |  |
| `caption` | `str` | ``''`` |  |


### OutboundMessage

Normalized message to send to an external platform.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `text` | `str` | `—` |  |
| `reply_to` | `str` | ``''`` |  |
| `reasoning` | `str` | ``''`` |  |
| `extra` | `dict[str, Any]` | ``{}`` |  |
| `attachments` | `list[Attachment]` | ``[]`` |  |
