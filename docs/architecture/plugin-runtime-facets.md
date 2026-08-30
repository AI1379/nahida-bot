# Plugin 运行时与 UI Surface

## 1. 结论：一个插件系统，多个运行时 facet

Nahida Bot 不为 Gateway、Node 和 Desktop 分别建立彼此独立的插件系统。插件的安装、
版本、依赖、权限、启停和配置都由同一个 `plugin.yaml` 与 Plugin Manager 管理；只有实际
执行代码的位置不同，这些位置称为 **runtime facet**。

```text
一个 plugin package / plugin.yaml
             │
             ├─ gateway facet    Python Plugin + BotAPI（当前）
             ├─ node facet       worker capability（后续）
             └─ UI contributions
                  ├─ pages       WebUI / Desktop 的复杂管理页面（后续宿主）
                  └─ surfaces    Desktop 原生渲染的轻量 view model（当前）
```

因此 SDK 统一的是领域契约、manifest 和生命周期，不强求 Python、Rust、TypeScript 共享
同一种二进制 SDK。各技术栈只实现同一份语言中立协议的薄适配层。

## 2. Page 与 Surface 必须分开

两类 UI 扩展解决的问题不同：

| 类型 | 适合内容 | 执行与渲染边界 |
|------|----------|----------------|
| `pages` | 复杂插件的设置、历史、报表和管理界面 | 插件提供独立前端资源；WebUI 或 Desktop 在隔离页面宿主中加载 |
| `desktop_surfaces` | 今日安排、余额摘要、状态徽标、倒计时、进度和短列表 | 插件只返回受限 view model；Desktop 用原生 Vue 组件渲染 |

Surface 不允许注入 Vue 组件、执行任意 JavaScript 或直接调用 Tauri API。这样桌宠窗口仍能
保持透明、点击穿透、尺寸和性能约束，也避免 Desktop 再形成一套高权限插件生命周期。

## 3. Manifest

```yaml
id: example.schedule
name: Schedule
version: "1.0.0"
entrypoint: schedule:SchedulePlugin

contributes:
  desktop_surfaces:
    - id: today
      target: desktop.home
      kind: list
      priority: 20
    - id: next_event
      target: pet.overlay
      kind: countdown
      priority: 50
  pages:
    - id: settings
      target: webui.admin
      entry: dist/settings.html
      title: 日程设置
```

当前 surface target 为 `desktop.home`、`desktop.sidebar`、`pet.overlay`、`pet.drawer`；kind
为 `text`、`badge`、`countdown`、`progress`、`list`、`card`。声明只决定可占用的槽位，
插件仍需通过 `BotAPI.register_desktop_surface_provider()` 注册对应 provider。未在 manifest
声明的 ID 会被拒绝，插件也不能伪造 `owner_plugin_id`。
provider 的底层数据变化后，插件调用 `request_desktop_surface_refresh(id)` 请求 Gateway 重新
生成并分发完整快照；倒计时只需在 deadline 改变时刷新，Desktop 会按 `expires_at` 本地派生
秒数，不应每秒请求同步。

## 4. 同步流程

```text
Plugin provider
  → DesktopSurfaceRegistry（所有权、超时、异常隔离、schema 校验）
  → DesktopSurfaceService（完整 desired-state snapshot + revision）
  → Gateway-Node capability: desktop.surface.sync
  → Desktop parser（数量、长度、枚举、重复 ID、数值范围校验）
  → PluginSurfaceHost（宿主组件渲染）
```

Desktop 注册 node 或 provider 集合变化时，Gateway 推送完整快照。完整快照比增量 patch 更容易
处理插件禁用、卸载和断线重连；revision 防止并发或重连造成旧状态覆盖新状态。provider 的
返回值允许按 Desktop node 的非敏感 metadata 生成不同视图，但单个 provider 超时或返回非法
数据时只丢弃该 surface，不影响其他插件。

本地能力也可适配成同一 surface contract。番茄钟的计时仍在 Desktop 本地运行，但显示已从
硬编码的 `PomodoroBadge` 迁移为本地 `pet.overlay/countdown` contribution，因此本地和远端
插件共享渲染层。

## 5. 当前边界与后续顺序

当前已实现 manifest、Python provider、Gateway 快照同步、Desktop contract/宿主渲染以及
WebUI 中 contribution 元数据展示。以下仍是后续工作：

1. `pages` 的静态资源打包、CSP/iframe 隔离、路由挂载与宿主 API；当前只有 manifest 元数据。
2. `desktop.sidebar` 与 `pet.drawer` 的具体槽位；当前宿主先覆盖 `desktop.home` 和
   `pet.overlay`。
3. 用户交互 action/event 协议；首版 surface 是只读展示，不能从 view model 触发任意命令。
4. Node worker facet 的 `NodeBotAPI` 与 capability bridge；它复用相同 manifest，但不会让
   Gateway Python SDK 直接在 Rust/TypeScript 中运行。

这四项应沿用同一个 Plugin Manager 与 manifest 演进，不新增第二或第三套安装、权限和启停
系统。
