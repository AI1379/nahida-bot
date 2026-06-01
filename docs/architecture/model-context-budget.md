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

`D:/Projects/codex` 中相关默认值：

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
