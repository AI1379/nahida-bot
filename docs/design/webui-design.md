# WebUI 设计

> 状态：✅ 已实现（主体落地）。本文档保留为历史设计参考；当前实际页面以 `webui/src/features/` 为准（首页状态、配置、CRON、Session、文件、知识库、插件、Skills、用量、进程、日志、身份、记忆、关于、登录）。
> 日期：2026-05-25
> 最近更新：2026-08-10
> 目标：为 nahida-bot 增加一个面向本地/私有部署的可视化运维 WebUI，覆盖系统状态、配置管理、CRON、Session、Workspace 文件和未来扩展页面。
> 相关文档：
>
> - [ROADMAP.md](../ROADMAP.md)
> - [cron-and-webapi-optimization.md](cron-and-webapi-optimization.md)
> - [plugin-web-panels.md](plugin-web-panels.md)
> - [plugin-system.md](../architecture/plugin-system.md)
> - [runtime-flows.md](../architecture/runtime-flows.md)

---

## 1. 结论

WebUI 推荐走 **Vue 3 + TypeScript + Vite SPA**，由现有 FastAPI Gateway 挂载静态资源并统一暴露 REST API。后端继续以 FastAPI 为唯一控制面，WebUI 不直接读 SQLite、配置文件或 workspace 文件；所有操作都走公开 API。

首版不做复杂聊天网页、不做 SSR、不做独立前端后端部署平台。核心目标是把运维入口做稳：

1. 首页展示系统健康、资源占用、运行时间、服务状态、token/费用统计和重启等危险操作。
2. 配置页读取、展示、验证、编辑 `config.yaml`；保存前自动验证，保存时自动备份旧文件；配置改动只标记“需要重启”，不热加载。
3. CRON 页展示全部定时任务，支持过滤、查看详情和后续增删改。
4. Session 页按 `ChatAddress` 聚合展示所有 session，先做只读历史和元数据，不把网页对话作为 MVP 必需能力。
5. 文件管理页只管理受控 workspace/root 下的 Markdown 等文本文件，不能变成任意服务器文件浏览器。
6. 前端页面、后端 API 和权限模型都预留扩展点，后续权限管理、多设备远控、gateway + node 架构可以直接接入。

关键取舍：

- **Vue 优先于 React**：本项目 WebUI 更像运维面板，不需要 React 全栈框架；Vue SFC 对中小型内部工具更直观，状态和表单代码更少。
- **Vite 优先于 Nuxt/Next**：WebUI 是静态 SPA，由 Gateway 服务即可；SSR/SSG 目前增加复杂度但收益很低。
- **REST 先行，SSE/WebSocket 后置**：现有 WebAPI 已是 REST；MVP 用轮询足够，实时日志、节点心跳、远控会话再加事件通道。
- **配置逻辑必须复用 CLI**：`nahida_bot.cli.config_commands` 里的 schema / validate / plugin config discovery 应抽到 service 层，CLI 和 WebUI 共享。

---

## 2. 当前基础

### 2.1 已有后端能力

现有 Gateway：

| 端点 | 状态 | 说明 |
|---|---|---|
| `GET /api/health` | 已有 | 仅返回 `status/app_name/started` |
| `GET /api/sessions` | 已有 | 列出 session summary，支持 `limit` |
| `GET /api/sessions/{session_id}` | 已有 | 获取最近 turns |
| `POST /api/send` | 已有 | 按 typed target 发送文本 |
| `GET /api/cron` | 已有 | 目前要求 target 或 platform/chat_id，适合聊天内查询，不适合全局管理页 |
| `POST /api/cron` | 已有 | 创建任务 |
| `GET/PATCH/DELETE /api/cron/{job_id}` | 已有 | 查看、更新、删除任务 |
| `POST /api/cron/{job_id}/cancel` | 已有 | 取消任务 |

现有配置能力：

- `Settings` 使用 Pydantic v2，`extra="allow"` 支持顶层插件配置。
- `config schema` 已能展开标准配置、provider 配置和插件 `config_schema`。
- `config validate` 已能检查 provider、model spec、memory、scheduler 和常见 channel 配置。
- 插件配置注入由 `Application._get_plugin_configs()` 和 `_inject_plugin_configs()` 完成。

现有 workspace 能力：

- `WorkspaceManager` 管理 `workspace_base_dir/workspaces/<id>`。
- `WorkspaceSandbox` 已提供相对路径解析和逃逸防护。
- 默认 workspace 内有 `AGENTS.md`、`SOUL.md`、`USER.md`、`MEMORY.md` 和 skills。

### 2.2 主要缺口

WebUI 所需但当前缺失：

- 系统级状态聚合：CPU、内存、磁盘、DB 文件大小、workspace 大小、uptime、pid、版本、服务状态。
- token / 费用总览：Provider 已解析 `TokenUsage`，但 `AgentLoop` 目前没有应用级 usage ledger，也没有持久化成本统计。
- 配置读写 API：需要读取原始 YAML、结构化视图、保存、备份、校验和脱敏。
- 全局 CRON 列表：当前 `GET /api/cron` 偏 chat-scoped，需要 admin list-all。
- Session 聚合：需要按 `ChatAddress`、main/cron named/isolated、workspace、runtime metadata 分组。
- 文件 API：需要受控目录浏览、读写、重命名、删除、预览。
- 前端静态资源挂载：Gateway 还没有 `StaticFiles` 或 SPA fallback。
- 权限粒度：当前只有全局 token；后续需要 admin/read/write/node 等 scope。
- 实时事件：后续需要 metrics streaming、日志尾随、节点心跳、任务事件。

---

## 3. 技术选型

### 3.1 前端框架评估

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| Vue 3 + Vite | SFC 易读；表单、列表、状态面板代码量低；官方推荐 Vite；生态成熟 | 团队若更熟 React 会有切换成本 | **推荐** |
| React + Vite | 生态最大；和很多 dashboard/ui 库兼容 | 官方新项目更偏推荐 framework；内部 SPA 需要自己拼表单/路由/状态约定 | 可作为备选 |
| Svelte 5 | 编译后轻，语法简洁 | 团队熟悉度和长期维护生态不如 Vue/React 稳 | 不作为首选 |
| Solid | 性能好，细粒度响应式 | 社区和 dashboard 生态较小 | 不作为首选 |
| Lit | 很轻，适合 Web Components；OpenClaw Control UI 使用 Vite + Lit | 复杂表单和应用状态会更手写 | 适合插件 surface，不适合主 WebUI |
| Nuxt / Next | 全栈能力强 | 本项目不需要 SSR；还会引入第二个服务模型 | 不采用 |

推荐前端栈：

| 类别 | 选择 | 理由 |
|---|---|---|
| Build | Vite | 开发快，产物静态，便于 FastAPI 挂载 |
| Framework | Vue 3 + TypeScript | 运维后台的表单和状态面板更省代码 |
| Routing | Vue Router | 页面级路由清晰，后续 extension registry 易接入 |
| Server state | TanStack Query for Vue | 轮询、缓存、mutation invalidation、错误状态统一 |
| Local state | Pinia | 仅存 UI 偏好、当前认证状态、侧边栏状态；session 凭证由 `HttpOnly` cookie 持有 |
| UI components | shadcn-vue + Reka UI | shadcn-vue 提供可修改的组件代码和默认审美，Reka UI 提供无样式、可访问 primitive；适合做有 nahida-bot 自己味道的 UI |
| Icons | lucide-vue-next | tree-shakable，和工具按钮/导航适配 |
| Tables | TanStack Table for Vue | CRON/session 表格会需要排序、过滤、分页；表格逻辑不自己写 |
| Forms | VeeValidate + Zod（或同级 typed schema） | 表单状态、校验、错误提示交给成熟库，避免在配置页手写复杂逻辑 |
| Editor | CodeMirror 6 | YAML/Markdown 编辑比 Monaco 轻，适合配置页和文件页 |
| Charts | 原生 SVG/sparkline 起步，必要时引入 uPlot | 首页只需要轻量趋势图，先避免 ECharts 级别依赖 |
| Styling | Vue SFC scoped CSS + CSS variables | 控制依赖；保证内部运维 UI 风格一致 |

UI 库取舍：

- 默认采用 **Reka UI + shadcn-vue**。原因是 WebUI 不是一次性后台模板，而会长期承载配置、远控、插件 surface 和个人助手控制面；从一开始拥有组件代码和设计 token，更容易形成自己的风格。
- 不把 Naive UI 作为默认方案。Naive UI 适合快速后台，但如果配置页、CRON 表、Session 页都深度使用 `n-form`、`n-data-table`、`n-modal` 等 API，后续迁移到 headless / shadcn 路线会重写大量组件结构。
- 不混用 Naive UI 和 Reka UI。两套主题、弹层、表单、交互状态会让一致性变差。
- 复杂逻辑不手写：表格用 TanStack Table，服务端状态用 TanStack Query，编辑器用 CodeMirror，表单校验用 VeeValidate/Zod。Reka/shadcn 只负责交互 primitive 和视觉组件。

### 3.2 后端技术

保留 FastAPI + Uvicorn，不引入新的 Python Web 框架。

新增依赖建议：

| 依赖 | 用途 | 是否必须 |
|---|---|---|
| `psutil` | 跨平台资源占用、进程内存、磁盘统计 | 建议加入 runtime deps |
| `aiofiles` | 已有 | 文件 API 可复用 |
| `pyyaml` | 已通过 `yaml` 使用 | 配置读写继续复用 |

后端原则：

- routes 只做 HTTP 参数、鉴权和 schema 转换。
- 业务逻辑放到 `nahida_bot/gateway/services/` 或更通用的 core service。
- 配置 schema/validate 从 CLI 模块抽出，变成 CLI 和 WebUI 共用 service。
- 所有危险 mutation 写 audit log。
- 所有文件操作走 `WorkspaceSandbox` 或显式配置的安全 root。

---

## 4. 目录结构

推荐新增：

```text
webui/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.ts
    app.vue
    router.ts
    api/
      client.ts
      schemas.ts
      queries.ts
    shell/
      AppShell.vue
      NavRail.vue
      TopBar.vue
      DangerActionDialog.vue
    features/
      home/
      config/
      cron/
      sessions/
      files/
      extensions/
    components/
      ui/
      data/
      editor/
    styles/
      tokens.css
      base.css
```

Python 侧新增：

```text
nahida_bot/gateway/
  routes/
    status.py
    config.py
    files.py
    webui.py
    events.py              # Phase 3
  services/
    status_service.py
    config_service.py
    file_service.py
    usage_ledger.py
    audit_log.py
  static/
    webui/                 # release build output，可由构建脚本生成
```

构建策略：

- 开发：`webui` 使用 Vite dev server，代理 `/api` 到 `http://127.0.0.1:6185`。
- 生产/本地运行：`pnpm build` 输出到 `nahida_bot/gateway/static/webui` 或 `webui/dist`，FastAPI 挂载 `/`。
- `GET /*` 做 SPA fallback 到 `index.html`；`/api/*` 永远由 API routes 处理。

配置建议：

```python
class WebUIConfigModel(BaseModel):
    enabled: bool = True
    base_path: str = "/"
    static_dir: str = ""
    require_auth: bool = True
```

可以挂到 `Settings.webapi` 下，也可以独立 `Settings.webui`。更推荐独立 `webui`，避免把 API 开关和 UI 开关混在一起。

认证配置建议：

```python
class WebUIAuthConfigModel(BaseModel):
    enabled: bool = True
    admin_password_hash: str = ""
    trusted_chat_address: str = ""
    require_chat_otp: Literal["never", "http_emergency", "always"] = "http_emergency"
    allow_http_emergency: bool = False
    session_ttl_seconds: int = 3600
    emergency_session_ttl_seconds: int = 1800
    otp_ttl_seconds: int = 300
    otp_cooldown_seconds: int = 60
    login_rate_per_minute: int = 5
```

浏览器 WebUI 使用 session cookie；`webapi.auth_token` 继续保留为脚本/API token，不与管理员登录密码混用。

---

## 5. 页面设计

### 5.1 首页：Overview

目标：打开后 5 秒内知道系统是否健康，以及哪些操作需要立即处理。

信息区：

- App：名称、版本、debug、启动时间、运行时长、pid、Python 版本。
- 服务：Gateway、router、scheduler、memory store、workspace、providers、channels、plugins。
- 资源：CPU、RSS 内存、内存百分比、磁盘剩余、DB 大小、workspace 大小、media cache 大小。
- Token：总 input/output/cached/reasoning tokens；按 provider/model/session/source_tag 聚合；费用未知时显示 token，不伪造金额。
- CRON：活跃任务数、运行中任务数、失败任务数、下一个触发时间。
- Session：总 session 数、最近活跃 session、今日 turns。
- 插件：已加载、已启用、失败插件。

快捷操作：

- 重启服务。
- 停止服务。
- 打开配置页并定位到需要重启的变更。
- 打开最新错误日志。
- 手动触发 health refresh。

危险操作规则：

- `restart` / `shutdown` 必须二次确认。
- 后端只做“请求重启”。如果没有外部 supervisor，返回 `accepted=true, mode="shutdown_only"`，进程退出后由用户或服务管理器拉起。
- UI 必须显示“改配置后需要重启”的 pending state，不能暗示热生效。

### 5.2 配置页：Config

目标：把 `config.yaml` 变成可理解、可验证、可安全保存的配置界面。

#### 5.2.1 图形化配置页重构

当前配置页已经具备读取、校验、保存 YAML 的基础能力，但主要交互仍然是扁平表格和整段 YAML 编辑，不适合日常维护。下一阶段配置页改为“图形化分区表单 + 高级 YAML 兜底”的操作台。

页面结构：

```text
顶部保存栏：配置路径 / checksum / 校验状态 / 未保存变更 / 保存 / 丢弃 / 查看 YAML
左侧分区：General / Providers / Channels / Agent / Router / Context / Memory / Multimodal / Scheduler / Plugins
中间表单：当前分区的字段控件
右侧状态：validation issues / 改动摘要 / restart required 提示
```

字段控件规则：

- `bool` 使用开关。
- `Literal` / enum 使用下拉或分段控件。
- `int` / `float` 使用数字输入，并显示后端 schema 约束。
- 长文本如 `system_prompt` 使用 textarea。
- `list[str]` 使用 chip/tag 编辑器或多行列表编辑器。
- `api_key`、`token`、`secret`、`password`、`private_key` 等敏感字段使用 SecretInput：默认“保持当前值”，只有用户显式输入新值或清空时才写回。
- `model spec` 优先从 `providers.*.models` 和模型 `tags` 中选择，也允许自由输入。
- 未知插件配置优先按插件 `config_schema` 自动生成；没有 schema 时退化为对象/YAML 编辑器。

Provider 区作为第一优先级：

- 以 provider card 管理 `providers.<id>`。
- 支持新增、删除 provider。
- 支持设置 `type`、`base_url`、`api_key`、`stream_responses`、`merge_system_messages`。
- 支持编辑 models 列表；对象形式模型用高级编辑或 JSON 行兜底。
- 支持设置 `default_provider`。
- 后续再补 provider 连接测试和模型能力专用编辑器。

保存模型：

- 图形化表单不提交整份脱敏 YAML，而是提交 path-level patch。
- 后端用 checksum 防止覆盖外部修改。
- 后端读取未脱敏原文件，应用变更，未修改的敏感字段保持原值。
- 保存前统一走 `Settings` + `validate_settings()` 校验。
- 保存时自动备份，返回 `backup_path`、新 checksum 和 `restart_required=true`。

需要新增 API：

```text
GET   /api/config/document?redact=true
PATCH /api/config/current
```

`GET /api/config/document` 返回结构化配置树、脱敏后的配置树、脱敏路径、schema、checksum、mtime 和配置文件路径。

`PATCH /api/config/current` 接收：

```json
{
  "expected_checksum": "sha256:...",
  "changes": [
    {"path": "debug", "value": true},
    {"path": "providers.default.base_url", "value": "https://example.com"},
    {"path": "providers.default.api_key", "secret_action": "keep"}
  ]
}
```

第一阶段范围：

- 新增结构化 config document API。
- 新增 path-level patch 保存 API。
- 前端配置页改为分区表单。
- 优先覆盖 General、Providers、Multimodal、Agent、Router、Context、Memory、Scheduler。
- YAML 视图保留为高级预览/兜底，不再作为默认编辑入口。
- 当前实现可以先使用 PyYAML 完成结构化 patch；若要求保存后完整保留注释、空行和手写排版，应引入 `ruamel.yaml` round-trip 写入。

视图：

1. 分组表单视图：
   - Application / Server / Database / Workspace
   - Providers
   - Multimodal
   - Agent / Context / Scheduler / Router / Memory
   - WebAPI / WebUI
   - Plugin configs
2. YAML 原文视图：
   - CodeMirror YAML editor。
   - 展示 checksum、mtime、文件路径。
   - 支持 diff 当前编辑内容和磁盘内容。
3. Validation panel：
   - 复用 CLI validate 规则。
   - error 阻止保存，warning 允许保存但醒目标注。
4. Plugin schema panel：
   - 对 manifest 中 `config_schema` 有定义的插件生成表单。
   - 没 schema 的插件只提供 YAML object editor。

保存流程：

1. UI 读取 `GET /api/config/current`，保存 `checksum`。
2. 用户编辑。
3. UI 调 `POST /api/config/validate`。
4. 用户点击保存。
5. 后端重新校验 `expected_checksum`，避免覆盖外部修改。
6. 后端把旧文件复制到备份目录。
7. 后端原子写入新配置。
8. 返回 `backup_path`、新 checksum、validation report、`restart_required=true`。
9. UI 显示“已保存，重启后生效”。

备份目录：

```text
data/config_backups/
  config.yaml.20260525-153012.bak
```

脱敏规则：

- 脱敏是 WebUI 的防泄漏 guardrail，不作为完整安全边界。
- 配置 schema 支持 `sensitive: true` 元数据；配置 UI/API 优先按源头标记脱敏，例如 `admin_password_hash` 和 provider/channel credential。
- 对日志等没有 schema 的结构化数据使用低误伤 fallback：只对规范化后的敏感字段名做精确匹配，例如 `api_key`、`access_token`、`refresh_token`、`auth_token`、`api_token`、`bot_token`、`secret`、`client_secret`、`password`、`password_hash`、`private_key`、`authorization`、`cookie`。
- 保留正常调试字段，例如 `input_tokens`、`output_tokens`、`cached_tokens`、`reasoning_tokens`、`token_usage`、`session_key`。
- 值模式只覆盖少量高置信格式，例如 PEM private key、`Bearer ...`、`sk-...`、GitHub token、Telegram bot token。
- 不做宽泛 substring 规则，例如所有包含 `token` 或 `key` 的字段都脱敏；误脱敏会降低配置排错效率。
- 原始 YAML 中 `${ENV_VAR}` 不展开显示为真实值。
- 保存时不把脱敏值写回文件；表单视图如果编辑秘密字段，必须明确输入新值。

CLI 复用重构：

```text
nahida_bot/core/config_schema.py
  build_config_schema(...)
  discover_plugin_config_schema(...)

nahida_bot/core/config_validation.py
  validate_settings(...)
  ValidationReport

nahida_bot/gateway/services/config_service.py
  read_current_config(...)
  validate_config_text(...)
  save_config_text_with_backup(...)
```

`nahida_bot.cli.config_commands` 只负责 Typer 参数和 Rich 输出。

### 5.3 CRON 页

目标：全局管理当前所有 CRON 任务，而不是只能按某个 chat 查询。

表格字段：

- job id
- channel / chat type / chat id / session key
- mode：once / interval / cron
- session mode：main / isolated / named
- prompt 摘要
- active / claimed / failed 状态
- next fire at
- last fired at
- run count / max runs
- failure count / last error
- workspace id
- created at

交互：

- 全局列表、按 channel/chat/session mode/status 过滤。
- 查看详情。
- 创建任务。
- 更新 prompt/time/mode/max_runs。
- cancel / delete。
- 对失败任务展示错误和下一次 retry。

后端需要新增：

```text
GET /api/cron/jobs?active=true|false|all&limit=100&cursor=...
```

现有 `GET /api/cron?target=...` 保留，作为 chat-scoped 查询。

`CronJobResponse` 需要补字段：

- `session_key`
- `chat_type`
- `last_fired_at`
- `failure_count`
- `last_error`
- `claimed_at`
- `workspace_id`
- `fire_at`
- `interval_seconds`
- `cron_expression`
- `max_runs`

### 5.4 Session 页

目标：按真实聊天地址理解 session，而不是只看一串 session_id。

首版不做完整网页对话。理由：

- 当前 bot 的主入口仍是 Telegram/Milky 等 channel。
- Web chat 需要处理消息来源、附件、流式响应、权限和 channel 语义，容易扩大范围。
- 当前更需要的是运维可观测性：看 session 分组、历史、metadata、关联 cron、token/费用。

分组模型：

```text
ChatAddress group
  channel: milky
  chat_type: group
  target_id: 123456
  main_session: milky:group:123456
  derived_sessions:
    - milky:group:123456:cron:daily
    - milky:group:123456:cron:<job_id>
```

页面能力：

- 左侧按 channel/chat_type 分组。
- 中间 session 列表，展示 last active、turn count、workspace、runtime metadata。
- 右侧 history viewer，支持 role/source 过滤、折叠长内容、复制 turn。
- 显示 active session override。
- 显示关联 CRON jobs。
- 可选 quick send：仅调用 `POST /api/send` 发一条纯文本到 typed target，默认放在二级操作中，不作为聊天主界面。

后端需要增强：

```text
GET /api/sessions?limit=100&cursor=...&group_by=chat_address
GET /api/sessions/{session_id}?limit=100&cursor=...&include_metadata=true
GET /api/sessions/groups
GET /api/sessions/{session_id}/runtime
```

历史接口要分页。现有 `limit=200` 对 WebUI 初期可用，但后续长 session 必须 cursor/page。

### 5.5 文件管理页

目标：管理 bot workspace 中的基础 Markdown / YAML / JSON / TXT 文件，不开放任意服务器文件系统。

默认 root：

```text
settings.workspace_base_dir/workspaces/<active_workspace>
```

允许文件类型首版建议：

- `.md`
- `.txt`
- `.yaml`
- `.yml`
- `.json`

页面能力：

- workspace 选择。
- 文件树。
- 新建文件/目录。
- 读取、编辑、保存。
- 重命名。
- 删除到回收区或软删除目录，首版不做硬删除。
- Markdown preview。
- YAML/JSON 格式化和基本语法错误提示。

后端 API：

```text
GET    /api/workspaces
GET    /api/workspaces/active
POST   /api/workspaces/active

GET    /api/files?workspace_id=default&path=.
GET    /api/files/content?workspace_id=default&path=MEMORY.md
PUT    /api/files/content
POST   /api/files/create
POST   /api/files/rename
POST   /api/files/delete
```

安全规则：

- 所有 path 必须是相对路径。
- 所有 path 走 `WorkspaceSandbox.resolve_safe_path()`。
- 默认不允许符号链接逃逸；如支持 symlink，必须 resolve 后再次 `relative_to(root)`。
- 单文件大小设上限，比如 1 MiB；超过只读预览或拒绝。
- Markdown preview 必须 sanitize HTML。
- 删除操作写 audit log。

如果未来确实需要管理项目工作目录而不是 workspace，应新增显式配置：

```yaml
webui:
  file_roots:
    - id: project-docs
      path: "./docs"
      writable: true
      extensions: [".md"]
```

不要把 `cwd` 隐式暴露给 WebUI。

---

## 6. API 设计

### 6.1 Bootstrap

```text
GET /api/webui/bootstrap
```

返回：

```json
{
  "app_name": "Nahida Bot",
  "version": "0.1.0",
  "api_base": "/api",
  "webui_base": "/",
  "auth": {
    "required": true,
    "mode": "session",
    "deployment_mode": "loopback",
    "http_emergency_enabled": false,
    "otp_available": false,
    "api_token_supported": true
  },
  "features": [
    {"id": "home", "route": "/", "label": "Overview", "scope": "operator.read"},
    {"id": "config", "route": "/config", "label": "Config", "scope": "operator.admin"}
  ],
  "server_time": "2026-05-25T00:00:00+08:00"
}
```

Bootstrap 不返回 secrets。未认证时最多返回登录所需信息。

### 6.2 Status

```text
GET /api/status
```

返回聚合视图：

```json
{
  "app": {
    "name": "Nahida Bot",
    "version": "0.1.0",
    "debug": false,
    "started": true,
    "started_at": "2026-05-25T12:00:00+08:00",
    "uptime_seconds": 12345,
    "pid": 1234
  },
  "resources": {
    "cpu_percent": 2.5,
    "memory_rss_bytes": 268435456,
    "memory_percent": 1.3,
    "disk_free_bytes": 1234567890,
    "db_size_bytes": 10485760,
    "workspace_size_bytes": 2097152
  },
  "services": {
    "router": "running",
    "scheduler": "running",
    "webapi": "running",
    "memory": "running",
    "workspace": "running"
  },
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cached_tokens": 0,
    "reasoning_tokens": 0,
    "estimated_cost": null,
    "currency": null
  }
}
```

需要新增 `Application.started_at`。资源统计优先用 `psutil`，不可用时返回 partial data 和 `capabilities.resources=false`。

### 6.3 System actions

```text
POST /api/system/actions/restart
POST /api/system/actions/shutdown
```

请求：

```json
{
  "confirm": true,
  "reason": "config_saved"
}
```

响应：

```json
{
  "accepted": true,
  "action": "restart",
  "mode": "supervisor_required",
  "message": "Shutdown requested; external supervisor must restart the process."
}
```

实现策略：

- 当前进程只能可靠 shutdown，不能保证自我重启。
- 如果以后有 service manager / node supervisor，可接入真正 restart。
- 所有 system actions 必须 admin scope。

### 6.4 Config

```text
GET  /api/config/current?redact=true
GET  /api/config/schema?include_plugins=true
POST /api/config/validate
PUT  /api/config/current
GET  /api/config/backups
POST /api/config/backups/{backup_id}/restore
```

保存请求：

```json
{
  "content": "app_name: Nahida Bot\n...",
  "expected_checksum": "sha256:...",
  "format": "yaml"
}
```

保存响应：

```json
{
  "saved": true,
  "backup_path": "data/config_backups/config.yaml.20260525-153012.bak",
  "checksum": "sha256:...",
  "restart_required": true,
  "validation": {
    "errors": 0,
    "warnings": 1,
    "issues": []
  }
}
```

### 6.5 Usage ledger

当前 ProviderResponse 已有 `TokenUsage`，但缺少应用级记录。建议新增：

```text
nahida_bot/gateway/services/usage_ledger.py
```

记录字段：

- timestamp
- trace_id
- session_id
- source_tag
- provider_id
- model
- input_tokens
- output_tokens
- cached_tokens
- reasoning_tokens
- cache_creation_tokens
- estimated_cost

SQLite 表：

```sql
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    source_tag TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL,
    currency TEXT
);
```

费用估算不要内置硬编码价格。使用可选配置：

```yaml
providers:
  default:
    pricing:
      currency: USD
      input_per_1m: 0.0
      output_per_1m: 0.0
      cached_input_per_1m: 0.0
```

未配置价格时 UI 显示 token 总量和“费用未配置”。

### 6.6 Events

Phase 3 增加：

```text
GET /api/events/stream   # SSE
```

事件类型：

- `status.updated`
- `usage.recorded`
- `cron.created`
- `cron.updated`
- `cron.fired`
- `session.updated`
- `config.saved`
- `file.updated`
- `node.heartbeat`

WebSocket 留给 gateway/node 远控协议，不急着和 UI metrics 混在一起。

---

## 7. 前端架构

### 7.1 Shell

主界面：

- 左侧固定导航。
- 顶部状态条：连接状态、API URL、auth 状态、pending restart。
- 主区域按 route 渲染。
- 所有 mutation 统一 toast + inline error。
- 危险操作统一 `DangerActionDialog`，要求输入确认或勾选确认。

视觉风格：

- 内部运维工具应安静、信息密度适中、可扫描。
- 避免营销式 hero、装饰性渐变和大块卡片堆叠。
- 页面区块用 full-width layout；卡片只用于独立指标、列表项、modal。
- 表格和编辑器是核心，不要为了装饰牺牲可读性。

### 7.2 API client

`webui/src/api/client.ts`：

- 统一 base URL。
- 浏览器优先使用 same-origin session cookie；`fetch`/query client 默认 `credentials: "same-origin"`。
- Bearer token 保留给脚本和旧 WebAPI 兼容，不作为 WebUI 浏览器会话的长期形态。
- JSON parse 和错误归一。
- 401 触发 auth state。
- mutation 后 invalidation 由 feature query module 控制。

错误模型：

```ts
export interface ApiError {
  status: number
  code?: string
  detail: string
  requestId?: string
}
```

### 7.3 Feature registry

前端内置页面使用 manifest：

```ts
export interface WebuiFeatureManifest {
  id: string
  label: string
  route: string
  icon: string
  requiredScope: string
  order: number
  component: () => Promise<Component>
}
```

`features/index.ts` 收集内置页面。后端 `bootstrap.features` 可以控制显示/隐藏和 scope。

插件页面 Phase 2 再做：

- 插件 manifest 声明 `webui_surfaces`。
- 后端暴露 feature metadata。
- UI 用 sandboxed iframe 或独立 static surface 加载插件页面。
- 插件页面不能直接拿全局 token；用 scoped token 或 postMessage RPC。

首版不要允许插件向主 Vue app 动态注入任意 JS。

### 7.4 Design system

首版采用“克制版设计系统”，先定义少量稳定组件，不追求一次覆盖所有控件。

基础 token：

- color：background、surface、muted、border、text、accent、danger、warning、success。
- spacing：4px 基准，页面密度偏运维后台，不做大面积留白。
- radius：默认 6px，工具按钮和表格控件保持紧凑。
- typography：正文、label、table cell、section heading、mono code 五类即可。
- elevation：少用阴影，主要靠 border 和 surface 区分层级。

首批 `components/ui`：

- `Button`
- `Input`
- `Textarea`
- `Select`
- `Switch`
- `Checkbox`
- `Dialog`
- `DropdownMenu`
- `Tabs`
- `Tooltip`
- `Badge`
- `Table`
- `Sidebar`
- `Toast`
- `Alert`
- `FormField`

页面级复杂组件单独放在 `components/data` 或 feature 内：

- CRON jobs table。
- Session grouped list。
- File tree。
- YAML/Markdown editor shell。
- Status metric panel。

规则：

- shadcn-vue 组件进入仓库后视为本项目代码，可以按 nahida-bot 风格修改。
- Reka UI 直接用于 dialog、dropdown、tabs、tooltip 等需要可访问性和键盘交互的底层 primitive。
- 业务页面不得直接散落复杂 Reka 组合；先封装到 `components/ui`，再在 feature 中使用。
- 表格排序、过滤、分页状态统一由 TanStack Table 管，不在 UI table 组件里重新发明。
- 表单校验 schema 和 API request schema 尽量共用类型定义，避免配置页表单和后端 schema 漂移。

---

## 8. 权限与安全

### 8.1 安全定位

WebUI 是自部署 bot 的本地/私有运维面板，不按公网 SaaS 或企业 IAM 的强度设计。安全目标是：

1. 默认状态不裸奔，非 loopback 暴露时必须有认证。
2. 把风险限制在用户能理解、能显式开启、能恢复的范围内。
3. 优先实现便宜但收益高的保护：强密码 hash、短会话、登录限速、chat OTP、日志脱敏、审计。
4. 不为了“纯 HTTP 下理论抗监听”重造 TLS，也不把前端 hash 密码宣传成抗监听方案。

需要明确承认：

- 没有 HTTPS/SSH/VPN 时，HTTP 请求、OTP 和 session 都可能被被动监听者看到。
- 主动 MITM 可以替换前端 JS 或代理完整登录流程。
- chat OTP 只能降低静态密码泄露、爆破和随机扫描风险，不能提供完整的 MITM 防护。

### 8.2 部署模式

按安全强度从高到低支持以下模式：

| 模式 | 入口 | 会话 | 定位 |
|---|---|---|---|
| `loopback` | WebUI 只监听 `127.0.0.1` / `::1`，用户通过本机或 SSH tunnel 访问 | 可用非 `Secure` cookie | 默认推荐；不需要域名和证书 |
| `private_network` | Tailscale/WireGuard/内网访问 | 有 HTTPS 时用 `Secure` cookie；否则按 HTTP 降级 | 适合多设备私有管理 |
| `https` | 可信证书、自签 CA、IP 证书或反代 TLS | `HttpOnly; Secure; SameSite=Strict` cookie | 公网推荐路径 |
| `http_emergency` | 公网 HTTP，显式开启 | 短会话 + chat OTP + 强 warning | 临时救急，不宣传为安全公网部署 |

启动规则：

- 监听非 loopback 且未配置认证时，启动应拒绝或至少强 warning；WebUI 默认不可进入。
- `http_emergency` 必须显式配置，例如 `webui.auth.allow_http_emergency=true`。
- 在 HTTP 模式下，UI 登录页和 bootstrap 都要显示当前是 weaker/emergency 模式。

### 8.3 当前 API 认证实现与缺口

当前已实现的是简单 shared API token，适合脚本和自动化继续保留：

- 后端读取 `webapi.auth_token`。
- 如果 token 为空，受保护 API 直接放行。
- 如果 token 非空，请求必须提供 `Authorization: Bearer <token>`，或用 `?token=<token>`。
- `GET /api/health` 和 `GET /api/webui/bootstrap` 不需要认证。
- 其它 WebUI/API router 当前统一挂 `require_token()`。

脚本推荐使用：

```bash
curl -H "Authorization: Bearer $NAHIDA_TOKEN" \
  http://127.0.0.1:6185/api/status
```

浏览器 WebUI 当前仍是临时 token bridge：

- `webui/src/api/client.ts` 从 Pinia auth store 读取 token，并发送 `Authorization: Bearer ...`。
- `webui/src/stores/auth.ts` 把 token 存在 `sessionStorage["nahida-bot:token"]`。
- 目前还没有正式 token 输入页或登录页；调试时需要手动写入 sessionStorage。
- SSE 因为原生 `EventSource` 不能设置 header，目前使用 `/api/events/stream?token=...`。

已知 gap：

- 如果后端没有配置 `webapi.auth_token`，REST API 会放行，但前端 SSE 当前因为本地 auth store 没有 token 而不会连接；应改为读取 bootstrap 的 `auth.required`。
- query token 容易出现在 URL、日志、浏览器历史或代理日志中；它只是当前 bearer/SSE 的临时兼容方案。
- 正式登录/session 方案落地前，应至少补一个临时 token 输入页，避免要求用户手动操作 DevTools。

长期策略：

- API token 继续保持 `Authorization: Bearer <token>`，用于脚本和外部自动化。
- 浏览器 WebUI 迁移到登录页 + 服务端 session cookie。
- SSE 在浏览器模式下改用 same-origin cookie；如仍需脚本订阅 SSE，可保留 `?token=` 或提供支持 header 的客户端说明。

### 8.4 认证实现路径

不要把当前 bearer token 输入框简单包装成登录页。推荐演进为单管理员密码 + 服务端 session：

1. 配置中保存 `admin_password_hash`，使用 Argon2id。
2. 提供 CLI 命令生成或修改密码 hash，避免在 WebUI 未认证状态下首次绑定管理员。
3. 新增认证端点：
   - `POST /api/auth/login`
   - `POST /api/auth/otp/request`
   - `POST /api/auth/otp/verify`
   - `POST /api/auth/logout`
   - `GET /api/auth/session`
4. 密码正确后，如果启用了 chat OTP，进入 pending login；否则直接签发 session。
5. session id 使用服务端随机值，服务端保存 session metadata，不把长期 secret 放到前端存储。
6. SSE 使用 same-origin cookie 鉴权，移除 URL query token。
7. 旧 `webapi.auth_token` 保留给脚本/API 调用，浏览器 WebUI 不再依赖 `sessionStorage` bearer token。

密码传输：

- 有 HTTPS/SSH/VPN 时，密码直接通过加密通道提交。
- HTTP emergency 模式下可以接受密码请求被监听的残余风险，因为其定位是临时救急；不要用前端 SHA256 当作安全承诺。
- 如果未来要做更强的 HTTP 密码协议，优先调研成熟 PAKE/OPAQUE 实现，但它仍不能保护后续 HTTP session 和前端 JS 完整性，因此不列入近期计划。

NapCat 可参考其登录 UI、登录限速、可选 SSL/Passkey 等产品形态；其浏览器端 hash 登录和 bearer credential 在 HTTP 链路中仍可被重放，因此不作为本项目抗监听设计基线。

### 8.5 Chat OTP

chat OTP 是 HTTP emergency 和高风险登录的第二因素，使用现有 channel 向可信 `ChatAddress` 发送一次性验证码。

配置建议：

```yaml
webui:
  auth:
    mode: "password"
    admin_password_hash: "$argon2id$..."
    trusted_chat_address: "milky:private:123456"
    require_chat_otp: "http_emergency"   # never / http_emergency / always
    otp_ttl_seconds: 300
    otp_cooldown_seconds: 60
    session_ttl_seconds: 1800
    allow_http_emergency: false
```

规则：

- `trusted_chat_address` 只能从本地配置或 CLI 设置，不能在未认证 WebUI 中首次绑定。
- OTP 只保存 hash，单次使用，短 TTL，验证后立即删除。
- pending login 绑定 client fingerprint 的弱标识，例如 IP、UA hash、创建时间；不把它当成强安全边界。
- OTP 请求、密码验证、OTP 验证都要分别限速。
- 发送给 chat 的消息只包含验证码、请求时间、来源 IP/UA 摘要和过期时间，不包含登录链接或 session。
- HTTP emergency 登录成功后的 session TTL 要短，建议 10-30 分钟。

### 8.6 限速与滥用防护

登录相关限速分三层：

- per-IP：限制单个来源的密码和 OTP 尝试。
- per-account/global：限制全局 Argon2id 校验频率，避免 CPU 被拖死。
- per-chat OTP：限制验证码发送频率，避免刷消息。

行为：

- 登录失败返回统一错误，不区分密码错、OTP 错、pending login 不存在。
- 不做永久锁定；单管理员场景下永久锁定容易造成自我 DoS。
- 可做渐进延迟和短时封禁。
- 对 `X-Forwarded-For` 只在显式配置 trusted proxy 时信任。

### 8.7 Session 与 CSRF

Cookie 策略：

- HTTPS 模式：`__Host-nahida_session=<id>; Path=/; Secure; HttpOnly; SameSite=Strict`。
- loopback HTTP 模式：允许不带 `Secure` 的开发/本地 cookie，但仍使用 `HttpOnly; SameSite=Strict`。
- public HTTP emergency：短 TTL session；UI 明确提示当前会话可被网络监听者窃取。

使用 cookie 后，mutation API 需要 CSRF 防护：

- state-changing requests 校验 `Origin` 或 `Referer`。
- 同源 SPA mutation 携带 CSRF token header。
- `SameSite=Strict` 作为基础保护，但不单独依赖它。

### 8.8 Scope 预留

后续权限模型建议：

| Scope | 能力 |
|---|---|
| `operator.read` | 读 status/session/cron/files |
| `operator.write` | 修改 cron、发送消息、编辑 workspace 文件 |
| `operator.admin` | 保存配置、重启、管理权限 |
| `node.read` | 查看节点 |
| `node.control` | 远控节点 |
| `plugin.surface` | 访问插件页面 surface |

`require_token` 后续升级为 `require_scope(scope)`，当前 token 没 scope 时视为 admin token 以保持兼容。

### 8.9 Audit log

需要审计的操作：

- config saved / restored
- system restart / shutdown requested
- cron created / updated / cancelled / deleted
- file written / renamed / deleted
- token / permission changed（未来）
- node command dispatched（未来）

MVP 可以先写结构化日志；如果 WebUI 要展示 audit，再新增 SQLite 表。

---

## 9. Gateway + Node 扩展预留

OpenClaw 的关键经验是：Gateway 是唯一控制面，CLI/WebUI/节点都通过 Gateway 交互；节点连接时声明 role、scope、capabilities，并通过协议版本握手。

nahida-bot 不需要首版复制完整 WebSocket RPC，但应预留以下边界：

1. `Gateway` 拥有状态和权限判断，WebUI 不绕过 Gateway。
2. 所有外部协议有版本：
   - REST bootstrap 返回 `api_version`。
   - WebSocket 连接首帧包含 `min_protocol/max_protocol/client/role/scopes/caps/auth`。
3. 节点模型：
   - node id
   - display name
   - platform
   - capabilities
   - last heartbeat
   - approved scopes
   - online/offline state
4. 远控动作必须是 capability gated，不允许 WebUI 直接下发任意 shell。
5. 插件 WebUI surface 和 node remote surface 都应走 scoped URL / scoped token。

未来 WebSocket 草案：

```json
{
  "type": "req",
  "id": "connect-1",
  "method": "connect",
  "params": {
    "min_protocol": 1,
    "max_protocol": 1,
    "client": {"id": "webui", "version": "0.1.0"},
    "role": "operator",
    "scopes": ["operator.read", "operator.write"],
    "auth": {"token": "..."}
  }
}
```

---

## 10. 实施阶段

### Phase 0：后端 service 边界

- [x] 抽出 config schema/validate service，CLI 改为调用 service。
- [x] 新增 `Application.started_at`。
- [x] 新增 status service 和 `GET /api/status`。
- [x] 新增 usage ledger 基础模型，先内存聚合，后续落 SQLite。
- [x] 新增全局 cron list endpoint。
- [x] 新增 config read/validate/save/backup API。
- [x] 新增 workspace files API。
- [x] 新增 WebUI bootstrap endpoint。

### Phase 1：前端壳和只读页面

- [x] 初始化 `webui/`：Vue 3 + TS + Vite。
- [x] 建立 API client、auth store、query client、router。
- [x] 建立 shadcn-vue + Reka UI 基础组件层和 `tokens.css`。
- [x] 建立首批 `Button/Input/Select/Dialog/Tabs/Tooltip/Badge/Table/FormField`。
- [x] AppShell / nav / topbar。
- [x] 首页状态面板。
- [x] 配置页只读 schema + YAML preview + validation。
- [x] CRON 只读表格。
- [x] Session 分组只读列表和 history viewer。
- [x] 文件树只读和 Markdown preview。
- [x] log 页面显示和筛选当前日志。

### Phase 2：写操作

- [x] 配置编辑、保存、自动备份、restart required 状态。
- [x] 重启/停止危险操作。
- [x] CRON 创建/编辑/cancel/delete。
- [x] 文件创建/编辑/重命名/软删除。
- [x] Audit log 结构化记录。
- [x] 前端 mutation 错误处理和并发 checksum 冲突处理。

### Phase 3：实时和可观测性

- [x] SSE `/api/events/stream`。
- [x] 首页资源和 token 低频实时刷新。
- [x] CRON fired / failed 实时更新。
- [x] Session updated 事件。
- [x] 日志 tail viewer（需脱敏）。

### Phase 4：登录与安全基线

- [x] 在完整 session 登录页前，补一个临时 bearer token 输入页，避免手动写 `sessionStorage`。
- [ ] 新增 Argon2id 管理员密码 hash 配置和 CLI 设置命令。
- [x] 新增 `/api/auth/login`、`/api/auth/logout`、`/api/auth/session`。
- [x] 用服务端 session cookie 替换浏览器 `sessionStorage` bearer token。
- [x] 浏览器 password 模式 SSE 改用 same-origin cookie 鉴权；bearer 兼容模式暂保留 query token。
- [x] SSE 启动逻辑读取 bootstrap `auth.required`，允许无认证 WebAPI 模式正常连接。
- [x] 新增登录 per-IP 限速和统一失败响应。
- [ ] 新增全局 hash 校验限速。
- [ ] 新增 chat OTP pending login 流程。
- [ ] 明确 `loopback`、`https`、`private_network`、`http_emergency` 部署模式和启动 warning。
- [ ] 对 cookie mutation 增加 Origin/Referer 校验和 CSRF token。

### Phase 5：权限、插件页面、节点

- [ ] `require_scope()`。
- [ ] token/scope 管理页。
- [ ] 插件 `webui_surfaces` manifest。
- [ ] sandboxed plugin surface。
- [ ] node registry、heartbeat、approval flow。
- [ ] node control 页面。
- [ ] memory 页面。

---

## 11. 测试计划

后端：

- status endpoint 在组件缺失时返回 degraded/partial，不 500。
- config validate 与 CLI 输出基于同一 service。
- config save 自动备份，checksum 冲突拒绝覆盖。
- config save 不把脱敏值写回。
- plugin config schema 能从 builtin 和 external plugin 发现。
- cron list-all 返回 active/inactive/failed/running 任务。
- session group 能正确识别 typed main session、cron isolated、cron named。
- file API 拒绝绝对路径和 `..` 逃逸。
- file API 拒绝非允许扩展和超大文件。
- auth login 使用 Argon2id hash 校验，不把管理员密码写入日志。
- HTTP emergency 未显式开启时拒绝公网 HTTP 登录。
- chat OTP 短 TTL、单次使用、失败限速和发送冷却生效。
- session cookie 具备预期属性；HTTPS 模式包含 `Secure`，loopback HTTP 模式不强制 `Secure`。
- mutation API 在 cookie auth 下校验 Origin/Referer 和 CSRF token。

前端：

- API client 401、403、422、500 显示正确。
- 首页 loading/error/partial states。
- 配置页 validation error 阻止保存。
- 保存成功后显示 backup 和 restart required。
- CRON 表格过滤和 mutation invalidation。
- Session history 长文本折叠。
- 文件编辑 checksum 冲突处理。
- 路由 guard 按 feature/scope 隐藏页面。
- bearer token 输入页能把 token 写入 auth store，并让后续 API 请求带 `Authorization` header。
- `auth.required=false` 时 SSE 能无 token 连接。
- 登录页能展示当前部署模式和 HTTP emergency warning。
- 401 后清理前端 auth state 并回到登录页。

端到端：

- 启动 Gateway + built WebUI，访问 `/` 可加载 SPA。
- 使用 token 登录后能访问 status。
- 修改配置保存，确认备份文件存在。
- 创建一个 interval cron，CRON 页能看到。
- workspace 内编辑 `USER.md`，文件内容正确写入。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| WebUI 直接耦合内部对象，后续 gateway/node 难扩展 | 所有页面只消费 API；后端 service 明确边界 |
| 配置表单和 CLI 校验漂移 | schema/validate 抽成共享 service |
| 保存配置误覆盖手工修改 | checksum + mtime + 自动备份 |
| secrets 泄漏到 UI 或日志 | 默认脱敏；`${ENV}` 不展开；audit 不记录内容全文 |
| restart 语义不可靠 | 明确“请求 shutdown，supervisor 负责拉起”；UI 展示 mode |
| 文件管理变成危险服务器文件浏览器 | 默认只管 workspace；额外 roots 必须显式配置 |
| token 费用显示不准 | 没有 pricing 时只显示 token；价格由 config 提供 |
| 纯 HTTP 登录被监听或 MITM | 不宣传为安全模式；公网 HTTP 只能作为显式 emergency；短 session + chat OTP + warning |
| 前端 hash 密码被误认为抗监听 | 不采用 NapCat 式客户端 hash 作为安全承诺；有加密通道时直接提交密码，HTTP emergency 接受残余风险 |
| OTP 被刷屏或爆破 | OTP 请求、验证和全局登录流程分别限速；OTP 短 TTL、单次使用、只存 hash |
| Cookie session 引入 CSRF | `SameSite=Strict`、Origin/Referer 校验、CSRF token header |
| 反代后 IP 限速被绕过或误伤 | 只在配置 trusted proxy 后信任 `X-Forwarded-For` |
| 前端依赖过重 | Vue + Vite + shadcn-vue/Reka 小组件集起步，表格/表单/编辑器只引入明确需要的库 |
| 自定义 UI 耗时失控 | 限定首批组件范围；复杂逻辑交给 TanStack Table、TanStack Query、VeeValidate/Zod 和 CodeMirror |
| 插件页面注入主应用导致 XSS | 插件 surface 使用 sandbox iframe/scoped token，不动态执行任意插件 JS |

---

## 13. 参考资料

- Vue Tooling / Vite recommendation: <https://vuejs.org/guide/scaling-up/tooling.html>
- Vite guide: <https://vite.dev/guide/>
- React new app guidance: <https://react.dev/learn/start-a-new-react-project>
- Svelte overview: <https://svelte.dev/docs/svelte/overview>
- Solid overview: <https://docs.solidjs.com/>
- FastAPI StaticFiles: <https://fastapi.tiangolo.com/tutorial/static-files/>
- TanStack Query Vue: <https://tanstack.com/query/latest/docs/framework/vue/overview>
- Pinia introduction: <https://pinia.vuejs.org/introduction.html>
- Reka UI introduction: <https://reka-ui.com/docs/overview/introduction>
- shadcn-vue introduction: <https://www.shadcn-vue.com/docs/introduction>
- CodeMirror docs: <https://codemirror.net/docs/>
- OpenClaw Control UI: <https://docs.openclaw.ai/web/control-ui>
- OpenClaw Gateway protocol: <https://github.com/openclaw/openclaw/blob/main/docs/gateway/protocol.md>
- OpenClaw session management deep dive: <https://github.com/openclaw/openclaw/blob/main/docs/reference/session-management-compaction.md>
- OWASP Authentication Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html>
- OWASP Session Management Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html>
- OWASP CSRF Prevention Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html>
- OWASP ASVS: <https://owasp.org/www-project-application-security-verification-standard/>
- NIST SP 800-63: <https://www.nist.gov/special-publication-800-63>
- Tailscale HTTPS certificates: <https://tailscale.com/docs/how-to/set-up-https-certificates>
- NapCat WebUI authentication reference: <https://github.com/NapNeko/NapCatQQ/tree/main/packages/napcat-webui-backend>
