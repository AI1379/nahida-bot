# RSS Notifier

外部 RSS/Atom 订阅插件。插件位于 plugins/rss-notifier，不会进入核心 nahida_bot/plugins。

## 配置

在主配置文件加入顶层 rss-notifier：

```yaml
rss-notifier:
  enabled: true
  target_chat_addresses:
    - "milky:group:123456789"
  feeds:
    - url: "https://example.com/feed.xml"
      title: "Example"
  polling:
    enabled: true
    interval_seconds: 300
  rendering:
    mode: "standard"
    max_text_chars: 500
    max_paragraphs: 3
    include_images: true
    max_images: 1
    send_image_attachments: true
  registration:
    enabled: true
```

feeds[].target_chat_addresses 可覆盖全局 target_chat_addresses。

首次轮询只建立基线，不会把历史条目一次性刷屏；后续发现新 item 才推送。

## 轻富文本渲染

插件会通用解析 RSS/Atom 条目的 HTML 内容：

- p/div/li 等块级结构会尽量保留为段落。
- 普通换行和 `<br>` 会保留为消息内换行。
- img、enclosure、media:thumbnail、media:content 会提取为图片。
- 默认发送前 3 段正文、最多 500 字、最多 1 张图片。
- send_image_attachments 为 true 时会下载图片并作为 photo 附件发送。
- 图片下载失败时不会阻断通知，会退化为在文本中附上图片链接。
- 图片附件使用核心托管临时文件 API，发送成功后由核心/插件清理，不会长期留在系统临时目录。

渲染是通用规则，不针对某个 feed 或站点特化。

自动轮询发现新 item 时，会向订阅目标发送一条 `OutboundMessage`：文本中包含订阅源名、标题、发布时间、链接和正文摘要；如果配置允许且图片下载成功，会附带图片附件。`/rss_latest` 也使用同一套渲染逻辑查询最近条目，并且可以返回图片附件。

## 动态订阅命令

- /rss_sub <url> [display title]：当前聊天订阅 feed。
- /rss_unsub <url-or-list-index>：当前聊天取消动态订阅。
- /rss_list：列出当前聊天订阅。
- /rss_poll：立即执行一次轮询。
- /rss_latest [n] [url-or-list-index-or-display-name]：查看当前聊天订阅源的最新 n 条 item，不修改轮询去重状态。别名：/rss_recent。
  display name 使用 `/rss_sub <url> [display title]` 或 `feeds[].title` 设置；匹配时 URL 优先，其次 display name 精确匹配，最后 display name 包含匹配。

动态订阅存入插件数据表，会和 config.yaml 中的静态订阅合并。
