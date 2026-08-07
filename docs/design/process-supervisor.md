# 进程监管服务（Process Supervisor）

> 状态：设计草案
> 日期：2026-08-07
> 目标：为 nahida-bot 提供统一的后台长驻进程（SSH 隧道、frpc、cloudflared、各类 sidecar）监管能力，确保它们在依赖它们的 Channel/服务之前就绪、在它们之后收尾，并提供统一的状态观测与管理入口。
> 相关文档：
>
> - [architecture/README.md](../architecture/README.md)
> - [webui-design.md](webui-design.md)
> - [plugin-web-panels.md](plugin-web-panels.md)
> - [cron-and-webapi-optimization.md](cron-and-webapi-optimization.md)

---

## 1. 背景

bot 在服务端运行时，常常需要附带跑一些"附属进程"：反向 SSH 隧道、frpc、cloudflared tunnel、TTS sidecar、外部协议后端等。这些进程有几个共同特征：

- 它们是**基础设施**，而非 bot 智能。需要存活整个 bot 生命周期。
- 它们有**依赖顺序**：例如 OneBot Channel 要通过 SSH 隧道连上游，隧道必须先于 Channel 就绪。
- 它们需要**监管**：崩溃自动重启、退避策略、健康检查。
- 它们需要**统一观测**：在 WebUI 一处看到全部 sidecar 的状态与日志。

历史上这类需求靠 systemd、`autossh`、手写 `nohup` 脚本散落在部署文档里。本设计把它们收敛进 bot 核心，让"附属进程"成为一等公民，和 CRON、Session、Plugin 一样有声明式配置、生命周期管理与 WebUI 面板。

---

## 2. 核心决策：核心服务，而非插件

本服务**放在核心层**（`nahida_bot/core/`），与 `TaskManager`、`SchedulerService`、`WebHostService` 同类，不作为外部插件实现。决定性理由是**生命周期顺序的确定性**。

经核查插件加载机制（`nahida_bot/plugins/manager.py` 的 `load_all` / `enable_all`）：同 `load_phase` 内插件按**发现顺序**加载，并未按 `depends_on` 做拓扑排序。Channel（telegram / milky / onebot）本身就是以插件形式在 `pre-agent` / `post-agent` 阶段启用并建连的。因此：

- **纯插件方案**：隧道插件与 Channel 插件处于同一加载池，谁先谁后取决于发现顺序，无法确定性保证隧道先于 Channel 建立。即便把隧道设为 `pre-agent`，依赖它的 Channel 也可能恰好在同阶段更早被发现。
- **核心服务方案**：可以挂到 `AppInitializing` 之后、`pre-agent` 插件 `enable_all` 之前，拿到**确定性早于所有 Channel** 的启动点；停止时则在所有插件（含 Channel）关闭之后再收尾，保证 Channel 断连期间隧道仍然可用。

SSH 隧道本身的知识（端口、密钥、`ServerAliveInterval`）**不进核心**——核心只提供通用进程监管器，SSH、frpc、cloudflared 都只是 `config.yaml` 里的一条进程声明。这样核心保持通用，未来 Node 分布式层（Phase 5）也能复用同一个监管器把进程委派到远端节点。

> 注：现有 `exec` 工具（`builtin-commands` 插件）是"跑一次性命令拿 stdout"的 agent 工具，与本服务（长驻保活、监管、统一面板）职责不重叠，互不影响。

---

## 3. 设计目标

1. **生命周期确定性**：监管进程在所有 Channel/插件启用之前启动，在它们全部关闭之后才停止。
2. **声明式配置**：进程定义在 `config.yaml` 的 `processes:` 段，核心启动时读取并拉起，无需手写 systemd unit。
3. **通用监管**：spawn、崩溃重启（退避）、健康检查、优雅停止、日志环形缓冲。不绑定任何具体协议。
4. **统一可观测**：一个 `/api/processes` 接口、一个 WebUI 面板、一套 `process.*` SSE 事件，覆盖全部 sidecar。
5. **安全收敛**：MVP 只接受 config 声明的进程，不接受 WebUI 运行时下发任意命令；密钥走环境变量插值；防重启风暴。
6. **可扩展**：二期允许插件通过 `self.api.spawn_supervised_process(...)` 贡献进程，登记到同一个监管器、出现在同一个面板。
7. **与现有架构一致**：复用 EventBus、structlog、`TaskManager` 风格的命名/owner 账本、Gateway route + service 分层。

---

## 4. 非目标

- 不做 systemd / supervisord 的完整等价物（无 cgroup、无 uid 切换、无优先级调度）。
- 不在 MVP 支持运行时从 WebUI 下发**任意**命令拉起新进程（防止把管理面板变成 RCE 入口）；只支持对已声明进程的 start/stop/restart。
- 不在核心实现 SSH/frp/cloudflared 等具体协议逻辑。
- 不在 MVP 做复杂依赖图（DAG）编排；`depends_on` 仅做**简单有序启动**，不做拓扑循环检测与跨节点等待。
- 不替代 `exec` 工具（一次性命令执行）。
- 不实现进程级资源配额（CPU/内存限额）。

---

## 5. 架构与分层

```text
┌─────────────────────────────────────────────────────────────┐
│  Interface Layer                                            │
│  CLI (nahida-bot doctor) · WebUI Processes 面板 · SSE       │
├─────────────────────────────────────────────────────────────┤
│  Gateway Layer                                              │
│  /api/processes (list/detail/start/stop/restart/logs) ·     │
│  process.* 事件 → EventBroadcaster → SSE                    │
├─────────────────────────────────────────────────────────────┤
│  Core Layer                                                 │
│  ProcessSupervisor (spawn / supervise / restart / health /  │
│    shutdown) · ProcessSpec 配置 · 日志环形缓冲 · EventBus   │
├─────────────────────────────────────────────────────────────┤
│  Plugin Layer（二期）                                       │
│  BotAPI.spawn_supervised_process → 复用核心监管器           │
└─────────────────────────────────────────────────────────────┘
```

`ProcessSupervisor` 与 `TaskManager` 平行存在：`TaskManager` 管 asyncio 协程任务，`ProcessSupervisor` 管 OS 子进程。两者风格对齐（命名、owner、`shutdown(timeout=)`、`list_*` 快照），但互不包含。

监管器内部的"监管循环"（wait 进程退出、重连、健康轮询）本身是 asyncio 任务。为避免被 `task_manager.shutdown()` 提前清理，监管循环**不**登记到 `TaskManager`，而由 `ProcessSupervisor.shutdown()` 自行收口；调用顺序见 §6。

---

## 6. 生命周期接入

接入点严格落在 `Application.initialize()` / `Application.stop()` 中，保证确定性：

**启动**（`initialize()` 内，`AppInitializing` 发布之后、`plugin_manager.enable_all(phase="pre-agent")` 之前）：

1. 读取 `settings.processes`，构造 `ProcessSupervisor`。
2. `await supervisor.start()`：按 `depends_on` 简单排序后逐组拉起所有 enabled 的 `ProcessSpec`。
3. 健康检查（若声明）通过后再标记该进程 `running`；`startup_wait_seconds` 控制启动期等待。
4. 进入正常监管循环。

**停止**（`stop()` 内，`plugin_manager.shutdown_all()` 之后、`AppStopped` 发布之前）：

```text
webapi.stop()
task_manager.shutdown()        # 媒体/临时文件清理等核心循环任务
scheduler.stop()
message_router.stop()
plugin_manager.shutdown_all()  # Channel 在此断连，期间隧道仍可用
await supervisor.shutdown(timeout=…)   # ← 最后才收尾附属进程
event_bus.publish(AppStopped)
event_bus.shutdown()
providers.close() / db.close()
```

这样保证：Channel（插件）断连期间，隧道始终在线；只有所有依赖者都已收尾，才关闭被依赖的隧道。

---

## 7. 配置模型

新增顶层 `processes` 段。`Settings` 模型追加 `processes: ProcessSupervisorConfig`（沿用现有 `model_config extra="allow"` + 显式字段模式）。

```yaml
processes:
  enabled: true
  defaults:
    restart_policy: on-failure      # no | on-failure | always
    backoff_initial_seconds: 1.0
    backoff_max_seconds: 60.0
    backoff_factor: 2.0
    restart_max_attempts: 0         # 0 = 不限；>0 触发熔断（见 §11）
    restart_window_seconds: 300     # 统计重启次数的滑动窗口
    shutdown_timeout_seconds: 10.0
    log_buffer_lines: 1000          # stdout/stderr 各自的环形缓冲行数
    startup_wait_seconds: 0.0       # 启动后等待健康检查的宽限
  specs:
    ssh-db-tunnel:
      command: "ssh -N -L 3306:db.internal:3306 bastion@example.com"
      args: []                      # 可选，与 command 二选一或组合
      shell: true                   # true=通过 shell 执行；false=execve
      env:
        SSH_KEY_PATH: "${SSH_KEY_PATH}"
      working_dir: null
      restart_policy: always        # 覆盖 defaults
      health_check:
        type: tcp_port              # tcp_port | none（一期）
        host: "127.0.0.1"
        port: 3306
        interval_seconds: 15
        timeout_seconds: 3
        unhealthy_after: 3          # 连续失败 N 次才判不健康并触发重启
      depends_on: []                # 同段内其他 spec 名，简单有序
      shutdown_timeout_seconds: 8.0 # 单进程覆盖
    frpc:
      command: "frpc -c ./frpc.ini"
      restart_policy: always
      health_check:
        type: none
```

**配置语义**：

| 字段 | 说明 |
|------|------|
| `enabled` | 总开关；false 时监管器构造但不拉起任何进程 |
| `defaults` | 所有 spec 的默认值，spec 内同名字段覆盖 |
| `specs` | 进程定义，key 为进程名（`[a-z0-9_-]+`，全局唯一，含插件贡献进程） |
| `command` + `shell` | `shell: true` 走 `create_subprocess_shell`；`false` 走 `create_subprocess_exec`，此时 `args` 为参数列表 |
| `env` | 追加到子进程环境，**不**继承也不暴露 bot 自身的敏感环境；`${VAR}` 插值沿用全局规则 |
| `restart_policy` | `no`=不重启；`on-failure`=非 0 退出才重启；`always`=无论退出码都重启 |
| `health_check` | `tcp_port`=尝试 TCP 连接端口；一期不实现 HTTP/命令探针 |
| `depends_on` | 仅影响**启动顺序**（被依赖者先就绪），不影响停止顺序；循环依赖在配置校验期报错 |

`env` 不继承 bot 进程的整个环境是安全默认；显式需要的变量（如 `PATH`）由用户在配置里声明，便于审计。`PATH` 等少量必需变量由监管器显式注入白名单集合。

---

## 8. 进程监管器 API

核心类 `ProcessSupervisor`（`nahida_bot/core/process_supervisor.py`）：

```python
class ProcessSupervisor:
    def __init__(self, config: ProcessSupervisorConfig, event_bus: EventBus) -> None: ...

    async def start(self) -> None:
        """按 depends_on 排序后逐组拉起所有 enabled 进程，进入监管循环。"""

    async def shutdown(self, timeout: float = 10.0) -> None:
        """向所有进程发 SIGTERM，等待 shutdown_timeout 后 SIGKILL 残留。"""

    # 运行时控制（供 Gateway 路由与二期插件 API 调用）
    async def restart(self, name: str) -> ProcessInfo: ...
    async def stop(self, name: str) -> ProcessInfo: ...      # 停止且不再自动重启
    async def start_one(self, name: str) -> ProcessInfo: ... # 启动单个已停止进程

    # 查询
    def list_processes(self) -> list[ProcessInfo]: ...
    def get_process(self, name: str) -> ProcessInfo | None: ...
    def get_logs(self, name: str, *, stream: str = "both", limit: int = 200) -> ProcessLogs: ...
```

`ProcessInfo` 为不可变快照（仿 `TaskInfo`）：

```python
@dataclass(slots=True, frozen=True)
class ProcessInfo:
    name: str
    owner: str                       # "core.config" 或 plugin_id
    status: Literal["pending","starting","running","unhealthy","stopping","stopped","failed","disabled"]
    pid: int | None
    restart_count: int
    exit_code: int | None
    started_at: datetime | None
    last_error: str | None = None
    health: Literal["unknown","healthy","unhealthy"] = "unknown"
    restart_policy: str = "on-failure"
```

日志用 `collections.deque(maxlen=log_buffer_lines)`，stdout / stderr 各自一条，按行追加；`get_logs` 返回尾部 N 行。结合现有 `gateway/services/log_redaction.py` 的脱敏规则，避免密钥/令牌泄漏到面板。

---

## 9. 事件

在 `nahida_bot_sdk/events.py` 新增 `process.*` 事件，沿用 `Event[PayloadT]` 基类，并在 `core/events.py` re-export：

```python
@dataclass(slots=True, frozen=True)
class ProcessPayload:
    name: str
    owner: str
    status: str
    pid: int | None
    restart_count: int
    exit_code: int | None
    error: str = ""

class ProcessStarted(Event[ProcessPayload]): ...   # 进程进入 running
class ProcessStopped(Event[ProcessPayload]): ...   # 正常停止（含手动 stop）
class ProcessFailed(Event[ProcessPayload]): ...    # 异常退出 / 熔断 / 健康检查失败
```

`EventBroadcaster`（`gateway/services/event_broadcaster.py`）订阅这三类事件，转成 SSE `process.started` / `process.stopped` / `process.failed` 推给 WebUI，与现有 `status.updated`、`session.updated` 一致。

---

## 10. Gateway / WebUI surface

### 10.1 REST 路由

新增 `nahida_bot/gateway/routes/processes.py`，在 `WebAPIApp._build_fastapi` 注册：

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/processes` | 列出全部进程快照 |
| GET | `/api/processes/{name}` | 单进程详情（含健康、重启计数、最近错误） |
| GET | `/api/processes/{name}/logs?stream=both&limit=200` | 尾部日志 |
| POST | `/api/processes/{name}/start` | 启动已停止进程 |
| POST | `/api/processes/{name}/stop` | 停止且不再自动重启 |
| POST | `/api/processes/{name}/restart` | 重启 |

全部走现有 `require_token` / WebUI 登录态鉴权。`{name}` 不存在返回 404；非法操作（如对 disabled 总开关）返回 409。

### 10.2 WebUI 面板

WebUI 新增"进程"页（与 CRON / Plugins 同级）：

- 表格：名称、owner、状态徽标、PID、重启次数、健康、最近错误。
- 行操作：启动 / 停止 / 重启、查看日志（抽屉式，尾部 200 行，可切换 stdout/stderr/both）。
- 实时：订阅 `process.*` SSE，行状态就地刷新。
- 只消费 `/api/processes`，不读内部状态——遵循 ROADMAP "WebUI 只消费公开 API" 原则。

### 10.3 CLI

`nahida-bot doctor` 增加一项检查：列出配置中 enabled 但当前非 running 的进程，提示运维。

---

## 11. 安全模型

1. **命令来源**：MVP 进程定义只来自 `config.yaml`（运维信任边界）。WebUI 只能 start/stop/restart **已声明**进程，不能新建任意命令。
2. **密钥处理**：`env` 支持 `${VAR}` / `${VAR:default}` 插值；密钥放 `.env`，不落配置文件明文。日志输出经 `log_redaction` 脱敏。
3. **环境继承**：子进程默认**不**继承 bot 全部环境；仅注入白名单（`PATH`、`SYSTEMROOT` 等系统必需）+ 用户 `env`。
4. **重启风暴防护**：
   - 指数退避：`backoff_initial_seconds` → `backoff_max_seconds`，`backoff_factor` 倍增。
   - 滑动窗口熔断：`restart_window_seconds` 内重启次数超 `restart_max_attempts`（0=不限）则进入 `failed` 状态并停止重启，发布 `ProcessFailed`，等待人工介入（WebUI restart）。
5. **停止双信号**：先 SIGTERM 等 `shutdown_timeout_seconds`，超时 SIGKILL；Windows 无 SIGTERM 则直接 terminate。
6. **shell 注入**：`shell: true` 时 `command` 来自受信配置，不接受运行时拼接；用户须自行对配置文件负责（与现有 `exec` 工具信任模型一致）。

---

## 12. 与 Node 分布式层的关系

ROADMAP Phase 5 规划的 Gateway-Node 分布式执行用于"远程节点跑重模型"。本服务的进程监管是**本地** sidecar 管控，二者互补：

- MVP：`ProcessSupervisor` 只在 bot 本机拉起子进程。
- 未来：`ProcessSpec` 可扩展 `node: <node_id>` 字段，由 Node 协议委派到远端执行；本机监管器退化为"期望状态持有者 + 远端状态同步"。本设计刻意把监管器做成通用的"spec → 期望状态"，为该演进预留空间，但 MVP 不实现远端委派。

---

## 13. 实施阶段

### Phase 1：核心监管器 + 配置 + 生命周期（本 PR）

- [ ] `nahida_bot/core/process_supervisor.py`：`ProcessSpec` / `ProcessSupervisorConfig` / `ProcessInfo` / `ProcessSupervisor`。
- [ ] spawn（shell/exec 两种）、监管循环、退避重启、滑动窗口熔断、`tcp_port` 健康检查。
- [ ] stdout/stderr 环形缓冲 + `get_logs`。
- [ ] `depends_on` 简单有序启动 + 循环依赖校验。
- [ ] `Settings.processes` 接入；`Application.initialize` / `stop` 生命周期挂载（含停止顺序）。
- [ ] SDK `events.py` 新增 `ProcessStarted/Stopped/Failed` + `core/events.py` re-export。
- [ ] 单元测试：spawn/退出回调、restart_policy 各分支、退避、熔断、health、logs、depends_on 循环检测、shutdown 顺序。

### Phase 2：Gateway + SSE

- [ ] `gateway/routes/processes.py`（list/detail/logs/start/stop/restart）+ schemas。
- [ ] `EventBroadcaster` 订阅 `process.*` → SSE。
- [ ] 集成测试：起真实短命子进程跑通 start→restart→stop→SSE。

### Phase 3：WebUI 面板

- [ ] Processes 页（表格 + 日志抽屉 + SSE 实时刷新）。
- [ ] `nahida-bot doctor` 进程健康检查。

### Phase 4：config 文档与示例

- [ ] `config.yaml` 注释段、`docs/guide/configuration.md`、README 命中点。
- [ ] ROADMAP 勾选。

### Phase 5（后续）：插件贡献进程

- [ ] SDK `BotAPI.spawn_supervised_process(spec)`，owner=plugin_id。
- [ ] 插件 disable 时随其所有进程一起停止（仿 `task_manager.cancel_by_owner`）。

---

## 14. 开放问题

1. 健康探针一期只做 `tcp_port`，是否同时加 `http`（期望状态码）？倾向二期再加，避免过早膨胀。
2. `depends_on` 是否需要等待被依赖进程**健康**而非仅 `running` 就绪？倾向：等待 health 通过（若声明 health_check），否则等待进程进入 running。
3. 日志环形缓冲放内存（MVP）还是可选落盘轮转？倾向内存，避免与 structlog 文件日志混淆；落盘留作插件自行重定向。
4. `restart_max_attempts` 熔断后，是否允许配置"冷却 N 秒后自动恢复尝试"？倾向 MVP 不自动恢复，需人工 restart。
5. Windows 下 `ssh`/`frpc` 的信号语义差异（无 SIGTERM）是否需要在配置层暴露平台特例？倾向：监管器内部按平台分支处理，对配置透明。
6. 是否为进程提供 `credentials` 段（引用 identity 体系的密钥）而非裸 `env` 插值？与 `person-identity-system` 的融合留待后续。

---

## 15. 结论

进程监管应作为**核心服务**实现，决定性原因是生命周期顺序的确定性（隧道必须确定性早于 Channel 就绪），这是纯插件方案无法保证的。核心只做通用监管器，SSH/frpc/cloudflared 等具体协议以 config 声明形式接入，保持核心通用、为 Node 远端委派预留演进。

主线负责：

- 声明式配置解析与校验。
- 确定性生命周期挂载（早于 Channel 启动、晚于 Channel 停止）。
- spawn / 退避重启 / 健康检查 / 优雅停止 / 日志缓冲。
- 统一 Gateway 接口、SSE 事件与 WebUI 面板。
- 重启风暴熔断与日志脱敏。

部署方负责：

- 在 `config.yaml` 声明自己的 sidecar 进程。
- 把密钥放 `.env`，通过 `${VAR}` 引用。
- 通过 WebUI 监控状态、按需手动重启。
