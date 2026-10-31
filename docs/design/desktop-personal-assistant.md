# Desktop 个人事务插件（第一版）

状态：2026-10-30 完成第一版实现。插件默认关闭，部署时可按下述配置启用。

## 模块边界

- `todo`：服务器保存个人任务，提供聊天工具、版本化服务和桌面今日任务列表。
- `daily-brief`：按个人时区在固定时间生成不可变简报；读取来源服务，独立于实时任务面板。
- `notification-router`：保存通知、选择桌面或个人私聊、领取投递、处理失败与不确定结果。
- Gateway：认证节点身份，传输版本化插件调用、设备状态与静音通知。
- Desktop：采集粗粒度状态，保存接收记录，在桌宠阅读气泡中显示正文。

业务代码保留在插件目录。核心新增版本化服务注册与插件数据 CAS；复用既有
`spawn_interval_task`、`plugin_data` 和 Desktop surfaces，无需新增数据库表或修改旧 cron 行为。

## 启用

与已有插件一样，可在 WebUI 插件管理中启用，或在机器人配置中添加顶层配置：

```yaml
todo:
  enabled: true
  timezone: Asia/Shanghai
notification-router:
  enabled: true
daily-brief:
  enabled: true
  sources: ["todo:tasks.v1"]
```

重新构建/安装 Desktop 后连接 Gateway。三个插件独立启停；启用插件本身不会开启
某个用户的每日简报，也不会自动启用私聊备用渠道。

在已绑定身份的个人聊天中，可以让 Agent 调用：

1. `todo_add`：添加任务，`planned_on` 表示计划日期，`due_date` 表示截止日期。
2. `daily_brief_preferences(enabled=true, time="08:00", timezone="Asia/Shanghai")`：开启该账户的早报。
3. `notification_preferences(fallback_enabled=true)`：允许电脑不适合提醒时发到该账户自己的私聊。

工具参数用结构化 JSON 传递；以上是说明性调用示例。`todo_list` 返回任务 ID 和版本，
`todo_update` 使用它们完成、取消或修改任务。日期使用 `YYYY-MM-DD`。

Desktop 首页与桌宠插件抽屉显示今日任务，可点击“完成”。简报卡片的“在桌宠气泡中阅读”
打开正文；长文只走文本阅读，不进入 TTS、Live2D 播报或系统通知流程。阅读气泡有滚动区，
关闭不会完成任务，可重新打开最近通知。桌宠设置中的“通知勿扰”在本机持久保存。

## 版本化接口

| 服务 | 操作 | Desktop 可调用 |
|---|---|---|
| `todo:tasks.v1` | `list` / `today` / `create` / `update` | 是 |
| `daily-brief:briefs.v1` | `preferences` / `configure` / `today` | 是 |
| `notification-router:notifications.v1` | `preferences` / `configure` / `list` | 是 |
| 同上 | `submit` | 仅服务器插件 |
| `host.desktop:v1` | `endpoints` / `deliver` | 仅服务器插件 |

服务 handler 接收 `operation`、`arguments`、`PluginServiceContext`。上下文中的账户身份由
Gateway token 或服务器插件调用方提供，Desktop 请求不能设置它。服务随插件停用撤销。
这是可信服务器插件之间的进程内接口，不是对任意 Python 插件代码的安全沙箱。

未来日历等来源提供 `today({date}) -> {items: [{id, title, due_date?, ...}]}`，并加入
`daily-brief.sources`。原始业务对象继续由来源插件维护。来源失败会明确写入简报，不能
将其表述成“今天没有事情”。

## 状态、路由与失败语义

Desktop 复用 Gateway-Node WebSocket：

- `node.context.update`：连接内递增序号、锁屏状态、active/idle/unknown、勿扰、界面是否聚焦。
- 原生层约每 2 秒检查变化，随连接心跳刷新快照；服务器在 90 秒后使状态失效。
- Windows 使用当前交互会话的输入空闲时间和输入桌面名称；无法检测时上报 unknown。
- 没有上传按键内容、截图或窗口标题。状态仅辅助路由，不授予额外权限。
- `plugin.service.call`：仅开放明确声明为 Desktop 可用的服务，任务修改带操作 ID 和预期版本。

路由使用新鲜、未锁屏、近期活跃的桌面。多设备时按主设备配置、稳定节点 ID 选择一台。
桌面状态未知或不适用时，可以使用明确开启的个人私聊备用渠道。个人任务不会回落到创建它的群聊。
用户勿扰或新鲜设备上的勿扰不会通过切换渠道绕过。客户端接收前再次检查本地锁屏和勿扰。

通知先写入插件存储，再由 worker 领取。领取有 30 秒租约；原子比较交换保证同一服务器
进程内并发 worker 不会同时发送同一记录。当前数据库访问契约是单服务进程。

- Desktop `accepted` 表示已持久保存本地内容，不表示系统展示成功或用户已读。
- Desktop 按通知 ID 去重；保存失败不得返回 accepted。
- Desktop 结果未知时只向原设备重试相同 ID，最多三次，不改发第二个渠道。
- 私聊发送结果未知时保留 `unknown`，不自动重发，以免重复发送。
- 无可用允许渠道或勿扰时保留 pending；过期转 expired。
- 投递记录保存选中渠道、原因、尝试记录与结果，便于解释路由。

简报按账户与当地日期去重。简报先保存，再用稳定键提交给通知插件，最后记录提交完成；
任何一步中断，下次周期重用相同快照和通知键恢复。默认早报补发窗口为计划时间后 240 分钟，
且不超过当天；恢复后不补发过去多天的简报。

## 第一版范围

- TODO 暂以认证账户为作用域；跨账号合并到 person、精确到时刻的截止时间和重复任务留作后续。
- 桌面任务支持查看和完成；新增及完整编辑通过聊天工具。离线编辑、日历适配器、通知稍后提醒及已读升级尚未实现。
- 本机缓存最近 200 条通知，并按 Gateway、node 和凭据隔离。今日任务面板连接服务器取得最新状态。
- 当前通知入口为新个人插件；已有 cron 保留固定聊天地址投递，尚未加入动态路由配置。
- Windows 提供原生存在状态；其他平台暂上报 unknown，可使用明确配置的备用渠道。
- 新插件的内容展示均静音；已有普通聊天语音和短提醒接口保持原行为。
- 外部渠道不承诺严格 exactly-once；不确定结果保守保留，不伪造送达或已读。

## 验证

测试使用临时 SQLite 数据库与假的投递端，无生产 API、聊天发送或生产数据库写入。
覆盖插件启停与数据保留、身份隔离、任务版本冲突、重复创建、简报快照与补发窗口、
并发 worker、锁屏/空闲/未知路由、勿扰、回执丢失、前端持久化失败及重复接收。

另执行 Desktop 类型检查、前端全量测试、Rust 编译检查和长文组件的浏览器合成数据预览。
原生窗口中的锁屏/解锁、系统托盘与实际 QQ 私聊投递仍需安装后验收。

验收结果：服务端定向回归 161 passed；Desktop 全量 321 passed；Rust 18 passed / 1 ignored
（既有交互桌面截图测试）。Python 类型检查、Ruff、Vue 类型检查与前端生产构建通过。
Vite 输出 VueUse 注释和大分包警告；不影响构建结果。质量审查见
[desktop-personal-assistant-review.md](desktop-personal-assistant-review.md)。
