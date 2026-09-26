# GitHub Channel 设计

> 2026-09-25 · 对应 issue #31（feat: 接入 GitHub Bot）· 状态：已实现（v1）

## 1. 结论

GitHub 作为正式 channel（`nahida_bot/channels/github/`），不是通知器：

- **入站**：仓库 webhook（Issues / Issue comments / Pull requests / Pull
  request reviews）→ HMAC 校验 → 归一化为 `InboundMessage` → 每个
  issue/PR 一个群聊会话。
- **出站**：会话回复以 issue 评论形式发回（GitHub 原生渲染 Markdown）。
- **工具**：注册 `github_list_issues` / `github_get_issue` /
  `github_create_issue` / `github_add_comment`（普通）与
  `github_update_issue`（关闭/重开，`requires_admin=True`），
  任意 channel 的会话都能用自然语言管理 issue。

与 `plugins/github-notifier`（单向通知器）的关系：本 channel 是 GitHub
集成的正主；notifier 目前 repo 保持 `OWNER/REPO` 未激活，两者无冲突。
若同时启用且占用同一 webhook 路径，webhost 注册会在加载时报
`KeyError`，属预期行为——届时二选一或改 notifier 路径。

## 2. 关键平台事实（2026-09 查证）

- **凭据**：PAT（classic 需 `repo` scope；fine-grained 授予目标仓库
  issues 读写即可）。REST 用 `Authorization: Bearer`，无刷新流程。
- **webhook 签名**：`x-hub-signature-256: sha256=<hmac>`，密钥为仓库
  webhook 配置的 Secret；`x-github-delivery` 是一次投递的全局唯一 id
  （GitHub 对非 2xx 会重投，需幂等去重）。
- **评论 API**：`POST /repos/{owner}/{repo}/issues/{number}/comments`
  对 issue 与 PR 通用（PR 是 issue 的超集）；评论不嵌套，无 reply 语义。
- **评论上限**：65536 字符；PR 的 review comment 与 issue comment 是
  不同的 webhook 事件与 API 端点。
- **rate limit**：PAT 5000 req/h，匿名 60 req/h；429 带 `Retry-After`。

## 3. 架构

```
GitHub ──webhook POST──▶ /webhooks/github (gateway catch-all)
                          │ HMAC 校验 + ping 应答 + delivery LRU 去重 + 快速 202
                          ▼
              GitHubChannelPlugin.handle_inbound_event
                          │ self-loop 防护（converter 内完成）
                          ▼
              GitHubEventConverter.to_inbound
                          │ 触发类 → GroupInteractionPolicy
                          │ 上下文类 → 直接 MessageObserved
                          ▼
              MessageReceived / MessageObserved (session: github:group:o/r#N)
                          ▼
              MessageRouter → 会话 → AgentLoop
                          ▼ 回复
              channel.send_message → POST issue comment
```

### 3.1 会话与地址模型

每个 issue/PR 是一个持久群聊会话：

```
chat_id   = "owner/repo#123"
ChatAddress = github:group:owner/repo#123
AccountKey  = github:user:<login>          # 可直接进 authorization.admins
```

`target_id` 含 `/` 和 `#` 但不含 `:`，`ChatAddress.parse` 安全。`#` 使
router 附加的 `extra["chat_address"]`（`core/router.py` 的 channel 集合
已加入 `"github"`）与裸 target 两种解析路径都收敛到同一评论端点。

### 3.2 事件分级

| 类别 | (event, action) | 处理 |
| --- | --- | --- |
| 触发类 | `issue_comment.created` | 走 GroupInteractionPolicy（默认 mention） |
| 触发类 | `issues.opened` / `pull_request.opened` | 同上，文本 = 正文 |
| 触发类 | `pull_request_review.submitted` / `pull_request_review_comment.created` | 同上 |
| 上下文类 | `issues/pull_request` 的 `closed` / `reopened` | 恒为 MessageObserved（`lifecycle_as_context` 可关） |
| 其他 | 一切未列出组合 | 丢弃 |

chat display_name = `owner/repo#N · [PR ]标题`（payload 自带，零额外
API 调用）。`mentions_bot` 用 `@<bot_login>` 带词边界的正则（大小写不
敏感）判定。

### 3.3 入站安全（fail-closed 顺序）

1. Content-Type 必须 `application/json`（415）
2. `ping` 事件直接 204（无签名也可应答）
3. **未配置 `webhook_secret` → 非 ping 一律 403**（与 notifier 的
   warn-and-accept 不同：channel 会触发 agent，必须 fail-closed）
4. HMAC 校验失败 → 403
5. JSON 解析失败 → 400
6. delivery-id LRU(1024) 去重 → 204
7. converter 过滤：`sender.type == "Bot"` 或 login == bot 自身 → 丢弃
   （**self-loop 防护**：bot 自己发的评论会以 `issue_comment.created`
   回流）；`allowed_repos` 白名单（支持 `org/*` 通配整组织）
8. 发送者过滤（静态名单 OR 组织成员，见 3.3.1）
9. 通过 → spawn 后台任务处理，立即 202

#### 3.3.1 发送者过滤：静态名单与组织成员

```
发送者通过 ⇔ 在 allowed_senders 名单内
           ∨ 是任一 allowed_orgs 组织的成员（GET /orgs/:org/members/:user 探测）
两个列表都留空 → 所有人类发送者直接通过，完全不发起组织判定
```

- 名单与组织是 **OR** 关系：任一命中即放行；`allowed_orgs` 留空时
  连 API 探测都不发生（零成本路径）。
- 组织成员判定结果按 login 缓存：确定性答案（是/否成员）TTL 30
  分钟；**探测失败（403/网络错误）按「非成员」拒绝但只缓存 60 秒**
  （fail-closed 且自愈），LRU 上限 512。
- **隐藏成员坑**：组织开了成员隐私限制时，非 owner 身份的 token 查
  隐藏成员得到 404（误判非成员）、查公开成员端点可能 403。当前部署
  用组织 owner 的 PAT，能查到全部成员；换 token 时注意此语义。

### 3.4 出站

`send_message(target, message)`：`extra["chat_address"]` 优先，裸
`owner/repo#N` 次之；`POST` 一条评论。要点：

- `include_reasoning: false` 默认——公开仓库绝不外泄思考过程
- 超过 `max_comment_length`（默认 65536）截断加 `…(truncated)` 标记
- 附件 v1 不支持（GitHub 无 issue 评论附件通道），告警跳过

### 3.5 工具权限

写操作分级：查询/新建/评论普通可用（受 `allowed_repos` 限制，`org/*`
通配同样生效于工具层），`github_update_issue`（close/reopen/retitle/
labels）`requires_admin=True`，与 builtin `message` 工具的先例一致。
陌生人 @bot 触发的会话走标准 authorization 拦截，无法调用 admin 工具。

## 4. 部署清单（服务器）

1. `.env`：`GITHUB_TOKEN`（PAT）、`GITHUB_WEBHOOK_SECRET`（随机串）
2. `webapi.enabled: true` 且端口对公网可达（反代 `/webhooks/github`
   即可；webhook 端点不走 `require_token`，入口防线就是 HMAC）
3. config.yaml 启用 `github:` 块（默认 `webhook_path: "github"`）
4. 仓库 Settings → Webhooks → Add webhook：
   - URL `http(s)://<host>:<port>/webhooks/github`
   - Content type `application/json`，Secret 同 `.env`
   - 勾选 Issues / Issue comments / Pull requests / Pull request reviews
5. 验证：Recent deliveries 里 ping 应为 204；日志出现
   `github.loaded`（含发现的 `bot_login`）

## 5. v1 明确不做

Discussions（GraphQL，另开 issue）、附件上传、轮询 fallback
（webhook 不可达的过渡方案可用 notifier 的 polling 模式）、GitHub App
installation token 流、多 bot 账号。

## 6. 目录结构

```
nahida_bot/channels/github/
├── plugin.yaml           # id: github, enabled: false, permissions, config 默认值
├── config.py             # GitHubChannelConfig + parse_github_config
├── client.py             # GitHubClient（httpx, Bearer, Retry-After 重试）
├── event_converter.py    # payload → InboundMessage（事件分级 + 过滤）
├── tools.py              # github_* 工具注册
└── plugin.py             # 生命周期 + webhook intake + send_message
```

测试：`tests/test_github_{config,client,event_converter,plugin,tools}.py`
（91 用例，全 mock，不碰真实 API）。
