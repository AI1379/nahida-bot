# Nahida Bot 记忆系统设计草案

> 记录时间：2026-05-11
> 状态：设计中
> 相关文档：
>
> - [ROADMAP.md](../ROADMAP.md#phase-25---记忆模型与持久化)
> - [data-and-state.md](../architecture/data-and-state.md#记忆模型与存储抽象)
> - [agent-core-rebuild.md](agent-core-rebuild.md#5-memory-持久化完整-agent-run)
> - [agent-loop-optimization.md](agent-loop-optimization.md#五压缩机制对比)

## 1. 结论先行

Nahida Bot 不应直接引入 Microsoft GraphRAG、LlamaIndex GraphRAG 这类重型 RAG 框架作为核心记忆系统，也不应把记忆系统简单替换成独立向量数据库。更稳妥的路线是：

1. 保留 SQLite 作为权威状态库。
2. 新增结构化 `MemoryItem` 层，区分聊天历史、agent run、长期事实、偏好、任务经验和媒体描述。
3. 第一阶段使用 SQLite FTS5/BM25 + 明确 metadata 过滤，替代当前 jieba 关键词精确匹配。
4. 第二阶段引入 `sqlite-vec` 做本地向量索引，形成 FTS + vector 的 hybrid retrieval。
5. 第三阶段增加异步 memory consolidation，也就是类似 Anthropic "dreaming" / OpenAI memory generation 的后台整理任务。
6. Markdown 记忆文件作为人类可读投影和手工编辑入口，而不是唯一权威存储。
7. GraphRAG 的思想可以借鉴，但只在长期知识图谱和跨会话模式发现成熟后，增量实现轻量 property graph，不直接采用其完整包。

一句话：先做一个可审计、可追溯、低依赖的 local-first memory core，再逐步叠加语义检索、整理和图谱能力。

## 2. 当前现状

当前实现已经打通了最小记忆闭环：

- `ConversationTurn` 保存 `role/content/source/metadata/created_at`。
- SQLite 表包括 `sessions`、`memory_turns`、`memory_keywords`。
- `SQLiteMemoryStore.search()` 通过 jieba/英文分词后做关键词 OR 匹配。
- `SessionRunner._load_history()` 每轮加载最近 N 条 turn 作为短期历史。
- `metadata.message_context` 已用于稳定重建 channel、sender、chat 类型等 envelope facts。
- 多模态附件默认不持久化 base64 或临时 URL，只保留引用、缓存路径和描述。

主要不足：

- `memory_turns` 同时承担聊天历史、上下文恢复和长期记忆检索，语义边界不清。
- `memory_keywords` 只能精确匹配，近义词、别名、跨语言表达、隐含偏好召回差。
- 没有 BM25/FTS 排序，也没有向量召回。
- 没有明确的长期 memory item、证据链、置信度、过期时间、冲突处理。
- 插件 API 的 `memory_store()` 目前仍是 no-op。
- 当前 memory 不适合保存完整 agent transcript；tool call/result、reasoning、provider replay metadata 应进入独立 agent run/event 层。
- 没有后台 consolidation，长期运行后会积累重复、过时、低信号记忆。

## 3. 目标与非目标

### 3.1 目标

- **本地优先**：默认不依赖外部数据库服务，适合个人 bot、群聊 bot、私有部署。
- **可追溯**：长期记忆必须能指向来源 turn、agent run、文件或人工编辑记录。
- **可审计/可删除**：用户能查看、修改、禁用、删除具体记忆。
- **可分层召回**：短期最近历史、中期摘要、长期事实/偏好/经验分开管理。
- **可预算注入**：记忆进入 LLM context 前必须经过 token 预算和相关性筛选。
- **多作用域隔离**：支持 global、workspace、channel、chat、user、session、agent profile 等 scope。
- **渐进增强**：FTS、向量、reranker、图谱、dreaming 都应可插拔，不绑定单一 provider 或框架。

### 3.2 非目标

- 不在第一版实现完整 GraphRAG。
- 不在第一版引入独立向量数据库服务。
- 不把所有聊天历史自动提升为长期事实。
- 不把 LLM 生成的摘要视为不可质疑的事实。
- 不把 Markdown 文件作为唯一权威存储。

## 4. 回答四个架构问题

### 4.1 是否引入 GraphRAG

结论：**暂不引入完整 GraphRAG 包；借鉴 GraphRAG 思路，后续做轻量图谱层。**

GraphRAG 更适合这些场景：

- 查询目标是“整个语料库的主题、群体、趋势、关系”。
- 数据是大量文档或叙事文本，而不是零散对话 turn。
- 系统愿意支付较高的 LLM indexing、entity extraction、community summary 成本。
- 有明确的实体关系分析需求，例如“这些项目、联系人、任务之间有什么模式”。

Nahida Bot 当前更常见的记忆问题是：

- “用户之前说过什么偏好？”
- “这个群/这个 workspace 的约定是什么？”
- “刚才那张图/上次那个任务结果在哪里？”
- “某个错误码、文件名、命令、模型名的历史讨论是什么？”

这些问题用 hybrid retrieval + metadata filter + consolidation 更直接。完整 GraphRAG 会带来额外复杂度：

- LLM 抽实体和关系成本高。
- 图构建和 community summary 更新复杂。
- 对动态对话记忆来说，旧关系失效和冲突处理比静态文档库更难。
- Microsoft GraphRAG 包本身偏 pipeline 化，开发体验和本项目已有 agent core、workspace、plugin、memory 边界不自然。

建议采用的 GraphRAG 子集：

- `memory_entities`：可选保存人、项目、文件、模型、服务、频道、任务等实体。
- `memory_links`：保存 `item -> entity`、`entity -> entity`、`item -> item` 关系。
- `memory_clusters`：后续由 consolidation job 生成主题聚类摘要。
- 图谱只作为召回增强和审计结构，不作为第一阶段上下文主入口。

触发引入轻量图谱层的条件：

- 长期 memory item 超过约 10k 条。
- 用户开始频繁问跨会话、跨项目、跨人关系问题。
- hybrid retrieval 召回太碎，无法回答“整体主题/趋势/模式”。
- consolidation 已经稳定，有足够高质量实体和证据。

### 4.2 LanceDB / ChromaDB / sqlite-vec 如何选择

结论：**默认选 SQLite FTS5 + sqlite-vec；保留向 LanceDB 迁移的抽象；不优先选 ChromaDB。**

选择 `sqlite-vec` 的原因：

- 当前项目已经 SQLite-first，`DatabaseEngine`、repository、migration 都围绕 SQLite。
- 单文件部署简单，适合 Windows、本地 bot、低维护私有部署。
- 可和 `sessions`、`memory_turns`、`memory_items` 共享事务、备份和权限模型。
- metadata 过滤、FTS5、向量结果可以在同一个数据边界内融合。
- 不需要新增 server、Docker、端口、生命周期和运维文档。

风险：

- `sqlite-vec` 仍是 pre-v1，API/存储格式可能变化。
- 大规模 ANN、分布式、多租户吞吐不是强项。
- Windows 扩展加载、打包和 CI 需要验证。

缓解：

- 定义 `VectorIndex` 接口，不让业务层直接依赖 sqlite-vec。
- 第一期先落 FTS5；sqlite-vec 作为可选能力。
- embedding 表保存 `provider/model/dim/content_hash`，便于重建索引。
- 若 sqlite-vec 不可用，系统降级到 FTS5 + recency。

LanceDB 更适合这些情况：

- 记忆规模明显变大，需要更成熟的向量索引和多模态数据管理。
- 需要内置 hybrid search、reranker 工作流、列式数据分析。
- 未来图片、音频、文档 chunk 的 embedding 数量远超聊天记忆。

ChromaDB 更适合快速 RAG 原型，但不建议作为 Nahida Bot 默认后端：

- 它更像应用级向量库/服务，和当前 SQLite 状态层会形成第二套权威存储。
- 持久化、迁移、备份、权限、metadata schema 需要额外维护。
- 对本项目这种“聊天历史 + session 状态 + workspace + 插件权限”强绑定的数据，不如 SQLite 内聚。

推荐抽象：

```python
class VectorIndex(Protocol):
    async def upsert(self, records: list[VectorRecord]) -> None: ...
    async def delete(self, ids: list[str]) -> None: ...
    async def search(
        self,
        query_embedding: list[float],
        *,
        scope: MemoryScope,
        limit: int,
        filters: dict[str, object] | None = None,
    ) -> list[VectorHit]: ...
```

默认实现顺序：

1. `NoopVectorIndex`
2. `SQLiteVecIndex`
3. 可选 `LanceDBIndex`

### 4.3 Anthropic / OpenAI 的 agent memory 和 "dreaming" 是否有参考价值

结论：**非常有参考价值，但应复刻设计原则，不依赖托管 API。**

Anthropic Managed Agents memory/dreaming 的关键点：

- memory store 是文件化、可导出、可 API 管理的。
- 有 scoped permissions、audit logs、rollback/redaction。
- dreaming 是异步任务，读取 memory store 和历史 session transcripts。
- dreaming 通常输出新的 memory store 或结构化变更；Nahida Bot 当前先输出候选审计记录，再按自动模式应用到长期记忆。
- 目标是合并重复、替换过时/矛盾内容、发现新模式。

OpenAI Agents SDK memory 的关键点：

- 区分 conversation session memory 和长期 agent memory。
- memory generation 分为两步：
  1. 从 conversation 文件生成 summary 和 raw memory extract。
  2. consolidation agent 读取 raw memories，并在需要时打开 rollout summary，把模式整理进 `MEMORY.md` 和 `memory_summary.md`。
- 读取采用 progressive disclosure：先注入很小的 `memory_summary.md`，相关时再搜索索引和打开详细摘要。
- 支持 read-only memory、generate-only memory、不同 layout 隔离。

对 Nahida Bot 的落地建议：

- 当前实现中，session 持久化后只跑低成本规则抽取；LLM dreaming 交给现有 scheduler/cron 后台循环周期性执行，避免每轮对话增加延迟和模型成本。
- 输入为最近完成的 user/assistant turn，后续扩展为 agent runs、raw memory candidates、现有 durable memories。
- 输出写入 `memory_candidates` 作为审计记录，并默认自动提升为 `memory_items`；人工 review 是可选能力，不作为默认必经路径。
- 支持三种提交模式作为后续配置方向：
  - `auto_safe`：默认，仅抽取显式记忆、偏好、决策和待办等低风险内容。
  - `manual_review`：写入候选，等待命令或 UI 审核。
  - `auto_full`：管理员明确开启后允许 LLM extractor 更主动地更新长期记忆。
- 每条自动写入的记忆都必须带 provenance、confidence 和 candidate id。
- 对安全敏感内容、token、临时 URL、base64、私钥、认证头默认禁止进入长期记忆。

建议称呼：

- 内部模块名用 `memory_consolidation`，避免把核心架构绑定到 "dreaming" 这个产品化名字。
- 用户命令可以叫 `/memory compact`、`/memory review`、`/memory dream`，其中 dream 只是别名。

### 4.4 OpenClaw 每日 Markdown 记忆是否值得参考

结论：**值得参考，但应作为人类可读层，不作为唯一存储。**

OpenClaw 的优点：

- 纯 Markdown，用户可以直接读、编辑、diff、备份。
- `MEMORY.md` 存长期事实和偏好。
- `memory/YYYY-MM-DD.md` 存每天的运行上下文和观察。
- `DREAMS.md` 或类似文件存 consolidation/dreaming 的审查记录。
- 每日文件天然适合 append-only 和人工审计。

不足：

- 文件会膨胀，直接注入会浪费 context。
- 模型容易把每日流水误认为长期事实。
- 缺少强 schema 时，去重、冲突、权限、scope、删除都难。
- 多 channel / 多用户 / 多 workspace 时，仅靠目录约定容易混乱。
- 语义检索、证据链和行级引用需要额外索引。

Nahida Bot 应采用“DB 为权威 + Markdown 为投影”的混合方案：

```text
workspace/
  MEMORY.md                 # 由 durable memories 编译出的长期摘要，可人工编辑
  memory/
    2026-05-11.md           # 当日记忆日志，append-only
    DREAMS.md               # consolidation 审查摘要
    index.md                # 可选：主题索引
```

规则：

- `memory_items` 是权威数据。
- Markdown 文件是可读投影和人工编辑入口。
- 用户手工编辑 `MEMORY.md` 后，通过 import/sync 任务回写为 `source=human_edit` 的 memory item。
- 每日 markdown 不直接全量注入，只进入 FTS/vector 索引；bootstrap 只注入小摘要。
- 每条 Markdown 记忆尽量带稳定 ID 或来源链接，便于回写和去重。

## 5. 推荐记忆分层

### 5.1 Conversation History

已有 `memory_turns` 继续保留，用于：

- 最近对话窗口。
- 用户可读历史。
- 多模态引用恢复。
- message envelope 稳定重建。

原则：

- 不把 `memory_turns` 当长期事实库。
- 不对普通 turn 动态改写 envelope。
- tool transcript 不应只靠 `memory_turns` 表达。

### 5.2 Agent Run Log

配合 [agent-core-rebuild.md](agent-core-rebuild.md)，新增：

```text
agent_runs
  - run_id
  - session_id
  - status
  - started_at
  - completed_at
  - provider_id
  - model
  - trace_id

agent_events
  - run_id
  - event_index
  - event_type
  - payload_json
  - created_at
```

用途：

- 完整回放 tool call/result、reasoning summary、provider response metadata。
- 给 memory consolidation 提供高质量输入。
- 诊断 provider protocol 和上下文裁剪问题。

### 5.3 Raw Memory Candidate

每次对话或 run 结束后，可轻量生成候选：

```text
memory_candidates
  - candidate_id
  - scope_json
  - kind
  - content
  - evidence_json
  - confidence
  - status              # pending | accepted | rejected | superseded
  - created_at
```

来源：

- 用户显式说“记住”。
- 插件调用 `memory_store()`。
- LLM extraction pass。
- consolidation pass。
- 人工编辑 Markdown 后同步。

### 5.4 Durable Memory Item

长期记忆主表：

```text
memory_items
  - item_id
  - scope_type          # global | workspace | channel | chat | user | session | agent
  - scope_id
  - kind                # fact | preference | decision | task | procedure | warning | media | summary
  - title
  - content
  - status              # active | archived | rejected | superseded
  - confidence
  - importance
  - sensitivity         # public | private | secret_like
  - source              # user_explicit | llm_extract | plugin | consolidation | human_edit
  - evidence_json       # turn ids, run ids, file refs, message ids
  - supersedes_item_id
  - valid_from
  - valid_until
  - last_verified_at
  - created_at
  - updated_at
```

### 5.5 Summary Memory

中期摘要不应覆盖原始证据：

```text
memory_summaries
  - summary_id
  - scope_json
  - period_start
  - period_end
  - content
  - source_item_ids_json
  - token_estimate
  - created_at
```

用途：

- 每日/每周 rollup。
- bootstrap `memory_summary.md`。
- 给 context builder 提供稳定、短小、高信号材料。

## 6. 检索策略

### 6.1 Retrieval Cascade

推荐检索顺序：

1. **Recent window**：最近 N 条 conversation turns，不走长期检索。
2. **Pinned / explicit memory**：用户显式保存、workspace 固定规则、`MEMORY.md` 编译摘要。
3. **FTS/BM25**：精确召回名称、命令、文件、错误码、模型名。
4. **Vector search**：召回语义相近内容、别名、中文自然表达。
5. **Hybrid fusion**：用 RRF 或加权分数融合 FTS/vector。
6. **Rerank**：可选，用 LLM 或 reranker 模型对 top-k 做重排。
7. **Context packing**：按 token 预算注入摘要、关键事实和必要证据。

### 6.1.1 中文 BM25 处理

BM25 不需要在应用层手写。Phase 1 使用 SQLite FTS5 内置 `bm25()` 排序，但中文必须在入库和查询前做分词处理：

- 原始 `title/content` 保存在 `memory_items`。
- FTS 表只保存 `title_index/content_index`，内容为 jieba search-mode token 组成的空格分隔文本。
- 查询时使用同一套 tokenizer，把中文 query 转成安全的 FTS OR query。
- 返回结果展示原始 `memory_items.content`，不展示 index text。

这样可以避免 SQLite 默认 tokenizer 对无空格中文文本召回差的问题，同时保留 FTS5/BM25 的成熟排序实现。

### 6.2 为什么不能只用向量

- 文件名、群号、用户 ID、错误码、tool call id 需要精确匹配。
- 个人偏好和项目约束需要 scope filter。
- 多语言 embedding 可能把语义相近但事实不同的内容召回。
- 长期记忆有过期和冲突问题，不能只按相似度决定注入。

### 6.3 注入格式

进入 provider context 的记忆应稳定、短小、带来源：

```text
Relevant memory:
- [preference, user:u123, confidence=0.92] 用户偏好用中文讨论架构和实现取舍。 source: mem_abc
- [decision, workspace:nahida-bot] 记忆系统默认使用 SQLite-first，不引入独立向量服务。 source: mem_def

Treat memory as helpful context, not unquestionable truth. Prefer current user instructions and current files when they conflict.
```

## 7. 写入策略

### 7.1 允许写入的来源

- 用户显式指令：“记住……”
- 插件获得 memory write 权限后调用 `memory_store()`。
- conversation extraction 后台任务。
- consolidation 后台任务。
- 人工编辑 Markdown 后 sync。

### 7.2 写入前过滤

默认拒绝或降级：

- API key、token、cookie、认证头、私钥。
- 临时 URL、base64、敏感本地绝对路径。
- raw event 全量 JSON。
- provider reasoning 原文。
- 未经确认的第三方个人敏感信息。

### 7.3 冲突处理

同一 scope + kind + subject 出现冲突时：

- 不直接覆盖旧记忆。
- 新 item 指向 `supersedes_item_id` 或进入 `pending_conflict`。
- consolidation 生成候选变更，并保留证据。
- context 注入时优先最新已验证 active item。

## 8. Markdown 投影设计

### 8.1 MEMORY.md

用于长期高信号摘要：

```markdown
# Memory

<!-- generated: partial, editable -->

## User Preferences

- [mem_123] 用户偏好中文讨论架构和实现细节。

## Project Decisions

- [mem_456] nahida-bot memory 默认 SQLite-first，向量索引用 sqlite-vec 可选启用。
```

### 8.2 memory/YYYY-MM-DD.md

用于 append-only 日志：

```markdown
# 2026-05-11

## Session summaries

- [run_abc] 讨论并确定 memory 系统走 SQLite-first + FTS/vector hybrid。

## Raw candidates

- [cand_123] 用户关注 GraphRAG、sqlite-vec、dreaming、OpenClaw Markdown memory 的取舍。
```

### 8.3 DREAMS.md

用于 consolidation 审查：

```markdown
# Memory Consolidation Reviews

## 2026-05-11 run memcon_001

Accepted:
- mem_123: ...

Needs review:
- cand_789 conflicts with mem_456 because ...
```

## 9. 配置建议

新增配置段：

```yaml
memory:
  enabled: true
  backend: sqlite

  retrieval:
    recent_turns: 50
    fts_enabled: true
    vector_enabled: false
    vector_backend: sqlite-vec
    hybrid_fusion: rrf
    rerank_enabled: false
    max_injected_items: 8
    max_injected_tokens: 1200

  embedding:
    provider_id: ""
    model: ""
    dimensions: 1024
    batch_size: 16

  consolidation:
    enabled: true
    schedule: "0 4 * * *"
    dreaming_interval_seconds: 3600
    mode: auto_safe
    max_sessions_per_run: 50
    max_input_tokens: 64000

  markdown:
    enabled: true
    sync_mode: export_and_import
    daily_notes: true
    memory_file: "MEMORY.md"
    daily_dir: "memory"

  safety:
    redact_secrets: true
    default_retention_days: 365
    require_user_explicit_for_private_profile: true
```

## 10. 分阶段计划

### Phase 0：文档与边界

- [ ] 完成本设计讨论。
- [ ] 明确 memory scope、kind、sensitivity 枚举。
- [ ] 明确 `ConversationTurn`、`AgentRun`、`MemoryItem` 三者边界。

### Phase 0.5：Markdown Memory MVP

目标：先做一个 OpenClaw-like 的 Markdown 记忆闭环，让系统能真实读写和使用记忆，再决定哪些内容需要结构化、FTS、向量或 consolidation。

设计取舍：

- `MEMORY.md` 和 `memory/YYYY-MM-DD.md` 先作为可运行的轻量权威数据。
- Context 注入只读取 bounded `MEMORY.md` 和最近少量 daily notes，避免把流水全塞进 prompt。
- 写入采用 append-only，用户可直接编辑 Markdown。
- 每条自动写入的 bullet 带稳定 ID，后续可导入 `memory_items`。
- 安全过滤先做最小可用版本：拒绝 token、cookie、API key、私钥、base64、临时签名 URL。

任务清单：

- [x] 新增 Markdown memory helper。
- [x] workspace 初始化时创建 `MEMORY.md` 和 memory skill。
- [x] `ContextBuilder` 注入 bounded Markdown memory。
- [x] 内置 `memory_read` 工具：读取 `MEMORY.md` 和最近 N 天 daily notes，支持简单 query 过滤。
- [x] 内置 `memory_write` 工具：写入 daily、long-term 或 both。
- [x] 内置工具 manifest 暴露 memory 工具。
- [ ] 根据真实使用结果决定 Phase 1 的 schema 和检索指标。

### Phase 1：FTS 和 MemoryItem

- [x] 新增 `memory_items`、`memory_item_fts`、`memory_candidates`。
- [x] 实现 SQLite FTS5/BM25 检索。
- [x] 中文入库和查询前使用 jieba search-mode 预分词。
- [x] `memory_store()` 从 no-op 改为写入 structured memory item。
- [x] 新增 `/memory search`、`/memory list`、`/memory remember` 基础命令。
- [ ] 新增 `/memory forget` 或等价删除/归档 API。
- [x] SessionRunner 按当前用户消息检索少量长期记忆并注入预算内 context。

### Phase 2：Embedding 和 sqlite-vec

- [x] 增加 `EmbeddingProvider` 抽象。
- [x] 增加 `memory_embeddings` 表和 content hash。
- [x] 增加 `SQLiteVecIndex` 可选实现，默认仍可回退到 SQLite JSON embedding 扫描，避免把 sqlite-vec 变成强依赖。
- [x] 实现 FTS + vector hybrid fusion；`search_items_hybrid()` 默认 FTS，传入 embedding provider 后用 RRF 融合向量召回。
- [x] embedding id 使用 item/provider/model/content hash 派生的稳定 ID，方便重复 upsert 和可选向量索引同步。
- [ ] 增加召回质量测试集。（暂缓，先用真实交互观察效果）

### Phase 3：异步 Consolidation

- [x] 新增 conversation extraction：每轮 session 持久化后自动抽取显式记忆、偏好、决策和待办信号。
- [x] 新增 memory consolidation：抽取结果写入 `memory_candidates` 审计记录，并默认自动提升为 `memory_items`。
- [x] 人工 review 不作为必选路径；后续可在候选记录基础上补 `/memory review`。
- [x] 生成 `memory_summary.md`，并在 `MEMORY.md` 中维护带 marker 的自动投影区块，保留人工编辑内容。
- [x] 接入 LLM dreaming：有 provider 时读取近期 turn 和现有长期记忆，要求模型输出严格 JSON 的 `add/archive` 变更。
- [x] dreaming 输出经过 JSON 解析、安全过滤、去重、candidate 审计和 item id 校验后再自动应用；失败时回退到规则抽取。
- [x] 将 LLM dreaming 从每轮 session 后同步调用迁移到现有 scheduler/cron 后台循环，按 `memory_dream_last_turn_id` 增量处理 session。
- [ ] 后续增加 dream run 历史表和更细的手动触发/暂停命令。

### Phase 4：Agent Run/Event 集成

- [ ] 配合 agent core rebuild 新增 `agent_runs`、`agent_events`。
- [ ] consolidation 输入支持完整 run events。
- [ ] tool result、subagent summary、media description 可进入候选记忆。

### Phase 5：轻量图谱层

- [ ] 新增 `memory_entities`、`memory_links`。
- [ ] consolidation 抽取实体和关系。
- [ ] 支持关系型查询和主题聚类摘要。
- [ ] 评估是否需要外部图数据库或 GraphRAG 风格 global search。

## 11. 外部参考

- Microsoft GraphRAG：适合 whole-dataset reasoning、community reports 和 map-reduce global search，但对当前 bot memory 过重。参考：<https://microsoft.github.io/graphrag/query/global_search/>、<https://www.microsoft.com/en-us/research/project/graphrag/>
- sqlite-vec：SQLite 向量扩展，适合本地优先部署，但仍需注意 pre-v1 风险。参考：<https://github.com/asg017/sqlite-vec>
- LanceDB hybrid search：成熟 hybrid/vector/FTS/rerank 能力，适合作为后续外部后端候选。参考：<https://docs.lancedb.com/search/hybrid-search>
- Chroma：适合快速向量检索原型或独立服务，但不作为默认状态层。参考：<https://docs.trychroma.com/docs/run-chroma/clients>
- Anthropic Managed Agents memory/dreaming：文件化 memory store、审计、权限、异步 dream 输出新 store。参考：<https://claude.com/blog/claude-managed-agents-memory>、<https://platform.claude.com/docs/en/managed-agents/dreams>、<https://claude.com/blog/new-in-claude-managed-agents>
- OpenAI Agents SDK memory：progressive disclosure、conversation extraction、layout consolidation、`MEMORY.md` 和 `memory_summary.md`。参考：<https://openai.github.io/openai-agents-python/sandbox/memory/>、<https://openai.github.io/openai-agents-js/guides/sandbox-agents/memory/>
- OpenClaw memory：Markdown-first、`MEMORY.md`、每日 notes、`DREAMS.md`、memory tools。参考：<https://github.com/openclaw/openclaw/blob/main/docs/concepts/memory.md>
