# 插件 Web 面板与 Admin API 设计

> 状态：设计草案
> 日期：2026-06-22
> 最近更新：2026-06-22
> 目标：为 nahida-bot 设计一种“插件自带 Web 面板”的通用扩展机制，让 RSS notifier 这类插件可以提供复杂管理界面，而不把插件业务 UI 硬编码进主 WebUI。
> 相关文档：
>
> - [plugin-audit-and-plan.md](plugin-audit-and-plan.md)
> - [webui-design.md](webui-design.md)
> - [plugin-system.md](../architecture/plugin-system.md)
> - [data-and-state.md](../architecture/data-and-state.md)

---

## 1. 背景

RSS notifier、MCP 动态服务器、GitHub notifier、知识库导入器等插件都可能需要比 config_schema 更复杂的管理界面：

- RSS notifier 需要展示订阅列表、目标 chat address、最近轮询状态、最近错误、手动触发轮询、导入/导出订阅。
- MCP 插件需要管理动态 server、连接状态、工具列表和重连操作。
- GitHub notifier 可能需要展示 webhook 状态、polling 状态、动态 watch 目标和最近 delivery。

如果这些页面直接写进主 WebUI，例如新增 RssNotifierPage.vue，插件就变成了“核心特殊功能”。这违背插件外置的目标：插件可以独立安装、独立更新、独立移除，主线只维护通用扩展协议。

因此，主线应该提供插件 Web 扩展 surface，而不是提供任何具体插件的业务页面。

---

## 2. 参考：AstrBot Plugin Pages

AstrBot 的 Plugin Pages 是合适的参考模型：

- 简单配置优先使用 schema 驱动的可视化配置。
- 复杂交互由插件提供 pages/page_name/index.html。
- Dashboard 在插件详情页中以受限 iframe 加载插件 Page。
- Page 脚本通过 window.AstrBotPluginPage bridge 与 Dashboard 通信。
- Dashboard 再把请求转发到插件注册的后端 Web API。
- Pages 适合复杂表单、状态面板、日志、文件上传下载、SSE 实时流和图表。

这个方向和 nahida-bot 的目标一致：主 WebUI 不理解 RSS/MCP/GitHub 的业务模型，只负责加载、隔离、鉴权和代理。

---

## 3. 设计目标

1. 插件独立性：插件页面随插件目录一起发布，不进入主 WebUI bundle。
2. 核心无业务特例：主 WebUI 只实现插件页面 host，不实现 RSS/MCP/GitHub 特定组件。
3. 安全隔离：插件 JS/CSS 不能污染主页面，不能直接读取 WebUI cookie、localStorage 或父页面 DOM。
4. 统一鉴权：插件 Page 的所有后端操作走 Gateway/WebUI 已有认证体系。
5. 插件作用域隔离：插件 Admin API 自动限定到当前 plugin_id，插件不能注册或访问其他插件的 admin route。
6. 向后兼容：没有 Web 面板的插件继续只用 config_schema 和命令。
7. 渐进实现：先做 Admin API，再做 iframe Page；SSE、文件上传、i18n/theme 同步可后续追加。

---

## 4. 非目标

- 本设计不要求近期实现；当前仅作为后续 issue 的设计依据。
- 不为 RSS notifier 单独新增核心路由，例如 /api/rss。
- 不把插件前端代码 import 到 webui/src 主 bundle。
- 不允许插件直接依赖 FastAPI/Starlette 请求对象作为公开 SDK。
- 不把插件页面设计成任意服务器文件浏览器或任意反向代理。

---

## 5. 分层方案

### 5.1 第一层：schema-driven 配置

简单配置继续使用 plugin.yaml 中的 config_schema。

适合：

- 布尔开关。
- interval、timeout、quota 等数值。
- 少量静态 feed/server/target 配置。

不适合：

- 动态订阅列表。
- 运行态状态。
- 日志/事件流。
- 文件导入导出。
- 需要多步交互的复杂表单。

### 5.2 第二层：插件 Admin API

新增插件专属的 authenticated admin API surface：

    /api/plugins/{plugin_id}/extensions/{path...}

插件通过 SDK 注册：

    self.api.register_admin_endpoint(
        "subscriptions",
        self._api_list_subscriptions,
        methods=("GET",),
    )

    self.api.register_admin_endpoint(
        "subscriptions",
        self._api_add_subscription,
        methods=("POST",),
    )

建议 SDK 类型：

    @dataclass(slots=True, frozen=True)
    class PluginAdminRequest:
        method: str
        path: str
        headers: dict[str, str]
        query: dict[str, str | list[str]]
        body: bytes
        user_id: str = ""
        username: str = ""

    @dataclass(slots=True, frozen=True)
    class PluginAdminResponse:
        status_code: int = 200
        body: bytes | str | dict[str, Any] | list[Any] = b""
        headers: dict[str, str] = field(default_factory=dict)

与现有 register_webhook_endpoint 的区别：

| 能力 | webhook endpoint | admin endpoint |
|---|---|---|
| 用途 | 外部系统回调 | WebUI 管理操作 |
| 认证 | 通常插件自己校验 secret | 走 WebUI/Gateway 登录态 |
| 路径 | /webhooks/{path} | /api/plugins/{plugin_id}/extensions/{path} |
| 暴露对象 | raw webhook request | SDK admin request/response |
| 安全语义 | 面向公网/反代入口 | 面向管理员控制面 |

### 5.3 第三层：插件 Page

插件可在目录中携带静态页面：

    plugins/rss-notifier/
    ├── plugin.yaml
    ├── nahida_plugin_rss_notifier/
    └── pages/
        └── dashboard/
            ├── index.html
            ├── app.js
            └── style.css

主 WebUI 的插件详情页读取插件 page manifest，展示“打开 Dashboard”等入口。点击后通过 iframe 加载：

    /api/plugins/{plugin_id}/pages/{page_name}/index.html

Page 内部脚本不直接调用 Gateway API，而是通过注入的 bridge：

    const bridge = window.NahidaPluginPage
    const ctx = await bridge.ready()

    const subscriptions = await bridge.apiGet("subscriptions")
    await bridge.apiPost("subscriptions", { url, targets })
    await bridge.apiPost("poll")

bridge 把相对 endpoint 转发到：

    /api/plugins/{plugin_id}/extensions/{endpoint}

---

## 6. Manifest 扩展草案

可以新增顶层 web 字段，也可以扩展 capabilities。推荐单独新增 web，因为它包含前端资源和 admin API 声明，不只是“能力摘要”。

    web:
      pages:
        - name: dashboard
          title: "RSS Subscriptions"
          description: "Manage RSS/Atom subscriptions and polling state"
          entry: "pages/dashboard/index.html"
      admin_api:
        enabled: true

约束：

- name 只能包含字母、数字、连字符和下划线。
- entry 必须位于插件目录内，不能包含路径逃逸。
- 插件禁用后，对应 page 和 admin endpoint 都应不可用。
- 插件 reload 后 page manifest 和 admin route 重新注册。

---

## 7. Bridge API 草案

最小版本：

    interface NahidaPluginPageBridge {
      ready(): Promise<PluginPageContext>
      getContext(): PluginPageContext | null
      apiGet(endpoint: string, params?: Record<string, unknown>): Promise<unknown>
      apiPost(endpoint: string, body?: unknown): Promise<unknown>
      apiDelete(endpoint: string, body?: unknown): Promise<unknown>
    }

    interface PluginPageContext {
      pluginId: string
      pluginName: string
      pageName: string
      pageTitle: string
      theme: "light" | "dark"
      locale: string
    }

后续可扩展：

- upload(endpoint, file)
- download(endpoint, params, filename)
- subscribeSSE(endpoint, handlers, params)
- onContext(handler)：主题/语言变化。
- t(key, fallback)：插件 i18n。

---

## 8. 安全模型

### 8.1 iframe sandbox

插件 Page 应运行在 sandboxed iframe 中：

    <iframe
      sandbox="allow-scripts allow-forms allow-downloads"
      src="/api/plugins/rss-notifier/pages/dashboard/index.html"
    ></iframe>

不启用 allow-same-origin 时，插件页面不能直接读主站 cookie/localStorage，也不能绕过 bridge 调 authenticated API。所有管理请求必须经父页面 bridge 转发。

### 8.2 静态资源服务

服务插件静态资源时必须：

- 解析真实路径后确认仍在插件 page 根目录内。
- 禁止路径逃逸、空片段、绝对路径和 URL scheme。
- 设置安全响应头，例如 CSP、X-Content-Type-Options、Cache-Control。
- 只允许常见静态类型：html、js、css、png、svg、json、woff 等。

### 8.3 Admin API

Admin API 必须：

- 复用 WebUI 登录态或 Bearer token 鉴权。
- 自动绑定当前 plugin_id，不允许跨插件调用。
- 有统一请求体大小限制。
- 有统一超时。
- 插件 handler 必须验证自己的业务输入。
- 插件需要在 manifest 中声明 web.admin_api.enabled 或等价权限。

---

## 9. RSS notifier 的应用方式

RSS 插件未来可以提供 pages/dashboard/index.html，页面功能包括：

- 展示当前所有动态订阅和静态订阅。
- 按 chat address 过滤订阅。
- 添加/删除动态订阅。
- 手动触发某个 feed 或全部 feed 的轮询。
- 展示每个 feed 的最近成功时间、最近错误、已知 item 数量。
- 导入/导出订阅 JSON。

对应 Admin API：

    GET    subscriptions
    POST   subscriptions
    DELETE subscriptions/{feed_key}
    POST   poll
    POST   poll/{feed_key}
    GET    status
    GET    errors

RSS 插件仍然保持外部插件身份。主 WebUI 只知道“该插件有一个 dashboard page”，不知道 RSS 业务结构。

---

## 10. 实施阶段

### Phase 0：文档与 issue

- [x] 记录设计。
- [x] 创建 GitHub issue 跟踪：[AI1379/nahida-bot#23](https://github.com/AI1379/nahida-bot/issues/23)。

### Phase 1：插件 Admin API

- [ ] SDK 增加 PluginAdminRequest / PluginAdminResponse。
- [ ] SDK 增加 BotAPI.register_admin_endpoint。
- [ ] Runtime 增加 admin endpoint registry。
- [ ] Gateway 增加 /api/plugins/{plugin_id}/extensions/{path} 路由。
- [ ] 测试认证、path scope、disable/unload 清理。

### Phase 2：插件 Page 静态资源

- [ ] Manifest 支持 web.pages。
- [ ] PluginManager 暴露 page summary。
- [ ] Gateway 提供 page 静态资源服务。
- [ ] WebUI 插件详情页展示 page 入口。
- [ ] iframe sandbox 加载插件 page。

### Phase 3：Bridge MVP

- [ ] 注入 window.NahidaPluginPage bridge SDK。
- [ ] 支持 ready、apiGet、apiPost、apiDelete。
- [ ] 校验 endpoint，禁止绝对 URL、路径逃逸、query/hash 拼接。
- [ ] 测试 iframe 与父页面通信。

### Phase 4：高级能力

- [ ] 上传/下载。
- [ ] SSE。
- [ ] 主题同步。
- [ ] i18n。
- [ ] 插件 page 开发文档与模板。

---

## 11. 开放问题

1. Page manifest 应放顶层 web，还是放 capabilities.web_pages？
2. iframe 是否完全不加 allow-same-origin？这更安全，但本地开发调试会更麻烦。
3. 插件静态资源是否允许 Vite 构建产物，还是只支持手写 HTML/JS/CSS？
4. Admin API 是否需要单独权限维度，例如 permissions.web_admin？
5. 插件市场/第三方插件是否需要标记“包含 Web 页面”，并在安装前额外提示？
6. 是否需要为 Page 提供 SDK npm 包，还是由 host 自动注入单文件 bridge？

---

## 12. 结论

插件 Web 面板应该作为通用插件扩展协议实现，而不是作为 RSS notifier 或其他插件的主线特例。

主线负责：

- 发现插件页面。
- 隔离加载页面。
- 提供 authenticated bridge。
- 路由到插件注册的 Admin API。
- 处理生命周期清理与安全边界。

插件负责：

- 自己的页面文件。
- 自己的业务 API。
- 自己的数据校验。
- 自己的状态展示与交互。

这保留了插件独立性，同时允许 RSS notifier 这类插件在未来拥有完整 Web 管理体验。
