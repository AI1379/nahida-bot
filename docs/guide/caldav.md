# CalDAV 提醒事项与日历

Nahida 可以把提醒事项与日程绑定到两个独立的 CalDAV 账户/服务。提醒事项使用 `VTODO`，
日历使用 `VEVENT`。可以只配置日历，继续保留本地 TODO；之后再接入提醒事项服务。

## 插件边界

- `caldav`：连接、凭据解析、账户绑定、iCalendar 读写、ETag 冲突保护。
- `todo`：统一个人任务入口，保留已有本地任务，按账户配置选择新任务的默认来源。
- `calendar`：日程工具、空闲时间查询、桌面今日日程。
- `daily-brief`：可组合 `todo:tasks.v1` 与 `calendar:events.v1`，以当地时间展示日程。

核心只增加插件发现项。外部任务不复制为另一份可独立修改的本地任务。
本地仅保存外部资源引用，任务状态每次向来源查询；来源不可用时明确报错，不悄悄在本地重复创建。

## 配置示例

以下是机器人运行 YAML 的顶层插件配置。账户键必须匹配当前聊天或 Desktop token 绑定的
`channel:user:id`。连接地址、凭据和集合选择由配置提供，Agent 工具不能任意指定 URL。

```yaml
caldav:
  enabled: true
  connections:
    reminders:
      url: https://dav.example.com/
      username: your-reminder-account
      password_env: NAHIDA_CALDAV_REMINDERS_PASSWORD
    icloud-calendar:
      url: https://caldav.icloud.com/
      username: your-apple-account
      password_env: NAHIDA_CALDAV_CALENDAR_PASSWORD
  bindings:
    "milky:user:YOUR_ID":
      timezone: Asia/Shanghai
      tasks:
        connection: reminders
        collection: /your-reminder-account/tasks/
      calendar:
        connection: icloud-calendar
        read_collections:
          - https://pNN-caldav.icloud.com/ACCOUNT/calendars/READ_CALENDAR/
        write_collection: https://pNN-caldav.icloud.com/ACCOUNT/calendars/BOT_CALENDAR/

todo:
  enabled: true
  timezone: Asia/Shanghai
  backends:
    "milky:user:YOUR_ID": caldav:tasks.v1

calendar:
  enabled: true
  timezone: Asia/Shanghai
  backends:
    "milky:user:YOUR_ID": caldav:calendar.v1

daily-brief:
  enabled: true
  sources: ["todo:tasks.v1", "calendar:events.v1"]
```

`pNN`、`ACCOUNT`、集合路径都是占位符，必须使用实际发现结果。也可把日历连接指向已有
代理服务。自建提醒事项账户与 Apple 账户的凭据完全分开；iCloud 通常使用应用专用密码。
`password_env` 与 `password_secret` 二选一，后者读取 `caldav` 插件已有的 opaque secret store。
不把密码作为 Agent 工具参数，不把它写入任务数据或工具返回值。

如果只接日历：省略 `caldav.bindings.<账户>.tasks`，并保留 `todo.backends: {}`。
日历的 `write_collection` 为空时只读；读取范围为显式 `read_collections` 加写入日历。
其他账户不会继承这些绑定。多个账户可使用不同的连接、清单和日历。

## 只读发现

先在启动 Bot 的环境中设置对应密码变量，再运行：

```powershell
uv run python scripts/probe_caldav.py --url https://caldav.icloud.com/ --username YOUR_ACCOUNT --password-env NAHIDA_CALDAV_CALENDAR_PASSWORD
```

输出集合名称、URL 和支持的组件类型，供填写上述配置。此脚本只执行发现请求。
从 `caldav.icloud.com` 发现到标准 `p数字-caldav.icloud.com` 分片是允许的；其他跨域跳转
必须在该连接的 `allowed_origins` 中显式列出。默认验证 HTTPS 证书。

## Agent 工具与桌面

- `todo_list` / `todo_add` / `todo_update`：本地与外部任务。新任务默认进入配置的来源；已有本地任务仍可读取和修改。
- `calendar_list`：按明确的时间窗口查询日程，展开重复事件与例外。
- `calendar_availability`：综合所有配置的日历，返回满足指定时长的空闲窗口。全天事件按日历时区计算；透明或已取消事件不占忙时。
- `calendar_create`：在指定写入日历创建个人事件，默认先检查冲突。
- `calendar_update` / `calendar_delete`：使用查询返回的 ID 和版本修改个人事件。

日期时间必须带 UTC offset，例如 `2026-11-06T14:00:00+08:00`。
全天事件使用日期字符串，结束日期为不包含的那一天，例如 `2026-11-06` 到 `2026-11-07`。
查询窗口最多 90 天，结果有数量上限；读取失败不会被当作“时间空闲”。

TODO 的 `due_date` 与 `due_at` 二选一，表示截止日期/时刻。`remind_at` 在 CalDAV 任务上
创建一个绝对时间的原生 `DISPLAY` 提醒；本地 TODO 尚未实现定时闹钟，会明确拒绝这个字段。
仅设置截止日期不会自动创建 cron。显式修改 `remind_at` 会替换该任务原有的 alarms；
其他修改保留原有 alarms 和未涉及的 iCalendar 属性。

`planned_on` 是 Nahida 的计划日期，通过 `X-NAHIDA-PLANNED-ON` 保存，其他客户端是否保留
扩展字段需要真机验收。标题、描述、优先级、截止时间和完成状态使用标准属性。
日程可以携带 `task_id` 关联事项；结束、修改或删除日程均不会自动完成或删除关联任务。

任务与日程操作分别返回结果，跨服务不假装具有原子事务。创建重试应使用同一个
`operation_id`，修改使用最新 `expected_revision`。网络写入结果未知时先查询并复用原操作键，
不要新建另一条。冲突检查是发送时的观察，不能阻止其他客户端随后同时预约。

## 本地测试服务

开发依赖包含 Radicale，`uv sync` 后可在两个终端启动：

```powershell
uv run python scripts/caldav_local_server.py --port 5232
uv run python scripts/caldav_local_server.py --port 5233
```

每个进程打印自己的临时目录和 `local-connection.json` 路径。该文件保存合成测试账户的
连接信息。把第一个服务的 tasks 集合、第二个服务的 calendar 集合分别配入 Bot，即可验证
两套地址和凭据互不混用。测试 URL 是 `http://127.0.0.1:端口/`，对应连接需要显式
`allow_local_http: true`。这个选项只允许回环地址，不能用于公网 HTTP。

服务只绑定本机，Ctrl-C 停止。没有部署服务器上的 Radicale，也没有连接真实 iCloud 数据。
自动测试会独立启动两套临时服务并清理进程，不需要提前手动启动。

## 当前兼容范围

- 支持真实 CalDAV HTTP 读写、发现、条件创建与版本更新；已在独立 Radicale 服务上集成测试。
- 当前使用 HTTP Basic 用户名/密码认证，适合应用专用密码；暂未实现 OAuth、Digest 或客户端证书认证。
- 支持重复日程读取和例外展开。第一版拒绝直接修改/删除重复系列或带参与者的会议，避免误改整组事件或发送邀请。
- 日历和任务来源的失败独立处理；不会跨连接回退写入另一个账户。
- 日历模块提供桌面卡片，现有 host 渲染协议可直接展示，无需新增桌面专属业务代码。
- 真实 iCloud 接口、iPhone 同步延迟、原生提醒触发、标签/子任务等高级功能尚需实际账户与手机验收。
- 服务端写入成功表示 CalDAV 已保存，不代表 iPhone 已同步或用户已经看到提醒。

## 参考

- [Apple：提醒事项添加其他账户](https://support.apple.com/guide/iphone/add-or-remove-accounts-iph8739025dd/ios)
- [CalDAV RFC 4791](https://www.rfc-editor.org/rfc/rfc4791)
- [Radicale 文档](https://radicale.org/v3.html)

## 本地验证记录

2026-11-05：相关定向回归 134 passed，完整 Pyright 与 Ruff 检查通过。
使用两个独立 Radicale 实例验证了不同地址/凭据/集合、Agent 工具入口、源故障隔离、
本地与远程任务共存、版本冲突、创建重试、全天/重复日程、DST 忙闲计算和字段保留。
质量复核见 [CalDAV integration review](../design/caldav-integration-review.md)。
