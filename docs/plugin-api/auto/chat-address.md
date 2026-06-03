---
title: "聊天地址与会话"
description: 从 nahida_bot_sdk.chat_address 自动生成的 API 参考。
---

# 聊天地址与会话

> **源码路径:** `nahida_bot_sdk.chat_address`

ChatAddress and SessionKey — typed identity for chats and sessions.

---


## 类


### ChatAddress

External platform address for sending/receiving messages.

Canonical string form: `channel:target_type:target_id[:thread_id]`.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `channel` | `str` | `—` |  |
| `target_type` | `TargetType` | `—` |  |
| `target_id` | `str` | `—` |  |
| `thread_id` | `str` | ``''`` |  |
| `is_typed` | `bool` | `—` | True when target_type is a known standard type (not 'unknown'). |
| `chat_key` | `str` | `—` | Canonical typed chat key for session lookup. |
| `legacy_key` | `str` | `—` | Legacy 2-segment key for backward compatibility. |

**方法:**

#### `parse(value: str)`

Parse a colon-separated address string.

Handles typed (`channel:type:id`) and legacy (`channel:id`) formats.

#### `from_inbound(platform: str, chat_id: str, is_group: bool = False, chat_type: str = '')`

Construct a ChatAddress from inbound message metadata.


### SessionKey

Session identity derived from a ChatAddress with an optional suffix.

String form: `channel:type:id[:suffix...]`.

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `address` | `ChatAddress` | `—` |  |
| `suffix` | `str` | ``''`` |  |
| `is_derived` | `bool` | `—` | True when this key has a suffix (/new, cron isolated, etc.). |

**方法:**

#### `parse(value: str)`

Parse a colon-separated session key string.


## 函数


### `is_valid_target_type(value: str)`

Return whether a raw string is a valid ChatAddress target type.

### `normalize_target_type(value: str)`

Convert a raw string into a TargetType, raising for invalid values.

### `classify_session_key(value: str)`

Classify a persisted session id for migration/status displays.

## 类型别名


- `KNOWN_TARGET_TYPES`

- `TARGET_TYPE_UNKNOWN`

- `VALID_TARGET_TYPES`
