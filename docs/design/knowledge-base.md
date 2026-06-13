# Knowledge Base 与统一上下文检索设计

> 最近审计：2026-06-13
> 状态：现有 KB 已可用，但检索模型需要与 Memory 方向合并重构
> 相关文档：
>
> - [memory-system.md](memory-system.md)
> - [memory-scoping.md](memory-scoping.md)
> - [data-and-state.md](../architecture/data-and-state.md)
> - [GitHub Issue #12：记忆系统完善与作用域机制](https://github.com/AI1379/nahida-bot/issues/12)

## 1. 结论

Knowledge Base 和长期 Memory 不应继续作为两套独立的检索系统演进。
它们应共享一个轻量、层级化的 Context Store，以及相同的索引、召回、
重排和上下文包装能力。

但这不意味着把 KB 与 Memory 粗暴合并成同一种记录。两者仍有不同的：

- 来源与证据；
- 作用域与访问权限；
- 生命周期和更新方式；
- 可信度与冲突策略；
- 默认上下文注入策略。

更准确的目标是：

> 建立一个统一的上下文存储与检索底座。文档知识、对话 episode、
> durable memory、人工笔记和工具结果是不同来源类型，而不是不同检索引擎。

第一阶段不引入完整 GraphRAG、外部向量数据库或递归 LLM 摘要树。优先解决
当前已经确认的结构丢失、分块失控、缺少来源路径和缺少检索评测等基础问题。

## 2. 当前实现

KB 是内置插件 `nahida_bot/plugins/knowledge_base/`，底层使用
`DocumentStoreManager` 和每 collection 独立的 SQLite 表：

```text
User                          Agent
  |                             |
  | WebUI / /kb import          | kb_search tool
  v                             v
KnowledgeBasePlugin ------> DocumentStoreManager
  |                             |
  | parsing + chunking          | FTS / vector / hybrid search
  v                             v
plugin_data              {collection}_docs
                         {collection}_doc_fts
                         {collection}_doc_embeddings
```

当前行为：

- Agent 通过 `kb_search` 工具按需检索，不会自动注入文档正文。
- 静态 PromptSupplement 只告诉 Agent 当前有哪些 collection，并提示必要时调用
  `kb_search`。
- 支持 FTS、vector 和 hybrid 检索；默认配置仍是 FTS-only。
- Markdown 按标题和段落分块，普通文本按段落分块。
- 每个 collection 使用独立物理表。
- 文本和 Markdown 无额外依赖。
- 安装 `document-import` extra 后，可通过 MarkItDown 导入 PDF、DOCX、
  PPTX、XLS/XLSX、HTML、CSV、JSON、XML、EPUB、MSG 和 Notebook。

```bash
uv sync --extra document-import
```

WebAPI 上传约束：

- 单文件兼容接口：
  `POST /api/kb/collections/{name}/import-file`
- 批量接口：
  `POST /api/kb/collections/{name}/import-files`
- 批量接口使用重复的 `files` multipart 字段。
- 单批最多 20 个文件，单文件最大 25 MiB。
- 批量导入逐文件返回结果，部分失败不会回滚成功文件。
- WebUI 支持多选和拖拽上传。

MarkItDown 通过 `convert_stream()` 处理上传内容，默认不启用第三方插件，
也不暴露 URL 抓取或任意本地路径转换。旧版 `.doc` 需要先转为 `.docx`。
扫描 PDF、图片型文档、复杂表格和强布局文档仍可能需要 OCR 或专门解析器。

## 3. 已确认的问题

### 3.1 工具触发不是可靠召回机制

当前 KB 正文只有在模型主动调用 `kb_search` 后才进入上下文。如果模型不知道
知识库中存在相关内容，或者没有意识到自己缺少信息，就不会调用工具。

Memory 的行为正好相反：每轮都会根据用户输入自动检索少量 durable memory
并注入上下文。两套系统在召回入口上不对称：

- Memory 容易过度注入或被脏记忆污染；
- KB 容易根本不触发；
- 两者使用不同的预算、排序和上下文包装策略。

### 3.2 分块后丢失文档身份和标题路径

当前 Markdown 解析主要保留局部标题。文件名、上级标题、章节路径和所属实体
没有成为 chunk 的一等检索字段。

例如原始结构：

```text
原神角色资料
└── 阿贝多
    └── 角色故事
        └── 角色故事 5
            └── 正文片段
```

分块后可能只剩：

```text
title: 角色故事5
content: ...
```

此时用户查询“阿贝多的角色故事”时，局部片段与“阿贝多”已经割裂。单纯为片段
生成 embedding 并不能恢复已经在预处理阶段丢失的结构。

### 3.3 Chunk 大小不是硬约束

现有算法遇到单个超长段落时会直接把整个段落作为一个 chunk，不会继续按句子
或字符窗口拆分。因此 `default_chunk_size` 实际只是段落聚合目标，不是上限。

2026-06-13 对本地 `Teyvat` 测试 collection 的审计结果：

| 指标 | 数值 |
|---|---:|
| 文件数 | 120 |
| Chunk 数 | 3981 |
| 平均 chunk 字符数 | 908.5 |
| 最大 chunk 字符数 | 25696 |
| 已持久化 embedding | 0 |

该数据只是压力测试，不应反过来决定通用 schema，但它清楚暴露了分块失控和
结构上下文丢失的问题。

### 3.4 Flat top-k 无法表达“先定位来源，再定位片段”

用户常见意图并不只是“找语义最相近的片段”，而是：

- 找某一本书中的某一段；
- 找某个角色的某篇故事；
- 找某个项目中的某个决策；
- 找一次对话中形成某项记忆的原始证据。

这些查询天然包含层级约束。让所有叶子 chunk 在同一个向量空间中直接竞争，
会丢失“文档 -> 章节 -> 片段”或“主题 -> episode -> evidence”的导航过程。

### 3.5 Memory 同样存在质量问题

Memory 与 KB 的问题不同，但根因相通：

- 当前 durable memory 主要是 flat item；
- 自动 consolidation 可能把普通回复误识别为 decision；
- scope 仍需要按 #12 和 `memory-scoping.md` 完成隔离；
- 缺少统一的 relevance threshold、rerank 和证据展开；
- Memory 与 KB 各自维护 embedding 和检索配置，策略容易漂移。

因此只优化 KB chunking，而不统一 Memory 与 KB 的检索基础设施，会继续制造
重复实现和不一致行为。

## 4. 新的职责边界

“世界知识”和“Bot 的社交记忆”可以帮助用户理解产品，但不适合作为底层系统
边界。更稳定的边界是来源、权限和生命周期。

| 维度 | 文档知识 | 社交/长期记忆 |
|---|---|---|
| 典型来源 | 文件、网页、人工导入 | 对话、工具结果、人工记忆 |
| 权威证据 | 原文、页码、章节、版本 | turn、session、用户陈述 |
| 默认作用域 | collection/workspace/global | person/chat/workspace/global |
| 更新方式 | 显式导入、重建、版本替换 | consolidation、冲突合并、遗忘 |
| 可信度 | 原始来源优先 | 显式陈述高于模型推断 |
| 默认读取 | 工具或按需检索 | 小预算自动召回，可继续展开 |
| 生命周期 | 版本化、显式删除 | 过期、归档、supersede |

应共享的能力：

- 节点和父子关系；
- provenance 与 source identity；
- FTS、embedding、hybrid 和 rerank；
- scope/collection filter；
- retrieval text 构建；
- 相邻节点和父级上下文展开；
- context packing 与 token budget；
- 召回评测与可观测性。

应保持独立的能力：

- 文档解析和格式转换；
- Memory consolidation/dreaming；
- 社交记忆的权限、冲突和遗忘；
- 文档版本与导入生命周期；
- KB 管理 API 和 Memory 管理 API。

## 5. 统一 Context Store

### 5.1 最小节点模型

层级结构必须是可选能力。扁平事实、短文档和单条记忆都是合法的根节点，
不要求人为生成复杂知识树。

```text
ContextNode
  node_id
  parent_id          # nullable
  root_id
  path               # nullable, human-readable
  node_type          # source/document/section/passage/episode/memory/summary
  source_type        # document/conversation/human/tool
  source_id
  scope_type
  scope_id
  collection
  title
  raw_text
  retrieval_text
  summary            # optional
  metadata
  provenance
  confidence
  status
  created_at
  updated_at
```

其中最重要的是区分：

- `raw_text`：用于展示、引用和最终交给模型的原始内容。
- `retrieval_text`：用于 FTS 和 embedding，可以包含来源名、完整标题路径、
  实体别名、局部摘要和正文。

例如：

```text
来源：原神角色资料 / 阿贝多
章节：角色故事 / 角色故事 5
主题：阿贝多的身世、黄金莱茵多特
正文：……
```

索引 `retrieval_text`，命中后返回 `raw_text` 和 provenance。这样不会为了提高
召回而污染原始内容，也不要求生成模型根据缺失信息猜测来源。

### 5.2 不同来源的映射

| 输入 | 建议层级 |
|---|---|
| 短文本/单条事实 | 一个根节点 |
| Markdown | 文档 -> 标题 -> 子标题 -> passage |
| PDF/DOCX | 文档 -> 页/章节 -> passage |
| 小说 | 书 -> 卷 -> 章 -> passage |
| Wiki/角色资料 | collection -> 实体 -> 属性/故事 -> passage |
| 对话 | session -> episode -> turn/evidence |
| Durable memory | topic/category -> memory item -> evidence |
| 工具结果 | run/task -> result summary -> raw artifact |

解析器只负责提取能够可靠识别的结构。无法识别时退化为单层文档和 passage，
不调用 LLM 强行生成树。

### 5.3 显式边优先于完整知识图谱

第一阶段只需要少量稳定关系：

```text
parent_of
derived_from
mentions
supersedes
adjacent_to
```

这些关系多数可以从标题结构、文档顺序、metadata 和 Memory provenance 直接
产生，不需要完整 GraphRAG 的实体抽取、community detection 和社区摘要。

## 6. 渐进式检索策略

统一架构不等于所有语料都运行最重的 pipeline。系统应根据语料 token 数、
结构和查询类型选择最低成本的有效策略。

| 语料情况 | 默认策略 |
|---|---|
| 很小，可放入上下文 | 整体读取，可结合 prompt caching |
| 少量短文档 | 文档级 FTS，命中后返回整篇 |
| 中型结构化文档 | 标题路径 + contextual chunk + hybrid retrieval |
| 大型语料库 | 两阶段层级检索 + rerank + 邻居展开 |
| 跨文档关系问题很多 | 可选实体索引或轻量图谱 |

阈值应基于 token 量、延迟预算和评测结果，而不是固定文件数量。

### 6.1 小知识库

对于总量可以安全放入上下文的知识库，不应为了“使用 RAG”而强行分块。
整体读取通常更简单，也避免检索漏召回。

对于少量短文档，可以先做 document-level search，命中后直接加载整篇。此时
父子表、embedding 和 rerank 都可以不启用。

### 6.2 中型知识库

默认采用：

1. 结构感知分段；
2. 为每个 passage 保留完整标题路径；
3. 将路径和来源写入 `retrieval_text`；
4. FTS + embedding hybrid retrieval；
5. 命中后附带父级标题和相邻 passage；
6. 按 token budget 包装上下文。

### 6.3 大型知识库

采用两阶段检索：

1. 在 document/entity/section 层定位候选父节点；
2. 只在候选父节点的子树中检索 passage；
3. 对候选 passage rerank；
4. 根据问题展开父级摘要、相邻节点或原始证据。

这更接近“先找到书，再找到书中的片段”，而不是让所有叶子 chunk 无条件竞争。

## 7. 推荐检索流程

```text
query
  -> scope / collection filter
  -> entity, source and path hints
  -> parent-level retrieval
  -> child passage retrieval
  -> FTS/vector rank fusion
  -> optional rerank
  -> parent/neighbor/evidence expansion
  -> context packing
  -> model or tool result
```

具体步骤：

1. 从 query 中提取可能的书名、角色、文件名、时间、路径和 scope。
2. 优先匹配明确来源和实体，避免精确约束被语义相似度稀释。
3. 搜索 document、entity 或 section 层的候选父节点。
4. 在候选子树内执行 FTS、vector 或 hybrid 检索。
5. 使用 RRF 或后续 reranker 合并候选。
6. 命中 passage 后展开完整路径、必要父级摘要和相邻片段。
7. 去重并按来源多样性、可信度和 token 预算选择最终内容。
8. 返回稳定引用，包括 collection、source、path 和 node id。

工具式 KB 和自动 Memory 注入都应调用这条统一 pipeline，只使用不同的策略：

- `kb_search` 可以允许较大候选集并支持多轮继续搜索；
- 自动 Memory 注入使用更严格阈值和更小预算；
- agent 可在结果不足时继续调用 `context_read(node_id)` 展开原文。

## 8. 分块与索引原则

### 8.1 结构边界优先

优先使用可靠结构：

- Markdown heading；
- HTML heading/article；
- PDF/DOCX 页、标题和段落；
- 表格、列表和代码块边界；
- 对话 episode 或时间窗口。

结构段落仍过长时，继续按句子或 token window 拆分。`chunk_size` 必须成为实际
上限附近的约束，不能让单段 20k 字符直接成为一个 chunk。

### 8.2 Contextual Retrieval

Contextual Retrieval 的核心不是简单增加一个 document summary，而是为每个
chunk 生成或构造 chunk-specific context，再同时用于 embedding 和 BM25。

在本项目中应优先使用无 LLM 的确定性上下文：

```text
source title + full heading path + aliases + local title + passage
```

只有缺少结构且召回评测证明有必要时，才启用可选 LLM contextualizer，为 chunk
生成简短定位说明。生成结果必须缓存并绑定 source version，不能在查询时重复生成。

### 8.3 Late Chunking

Late Chunking 使用长上下文 embedding 模型先编码完整文档，再在 token 表示上
做 chunk pooling，使每个 chunk embedding 保留文档上下文。

它适合：

- embedding provider 支持长上下文和 token-level 输出；
- 文档长度可控；
- 需要高质量上下文化 embedding。

它不应成为基础依赖，因为标准 OpenAI-compatible embeddings API 通常只返回
整体向量，不暴露 token embeddings。Context Store 应允许未来增加该索引后端，
但首版采用 contextual retrieval text 更通用。

### 8.4 多粒度索引

同一来源可以索引多个粒度：

- document/entity 节点：用于定位来源；
- section/summary 节点：用于主题和整体问题；
- passage 节点：用于精确回答和引用。

不要只存叶子 chunk，也不要只存摘要。摘要负责导航，原始 passage 负责证据。

## 9. 层级 Memory 与 Issue #12

Issue #12 的核心需求包括：

- chat/person/global 等作用域隔离；
- 提升 memory extraction 质量；
- 引入类似文件系统的层级组织和按需导航。

这些需求与 KB 的“文档身份和片段割裂”是同一个基础设施问题。文档与社交记忆
都需要：

- 上层目录/主题节点作为导航；
- 下层原始证据保持完整；
- 从粗粒度定位到细粒度展开；
- 用 scope 和 provenance 限制可见范围。

HORMA 的方向可以概括为“organize then retrieve”：先把经验组织为文件系统式
层级，再通过导航只加载最小充分上下文。它不要求把所有信息都转为知识图谱，
更接近本项目需要的 progressive disclosure。

Issue #12 评论中原先记录的 arXiv 链接 `2506.12757` 是错误的，该编号对应
Toeplitz 矩阵论文。HORMA 的正确论文是：

- **Organize then Retrieve: Hierarchical Memory Navigation for Efficient Agents**
- arXiv:2606.11680
- 提交日期：2026-06-10

相关的 H-MEM 和 HiMem 也支持相同方向：高层摘要或 topic 负责筛选，下层 episode
或 evidence 保留细节；但这些论文中的固定四层、逐轮 LLM 提取和专用训练导航器
不应原样复制到本项目。

## 10. GraphRAG 的位置

当前不引入完整 GraphRAG。

GraphRAG 更适合：

- 跨大量文档询问整体主题、群体和趋势；
- 复杂实体关系和多跳推理；
- 可以承担实体抽取、关系生成、聚类和社区摘要成本的离线语料。

当前主要问题是来源定位和层级上下文丢失。这个问题使用以下能力即可解决：

- 完整标题路径；
- contextual retrieval text；
- 父子节点；
- 两阶段检索；
- rerank；
- 邻居和证据展开。

只有在真实查询集中频繁出现跨文档关系问题，并且上述方案仍不足时，再增加：

- entity nodes；
- `mentions` 和少量 typed edges；
- 主题 cluster summary；
- 可选 global search。

图结构应是召回增强和审计结构，不应替代原文、provenance 和 scope。

## 11. 控制代码库复杂度

Nahida Bot 不应演变成通用 RAG 框架。核心只保留项目真正需要的最小能力：

```text
Context Store
├── node + hierarchy + provenance
├── FTS / vector index adapters
├── scope and collection filters
├── retrieval and context packing
└── evaluation hooks

Knowledge Base
├── file conversion and parsing
├── document/version lifecycle
└── kb_search and management API

Memory
├── conversation extraction
├── person/chat/workspace scope
├── conflict, expiry and forgetting
└── automatic injection policy
```

复杂能力应作为可选策略或 adapter：

- LLM contextualizer；
- reranker；
- Late Chunking backend；
- recursive summary tree；
- entity graph；
- external vector database。

基础核心不能依赖这些能力才能正常工作。默认 SQLite + FTS 必须保持可用，
embedding 不可用时应退化而不是失败。

## 12. 实施路线

### Phase 0：建立召回评测

- 为 Teyvat、短文档和 Memory 各建立小型 query/gold 数据集。
- 记录 recall@k、MRR、命中来源、最终注入字符数和查询延迟。
- 覆盖实体名、别名、章节路径、精确片段、主题问题和跨片段问题。
- 在没有评测前，不继续凭主观感觉调整 chunk size 或 embedding 模型。

### Phase 1：修复现有文档索引

- 长段落继续按句子/token window 拆分。
- Markdown parser 保留完整 heading path。
- 文件名、source id、路径和别名进入 `retrieval_text`。
- `raw_text` 与 `retrieval_text` 分离。
- 搜索结果返回稳定 provenance。
- 增加相邻 chunk 和父级上下文展开。

该阶段不要求迁移到全新表结构，可以先扩展现有 DocumentStore metadata 和索引。

### Phase 2：统一检索服务

- 抽出 Memory 与 KB 共用的 retrieval request/result 类型。
- 统一 FTS/vector/hybrid、RRF、threshold 和 context packing。
- KB 工具和 Memory 自动注入调用相同服务。
- 保留不同 scope、预算和触发策略。
- 修复 embedding 增量维护，避免进程重启后全量重复计算。

### Phase 3：层级 Context Store

- 引入 parent/root/path/node_type。
- 支持 document/section/passage 和 episode/memory/evidence。
- 实现父级定位后子树检索。
- 支持按 node id 继续展开父节点、子节点和邻居。
- 为现有 KB 和 Memory 提供可回滚迁移。

### Phase 4：按评测增加高级能力

按实际收益选择，而不是全部实现：

- reranker；
- 可选 LLM contextualizer；
- Late Chunking adapter；
- document/section summaries；
- 轻量 entity links；
- 大规模语料外部索引。

## 13. 暂不实施

- 完整 Microsoft GraphRAG/LlamaIndex GraphRAG pipeline；
- 每个 chunk 都调用 LLM 生成摘要；
- RAPTOR 式全语料递归聚类和重建；
- 固定四层的 Memory 树；
- 依赖外部向量数据库作为默认存储；
- 自动把全部 KB 正文注入每一轮；
- 用单一 embedding 相似度替代 scope、来源和精确匹配。

## 14. 参考资料

### Contextual Retrieval

- Anthropic, **Introducing Contextual Retrieval**:
  <https://www.anthropic.com/engineering/contextual-retrieval>
- 核心启发：传统 chunk 会丢失来源上下文；为每个 chunk 添加针对该 chunk 的
  文档上下文，并同时用于 BM25 与 embedding。Anthropic 报告 contextual
  embeddings + contextual BM25 可显著降低 top-k 漏召回，增加 rerank 后进一步
  改善。

### Tree-organized Retrieval

- Sarthi et al., **RAPTOR: Recursive Abstractive Processing for
  Tree-Organized Retrieval**:
  <https://arxiv.org/abs/2401.18059>
- 核心启发：同时索引叶子片段和不同抽象层级的递归摘要，允许查询整体主题和
  具体细节。
- 本项目取其“多粒度节点和层级导航”思想，不直接采用全量 LLM 聚类与递归摘要。

### Context-aware Embeddings

- Günther et al., **Late Chunking: Contextual Chunk Embeddings Using
  Long-Context Embedding Models**:
  <https://arxiv.org/abs/2409.04701>
- 核心启发：先在完整长文本上计算 token 表示，再对 chunk 做 pooling，使
  chunk embedding 保留全文上下文。
- 该方法需要 embedding backend 支持 token-level 表示，不适合作为默认 API
  假设。

### Hierarchical Agent Memory

- Hsu et al., **Organize then Retrieve: Hierarchical Memory Navigation for
  Efficient Agents (HORMA)**:
  <https://arxiv.org/abs/2606.11680>
- 核心启发：文件系统式组织、原始轨迹与摘要节点连接、导航式按需检索，以及
  最小充分上下文。

- Sun et al., **H-MEM: Hierarchical Memory for High-Efficiency Long-Term
  Reasoning in LLM Agents**:
  <https://aclanthology.org/2026.eacl-long.15/>
- 核心启发：按抽象层级逐层筛选，父节点保存到下层记忆的位置索引。

- Zhang et al., **HiMem: Hierarchical Long-Term Memory for LLM
  Long-Horizon Agents**:
  <https://arxiv.org/abs/2601.06377>
- 核心启发：区分 episode memory 与稳定 note memory，并通过层级关系连接具体
  事件和抽象知识。

### GraphRAG

- Microsoft GraphRAG:
  <https://microsoft.github.io/graphrag/>
- 核心启发：实体关系、community summary 和 global search 适合跨文档整体问题。
- 当前结论：不作为近期基础架构，只保留未来可选的轻量实体关系层。

## 15. 最终原则

1. 先保留结构，再讨论 embedding。
2. 先定位来源，再定位片段。
3. 摘要用于导航，原文用于证据。
4. 小语料优先整体读取，不为使用 RAG 而使用 RAG。
5. KB 与 Memory 共享检索基础设施，但不共享权限和生命周期策略。
6. Graph 是可选增强，不是默认数据模型。
7. 所有高级策略必须由评测证明收益。
