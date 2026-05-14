# Model Spec Resolution and Role Tags

## 背景

内部任务，例如 memory dreaming、embedding、reranker、图片理解、摘要等，确实需要按用途选择模型。但当前 `model_routing.<task>.prefer_tags/fallback` 这层公开配置过早暴露了策略复杂度，并且和 provider model tags 的职责重叠。

更合适的当前设计是：

- `providers.*.models[].tags` 声明模型能力和用途。
- `ModelRouter` 只负责解析一个 model spec 字符串。
- 内部任务在代码中声明自己的默认 tag 和 fallback 行为。
- 各模块配置层只暴露一个可选 model spec 字符串；这个字符串可以是 tag，也可以是固定模型 ID。

## Model Spec 语义

一个 model spec 是单个字符串，以下形式等价地进入同一个解析器：

```text
embedding
vision
memory
siliconflow/Qwen/Qwen3.6-35B-A3B
Qwen/Qwen3.6-35B-A3B
```

解析顺序：

1. `provider_id/model_name`
2. 裸模型名
3. tag

模型名本身可以包含 `/`；只有第一段命中 provider id 时才按 `provider/model` 拆分。

## 推荐配置形态

```yaml
providers:
  siliconflow:
    type: openai-compatible
    models:
      - name: Qwen/Qwen3.6-35B-A3B
        tags: [primary, vision]
      - name: Qwen/Qwen3-Embedding-8B
        tags: [embedding]
      - name: cheap-chat
        tags: [memory, cheap]

memory:
  embedding:
    enabled: true
    model: ""        # 空 = 默认找 embedding tag
    # model: embedding
    # model: siliconflow/Qwen/Qwen3-Embedding-8B

scheduler:
  memory_dreaming_model: ""  # 空 = 默认找 memory tag

multimodal:
  image_fallback_model: ""   # 空 = 默认找 vision tag
```

## 内部任务默认行为

- `memory_dreaming`：显式 spec -> `memory` tag -> session provider fallback。
- `embedding`：显式 spec -> `embedding` tag -> disabled。
- `reranker`：显式 spec -> `reranker` tag -> disabled。
- `image_fallback`：显式 spec -> `vision` tag -> disabled。

这些 fallback 是代码里的任务语义，不作为公开 `model_routing` 配置项暴露。未来如果真实需要多 tag 优先级或用户可配置 fallback 链，再新增高级配置。

## 迁移原则

- 废弃 `model_routing` 配置项；保留字段兼容旧配置但不参与新解析。
- 废弃各模块的 `provider_id + model` 双字段配置；新配置使用单个 `model` spec 字符串。
- 旧字段若仍存在，只作为兼容输入拼成一个 spec，并在文档中标为 legacy。
- 新代码不得绕过 `ModelRouter.resolve()` 直接解析 tag 或 provider/model。

## TODO

- [x] 扩展 provider model config，支持 `tags`。
- [x] 新增统一 `ModelRouter.resolve(spec)`，支持 provider/model、裸模型名、tag。
- [x] 移除公开 `model_routing.<task>.prefer_tags/fallback` 策略层。
- [x] 新增代码级 `resolve_for_task(task, explicit, default_spec, fallback)` helper。
- [x] 将 memory embedding 配置收敛为单个 `model` spec。
- [x] 将 memory dreaming 配置收敛为单个 `memory_dreaming_model` spec。
- [x] 将 image fallback 配置收敛为单个 `image_fallback_model` spec。
- [x] 更新配置样例和文档，明确 tag 与固定模型 ID 都是 model spec 字符串。
