# 模型上下文窗口与预算

> 设计时间：2026-05-18
> 状态：已实现（已迁移自 design/）

## 1. 问题

改动前 `ContextBudget` 以全局固定值工作：

- `ContextConfig.max_tokens = 8000`
- `ContextConfig.reserved_tokens = 1000`
- `Application` 初始化 provider 时构建一次 `ContextBuilder`
- `SessionRunner` 每轮虽然会解析 `effective_model` 和 `ModelCapabilities`，但不会用模型能力重算上下文预算

这会导致两个问题：

- 不同模型的上下文窗口无法表达，也无法在模型切换时生效。
- 默认窗口停留在 8k 级别，明显小于现代 272k / 1M 上下文模型。

## 2. Codex 参考

本地审计的 Codex 比较检出中，相关默认值为：

- 未知模型 fallback metadata：`context_window = 272_000`
- `effective_context_window_percent = 95`
- `auto_compact_token_limit` 省略时从 context window 派生为 90%
- 工具输出默认截断预算：`10000` tokens
- 部分模型声明 `max_context_window = 1_000_000`

## 3. 当前设计

上下文预算从"全局 provider 初始化值"改为"每轮按实际选中模型解析"：

1. `ModelCapabilities` 增加模型窗口元数据：
   - `context_window`
   - `max_context_window`
   - `effective_context_window_percent`
   - `auto_compact_token_limit`
2. `ContextConfig` / `ContextBudget` 默认值提升到现代 fallback：
   - `max_tokens = 272000`
   - `reserved_tokens = 10000`
3. `build_context_budget()` 支持传入 `ModelCapabilities`：
   - 有模型窗口时使用模型窗口作为硬上限。
   - 默认按 `effective_context_window_percent` 派生响应/系统余量。
   - 默认按 90% context window 派生软压缩阈值。
4. `SessionRunner` 在每轮 resolve provider/model 之后创建本轮 `ContextBuilder`，并传给 `AgentLoop`。
5. 文档和测试同步更新，明确模型上下文在哪里声明与生效。

## 4. 实施清单

- [x] 扩展 `ModelCapabilities` 的窗口字段和解析 helper。
- [x] 扩展 `ContextBudget` 的自动压缩软阈值，并让 `ContextBuilder` 使用软阈值触发窗口化/摘要。
- [x] 修改 `build_context_budget()`，支持按模型能力派生预算。
- [x] 修改 `SessionRunner`，按每轮选定模型创建上下文 builder。
- [x] 提升配置默认值并更新文档。
- [x] 增加/更新单元测试。
- [x] 运行聚焦测试集。

## 5. 默认模型 compact（2026-11-05）

应用默认先调用当前会话选中的 provider/model 生成滚动摘要，失败后再使用原有
消息窗口、token 裁剪和截取式摘要。无需指定额外模型或变更生产配置即可启用。
直接手工构造 `ContextBudget` 的同步 API 仍保持裁剪行为；应用通过
`build_context_budget(ContextConfig(...))` 启用模型 compact。

- 在 200 条历史消息窗口**之前**执行，避免旧内容先被丢弃。历史超过消息窗口或
  60,000 估算 token 时触发；按完整用户轮次压缩，通常保留最近四轮，超预算时再缩小。
- 成功摘要连同覆盖时间写入会话 metadata。后续轮次只处理未覆盖的历史并合并旧摘要，
  重启后可复用；不同会话、群聊连续性范围和回放/纯文本模式不混用摘要。
  同一次执行中的追加用户输入使用同一检查点边界，缩小保留窗口时不会拆开该次执行，
  避免重启后跳过尚未进入摘要的尾部消息。
- `AGENTS.md`、`SOUL.md`、`USER.md` 和系统前缀始终独立加载，不交给模型压缩。
  摘要作为有来源标识的历史数据注入，不获得系统指令权限。
- 一次长任务内部超过模型软预算时，也先尝试压缩旧历史及已完成的工具步骤。
  当前用户请求和最新完整工具调用/结果组保留；原始执行 transcript 不被覆盖。
- compact 不开放普通工具及 provider 内置工具，不发送图片或 reasoning 内容。
  输入分片受上下文预算和字符数限制，最多八片，整个操作默认限时 60 秒并响应停止请求。
- 超时、provider 异常、空白/拒绝/截断输出、意外工具调用、摘要过长或检查点写入失败，
  都不推进覆盖时间；保留上一份成功摘要并回退裁剪。清空会话会同时移除摘要并隔离旧回放。

```yaml
context:
  model_compaction_enabled: true
  compaction_timeout_seconds: 60
  compaction_summary_max_chars: 12000
  compaction_history_tokens: 60000
  compaction_keep_recent_turns: 4
  compaction_max_input_chars: 180000
```

首次摘要使用有界历史：默认最多读取 200 次执行的 transcript，以及 800 条原始消息。
不会全库追溯，也不能还原此前已截断的工具输出。原始聊天和执行记录继续保留，
摘要属于有损上下文投影，不能作为动作已执行的独立凭证。可通过
`context.compacted`、`context.compaction_failed`、`context.checkpoint_*_failed` 日志观察结果。
