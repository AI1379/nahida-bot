# RSS Notifier

外部 RSS/Atom 订阅插件。插件位于 plugins/rss-notifier，不会进入核心 nahida_bot/plugins。

## 配置

在主配置文件加入顶层 rss-notifier：

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
      registration:
        enabled: true

feeds[].target_chat_addresses 可覆盖全局 target_chat_addresses。

首次轮询只建立基线，不会把历史条目一次性刷屏；后续发现新 item 才推送。

## 动态订阅命令

- /rss_sub <url> [display title]：当前聊天订阅 feed。
- /rss_unsub <url-or-list-index>：当前聊天取消动态订阅。
- /rss_list：列出当前聊天订阅。
- /rss_poll：立即执行一次轮询。

动态订阅存入插件数据表，会和 config.yaml 中的静态订阅合并。
