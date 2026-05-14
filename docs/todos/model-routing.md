# Model Routing and Role Tags

## 背景

当前 provider 选择主要服务主对话模型。memory dreaming、embedding、reranker、图片理解、未来的工具规划和摘要任务都需要“按任务选择模型”，否则会出现两个问题：

- 低价值后台任务误用昂贵主模型。
- 每个子系统各自配置 provider/model，后续 fallback、限流、成本统计会分散。

## 目标

- 给模型配置增加 role/tag，例如 `primary`、`cheap`、`vision`、`embedding`、`reranker`、`memory`。
- 提供统一的 `ModelRouter` 或 `ProviderSelector`，按任务类型解析可用模型。
- 支持 fallback 链：优先使用指定 role 的模型，失败时按配置回退到 session provider 或 default provider。
- 让 memory dreaming、embedding、reranker、image fallback 共用同一套路由逻辑。

## 初始角色

- `primary`：主对话模型。
- `cheap`：低成本后台任务，例如 dreaming、摘要、轻量分类。
- `memory`：专门用于 memory dreaming/consolidation。
- `embedding`：文本向量模型。
- `reranker`：检索重排模型。
- `vision`：图片理解模型。

## 配置草案

```yaml
providers:
  deepseek:
    type: openai-compatible
    models:
      - name: deepseek-chat
        tags: [primary]
      - name: deepseek-chat-lite
        tags: [cheap, memory]

model_routing:
  memory_dreaming:
    prefer_tags: [memory, cheap]
    fallback: session
  embedding:
    prefer_tags: [embedding]
    fallback: none
  reranker:
    prefer_tags: [reranker, cheap]
    fallback: disabled
```

## 与当前实现的关系

当前 memory dreaming 已有专用配置：

```yaml
scheduler:
  memory_dreaming_provider_id: ""
  memory_dreaming_model: ""
```

这可以作为过渡方案。后续引入 model routing 后，保留显式 provider/model 作为最高优先级，然后再走 role/tag fallback。

## TODO

- [x] 扩展 provider model config，支持 `tags` / `roles`。
- [x] 新增统一 model routing 服务，避免各子系统重复解析 provider/model。
- [x] 将 memory dreaming 接入 `memory` -> `cheap` -> `session` fallback。
- [x] 将 embedding provider 选择改为 `embedding` role。
- [x] 为 reranker 预留 `reranker` role。
- [ ] 将 image fallback 的 provider/model 配置迁移到同一解析器。
- [x] 增加模型选择日志：任务类型、候选、命中、fallback 原因。
