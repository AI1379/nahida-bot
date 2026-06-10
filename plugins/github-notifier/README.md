# GitHub Notifier 插件

把固定仓库的 issue / pull request opened、closed 事件推送到一个或多个
nahida-bot chat address。

## 配置

在主配置文件中加入顶层 `github-notifier`：

```yaml
github-notifier:
  repo: "OWNER/REPO"
  target_chat_addresses:
    - "milky:group:123456789"
  webhook:
    enabled: true
    path: "github"
    secret: "${GITHUB_WEBHOOK_SECRET:}"
  polling:
    enabled: true
    interval_seconds: 60
    api_base_url: "https://api.github.com"
    token: "${GITHUB_TOKEN:}"
  registration:
    enabled: false
```

Webhook URL:

```text
http://127.0.0.1:6185/webhooks/github
```

本地测试可使用 GitHub CLI：

```powershell
gh extension install cli/gh-webhook
gh webhook forward --repo=OWNER/REPO --events=issues,pull_request --url=http://127.0.0.1:6185/webhooks/github
```

GitHub CLI forwarding 只建议用于开发测试。生产部署建议使用公网 HTTPS
入口、GitHub webhook secret、GitHub hooks IP allowlist 和反向代理限流。

## 动态订阅

打开 `registration.enabled: true` 后，聊天中可用：

```text
/github_watch
/github_unwatch
/github_watch_status
```

动态订阅会存入插件数据表，和 `target_chat_addresses` 合并后一起通知。
