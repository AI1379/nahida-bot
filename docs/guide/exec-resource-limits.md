# 本地命令执行与资源保护

`exec` 工具和 cron 外部脚本共用应用级 `ExecRunner`、并发预算和资源配置。
每次执行启动一个独立 Python 监督进程，由它启动命令、持续排空输出、采样资源，
并在超时、取消、资源超限及正常退出后回收任务。无需 Docker、systemd 或 cgroup。

## 配置

在 YAML 顶层设置，修改后重启应用：

```yaml
execution:
  memory_limit_mb: 512
  max_concurrency: 2
  min_available_memory_mb: 512
  check_interval_seconds: 0.1
  max_output_bytes: 50000
  max_processes: 64
  queue_timeout_seconds: 30
  max_queued: 64
  max_queued_per_owner: 8
  default_timeout_seconds: 120
  max_timeout_seconds: 900
```

| 配置 | 含义 |
|---|---|
| `memory_limit_mb` | 已追踪的任务进程及后代的 RSS 合计阈值，MiB，正整数 |
| `max_concurrency` | exec 和 cron 共享的并发数，正整数；额度不足进入有界公平队列 |
| `min_available_memory_mb` | 宿主机可用内存预留线，MiB；准入还会计入新任务及已有任务尚未使用的内存预留；运行期间不足则停止任务；0 关闭宿主内存准入与预留检查 |
| `check_interval_seconds` | 监督进程目标采样间隔，0.01–1 秒 |
| `max_output_bytes` | stdout 和 stderr 各自最多缓存的字节数，超出部分排空并丢弃 |
| `max_processes` | 已追踪的活跃任务进程数阈值，包含外层 shell；通过采样检查 |
| `queue_timeout_seconds` | 最长排队时间，受更早的父任务总 deadline 约束 |
| `max_queued` / `max_queued_per_owner` | 全局和每发起账号的排队数量上限，默认 64 / 8 |
| `default_timeout_seconds` / `max_timeout_seconds` | exec 工具默认和最大执行时长，默认 120 / 900 秒；Cron 脚本仍使用其作业的 script timeout |

旧的未发布实现中的 `builtin-commands.exec_isolation`、`exec_memory_limit_mb`、
`exec_max_concurrency` 已移除；请将配置迁移到顶层 `execution`。没有无限制后端开关。
exec 省略 `timeout` 时默认运行最多 120 秒，可显式请求最多 900 秒，两个值均可配置。
外层工具预算自动包含本次请求的执行时长、最长排队时间和 30 秒收尾余量，
不会再被普通工具的 135 秒兜底提前截断。可在 `agent.tool_timeout_overrides`
按工具名设置明确的总时限；显式覆盖优先于自动预算。`agent_wait` 工具也会覆盖
其请求的观察窗口，但观察超时不会取消后台子任务。
后台子 Agent 不继承创建它的工具 deadline，使用自己的执行预算；
`TaskManager` 启动的独立后台工作也会清除提交轮次的临时 deadline。

Cron 的默认单次 fire 总预算调整为 900 秒，已有 job 的非空 `timeout_seconds`
继续优先；脚本仍使用 `script_timeout_seconds`。总预算包含排队、脚本、Agent 和投递，
并传给内部 exec。剩余时间不足时会缩短执行时长，为清理留出一部分时间，
不能通过请求较长 exec 绕过上层期限。普通命令和 Provider 的既有超时独立生效。

队列按交互、后台、维护以 4:2:1 分配派发机会，同级按发起账号轮转，组内 FIFO。
Cron 脚本显式传入创建账号，子 Agent 继承原发起账号；合成的子 session 不产生新额度。
排队取消不会启动命令，运行中取消则等待监督进程清理后才释放并发槽。
工具运行中收到 `/stop` 会停止本地等待、清理 exec，并返回明确的取消工具结果；
Agent 仍通过正常的 cancelled done 路径保存部分轮次，未启动的同批调用不会继续执行。
单项请求延用监督协议的 1 MiB 上限，并在排队前检查，限制队列持有的载荷规模。
无法确认 supervisor/进程清理的槽位会标记为 quarantined，不继续用于新执行；
确认宿主进程状态并重启服务后重新建立预算。运行快照提供 running、queued、quarantined 数量。

## 进程生命周期

监督进程使用 Bot 的 Python 解释器，避免执行 venv 中缺少 psutil 导致监测失效。
实际命令继续沿用工作目录、环境变量和 `exec_venv_path` 指定的专用 Python venv。
监督进程只通过受控 stdin/stdout 与 Bot 通信；命令自己的 stdin 为关闭状态，
stdout/stderr 分别捕获，不能伪造执行结果。

- Windows 使用 pywin32 Job Object 管理任务及其后代。先以暂停状态创建 shell，
  成功加入 Job 后恢复；分配失败就终止该进程，禁止无保护继续执行。Job 句柄不
  传给命令，监督进程消失时句柄关闭会回收任务。
- Linux/macOS 将命令放入独立进程组，监督进程留在组外。持有 shell 的未回收 PID，
  清理完进程组再回收 shell，避免 shell 先退出后丢失进程组。psutil 同时追踪已经
  发现并脱离进程组的后代，以进程创建时间识别 PID 重用。
- Bot 取消任务或崩溃后，控制管道关闭，监督进程执行清理。父协程取消会等待清理，
  然后重新抛出 `CancelledError`。通信过程中还有独立的超时兜底。

监督工作不依赖 Bot 的 asyncio 事件循环及时调度。达到内存或进程阈值、宿主机
内存不足时直接终止任务。配置多个并发时，宿主机内存不足可能同时停止多个任务；
默认并发 2；准入使用任务内存上限减去已观察的子进程 RSS 估算尚未使用的预留。
不能观测时保留完整预留，因此实际并发可能小于配置值。此估算仍是采样式保护。

## 结果与限制

执行结果区分 `completed`、`exit_error`、`timeout`、`memory_limit_exceeded`、
`process_limit_exceeded`、`host_memory_pressure`、`queue_full`、`queue_timeout`、`spawn_error`、
`supervisor_error`、`cleanup_failed`。记录观察到的峰值 RSS；它不是实际瞬时峰值。
普通非零退出码不会被猜测成 OOM。结果另记录 `started` 和 `queue_wait_seconds`，
区分命令尚未启动与运行中失败；监督进程结果丢失时启动状态可能为未知。

exec 失败通过工具适配器返回结构化错误，标记为不可自动重试；模型可依据错误
主动缩小图片或拆分任务。Cron 遇到排队满、排队过期或启动前内存不足时，
释放 claim 并退避至多 30 秒后重新认领，保留原到期时间和迟到窗口，
不增加失败次数、不回退 Agent，也不会让该 job 挡住其他到期任务。
运行中失败仍按原脚本失败策略传递上下文，提醒不要原样重复超限命令。
关闭取消不增加 Cron 失败次数。输出字节缓存有上限，展示层继续使用已有字符截断。

所有平台都使用采样式 RSS 保护，Windows Job 在这里用于生命周期管理，没有
将与 RSS 不同的 committed-memory 配额混为同一个配置。RSS 合计会重复计算
部分共享页；突然分配、采样延迟、瞬间创建并重新托管的 POSIX 后代可能逃过监测。
因此不能保证不会发生系统 OOM，也不承诺恶意脚本隔离。POSIX 监督进程遭 SIGKILL
时，独立进程组不会自动消失；正常取消和控制管道断开则会触发回收。

服务关闭时中断回复显示 `[Service is shutting down; the current response was interrupted.]`，
普通会话取消继续显示 `[Agent stopped.]`。

## 工具批次并行

`agent.max_parallel_tools` 默认 4，设为 1 可恢复批内串行。仅注册时明确声明
`parallel_safe=True` 的连续调用组成并行段；未声明工具、写文件、发消息、exec、
修改记忆或计划等操作继续作为屏障，等待前面的段完成后单独执行。
这是每个工具批次的上限，exec 仍单独服从共享的 `execution.max_concurrency`。

目前声明安全的内置工具包括 workspace_read、memory_read、聊天历史读取/搜索、
find_chat、recall_cross_chat、search_files、web_fetch，以及 KB 搜索和 context_read。
KB 的首次索引准备与导入后回填按集合互斥；MCP 工具默认不依据 readOnlyHint 自动放行。

SDK 的 `BotAPI.register_tool` 和 `@register_tool` 支持 `parallel_safe`，默认 false。
它表示 handler 可与其他独立调用重叠，不扩大权限或保证跨会话的文件互斥。
执行器在每批开始时固定注册信息，防止热重注册将正在运行的只读批次换成另一种策略。
所有调用仍独立校验参数和权限；结果按原 call_id 顺序写回 transcript，ledger 事件串行提交。
`/stop` 会取消活动调用、清理资源，并为尚未启动的同批调用返回取消结果。

`web_fetch` 逐跳校验公开目标和固定 DNS 连接，DNS 与正文转换在线程池执行。
响应正文流式读取，达到既有的 5 MiB 上限就停止，避免并行时先缓冲无界响应。

## 回复后的记忆维护

应用默认在用户/assistant 历史与 transcript 提交后，将自动记忆固化及其嵌入刷新
交给应用级单写入者队列，完成入队即可结束本轮并处理下一条消息。
显式 memory_write、memory_update、memory_archive 工具仍同步执行。它们先等待更早
入队的维护完成，并在提交期间阻止新的维护越过，避免旧自动任务重新补回刚归档的记忆。

```yaml
memory:
  consolidation:
    background_enabled: true
    max_pending: 64
    max_pending_bytes: 8388608
    shutdown_timeout_seconds: 30
```

每项工作保存本轮证据的副本、账号与 Person 快照，不依赖后台任务继承的 ContextVar，
也不会重新读取“最新一轮”猜测归属。共享记忆的自动派生写入串行处理，继续使用
存储层已有的事务提交。自动提取的记忆可能稍后可见，完整对话历史则在入队前已提交。

任务数量和常规载荷都设上限；队列满时生产者等待容量。超大单项先排空旧任务，
再等待该项处理结束，不在后台继续积压多个大对象。队列失败记录日志和计数，
不将已提交的回复改成失败；`background_enabled: false` 可恢复原同步路径。

关闭时先停止 Cron、会话、子 Agent 与其他生产者，再排空后处理队列，最后关闭
插件、Provider 和数据库。超过排空期限后取消剩余派生工作并记录数量。
队列尚未持久化，重启不保证恢复未完成的派生任务；其来源对话仍保留。

## 验证

```bash
uv run pytest tests/test_execution_admission.py tests/test_exec_timeout_policy.py tests/test_exec_runner.py tests/test_exec_integration.py tests/test_exec_stop_notifications.py tests/test_process_tree.py tests/test_scheduler.py tests/test_cron_timeout.py tests/test_plugin_tool_executor.py
```

工具并行与后处理回归：

```bash
uv run pytest tests/test_parallel_tools.py tests/test_memory_postprocess.py tests/test_web_fetch_tools.py tests/test_knowledge_base_plugin.py tests/test_sdk_testing.py tests/test_transcript_replay.py
```

`.github/workflows/local-execution.yml` 在 Windows、Linux 和 macOS 上运行同一组测试。
测试使用小规模的多子进程分配验证合计 RSS 超限、恢复、事件循环阻塞、父进程崩溃、
取消、超时、正常退出后的后代回收，以及 exec/cron 共享并发预算。
