# 本地命令执行与资源保护

`exec` 工具和 cron 外部脚本共用应用级 `ExecRunner`、并发预算和资源配置。
每次执行启动一个独立 Python 监督进程，由它启动命令、持续排空输出、采样资源，
并在超时、取消、资源超限及正常退出后回收任务。无需 Docker、systemd 或 cgroup。

## 配置

在 YAML 顶层设置，修改后重启应用：

```yaml
execution:
  memory_limit_mb: 512
  max_concurrency: 1
  min_available_memory_mb: 512
  check_interval_seconds: 0.1
  max_output_bytes: 50000
  max_processes: 64
```

| 配置 | 含义 |
|---|---|
| `memory_limit_mb` | 已追踪的任务进程及后代的 RSS 合计阈值，MiB，正整数 |
| `max_concurrency` | exec 和 cron 共享的并发数，正整数；额度不足立即返回 `busy` |
| `min_available_memory_mb` | 宿主机可用内存预留线，MiB；启动前不足则拒绝执行，运行期间不足则停止任务；0 关闭此项 |
| `check_interval_seconds` | 监督进程目标采样间隔，0.01–1 秒 |
| `max_output_bytes` | stdout 和 stderr 各自最多缓存的字节数，超出部分排空并丢弃 |
| `max_processes` | 已追踪的活跃任务进程数阈值，包含外层 shell；通过采样检查 |

旧的未发布实现中的 `builtin-commands.exec_isolation`、`exec_memory_limit_mb`、
`exec_max_concurrency` 已移除；请将配置迁移到顶层 `execution`。没有无限制后端开关。
exec 继续采用默认 30 秒、最大 120 秒超时；cron 沿用每个作业的脚本超时。

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
默认并发 1，采用保守保护策略。

## 结果与限制

执行结果区分 `completed`、`exit_error`、`timeout`、`memory_limit_exceeded`、
`process_limit_exceeded`、`host_memory_pressure`、`busy`、`spawn_error`、
`supervisor_error`、`cleanup_failed`。记录观察到的峰值 RSS；它不是实际瞬时峰值。
普通非零退出码不会被猜测成 OOM。

exec 失败通过工具适配器返回结构化错误，标记为不可自动重试；模型可依据错误
主动缩小图片或拆分任务。cron 保留脚本失败后进入 Agent 的行为，传递明确原因，
提醒不要原样重复超限命令。输出字节缓存有上限，展示层继续使用已有字符截断。

所有平台都使用采样式 RSS 保护，Windows Job 在这里用于生命周期管理，没有
将与 RSS 不同的 committed-memory 配额混为同一个配置。RSS 合计会重复计算
部分共享页；突然分配、采样延迟、瞬间创建并重新托管的 POSIX 后代可能逃过监测。
因此不能保证不会发生系统 OOM，也不承诺恶意脚本隔离。POSIX 监督进程遭 SIGKILL
时，独立进程组不会自动消失；正常取消和控制管道断开则会触发回收。

服务关闭时中断回复显示 `[Service is shutting down; the current response was interrupted.]`，
普通会话取消继续显示 `[Agent stopped.]`。

## 验证

```bash
uv run pytest tests/test_exec_runner.py tests/test_exec_integration.py tests/test_exec_stop_notifications.py tests/test_process_tree.py tests/test_scheduler.py tests/test_plugin_tool_executor.py
```

`.github/workflows/local-execution.yml` 在 Windows、Linux 和 macOS 上运行同一组测试。
测试使用小规模的多子进程分配验证合计 RSS 超限、恢复、事件循环阻塞、父进程崩溃、
取消、超时、正常退出后的后代回收，以及 exec/cron 共享并发预算。
