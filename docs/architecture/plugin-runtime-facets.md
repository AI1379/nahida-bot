# Plugin 运行时与 UI Surface

## 1. 结论：一个插件系统，多个运行时 facet

Nahida Bot 不为 Gateway、Node 和 Desktop 分别建立彼此独立的插件系统。插件的安装、
版本、依赖、权限、启停和配置都由同一个 `plugin.yaml` 与 Plugin Manager 管理；只有实际
执行代码的位置不同，这些位置称为 **runtime facet**。

```text
一个 plugin package / plugin.yaml
             │
             ├─ gateway facet    Python Plugin + BotAPI（当前）
             ├─ node facet       worker capability（清单与同步当前；执行器后续）
             ├─ desktop facet    本地 service/capability/action（内置 facet 当前）
             └─ UI contributions
                  ├─ pages       WebUI / Desktop 的隔离管理页面（当前）
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
runtimes:
  gateway:
    entrypoint: schedule:SchedulePlugin
    mode: python
  node:
    entrypoint: dist/schedule-worker.js
    mode: javascript
  desktop:
    entrypoint: builtin:example.schedule
    mode: builtin

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

旧清单的顶层 `entrypoint` 仍被解析为 `runtimes.gateway.entrypoint`，但新清单应显式写 facet。
只有 Node/Desktop/Page 的插件也是合法插件：Plugin Manager 会管理它的发现、加载、启停和
卸载状态，但不会为没有 Gateway facet 的插件导入 Python 模块。

当前 surface target 为 `desktop.home`、`desktop.sidebar`、`pet.overlay`、`pet.drawer`；kind
为 `text`、`badge`、`countdown`、`progress`、`list`、`card`。声明只决定可占用的槽位，
Gateway facet 产生的 surface 需通过 `BotAPI.register_desktop_surface_provider()` 注册对应
provider；Desktop facet 产生的本地 surface 则通过 `DesktopPluginContext.setSurface()` 写入。
两条路径都会校验 manifest 声明，未声明的 ID 会被拒绝，插件也不能伪造
`owner_plugin_id`。
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

Plugin Manager 的生命周期变化还会通过独立的 `plugin.runtime.sync` desired-state 快照发给
所有在线 Node；新注册 Node 会立即收到当前快照。快照同时带 Gateway 启动代次和递增 revision，
因此 Gateway 重启后的低 revision 不会被仍在运行的 Desktop 当成旧消息。Desktop 以 Gateway 的 enabled 状态为准，
只激活版本、entrypoint 和 `builtin` mode 都与本地 bundle 相符的 facet。缺少本地 artifact、
版本不一致或尚不支持的 mode 都会 fail closed，并显示兼容性诊断。离线/开发 mock 模式则保留
随 Desktop bundle 交付的内置 facet 作为本地 fallback。

本地能力也可适配成同一 surface contract。番茄钟的计时仍在 Desktop 本地运行，但显示已从
硬编码的 `PomodoroBadge` 迁移为本地 `pet.overlay/countdown` contribution，因此本地和远端
插件共享渲染层。

Desktop 当前提供 `DesktopPluginHost` 作为 runtime facet 适配器。它不负责再次发现或安装插件，
只激活已经随 Desktop bundle 交付、且由同一 manifest 声明的 facet；运行时会校验 capability、
action 和 surface 所有权，隔离 handler 异常，并在停用时释放计时器和清除本地 surface。
`nahida.pomodoro` 是第一条完整垂直链路：独立 Gateway 插件拥有 `desktop_pomodoro` 工具，
同 ID 的内置 Desktop facet 拥有计时服务、`desktop.pomodoro.control`、设置面板和倒计时 surface。
核心 `desktopRuntimeController` 与设置页不再直接实现番茄钟行为。插件设置保存在
`desktopPluginSettings[plugin_id]` 命名空间，不再混入会同步给 pet window 的
`LocalDesktopConfig`；旧版顶层 `pomodoro` 设置会在读取时迁移。

## 5. Page 宿主和安全边界

`webui.admin` 与 `desktop.main` 已有页面宿主。页面必须是插件目录中 manifest 声明的单个
UTF-8 HTML 文件，最大 1 MiB；Gateway 会拒绝目录逃逸、未声明页面、非 HTML 文件和未启用
插件。宿主用 `srcdoc` 加载，并强制注入 CSP：禁止网络、表单、对象、base URL 和外部资源；
iframe 只有 `allow-scripts`，没有 same-origin、弹窗、导航或存储权限。页面仅能读取冻结的
`window.__NAHIDA_PLUGIN_CONTEXT__` 元数据，没有高权限宿主 API。

这是一条有意保守的 V1：足够承载自包含的设置表单、报表和本地交互，但数据提交与宿主调用
协议尚未开放。需要写数据的页面应等待受权限约束、带来源校验的 message/action bridge，不能
通过放宽 sandbox 或直接暴露 Tauri/WebUI token 绕过。

## 6. 当前边界与后续顺序

当前已实现统一 manifest、无 Gateway facet 生命周期、runtime desired-state 同步、内置 Desktop
facet 对账、页面隔离宿主、声明式 surface 以及 WebUI 运行时/贡献项诊断。以下仍是后续工作：

1. 第三方 Desktop artifact 加载与进程隔离；`javascript`、`wasm`、`sidecar` 只做清单校验和
   不支持诊断，当前只执行随 Desktop bundle 交付且校验一致的 `builtin` entrypoint。
2. 页面与 surface 的受限 action/event 协议；当前页面没有宿主写 API，声明式 surface 只读。
3. `desktop.popup` 页面窗口策略与宿主；当前实现的是 `webui.admin` 和 `desktop.main`。
4. Node worker facet 的 artifact 分发、`NodeBotAPI` 与 capability bridge。Node facet 已进入统一
   清单和生命周期快照，但任何 `python/javascript/wasm/sidecar` 都不会因此被自动执行。

这些工作应沿用同一个 Plugin Manager 与 manifest 演进，不新增第二或第三套安装、权限和启停
系统。

四个声明式槽位均已有 Desktop 宿主：`desktop.home` 位于主运行界面，`desktop.sidebar` 是
主窗口可折叠侧栏，`pet.overlay` 是点击穿透状态也可见的轻量浮层，`pet.drawer` 只在桌宠进入
交互模式后允许展开，避免插件破坏常驻桌宠的点击穿透。开发模式可用
`?surface-preview=1` 注入四个本地 fixture 做布局验收；pet 窗口同时加 `?window=pet`。
