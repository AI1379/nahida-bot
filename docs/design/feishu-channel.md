# 飞书（Feishu/Lark）Channel 设计

> 状态：✅ M1+M2 已实现（长连接事件流、文本收发、Markdown→post 富文本渲染、媒体双向、
> 出站 mention 校验、群名/成员缓存）。默认 `enabled: false`，尚未在真实飞书租户上
> 端到端验证。未实现：卡片消息与流式更新、reactions、话题群 thread 映射、音频 OPUS
> 转码（M3，见 §8）。相比原始设计的调整：单聊定位也用 chat_id（`feishu:private:oc_xxx`），
> 因为飞书单聊会话本身有 chat_id，出站统一 `receive_id_type=chat_id`；
> Markdown 渲染采用 post 结构化 AST（text 元素 `style` 数组 + `code_block`/`a`/`at`/`hr`
> 元素，与官方 SDK 高层转换器的 structured 模式同构），未用 `<b>` 内联标签。
> 日期：2026-09-03
> 实现日期：2026-09-04
> 目标：为 nahida-bot 增加飞书自建应用机器人 channel，支持单聊与群聊收发消息、@ 触发与出站 mention，复用现有 `Plugin + ChannelService` 架构。
> 相关文档：
>
> - [channel-plugin.md](../architecture/channel-plugin.md)
> - [onebot-channel.md](onebot-channel.md)（结构范本）
> - 飞书官方 Python SDK：[larksuite/oapi-sdk-python](https://github.com/larksuite/oapi-sdk-python)（PyPI 包名 `lark-oapi`）

---

## 1. 结论

推荐实现 **飞书自建应用 + 官方 SDK `lark-oapi` 长连接（WebSocket）接收事件 + 自写 httpx 异步客户端调用 REST API** 的组合，作为标准内置 channel 放在 `nahida_bot/channels/feishu/`。

关键取舍：

- **事件接收用 SDK 长连接，不用 Webhook 回调**：长连接免公网 IP/域名/内网穿透（ECS 部署无需暴露新端口），与 milky（WS 收事件 + HTTP 发 API）拓扑一致。Webhook 模式作为后备（框架已有 `register_webhook_endpoint` 基建，切换成本可控），首版不做。
- **SDK 只用于 WS 事件流，REST API 自己用 httpx 写**：飞书 WS 长连接协议是私有 protobuf 帧格式（`pbbp2_pb2.Frame`），自实现不现实，必须用 SDK；但 SDK 的 API 客户端（`lark.Client`）是同步 requests 实现，直接用会阻塞 asyncio 主循环。自写 `FeishuClient`（httpx，token 自动管理 + 重试）与 `MilkyClient` 风格完全对齐，代码量约 200 行。
- **`lark.ws.Client` 在专用后台线程运行，跨线程桥接进主 loop**：SDK 的 ws 客户端拥有自己的 asyncio loop 且 `start()` 阻塞；handler 是同步回调。在专用线程里 import + start，handler 内用 `asyncio.run_coroutine_threadsafe` 把原始事件 dict 投递进 bot 主循环后立即返回（满足飞书"3 秒内处理"的 ACK 要求）。
- **出站 v1 纯文本（text msg_type）**：飞书文本消息内联支持 `<at user_id="ou_xxx"></at>`，mention 不需要富文本；图片/文件作为独立消息逐个发送（对齐 milky 的分段发送习惯）。post 富文本/卡片/卡片流式更新分期实现。
- **mention 出站复用 milky 验证模式**：LLM 写 `[CQ:at,qq=<open_id>]` token → 群成员列表校验（TTL 缓存）→ 注入 `<at>` 标签；校验不过保持字面文本。需要把 `"feishu"` 加入 `MENTION_CAPABLE_PLATFORMS`。

与 Milky 的差异对照：

| 维度 | Milky | Feishu |
|---|---|---|
| 事件接收 | bot → Milky `/event` WS（JSON） | SDK 长连接（私有 protobuf 帧，飞书云端推送） |
| API 调用 | HTTP POST `/api/:api` | HTTPS REST `open.feishu.cn/open-apis/...`（tenant_access_token 鉴权） |
| 鉴权 | access_token（Milky 端配置） | app_id + app_secret → tenant_access_token（2h 过期，需自动续期） |
| 消息格式 | Milky segment dict | `msg_type` + content JSON 字符串（text/post/image/file/...） |
| @ 用户 | mention segment | 文本内联 `<at user_id>` 标签（text）/ `at` 元素（post） |
| 文件上传 | 独立文件 API 后带 segment | 先 `im/v1/images`、`im/v1/files` 上传拿 key，再发 key |
| 身份 ID | QQ 号（数字） | open_id（`ou_` 前缀，应用内稳定）；群 `oc_` 前缀 chat_id |
| bot 自身标识 | `get_login_info` | `GET /bot/v3/info`（无需权限） |

---

## 2. 飞书平台事实（2026-09 查证）

### 2.1 Python SDK

官方 SDK [`lark-oapi`](https://pypi.org/project/lark-oapi/)（GitHub [larksuite/oapi-sdk-python](https://github.com/larksuite/oapi-sdk-python)，v2_main 分支，Python 3.8+）。两层能力：

1. **经典层**（本设计采用）：
   - `lark.ws.Client(app_id, app_secret, event_handler, auto_reconnect=True)` — 长连接客户端。源码确认：模块 import 时创建/绑定一个 asyncio loop，`start()` = `loop.run_until_complete(...)` 阻塞运行；内置断线重连（随机抖动 + 固定间隔）、ping/pong、事件合包；**没有公开的 stop()**（见 §3.3 关闭策略）；事件 handler 为**同步回调**（在 SDK 自己的 loop 线程里执行）。
   - `lark.Client` — REST API 客户端，**同步 requests 实现**，本设计不用它发 API。
2. **高层 `lark_oapi.channel.FeishuChannel`**（v1.6.0 新增，对齐 node-sdk）：`asyncio.run(channel.connect())`、`channel.on("message", ...)`、`send/stream/update_card`，自带群策略、去重、重试、markdown 转换、卡片流式节流等。**首版不采用**：自带策略层与 bot 核心的 `GroupInteractionPolicy`/session 机制职责重叠，且发布时间短成熟度存疑；但它是 M3 卡片流式输出的候选加速器，保持关注。

SDK 依赖 requests + websockets + protobuf 运行时，与现有依赖兼容（项目已有 `websockets>=12`；SDK 对 websockets 15 的 proxy 参数变化已做兼容）。

### 2.2 鉴权与机器人身份

- **tenant_access_token**：`POST /open-apis/auth/v3/tenant_access_token/internal`，body `{app_id, app_secret}`，返回 `{tenant_access_token, expire}`（约 7200 秒）。所有 REST API 走 `Authorization: Bearer <token>`。
- **机器人自身 open_id**：`GET /open-apis/bot/v3/info`，无需权限，返回 `open_id`/`activate_status`。启动时获取（对齐 milky 的 `get_login_info` 模式），失败后台重试。
- 国际版 Lark 域名为 `open.larksuite.com`，API 路径一致——`domain` 做成配置项即可双兼容。

### 2.3 事件接收（长连接模式）

- 订阅 `im.message.receive_v1`（接收消息 v2.0）。事件体关键结构（已核对官方文档）：

  ```json
  {
    "schema": "2.0",
    "header": {"event_id", "event_type": "im.message.receive_v1", "create_time", "app_id", "tenant_key"},
    "event": {
      "sender": {
        "sender_id": {"union_id", "user_id", "open_id"},
        "sender_type": "user" | "bot",
        "tenant_key"
      },
      "message": {
        "message_id": "om_...",
        "root_id": "om_...",        // 仅回复消息时返回
        "parent_id": "om_...",
        "create_time": "1609073151345",   // 毫秒
        "chat_id": "oc_...",         // 单聊和群聊都有 chat_id
        "thread_id": "omt_...",      // 话题群消息才有
        "chat_type": "p2p" | "group",
        "message_type": "text" | "post" | "image" | "file" | "audio" | "media" | "sticker" | "share_chat" | "share_user" | "system",
        "content": "{\"text\":\"@_user_1 hello\"}",   // JSON 字符串
        "mentions": [
          {"key": "@_user_1", "id": {"open_id": "ou_...", "union_id", "user_id"},
           "mentioned_type": "user" | "bot", "name": "Tom", "tenant_key"}
        ]
      }
    }
  }
  ```

- **mention 占位符**：文本中的 @ 以 `@_user_N` 占位符出现，`mentions[].key` 与之一一对应，真实身份在 `mentions[].id.open_id`。入站必须做占位符 → `@名字` 还原，并用 `mentions[].id.open_id == bot open_id` 判定 `mentions_bot`。
- **幂等**：官方明确可能重复推送，**必须按 `message_id` 去重**（不要用 event_id）。SDK 长连接模式下不做事件级去重（`_do_without_validation` 跳过 token 校验和去重缓存），需自建 LRU 去重。
- **长连接限制**：仅支持**企业自建应用**；每应用最多 **50 个连接**；多连接时同一事件**随机推送**到某一条连接（多实例部署会分片，不是负载均衡复制）——单 bot 实例保持单连接即可；服务器需能访问公网。
- **ACK 时限**：要求 3 秒内处理完一条消息（SDK 会测量 handler 耗时并回 ACK 帧）。我们的 handler 只做跨线程投递，立即返回，天然满足。

### 2.4 能收什么消息由权限决定（重要）

接收侧权限分三档，直接决定 `group_context_capture`（观察未触发消息）能力：

| 权限 scope | 能收到的消息 | 用途 |
|---|---|---|
| `im:message.p2p_msg:readonly` | 单聊消息 | 基础必选 |
| `im:message.group_at_msg:readonly`（或 `...include_bot:readonly` 含其他 bot 的 @） | 群里 **@ 本 bot** 的消息 | 群触发基础必选 |
| `im:message.group_msg` / `im:message.group_msg:readonly`（**敏感权限**，需管理员审批） | 群里**所有**消息 | 仅 `group_context_capture: true` 时才需要申请 |

默认配置按前两档申请；`group_context_capture` 默认 `false` 并在文档里注明需要敏感权限审批。

### 2.5 发送消息

- `POST /open-apis/im/v1/messages?receive_id_type=open_id|chat_id|union_id|user_id`，body `{receive_id, msg_type, content, uuid?}`，content 是 JSON 字符串。
- **频率限制：1000 次/分钟、50 次/秒（应用级）**；超限错误码 230020，按 milky 的重试模式退避。
- 大小上限：文本 150 KB；post/卡片 30 KB（超限 230025）。
- **`uuid` 参数原生支持发送幂等**：相同 uuid 一小时内至多成功发一条。用 `sha1(session_id + attempt + 序号)` 生成，实现网络重试下的防重发（比 milky 强）。
- 回复：`POST /open-apis/im/v1/messages/{message_id}/reply`（无 receive_id，body 只需 msg_type/content）。
- 文本 @ 语法：`<at user_id="ou_xxx"></at>`；@所有人 `<at user_id="all"></at>`。
- post 富文本：`content` 为 `{"post":{"zh_cn":{"title", "content": [[{tag,...}]]}}}`，二维数组=段落×元素，tag 有 `text/a/at/img/media/emotion`。v1 不用，v1.1 做 markdown→post。

### 2.6 媒体

- 上传图片：`POST /open-apis/im/v1/images`（multipart，`image_type=message`），≤10 MB，格式 JPG/PNG/GIF/BMP/WEBP 等，返回 `image_key`。
- 上传文件：`POST /open-apis/im/v1/files`（multipart，`file_type=opus|mp4|pdf|doc|xls|ppt|stream`），≤30 MB，返回 `file_key`。**音频必须是 OPUS**（其他格式需 ffmpeg 转码），v1 语音出站以 `stream` 文件发送，转码后置。
- 下载消息资源：`GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=image|file`，≤100 MB，不支持表情包。入站附件经此接口流入共享 `MediaStore`。
- ⚠️ 上传接口的 key 只能配合 `im/v1/messages` 发送，云文档接口的 file_token 不通用。

### 2.7 群信息与成员

- `GET /open-apis/im/v1/chats/{chat_id}`：群名 `name`、描述、`chat_mode=group|topic|p2p`、`external`（是否外部群）。
- `GET /open-apis/im/v1/chats/{chat_id}/members?member_id_type=open_id&pageSize&page_token`：分页成员列表（member_id + name）。**不返回群内机器人成员**。用途：① 出站 mention 校验；② 入站 sender 显示名（事件里没有 sender name）。

### 2.8 权限清单（开发者后台申请）

- 事件订阅方式选「使用长连接接收事件」，订阅 `im.message.receive_v1`。
- 接收：§2.4 三档（默认前两档）。
- 发送：机器人能力 + 「发送消息」权限（im:message 系 scope，以[发送消息文档](https://open.feishu.cn/document/server-docs/im-v1/message/create)权限要求为准）。
- 媒体与群：图片上传、文件上传、下载消息资源、获取群信息、获取群成员列表（`im:chat:readonly` / `im:chat.member:readonly` 等对应 scope，逐 API 按文档页勾选）。
- 可选：`contact:user.base:readonly`（单聊 sender 显示名的兜底解析）。
- `bot/v3/info` 无需权限。

### 2.9 命令发现：机器人菜单只能后台手动配置（2026-09-04 查证）

飞书没有 Discord 输入框 `/` 命令 + API 动态注册的机制，插件无法像 discord channel
的 `register_slash_commands` 那样自动挂载命令：

- **应用机器人菜单**（单聊悬浮菜单，最接近 slash command）：**仅开发者后台手动配置**，
  无公开服务端 API。菜单项类型：发送预设消息（点击=给 bot 发一条文本，走
  `im.message.receive_v1`，现有 CommandMatcher 直接命中，零代码）/ 打开网页 / 事件
  （`application.bot.menu_v6`，但事件体**不含 chat_id**，无法定位回复会话，不采用）。
  随应用版本发布生效，**命令增删改后需手动同步控制台菜单**。
- **群菜单**（`im/v1/chats/{chat_id}/menu_tree` 系列 API）：按群 API 配置，但菜单项
  `action_type` 只有 `NONE`（容器）/ `REDIRECT_LINK`（跳链接），挂不了命令。
- 群内命令发现只能靠正常输入 `@bot /help`；将来的可编程命令面板是卡片按钮回调（M3，
  且经典 `lark.ws.Client` 丢弃 CARD 帧，接收路径待验证）。

**部署动作**：在开发者后台把稳定命令（`/help`、`/new`、`/status` 等）配成
「发送预设消息」型机器人菜单，一次性配置。

---

## 3. 架构设计

### 3.1 分层架构

```
飞书云端 ──WS(protobuf, SDK)──> FeishuEventStream(专用线程)
                                   │ sync handler: run_coroutine_threadsafe → 主 loop
                                   ▼
                          FeishuPlugin.handle_inbound_event(raw dict)
                                   │ FeishuMessageConverter（纯函数，可单测）
                                   ▼
                          InboundMessage ── GroupInteractionPolicy ──> MessageReceived / MessageObserved
                                                                        （下游 router/session_runner 与平台无关）

router/scheduler ──> FeishuPlugin.send_message(target, OutboundMessage)
                                   │ FeishuOutboundConverter：文本/mention/分片/附件→上传key
                                   ▼
                          FeishuClient(httpx)：token 管理 + REST（send/reply/upload/download/chat/members）
```

### 3.2 `FeishuEventStream`：SDK 线程桥（本设计最特殊的点）

```python
class FeishuEventStream:
    """在专用线程里跑 lark.ws.Client，事件 dict 桥接进主 asyncio loop。"""
    def __init__(self, config, on_event, loop): ...  # on_event: async callable

    def start(self):
        # threading.Thread(target=self._thread_main, daemon=True)

    def _thread_main(self):
        import lark_oapi as lark            # ★ 必须线程内 import
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        def handle(data) -> None:           # 同步回调，跑在 SDK 的 loop 线程
            raw = json.loads(lark.JSON.marshal(data))
            asyncio.run_coroutine_threadsafe(self._on_event(raw), self._main_loop)

        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(handle).build())
        client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                                domain=config.domain, auto_reconnect=True, log_level=...)
        client.start()                      # 阻塞
```

三个实现要点（都有源码/文档依据）：

1. **线程内 import**：`lark_oapi/ws/client.py` 在模块级 `asyncio.get_event_loop()`（失败则 new + set），loop 会绑定到首个 import 它的线程。若在主线程 import、在 worker 线程 `start()`，会因 loop 线程亲和抛 RuntimeError。import 放进线程函数即天然规避（Python 3.12 下也避开 DeprecationWarning 路径）。
2. **handler 秒回**：只做跨线程投递立即返回，重活全在主 loop，满足 3 秒 ACK。
3. **关闭策略**：SDK 没有公开 stop()，`start()` 最终 `run_until_complete(_select())` 永久阻塞。`stop()` 实现：`loop.call_soon_threadsafe(sdk_loop.stop)` 打断 `run_until_complete` 让线程退出；线程为 daemon，进程退出时兜底。on_disable 时调用。这是与 milky（自管 websockets 连接）的最大差异，风险已知且可控。

### 3.3 入站转换（`FeishuMessageConverter`）

| 平台字段 | → InboundMessage |
|---|---|
| `message.chat_id`（`oc_`） | `chat_id`（**单聊/群聊统一用 chat_id 定位会话**，见 §3.5） |
| `chat_type` | `p2p`→private；`group`→group（`is_group`） |
| `sender.sender_id.open_id` | `user_id`（→ `sender_account_key = feishu:user:ou_...`） |
| `sender.sender_type` | `is_bot`（`"bot"` 时跳过处理防回环） |
| `message_id` | `message_id`；LRU(1024) 去重（官方明确会重复推送） |
| `parent_id`/`root_id` | `reply_to`（parent 优先） |
| `create_time`（ms） | `timestamp` |
| content JSON + mentions | `text`（见下）+ `attachments` |

文本规范化：

- `text`：`content.text` 里的 `@_user_N` 占位符逐个替换——指向 bot 自身的 → 删除（对齐 milky `_strip_self_mentions`）；其他 → `@{mentions[].name}`。
- `mentions_bot`：任一 `mentions[].id.open_id == self_open_id`。
- `mentioned_user_ids`：全部 `mentions[].id.open_id`。
- `post`：遍历二维数组，`text` 拼接、`at` → `@名字`、`img/media/audio/file` → `InboundAttachment`。
- `image` → attachment(image, platform_id=image_key)；`audio` → attachment(audio, file_key)；`file` → attachment(file, file_key+file_name)；`media` → attachment(video)；`sticker/share_chat/share_user/system` → 占位文本（sticker 不可下载）。
- sender 显示名：群 → 成员缓存（§3.4）；单聊 → `contact/v3/users`（可选权限，有则用）→ 兜底 `ou_` 尾 6 位。

事件循环防护：`sender_type == "bot"` 的消息默认丢弃（飞书群消息权限不含"自己发的"，但含其他 bot 时需防互聊死循环）。

### 3.4 出站转换（`FeishuOutboundConverter`）

`send_message(target, message)` 流程（对齐 milky plugin.py:889-1018 的骨架）：

1. `reasoning` 前缀 `[💭 思考过程]\n` 纯文本块。
2. `resolve_target`：优先 `message.extra["chat_address"]`（cron/主动发送路径）→ `feishu:group:oc_x`/`feishu:private:oc_x` → receive_id_type=chat_id；target 以 `ou_` 开头 → receive_id_type=open_id（私聊直发）；`oc_` → chat_id。
3. 群消息 + `outbound_mentions_enabled`：`core/outbound_mentions.parse_outbound_parts(text)` 提取 `[CQ:at,qq=ou_xxx]` token → 成员缓存校验（member_id_type=open_id 分页拉全量，TTL `member_cache_seconds`，入站显示名共用）→ 通过者写 `message.extra["feishu_mention_ids"]`，未通过 token 保持字面文本（防幻觉 id；@bot 的 token 必然失败——members 接口不返回机器人——安全降级）。校验/降级打 `feishu.mention_outbound` 日志事件（对齐 milky 观测点）。
4. 文本转换：通过校验的 mention token → `<at user_id="ou_xxx"></at>` 内联；按 `max_text_length` 分片（30 KB 上限内）；逐条 `im/v1/messages` 发送，`uuid` 幂等，首条带 `reply_to`（走 reply API）。
5. 附件逐个：photo → `im/v1/images` 上传 → image 消息；document/audio/video → `im/v1/files`（file_type 按扩展名映射，其余 stream）→ file 消息。上传或富消息失败 → 纯文本 fallback 重发（对齐 milky 的 fallback 策略）。
6. 230020 频率限制 / 5xx / 网络错 → 指数退避重试（FeishuClient 内置，对齐 MilkyClient.post_api retry）。

`reply_to_inbound` 默认 `true`（飞书的 reply 有清晰的引用串展示，体验好）。

### 3.5 ChatAddress 与会话定位

```python
ChatAddress.from_inbound(platform="feishu", chat_id="oc_xxx", chat_type="group"|"p2p")
# → feishu:group:oc_xxx / feishu:private:oc_xxx
```

**单聊也用 chat_id 定位**（飞书单聊会话同样有 `oc_` id），出站统一 `receive_id_type=chat_id`，不需要维护 ou_↔oc_ 映射表；`ou_` 直发仅作为 cron 主动触达场景的可选路径。话题群（`chat_mode=topic`）v1 按普通群处理，`thread_id` → ChatAddress 的 thread 段留作扩展点（`feishu:group:oc_x:omt_y`）。

### 3.6 核心侧改动（横切）

- `core/outbound_mentions.py:23` `MENTION_CAPABLE_PLATFORMS` 加 `"feishu"`；`MENTION_INSTRUCTION`（core/message_context.py:88-105）需要平台参数化措辞（token 里填 open_id 而非 QQ 号）。
- router 的 `_with_chat_address`（router.py:1688）把 feishu 加进注入 `extra["chat_address"]` 的平台集合（milky/onebot 之后的第三个）。
- `register_prompt_supplement(key="no_markdown", channel="feishu")`：v1 纯文本发送，注入与 milky 相同的"不支持 Markdown"提示。

### 3.7 可选能力（按 ChannelService 扩展协议）

- `download_media(file_key, message_id)` → resources API → MediaStore（SSRF 防护内建，因为走自家 client 而非任意 URL），注册 LLM tool `feishu_download_file`。
- `get_group_info(chat_id)` → 群名（members 缓存附带覆盖）。
- `get_user_info(open_id)` → contact API（可选权限）。
- 表情回应（`im/v1/messages/{id}/reactions`）可对接现有 `PokeEvent`/reaction 语义，M3。

---

## 4. 配置模型

```yaml
feishu:
  enabled: false
  app_id: cli_xxxx                      # ${FEISHU_APP_ID}
  app_secret: ""                        # ${FEISHU_APP_SECRET}
  domain: https://open.feishu.cn        # Lark 国际版 → https://open.larksuite.com
  command_prefix: "/"
  group_trigger_mode: mention           # none|mention|command|always
  group_context_capture: false          # true 需申请 im:message.group_msg 敏感权限
  reply_to_inbound: true
  allowed_chats: []                     # oc_ 白名单；空 = 不限
  allowed_users: []                     # ou_ 白名单（单聊）；空 = 不限
  max_text_length: 3000                 # 分片阈值（post/卡片硬上限 30KB）
  outbound_mentions_enabled: true
  max_mentions_per_message: 3
  member_cache_seconds: 1800            # 成员列表缓存（显示名 + mention 校验共用）
  send_max_retries: 3
  send_retry_backoff_seconds: 1.5
  token_refresh_margin_ratio: 0.8       # expire*0.8 主动续期
```

`plugin.yaml`：

```yaml
id: feishu
entrypoint: "nahida_bot.channels.feishu.plugin:FeishuPlugin"
enabled: false
permissions:
  inbound: true
  network:
    outbound: ["open.feishu.cn", "open.larksuite.com"]
config: # §4 默认值
```

依赖（pyproject.toml）：

```toml
[dependency-groups]
feishu = ["lark-oapi>=1.6.0"]
# 同时加进 dev group 供测试；文档写 uv sync --group feishu
```

插件在 `on_load` 延迟 import SDK（对齐 telegram 模式），缺依赖时报清晰错误。

---

## 5. 目录结构

```
nahida_bot/channels/feishu/
├── plugin.yaml
├── plugin.py            # FeishuPlugin(Plugin) + ChannelService；生命周期、策略、publish、mention 校验
├── config.py            # FeishuPluginConfig(BaseModel) + parse_feishu_config
├── client.py            # FeishuClient(httpx)：token 管理、send/reply/upload/download/chat/members、重试
├── event_stream.py      # FeishuEventStream：SDK ws 线程桥（start/stop，loop 可注入 fake 测试）
├── message_converter.py # 入站：event dict → InboundMessage（占位符还原/附件/去重素材）
├── segment_converter.py # 出站：OutboundMessage → [(msg_type, content)] + resolve_target + fallback
└── _parsing.py          # coerce 工具（照搬 milky 模式）
```

测试（对齐 milky 测试三件套）：`test_feishu_plugin.py`、`test_feishu_message_converter.py`、`test_feishu_event_stream.py`（SDK client/loop 注入 fake）、`test_feishu_segment_converter.py`。converter 全部纯函数化，不碰网络。

---

## 6. API 覆盖计划

| API | 方法 | 用途 | 阶段 |
|---|---|---|---|
| `/auth/v3/tenant_access_token/internal` | POST | token 获取/续期 | M1 |
| `/bot/v3/info` | GET | bot open_id（mentions_bot 判定） | M1 |
| `/im/v1/messages` | POST | 发消息（uuid 幂等） | M1 |
| `/im/v1/messages/{id}/reply` | POST | 引用回复 | M1 |
| `/im/v1/chats/{chat_id}` | GET | 群名（chat_display_name） | M1 |
| `/im/v1/chats/{chat_id}/members` | GET | mention 校验 + 显示名缓存 | M2 |
| `/im/v1/messages/{id}/resources/{file_key}` | GET | 入站媒体下载 | M2 |
| `/im/v1/images` | POST | 出站图片上传 | M2 |
| `/im/v1/files` | POST | 出站文件上传 | M2 |
| `/im/v1/messages/{id}`（PATCH） | PATCH | 卡片流式更新 | M3 |
| `/im/v1/messages/{id}/reactions` | POST/DELETE | 表情回应 | M3 |

事件覆盖：M1 仅 `im.message.receive_v1`；M3 视需要加 `im.chat.member.bot.added_v1`（入群，对齐 router 的 chat 元数据观察）、`im.message.reaction.created_v1`。

---

## 7. 风险与开放问题

1. **SDK ws 客户端无优雅 stop**：用 `loop.stop()` 打断 + daemon 线程兜底；若后续 SDK 提供官方 stop/async 接口（channel 层已在演进）则替换。升级 SDK 前跑 event_stream 测试。
2. **SDK 版本演进**：`lark_oapi.channel` 高层封装（v1.6.0+）持续迭代，未来可能直接提供 asyncio 事件流；届时 event_stream 桥接层可换实现、上层不动。锁版本 `lark-oapi>=1.6.0` 并在 lock 固定。
3. **敏感权限审批**：`im:message.group_msg` 需企业管理员审批，`group_context_capture` 功能依赖它；默认关闭，文档注明。
4. **外部群/跨租户**：`external=true` 的群里 open_id 语义仍是本应用视角，无额外处理；用户离开可用范围后发送会报错（230034/无权限类），按发送失败 fallback 处理即可。
5. **音频出站需 OPUS 转码**：v1 用 `stream` 文件发送（体验为文件而非语音条），ffmpeg 转码（bot 已有 ffmpeg 相关能力）留 M3。
6. **多实例部署**：50 连接上限 + 事件随机推送意味着多实例会分片事件，当前单实例部署不受影响；文档注明单实例约束。
7. **开放问题（待决策）**：① 部署目标是国内飞书还是国际版 Lark（只影响 domain 默认值）；② 是否申请敏感权限开 `group_context_capture`；③ M3 卡片流式输出是否要上（LLM 流式回复体验，需要卡片 PATCH 或 FeishuChannel stream）。

---

## 8. 实施分期

- **M1（文本闭环）**：config + client（token/重试）+ event_stream 线程桥 + 入站转换（text/post 文本部分、mention 判定、去重）+ GroupInteractionPolicy 接入 + 出站纯文本（分片、uuid、reply）+ no_markdown supplement + 单测三件套。验收：单聊与群 @ 触发对话，长文分片正常。
- **M2（媒体 + 出站 mention）**：入站附件（image/file/audio/media → MediaStore + download tool）、出站图片/文件上传、mention 出站校验（members 缓存 + `feishu_mention_ids` + MENTION_CAPABLE_PLATFORMS 接入 + 指令注入）、显示名缓存。验收：群里 @ 指定成员正确渲染，图片双向收发。
- **M3（体验增强，按需）**：markdown→post 富文本、卡片消息与流式更新、reactions、话题群 thread 映射、音频 OPUS 转码、`im.chat.member.bot.added_v1` 入群感知。
