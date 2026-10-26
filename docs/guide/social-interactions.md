# 戳一戳与表情包库

主动戳一戳和主动发表情由当前会话的 Agent 决定。普通回复与 conversation joiner
触发的群聊回复都可调用这些工具，无需增加一次选图模型请求。两个功能分别启用。

## 主动戳一戳

在现有 Milky 配置中添加：

```yaml
milky:
  enabled: true
  # 保留现有 base_url、access_token 等配置
  enable_poke_tool: true
  poke_cooldown_seconds: 30
```

`milky_poke_user(user_id="10001")` 在当前群戳指定成员一次；省略 `user_id`
时戳当前发言人。群成员资格复用出站 mention 的 TTL 校验缓存。私聊只能戳当前联系人，
不接受其他目标、其他会话或机器人自己；群/好友白名单仍然生效。

工具使用 Milky 的 `send_group_nudge` / `send_friend_nudge`，需要协议端实现对应 API。
当前版本只支持 Milky。每个 typed chat 共用一个出站冷却，发送错误会返回明确结果。
超时保留冷却且不自动重试，因为动作可能已经到达 QQ。冷却状态只保存在内存中。

收到的戳一戳仍通过 `PokeEvent` 进入既有处理链路。若希望机器人被戳后有机会回应，
还需启用并配置 conversation joiner，在其 `prefilter` 中设置 `enable_poke: true`，
通过 `poke_sample_rate` 控制采样率。它仍是弱互动信号，继续遵守入话题门控与预算，
不会每次必回。现有 joiner 的被戳回应只覆盖群聊，好友戳一戳仅派发事件。

日志：`milky.poke_outbound`、`milky.poke_outbound_failed`。

## 表情包库

```yaml
meme_library:
  enabled: true
  directory: "./data/memes"
  max_image_bytes: 10485760
  max_items: 2000
  refresh_seconds: 30
  send_cooldown_seconds: 10
  collect_enabled: true
  collect_cooldown_seconds: 30
  allowed_chats:
    - "milky:group:833325688"
```

`allowed_chats` 同时限制搜索、发送和导入入口。空列表表示对所有聊天开放。
这是运营者维护的共享图库，导入即意味着该图片可被允许的会话检索、发送。

### 批量放图

将 PNG、JPEG、GIF 或 WebP 放入图库。相对目录作为分类，文件名作为默认标题：

```text
data/memes/
  开心/
    纳西妲_好耶.gif
    纳西妲_好耶.gif.json
  无语/
    扶额.png
```

可以为图片添加同名 JSON 旁注，例如 `纳西妲_好耶.gif.json`：

```json
{
  "title": "纳西妲好耶",
  "tags": ["纳西妲", "开心", "庆祝", "happy"],
  "description": "笑着挥手，适合庆祝成功或表示赞同"
}
```

标题最多 120 字，描述最多 500 字，最多 32 个标签、每个标签 80 字。
没有 JSON 也可使用。无效图片、超大图片和损坏元数据会跳过并记录索引告警。
目录变化在下一次到期的搜索/发送中刷新；达到容量上限不会删除任何文件。

ID 取图片内容 SHA-256 的前 20 位；相同图片去重，移动、重命名后 ID 保持一致。
同图出现在多个分类时使用扫描顺序中的首份元数据。GIF 保留原始文件，不转换为静态图；
Telegram 的 GIF 图片附件使用 `sendAnimation` 发送。

### 对话中使用

模型调用 `meme_search(query="纳西妲 开心")` 获取候选，再调用
`meme_send(meme_id="搜索返回的ID")` 发送一张。可以直接说“发张开心的纳西妲表情”。

检索支持标题、目录分类、标签和描述，空格分隔的词需要全部匹配；可额外传入 `category`
精确筛选分类。当前实现是本地关键词检索，推荐用标签覆盖常用同义词。

也可以直接使用命令：

```text
/meme
/meme 纳西妲 开心
/表情 无语
/meme send <搜索得到的ID>
```

发送仅接受已有图片 ID，目标固定为当前会话；没有任意路径或跨会话发送参数。
发送前重新验证文件路径、大小和内容，防止旧索引指向已替换的图片。
每个 typed chat 有独立发送冷却；失败或未获确认的发送同样保留冷却。

### 她选择收藏收到的图片

`collect_enabled: true` 会注册 `meme_collect`。她看见本轮对话中的图片后，可以自主
判断是否值得作为表情保存，选择图片并填写标题、分类、标签和描述；不需要用户先说“收藏”。
插件不会额外调用一个选图模型，也不会把每张收到的图片都保存下来。

```json
{
  "image_index": 1,
  "category": "开心",
  "title": "纳西妲好耶",
  "tags": ["纳西妲", "开心", "庆祝"],
  "description": "笑着挥手，适合庆祝成功或表示赞同"
}
```

`image_index` 从 1 开始，只在**本轮收到的图片附件**中计数，不包括音频、文件等附件。
工具不接受任意路径、URL 或历史消息 ID；使用通道已缓存的原图，必要时经原通道下载。
收藏保留原始字节，重复内容复用已有条目，仍受图库容量与图片大小限制。

收藏与发图有独立的会话冷却。插件记录来源会话、用户、媒体 ID 和收藏时间，
这些来源信息只提供给管理员页面，不出现在普通 `meme_search` 结果中。
收藏后进入共享图库，可被 `allowed_chats` 允许的其他会话使用。

它只在消息已进入 Agent 对话轮次时工作：普通群聊中没有触发 Agent 的图片不会自动分析。
conversation joiner 触发的轮次同样可以使用收藏工具。

### 插件管理页

启用插件后，打开 **WebUI → Plugins → Meme Library → 表情包库**。
页面提供图片/GIF 预览、关键词与分类筛选、分页，以及标题/标签/描述编辑、收藏来源和删除确认。
删除会移除库内相同内容的图片副本和元数据，不影响来源聊天的原始消息。

页面 HTML 与管理操作均位于 `nahida_bot/plugins/meme_library/`，随插件启停。
核心仅提供通用的页面操作通信与当前轮次附件解析，不保存表情业务状态。
页面通过宿主的管理员认证调用自己所属插件，保持 iframe 沙箱与直接网络访问限制。

### 管理员导入

管理员可让模型调用 `meme_import`，从当前 workspace 复制图片：

```json
{
  "path": "images/nahida-happy.gif",
  "category": "开心",
  "title": "纳西妲好耶",
  "tags": ["纳西妲", "开心", "庆祝"]
}
```

仅接受 workspace 内的相对路径，遵守现有 sandbox；原文件不变。导入同图直接复用现有条目，
修改标签或分类请编辑库内旁注或目录。新增图片导入后立即可检索，无需重启。

日志：`meme_library.loaded`、`meme_library.index_incomplete`、
`meme_library.sent`、`meme_library.send_failed`。

## 实现范围与参考

当前版本支持主动戳人、图库导入/检索/发送、收到图片后的选择性收藏与插件管理页。
未触发 Agent 的群消息不会逐张分析；独立后台收图/视觉打标、向量检索和定时戳人尚未实现。
图库不附带默认素材，可以人工导入或在对话中逐渐收藏。

参考了 AstrBot [litepoke](https://github.com/kazamisama/astrbot_plugin_litepoke)
的显式戳人工具思路，以及 [meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager)
的分类检索与工具选图方式；代码依照 Nahida 的通道、SessionContext 与插件 API 独立实现。
Milky API 参数以[群聊 API](https://milky.ntqqrev.org/api/group)和
[好友 API](https://milky.ntqqrev.org/api/friend)为准。
