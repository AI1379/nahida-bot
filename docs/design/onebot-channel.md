# OneBot Channel 设计

> 状态：✅ OneBot **v11 已实现**（正向 WebSocket + WebHook、CQ 码与 array segment 归一化）。v12 为预留空模块（`channels/onebot/v12/` 仅有占位 `__init__.py`），**尚未实现**；下文涉及"双版本"的内容为原始设计意图，v12 部分以实际代码为准。
> 日期：2026-05-28
> 最近更新：2026-08-10
> 目标：为 nahida-bot 增加 OneBot 协议支持，统一对接 NapCat、Lagrange、LLOneBot 等 QQ 端实现，以及可能的非 QQ OneBot 实现。
> 相关文档：
>
> - [channel-plugin.md](../architecture/channel-plugin.md)
> - [runtime-flows.md](../architecture/runtime-flows.md)
> - [milky channel plugin](../ROADMAP.md#phase-46--milky-qq-channel-plugin临时新增计划项)
> - [ROADMAP.md](../ROADMAP.md)

---

## 1. 结论

推荐实现 **OneBot v11 + v12 双版本支持**，通过内部统一中间表示消除版本差异。通信方式按使用广度优先支持 **正向 WebSocket**（bot 作为 client 连接 OneBot 实现端），其次 **WebHook**（OneBot 实现端 HTTP POST 推送事件 + 反向 HTTP API 调用），暂不优先支持反向 WebSocket（bot 作为 server）。

关键取舍：

- **v11 和 v12 共用一个插件、一份配置**：内部用 `OneBotProtocol` 抽象层归一化两个版本的事件和 API，外层 plugin、config、message converter 不感知版本差异。
- **正向 WS 优先于 WebHook**：绝大多数 OneBot 实现端（NapCat、Lagrange、LLOneBot）默认提供正向 WS server，bot 主动连接是最简单的部署拓扑，不需要 bot 侧暴露公网端口。
- **不实现完整 OneBot API 覆盖**：首版只实现收发消息所需的最小 action 集（`send_msg`、`get_msg`、`get_login_info`、`get_group_info`、`get_group_list` 等约 10 个）。扩展 action 按需添加。
- **Segment 消息模型直接对接现有 `InboundMessage`/`OutboundMessage`**：OneBot 的 array-based message segment（`[{type, data}, ...]`）归一化为 nahida-bot 的文本 + 附件模型；CQ 码文本模式仅做降级兼容，不作为主解析路径。
- **不引入 nb2 或 nonebot 作为依赖**：OneBot 插件是本项目自己的 `Plugin + ChannelService` 实现，参考但不依赖 nonebot2 生态。

Cross-cutting 与 Milky 的差异：

| 维度 | Milky | OneBot |
|---|---|---|
| 连接方向 | bot → WS server 收事件；bot → HTTP 发 API | 正向 WS：bot → WS server 收发双工（v11）/ 仅事件（v12）；反向 WS / WebHook 也支持 |
| API 协议 | HTTP POST `/api/:api` | v11：WS 内 JSON-RPC-style envelope；v12：HTTP POST `/` + action body |
| 事件协议 | WS JSON frame | v11：WS JSON frame；v12：WS/HTTP POST |
| 消息格式 | Milky segment dict | OneBot message segment array |
| 文件上传 | 独立 file API | 内嵌于 `send_msg` 的 segment |
| 单 WS 双工 RPC | 否——`/event` 只推送事件，API 走 HTTP | v11 是；v12 分离 |

---

## 2. OneBot v11 vs v12 差异分析

### 2.1 协议分层

**v11** 是单协议版：所有通信（事件接收 + API 调用）可以在同一条 WebSocket 上完成（正向 WS），也可以拆成 WebHook（事件推送） + HTTP（API 调用）。

**v12** 拆分为两个独立规范：

- **OneBot Connect**：通信方式规范（HTTP、WebSocket、WebHook 三种模式）
- **OneBot Standard**：事件和 action 的语义定义

### 2.2 连接模式对比

| 模式 | v11 | v12 | nahida-bot 首版 |
|------|-----|-----|----------------|
| 正向 WS（bot 连 impl） | ✅ 双工：同一连接收发事件和 API | ✅ 仅推送事件；API 走 HTTP | **优先实现** |
| 反向 WS（impl 连 bot） | ✅ 双工 | ✅ 仅推送事件 | 暂缓 |
| WebHook（事件推送） | ✅ HTTP POST 事件 + 反向 HTTP API | ✅ HTTP POST 事件 + 反向 HTTP API | **其次实现** |
| HTTP API | ✅ 配合 WebHook 用 | ✅ 配合所有模式用 | 随 WebHook 一起实现 |

正向 WS 是大多数用户的首选：NapCat 默认在 `ws://127.0.0.1:3001` 提供正向 WS；Lagrange 同样提供；LLOneBot 也支持。这个模式下 bot 不需要暴露端口。

### 2.3 消息格式差异

**v11**：

```json
[
  {"type": "text", "data": {"text": "你好"}},
  {"type": "image", "data": {"file": "http://...", "url": "http://..."}},
  {"type": "at", "data": {"qq": "123456"}}
]
```

- 同时支持 message segment array 和 CQ 码 string 两种表示
- `post_type` 区分 `message`、`notice`、`request`、`meta_event`
- `message_type` 区分 `private`、`group`

**v12**：

```json
[
  {"type": "text", "data": {"text": "你好"}},
  {"type": "image", "data": {"file_id": "...", "url": "..."}},
  {"type": "mention", "data": {"user_id": "123456"}}
]
```

- 只有 message segment array，不保留 CQ 码
- event `type` 使用 `message.private`、`message.group` 等 namespaced 格式
- `alt_message` 字段提供纯文本降级

### 2.4 Action/API 差异

| 方面 | v11 | v12 |
|------|-----|-----|
| 调用格式 | `{action, params, echo}` over WS | `{action, params, echo, self}` over HTTP |
| 响应格式 | `{status, retcode, data, echo}` | `{status, retcode, data, echo}` |
| 错误码 | `retcode=0` 为成功 | `retcode=0` 为成功 |
| action 命名 | `send_msg`、`get_login_info` | `send_message`、`get_self_info` |
| action 参数 | 各 action 独立 | 更标准化，有 type/param schema |

v12 的 action 普遍更规范，例如 `send_message` 替代 `send_msg`，`get_self_info` 替代 `get_login_info`。但语义差异不大，可以在协议适配层统一。

### 2.5 事件差异

| 方面 | v11 | v12 |
|------|-----|-----|
| 事件标识 | `post_type` + 各子字段 | `type`（如 `message.private`） |
| 消息事件 | `message_type: private/group` | `message.private` / `message.group` |
| 通知事件 | `notice_type` + sub 字段 | 各自独立 type（如 `friend.increase`） |
| 自身信息 | `self_id`、`self_tiny_id` | `self` object（`{platform, user_id}`） |
| 消息 ID | `message_id` (int) | `message_id` (string) |
| 时间戳 | `time` (int, unix) | `time` (float, unix) |

---

## 3. 架构设计

### 3.1 分层架构

```text
┌─────────────────────────────────────────────────────┐
│ OneBotPlugin (ChannelService)                        │
│   plugin.py — 生命周期、注册 channel                 │
│   config.py — 版本无关的配置模型                      │
├─────────────────────────────────────────────────────┤
│ OneBotAdapter                                        │
│   adapter.py — 协议版本检测，统一事件/action接口      │
├──────────────────────┬──────────────────────────────┤
│ OneBotV11Protocol    │ OneBotV12Protocol             │
│   v11/event.py       │   v12/event.py                │
│   v11/action.py      │   v12/action.py               │
│   v11/connect.py     │   v12/connect.py               │
├──────────────────────┴──────────────────────────────┤
│ Transport Layer                                      │
│   transport/ws.py       — 正向 WS client             │
│   transport/webhook.py  — WebHook HTTP server        │
│   transport/http_api.py — HTTP API client (v12 /      │
│                            WebHook 模式)              │
└─────────────────────────────────────────────────────┘
│ Message Converter                                    │
│   message_converter.py — segment ↔ Inbound/Outbound  │
└─────────────────────────────────────────────────────┘
```

### 3.2 协议适配层

核心接口：

```python
class OneBotProtocol(Protocol):
    """Normalize v11/v12 differences behind a single interface."""

    @property
    def version(self) -> str: ...
    """Return "v11" or "v12"."""

    def detect_event_type(self, raw: dict[str, Any]) -> str | None: ...
    """Return normalized event type string or None if not an event frame."""

    def normalize_event(self, raw: dict[str, Any]) -> dict[str, Any]: ...
    """Convert a v11 or v12 event dict into a stable intermediate format."""

    def encode_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]: ...
    """Build the protocol-specific action payload."""

    def decode_response(self, raw: dict[str, Any]) -> OneBotResponse: ...
    """Parse an action response into a stable result object."""
```

中间事件格式（版本无关）：

```python
@dataclass
class NormalizedEvent:
    type: str                    # "message.private" | "message.group" | ...
    sub_type: str                # "friend" | "group" | "normal" | ...
    message_id: str
    user_id: str
    group_id: str | None
    self: OneBotSelf             # {platform, user_id}
    message: list[dict]          # normalized segments
    alt_message: str
    time: float
    raw: dict[str, Any]
```

版本检测策略：

- 正向 WS 连接成功后，v11 impl 会立即推送 `meta_event.lifecycle` 或 `meta_event.heartbeat`；v12 在 connect 握手后推送事件
- 通过事件 field 检测：有 `post_type` 且值为 `message|notice|request|meta_event` → v11；有 `type` 且值为 `message.*|notice.*|meta.*` → v12
- 也支持配置显式指定 `protocol_version: "v11" | "v12" | "auto"`（默认 `auto`）

### 3.3 连接模式适配

#### 正向 WebSocket（首版优先）

```text
nahida-bot                     OneBot 实现端 (NapCat/Lagrange/LLOneBot)
    │                                │
    │── WS connect ─────────────────→│
    │                                │
    │←── lifecycle event (v11 only)──│
    │                                │
    │  [v11: 同一条 WS 上收发 action] │
    │── {action, params, echo} ─────→│  (v11 only)
    │←── {status, data, echo} ──────│  (v11 only)
    │                                │
    │←── event JSON frame ──────────│
    │                                │
    │  [v12: API 通过 HTTP 发送]      │
    │── HTTP POST / {action} ───────→│  (v12 only)
    │←── HTTP 200 {status, data} ───│  (v12 only)
```

正向 WS 实现要点：

- **v11**：`OneBotV11WSConnection` 管理一条 WS 连接，同时作为事件源和 API transport。事件帧和 action 响应帧通过 `echo` 字段区分路由。连接断开时自动重连。
- **v12**：`OneBotV12WSConnection` 管理 WS 仅作为事件源；API 调用走独立 `OneBotHTTPClient`（复用同一 `base_url` 的 HTTP 端点）。
- 重连策略：初始延迟 1s，指数退避至最大 30s，无限重试（用户停止插件时退出）。

#### WebHook（其次实现）

```text
nahida-bot                     OneBot 实现端
    │                                │
    │  [事件]                         │
    │←── HTTP POST /onebot/event ────│
    │── HTTP 204 ──────────────────→│
    │                                │
    │  [API 调用]                     │
    │── HTTP POST {impl_url}/ ──────→│
    │←── HTTP 200 ──────────────────│
```

WebHook 实现要点：

- bot 侧在 Gateway（FastAPI）注册 `/onebot/event` 端点接收事件
- `X-Self-ID` header 区分多账号
- `X-Signature` / token 校验防止伪造事件（复用 OneBot 实现的 secret/token 机制）
- API 调用方向：bot → impl 的 HTTP API（主动出站），通过配置的 `impl_base_url` 或事件中携带的 impl 地址

### 3.4 消息转换

#### v11 CQ 码兼容策略

v11 同时支持 message array 和 CQ 码 string。转换策略：

1. 优先使用 `message` 字段（array），这是 OneBot 规范的标准格式
2. 如果 `message` 为空或缺失，fallback 到 `raw_message`（CQ 码 string）
3. CQ 码解析作为**降级兼容路径**，不完全覆盖所有 CQ 码类型，只覆盖常见类型（`[CQ:text]`、`[CQ:image]`、`[CQ:at]`、`[CQ:face]`、`[CQ:reply]`、`[CQ:record]`、`[CQ:video]`、`[CQ:file]`）
4. 无法解析的 CQ 码保留原文不丢弃

Segment → InboundMessage 映射：

```python
SEGMENT_TO_INBOUND = {
    "text":     → InboundMessage.text 追加
    "image":    → InboundAttachment(kind="image", platform_id=file_id, url=url)
    "record":   → InboundAttachment(kind="audio", platform_id=file_id, url=url)
    "video":    → InboundAttachment(kind="video", platform_id=file_id, url=url)
    "file":     → InboundAttachment(kind="file", platform_id=file_id, url=url)
    "at":       → InboundMessage.text 追加 "@display_name"；填充 mentioned_user_ids
    "mention":  → (v12) 同 at
    "reply":    → InboundMessage.reply_to = message_id
    "face":     → InboundMessage.text 追加 "[表情: {id}]"
    "forward":  → 解析合并转发（见下文「合并转发解析」）
```

合并转发解析（已实现，关闭 #46）：

- `forward` segment 不再只是文本占位符。`to_inbound` 是 async，通过
  `get_forwarded_messages` action（v11 映射 `get_forward_msg`）递归拉取，
  按 `- {sender_name}: {content}` 逐条渲染进 `InboundMessage.text`。
- 嵌套转发超过 `max_forward_depth`（默认 3）时渲染
  `[Forward: {id}, messages={n}, truncated=true]`；文本超过
  `forward_render_max_chars`（默认 12000）时以 `[Truncated]` 截断。
- 转发内部的图片、语音、视频、文件通过 `_iter_content_segments` 提取为一等
  `InboundAttachment`；v11 嵌套消息的 `content` 可能是 CQ 码 string，会走
  `parse_cq_code` 再提取。
- 相关配置键：`max_forward_depth`、`max_forward_messages`（单次最多消息数，
  默认 80）、`forward_render_max_chars`。
}
```

OutboundMessage → Segment 映射：

```python
OutboundMessage.text        → {"type": "text", "data": {"text": ...}}
OutboundMessage.reply_to    → {"type": "reply", "data": {"id": ...}}
OutboundMessage.attachment(kind="image")  → {"type": "image", "data": {"file": ...}}
OutboundMessage.attachment(kind="audio")  → {"type": "record", "data": {"file": ...}}
OutboundMessage.attachment(kind="video")  → {"type": "video", "data": {"file": ...}}
```

### 3.5 群聊触发策略

复用 Milky 的 `GroupInteractionPolicy` 模式：

```python
GroupTriggerMode = Literal["none", "mention", "command", "always"]
```

| 模式 | 行为 |
|------|------|
| `mention` | 仅当 @bot 或 @全体成员时触发 |
| `command` | 仅当消息以 command_prefix 开头或 @bot 时触发 |
| `always` | 所有群消息都触发 |

`@bot` 检测逻辑：检查 message segment 中的 `at`/`mention` 是否包含 `self_id`，或在 v11 CQ 码检测 `[CQ:at,qq={self_id}]`。

### 3.6 ChatAddress 模型

OneBot 消息天然有明确的 channel/chat_type/chat_id 三元组：

```python
# 私聊
ChatAddress(channel="onebot", target_type="private", target_id="u_{user_id}")

# 群聊
ChatAddress(channel="onebot", target_type="group", target_id="{group_id}")

# 支持多账号场景：用 self_id 区分不同 bot 账号的同一群/用户
# 通过 session metadata 存储 self_id
```

---

## 4. 配置模型

```python
class OneBotPluginConfig(BaseModel):
    """OneBot channel plugin configuration, protocol-version agnostic."""

    # --- 协议版本 ---
    protocol_version: Literal["v11", "v12", "auto"] = "auto"

    # --- 正向 WS 模式 ---
    ws_url: str = ""
    """正向 WebSocket 地址，例如 ws://127.0.0.1:3001。空字符串表示不启用正向 WS。"""
    ws_access_token: str = ""
    """正向 WS 鉴权 token，在 v11 中可作为 connect 参数或 header，v12 中作为 Authorization header。"""

    # --- WebHook 模式 ---
    webhook_enabled: bool = False
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 6186
    webhook_path: str = "/onebot/event"
    webhook_secret: str = ""
    """WebHook 签名密钥，用于验证事件来源。"""

    # --- HTTP API（v12 或 WebHook 模式下的出站 API 调用）---
    impl_base_url: str = ""
    """OneBot 实现端的 HTTP API 地址，例如 http://127.0.0.1:3001。正向 WS v12 和 WebHook 模式都需要。"""
    impl_access_token: str = ""
    """HTTP API 调用的 access token。"""

    # --- 通用配置 ---
    command_prefix: str = "/"
    group_trigger_mode: GroupTriggerMode = "mention"  # none | mention | command | always
    group_context_capture: bool = False
    reply_to_inbound: bool | None = None

    allowed_friends: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)

    # --- 重连策略（正向 WS）---
    reconnect_initial_delay: float = 1.0
    reconnect_max_delay: float = 30.0

    # --- 消息 ---
    max_text_length: int = 4000

    # --- 媒体 ---
    media_download_dir: str = "./data/temp/onebot"
    enable_media_download_tool: bool = True
    cache_media_on_receive: bool = True

    # --- 分段发送 ---
    split_long_text: bool = True
    """超长文本是否自动分段发送（群聊场景常见限制）。"""

    @model_validator(mode="after")
    def _validate_connectivity(self) -> "OneBotPluginConfig":
        if not self.ws_url and not self.webhook_enabled:
            raise ValueError(
                "At least one connectivity mode must be configured: "
                "ws_url or webhook_enabled"
            )
        if self.protocol_version in ("v12", "auto") and not self.impl_base_url:
            if self.ws_url:
                # v12 模式需要 HTTP API 地址；从 ws_url 推导
                pass  # 由 plugin on_load 推导
            elif self.webhook_enabled and not self.impl_base_url:
                raise ValueError(
                    "impl_base_url is required for v12 HTTP API calls"
                )
        return self

    @property
    def derived_http_base_url(self) -> str:
        """Derive HTTP API base from ws_url if not explicitly configured."""
        if self.impl_base_url:
            return self.impl_base_url.rstrip("/")
        if self.ws_url:
            return self.ws_url.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
        return ""
```

---

## 5. 目录结构

```text
nahida_bot/channels/onebot/
  __init__.py               # 导出 OneBotPlugin
  plugin.yaml               # manifest
  plugin.py                 # OneBotPlugin (Plugin + ChannelService)
  config.py                 # OneBotPluginConfig

  protocol.py               # OneBotProtocol abstract + NormalizedEvent + OneBotResponse
  adapter.py                # OneBotAdapter — version detection + dispatch

  v11/
    __init__.py
    event.py                # v11 → NormalizedEvent
    action.py               # Normalized action → v11 payload
    connect.py              # v11 WS connection (双工: 事件 + API)

  v12/
    __init__.py
    event.py                # v12 → NormalizedEvent
    action.py               # Normalized action → v12 payload
    connect.py              # v12 WS connection (仅事件)

  transport/
    __init__.py
    ws.py                   # 正向 WS client（版本感知）
    webhook.py              # WebHook HTTP endpoint handler
    http_api.py             # 出站 HTTP API client (v12 / WebHook mode)

  message_converter.py      # segment ↔ InboundMessage / OutboundMessage
  segment_models.py         # OneBot segment dataclasses
  cq_code.py                # v11 CQ 码降级解析器
```

---

## 6. Action 覆盖计划

首版实现的最小 action 集：

| Action | v11 名称 | v12 名称 | 用途 |
|--------|---------|---------|------|
| 发送消息 | `send_msg` | `send_message` | 发送私聊/群聊消息 |
| 获取消息 | `get_msg` | `get_message` | 按 message_id 获取消息详情 |
| 获取自身信息 | `get_login_info` | `get_self_info` | 获取 bot 自己的 QQ 号和昵称 |
| 获取群信息 | `get_group_info` | `get_group_info` | 获取群名称、成员数等 |
| 获取群列表 | `get_group_list` | `get_group_list` | 获取 bot 加入的所有群 |
| 获取好友列表 | `get_friend_list` | `get_friend_list` | 获取 bot 的所有好友 |
| 获取群成员信息 | `get_group_member_info` | `get_group_member_info` | 获取群成员的群名片等 |
| 获取群成员列表 | `get_group_member_list` | `get_group_member_list` | 获取群成员列表 |
| 撤回消息 | `delete_msg` | `delete_message` | 撤回已发送的消息 |
| 获取文件 URL | `get_file_url` (ext) | `get_file` | 获取文件下载 URL |

优先级排序：

1. **P0**：`send_msg/message`、`get_login_info/self_info`、`get_msg/message`——收发消息闭环
2. **P1**：`get_group_info`、`get_group_list`——群管理基础
3. **P2**：`get_friend_list`、`delete_msg`、群成员查询
4. **P3**：扩展 action（`set_group_card`、`set_group_ban` 等）

不在首版范围的 action（如 `send_like`、`set_restart`、`clean_cache` 等）返回 protocol-level "unsupported action" 错误。

---

## 7. 事件覆盖计划

### 7.1 消息事件

| v11 post_type | v12 type | 处理方式 |
|---------------|----------|---------|
| `message.private` | `message.private` | 转换为 `MessageReceived` |
| `message.group` | `message.group` | 转换为 `MessageReceived` + GroupPolicy 判断 |

### 7.2 通知事件（可观测性，首版不触发 agent）

| v11 notice_type | v12 type | 处理方式 |
|-----------------|----------|---------|
| `friend_add` | `friend.increase` | 日志记录 |
| `group_increase` | `group.increase` | 日志记录，可选欢迎消息 |
| `group_decrease` | `group.decrease` | 日志记录 |
| `group_ban` | — (v12 暂无) | 日志记录 |
| `friend_recall` | — | 日志记录 |
| `group_recall` | — | 日志记录 |

### 7.3 请求事件

| v11 request_type | v12 type | 处理方式 |
|------------------|----------|---------|
| `friend` | `request.friend` | 日志记录；可配置自动同意 |
| `group.invite` | `request.group.invite` | 日志记录；可配置自动同意 |

### 7.4 元事件

| v11 | v12 | 处理方式 |
|-----|-----|---------|
| `meta_event.lifecycle` | `meta.connect` | 更新内部状态（`self_id`、连接时间） |
| `meta_event.heartbeat` | `meta.heartbeat` | 日志 debug 级别，超时检测 |

---

## 8. 实施阶段

### Phase 0：基础设施

- [x] 新增 `nahida_bot/channels/onebot/` 目录结构
- [x] 新增 `plugin.yaml`（id: `onebot`，entrypoint: `nahida_bot.channels.onebot.plugin:OneBotPlugin`）
- [x] 实现 `config.py`：`OneBotPluginConfig` 含所有字段和校验
- [x] 实现 `segment_models.py`：v11/v12 segment dataclasses

### Phase 1：v11 正向 WS 最小闭环

- [x] 实现 `protocol.py`：`NormalizedEvent`、`OneBotResponse`、`OneBotProtocol` 接口
- [x] 实现 `v11/event.py`：v11 事件解析和归一化
- [x] 实现 `v11/action.py`：v11 action 编码 + 响应解码
- [x] 实现 `v11/connect.py`：v11 正向 WS 双工连接（事件 + API 走同一 WS）
- [x] 实现 `cq_code.py`：CQ 码降级解析器
- [x] 实现 `message_converter.py`：segment → `InboundMessage` / `OutboundMessage` → segment
- [x] 实现 `plugin.py`：`OneBotPlugin` 生命周期，注册 channel
- [x] 实现 `adapter.py`：版本 auto-detection 逻辑

### Phase 2：v12 正向 WS

- [ ] 实现 `v12/event.py`：v12 事件解析和归一化
- [ ] 实现 `v12/action.py`：v12 action 编码 + 响应解码
- [ ] 实现 `v12/connect.py`：v12 正向 WS（仅事件推送）
- [ ] 实现 `transport/http_api.py`：出站 HTTP API client
- [ ] 扩展 adapter 支持 v12 协议 auto-detection

### Phase 3：WebHook 模式

- [ ] 实现 `transport/webhook.py`：在 Gateway 注册 `/onebot/event` 端点
- [ ] 支持 `X-Signature` 签名校验（v11 的 secret 机制）
- [ ] 支持 v12 WebHook 事件格式
- [ ] WebHook 模式下通过 `http_api.py` 出站调用 API

### Phase 4：群聊增强与可观测性

- [ ] 群通知事件处理（成员加入/退出/禁言）
- [ ] 请求事件自动处理（加好友、加群邀请）
- [ ] 群成员缓存（减少频繁 API 调用获取群名片）
- [ ] 多账号支持（配置级多实例或连接级多 self）
- [ ] 连接状态暴露到 Gateway status API

### Phase 5：扩展 action 与优化

- [ ] 按需添加更多 OneBot action（群管理、文件操作）
- [ ] 超长消息自动分段发送
- [ ] 媒体文件下载缓存与过期清理
- [ ] 连接 metrics（延迟、重连次数、消息吞吐）

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| v11/v12 行为差异导致适配层膨胀 | 统一 `NormalizedEvent`/`OneBotResponse` 有限字段；差异只在 protocol 子类内部消化 |
| OneBot 实现端行为不一致（NapCat vs Lagrange vs LLOneBot） | 优先在 NapCat + Lagrange 两个最常用实现上测试；adapter 层对字段缺失做 defensive 处理 |
| CQ 码格式各异，降级解析覆盖率不足 | CQ 码只作为 fallback；主路径始终是 segment array；解析失败时保留原文 |
| 正向 WS 连接断开后消息丢失 | 正向 WS 模式下事件推送是实时的，断线期间消息不可恢复，这是 OneBot 协议本身的约束；在配置文档中说明，并建议关键场景使用 WebHook |
| v12 生态尚不成熟，多数用户仍用 v11 | 首版即支持双版本，降低用户迁移压力；v11 做完整闭环，v12 保持兼容跟进 |
| 多 bot 账号场景（多个 OneBot 实现端）共享一个 nahida-bot | 首版只支持单个 OneBot 连接；多账号通过 Channel 多实例化（加载多个 OneBot plugin 副本）解决，Phase 4 再简化配置 |
| OneBot 实现端 API 调用频率限制 | send_msg 做有限重试（仅网络错误和 429），不在插件层做全局 rate limit |
| 与 Milky 功能重叠（都是 QQ 接入） | 两者是平行的 channel plugin，用户根据部署环境二选一；不互相依赖，各自独立维护 |

---

## 10. 与 Milky 的共存策略

nahida-bot 可能同时拥有 OneBot 和 Milky 两个 QQ Channel。共存时需要注意：

1. **Session 隔离**：`ChatAddress` 使用不同 `channel` 值（`onebot` vs `milky`），session 完全隔离。
2. **同一 QQ 群/用户**：即使同一个 QQ 群同时出现在 Milky 和 OneBot 中，session 也不互通。这是预期行为——channel 是消息来源的权威标识。
3. **跨 channel 消息转发**：如果用户想让 OneBot 收到的消息触发 Milky 回复（或反之），通过 `message` 工具显式跨 channel 发送，不做隐式路由。
4. **配置文档**：明确告知用户不要同时为同一个 bot QQ 号启用两个 channel，避免群友收到重复回复。

---

## 11. 参考资料

- OneBot v11 标准：<https://github.com/botuniverse/onebot-11>
- OneBot v12 标准：<https://12.onebot.dev/>
- NapCat 实现：<https://github.com/NapNeko/NapCatQQ>
- Lagrange.OneBot 实现：<https://github.com/LagrangeDev/Lagrange.Core>
- LLOneBot 实现：<https://github.com/LLOneBot/LLOneBot>
- nonebot2 adapter-onebot：<https://github.com/nonebot/adapter-onebot>
- AstrBot OneBot 平台接入：<https://github.com/Soulter/AstrBot>
- Milky 协议参考（非 OneBot）：<https://github.com/ProtocolScience/milky-python-sdk>
