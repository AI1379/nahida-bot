# Knowledge Base 与统一上下文检索设计

> 最近审计：2026-06-14
> 状态：现有 KB 已可用，但检索模型需要与 Memory 方向合并重构
>
> 本次修订要点（2026-06-14）：
>
> - 标题树是 **containment（`parent_of`）**，不是 alias；alias 只是稀疏的横向等价（见 §5.3）。
> - **FTS + 结构 + 别名为默认检索主线**，向量是可重建的派生缓存（见 §6、§8.5、§11）。
> - embedding **不锁死**：版本化 + 内容哈希去重 + 双写切换（见 §8.5）。
> - 当前架构**未解决 §3.1 的触发问题**，触发需独立策略。
> - 与 #7/#10/#12：**统一设计、分阶段实现**；scope/provenance 等共享 schema 契约（见 §5.1、§12）。
> 相关文档：
>
> - [memory-system.md](memory-system.md)
> - [memory-scoping.md](memory-scoping.md)
> - [person-identity-system.md](person-identity-system.md) — scope/visibility/sensitivity 访问语义的 source of truth
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

需要强调：本节描述的是**触发**问题，而第 5–8 节的层级 Context Store、contextual
retrieval、两阶段检索解决的是**召回质量**问题。两者正交——统一 pipeline 让 Memory
和 KB 共用一条路径，但 pipeline 再好也不能让“模型没想到去搜”自动发生。因此触发
不对称需要独立策略，候选方向：

- 每轮做一次极小预算的 KB 自动召回（像 Memory 那样，但更严阈值、更小 top-k）；
- 改进 PromptSupplement，把可用 collection / 主题 / 实体名暴露给模型，让它知道“有东西可搜”；
- 一个轻量的“该不该查 KB”判别器，避免每轮无条件触发。

这些应作为统一 pipeline 的**触发层**单独设计，并纳入 Phase 0 评测。

**跨 scope 召回的意图触发**——触发层的一个具体、可独立设计的职责：用户常希望“在群聊
里让 bot 召回我和她私聊的记忆”，且由**自然语言**触发而非固定命令。这要求召回的访问
控制从静态配置（`group_person_memory` 开关 / slash 命令）升级为意图驱动：检测到这类
意图时，把本轮可读 scope 集合从默认 `{当前 chat, global}` 扩张到**请求者本人**
person/account scope 的非 sensitive 项。

访问语义（“本人”如何解析、scope cascade、visibility、sensitivity 分级）以
[person-identity-system.md](person-identity-system.md) 为准——身份解析见其 §4，
scope/visibility/sensitivity 见其 §5、§10；本节只负责**触发判断**（该不该扩张 capability），
不重复推导访问语义。两条安全约束：

- **意图只放松 recall 的默认收敛**，碰不到 `sensitivity=sensitive` 的硬隔离（其 §5.3：
  该级“grant 也不能越权”，意图亦不能越过）。
- **召回后的公开**由其 §10.2“私下召回、群里慎言”的回复约束把关——召回进上下文 ≠ 可在
  群里复述，类比人“想得起来但不当众说”。

意图判别器必须保守：假阳性 = 把不该跨 scope 的内容拉进群聊上下文 = 隐私泄露。该能力以
#7 为前置，#7 当前为规划中（其 §12），故现阶段未实现、默认走硬隔离 fallback。

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

两个必须在第一阶段就定死的 schema 约束（否则后续 issue 进来要反复迁移）：

- **`scope_type` 枚举一次列全**：`global` / `chat` / `person` / `account` /
  `collection` / `workspace`，即使 `person` / `account`（来自 #7）暂不实现。
  scope 是 KB、Memory、身份系统三方的公共契约，后加枚举值意味着历史数据回填。
- **`provenance` 必须双模**：既能表达文档来源（file / page / section / version），
  也能表达对话来源（turn / session / person / account）。当前实现偏文档来源，
  Memory 和 #7 进来后会不够用。

注意：#7 的身份层（`persons` / `person_accounts` / `account_observations`）**不是
ContextNode**，它是 scope 解析表，memory 节点通过 `scope_id = person_id` 引用它。
不要把身份关系误建为 memory 节点。

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
parent_of        # 标题/章节的纵向包含，文档树的主导关系
derived_from
mentions         # 稀疏的横向关联（实体共现）
alias_of         # 同一实体的不同称呼（阿贝多 = 黄金莱茵多特）
supersedes
adjacent_to
```

要区分两类**不能混用**的关系：

- **Containment（`parent_of`）**：纵向包含/导航。“角色故事 5”不是“阿贝多”的别名，
  而是嵌套在它之下的一节。这是文档树和 memory 树的**主导**关系，占绝大多数。
- **Equivalence（`alias_of` / 部分 `mentions`）**：横向等价或关联，**稀疏**，只用来
  解决“同物异名”导致词面召回失败，不能概括标题层级。

把多层标题塞进 alias 框架是类型错误：`parent_of` 承担结构，`alias_of` 只补在叶子
和实体上。

这些关系多数可以从标题结构、文档顺序、metadata 和 Memory provenance **确定性产出**，
不需要完整 GraphRAG 的 LLM 实体抽取、community detection 和社区摘要。这正是与
GraphRAG 的本质区别——后者从无结构文本用 LLM **造**结构，前者只是**捡起已有的结构**
（目录名、标题、字段、provenance）。守住两条红线即可避免滑向重 GraphRAG：

1. 索引时不调用 LLM 产出图结构；实体/别名来自源数据的结构或人工维护。
2. 去掉图，FTS 必须仍能工作——图是召回增强，不是承重墙。

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

更根本的分层按**检索手段的成本和锁死风险**排，而不是按语料规模：

| 层 | 内容 | 模型绑死风险 | 默认开关 |
|---|---|---|---|
| L0 | 完整标题路径 + 实体/别名 + FTS | 无 | **永远开** |
| L1 | dense vector（按 §8.5 版本化、内容哈希去重、可切换后端） | 低（派生缓存） | 评测证明 L0 漏召回才开 |
| L2 | cross-encoder reranker | 无（对任意召回源重排） | 收益够大才开 |

BM25 / FTS 本身就是那个廉价的第一阶段过滤器，已经解决了“不让 LLM 扫全文”的成本
问题；dense vector 只在 L0 漏掉转述类查询（用户用了文档里没有的同义表达）时补位。
**默认主线是 FTS + 结构 + 别名，向量是可选补盲区，不是检索引擎本身。**

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

### 8.5 Embedding 是可重建的派生缓存，不是真相

为避免 embedding 模型绑死（换模型 = 全量重建），核心原则是：

> **文本是唯一真相（source of truth）；向量是从 `retrieval_text` 派生的、可随时重建的缓存。**

落地做法：

- 向量表单独存 `(node_id, model_id, model_version, content_hash, vector)`，并明确
  标注“此表可由 node 表完整重建”。换模型只动这张表，node 表不受影响。
- **按 `content_hash` 去重**：内容没变的 chunk 复用既有向量，增量更新和模型切换都
  不会全量重算（顺带修复 Phase 2 列出的“重启后全量重复计算”问题）。
- **切换用双写**：新模型写入新 `model_version`，旧表继续服务读；后台按 hash 批量重算；
  评测通过后读切到新版本，旧表回收。全程对线上只读，切换是一个布尔位。
- **后端做成 adapter**：默认 SQLite + sqlite-vec，未来换专用向量库是 adapter 替换，
  不触碰核心 schema。

因此“embedding 不可用就退化到 FTS”不是口号，而是自然结果——向量从来不是核心数据。

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
embedding 不可用时应退化而不是失败。具体到向量：按 §8.5，embedding 表是派生缓存，
模型绑死风险用版本化 + 内容哈希去重 + 双写切换化解，核心数据流不经它；对于图，
守住 §5.3 的两条红线（索引时不调用 LLM、去掉图 FTS 仍能工作）。

## 12. 实施路线

> 范围说明：#10（KB）核心已落地（plugin / FTS / hybrid / document-import / WebAPI），
> 剩下的是检索质量重构；#12（Memory 提炼 + scoping）是地基；#7（person/account 身份，
> 详见 [person-identity-system.md](person-identity-system.md)）定义 scope/visibility/
> sensitivity 的访问语义，是 §3.1 意图触发跨 scope 召回的**前置依赖**。三者**统一设计、
> 分阶段实现**（共享 §5.1 的 scope/provenance 契约与统一检索底座），但 #7 因安全边界
> （LLM 不参与权威身份解析、跨 scope 召回须 identity 解析 + sensitivity 硬隔离把关）
> 单独成轨，不与 KB 检索重构混入同一交付。访问控制语义以 person-identity-system.md 为
> 准（群聊**默认**不注入 private person memory，非硬墙——按其 §5.2/§5.3 受控注入）；
> 本文件不重复推导 scope 语义。

### 地基：记忆作用域隔离（#12 scoping）

> 详见 [memory-scoping.md](memory-scoping.md)。这是 KB 与 person scope 的公共地基——
> store 层早已 scope-ready，本次落地的是应用层 chat/global 隔离。
> 状态：**Phase 0 / 1 / 2 / min-3 / 5 已完成并验证（2026-06-14）**；Phase 4（存量
> 迁移）工具已完成，生产服务器必须先审计再迁移。`scope_type` 目前是开放字符串，active 取值 `global` / `chat`；
> 完整枚举（`person` / `account` / `collection` / `workspace`）等身份系统与 KB 落地时再闭合。

- [x] Phase 0：新建 `nahida_bot/agent/memory/scope.py`（常量 + `resolve_scope_from_session` + `scope_for_kind`；typed→chat、legacy/空/非法→global、绝不抛异常）
- [x] Phase 1 写入：consolidator 构造默认 scope + 每调用覆盖 + per-kind 写入（preference/fact/task→chat，decision/procedure/warning/summary→global）；`_load_existing_items` / `_has_duplicate` / `project_workspace_memory` 按 scope 过滤（修复跨 chat 误判重/误归档）
- [x] Phase 1 调用点：`session_runner._consolidate_memory_after_turn`、`scheduler._dream_session`、`api_bridge.memory_store` 接 scope
- [x] Phase 2 读路径：`_load_relevant_memory(query, *, session_id="")` 与 `memory_search` 做 chat→global cascade（满额优先 chat、剩余补 global、item_id 去重）；legacy 字节级不变
- [x] Phase 3（最小）：`list_memory_items_all_scopes` + `embed_items_all_scopes`，两个 embedding 刷新调用点切全 scope
- [x] 验证：`tests/test_memory_scope.py`（解析单测 + 两 chat 隔离 + per-kind + dedup + cascade + embedding）；`uv run pytest` / `ruff` / `pyright` 全绿
- [x] Phase 4a（存量 global 数据迁移工具）：`scripts/migrate_memory_scope.py inspect/apply` 已完成；基于 `evidence_json` / `metadata_json` 中的 typed session、chat_address 或 message_context 生成计划；只有 `preference` / `fact` / `task` 且能唯一推断 chat scope 的条目可迁移；legacy session 或冲突证据保留 global / manual review；`apply` 支持 approved-only、`--apply-all-safe`、`--dry-run`、自动备份和迁移日志。
- [ ] Phase 4b（生产执行）：服务器上的历史 durable memory 不能直接丢弃，也不能长期全部留在 global；需要在生产库上执行 inspect → 人工审查 → dry-run → 备份 apply → 读路径验证。详见 [memory-scoping.md](memory-scoping.md) §5 Phase 4。
- [x] Phase 5：全局审计收尾——grep 确认无 buggy 硬编码（sqlite.py 默认参数已改用 scope.py 常量；sqlite_memory_repo.py 保留 2 处字面量以维持 db 层独立于 agent.memory；api_bridge 的 `"__global__"` 是 legacy turn-storage fallback，与 durable-item scope 无关）；`memory-system.md` §3.1/§9.0 已更新；补 dreaming 跨 session 不串归档的 e2e 测试；pyright/ruff/pytest 全绿

### Phase 0：轻量召回回归评测

- [x] 主线代码只保留开发脚本，不把评测语料、gold 数据和运行结果纳入产品运行时。
- [x] 本地测试集合放独立目录（当前约定 `kb-eval-data/`），通过 `.git/info/exclude`
  排除；不要把大语料或版权敏感数据写进 `.gitignore` 或仓库历史。
- [x] 为 Teyvat 建立本地 manifest 与 seed queries；Teyvat 全量导出只作为
  本地手动评测语料，主线脚本通过 manifest 引用原始路径。
- [ ] 为短文档和 Memory 建立小型 query/gold 数据集。
- [x] 记录 doc-level / heading-level recall@k、MRR 和命中来源。
- [ ] 记录最终注入字符数和查询延迟。
- [ ] 覆盖实体名、别名、章节路径、精确片段、主题问题和跨片段问题。
- [x] 已建立最低限度 Teyvat smoke eval；后续不再在没有评测样例的情况下调整 chunk size 或 embedding 模型。

当前本地约定：

- `scripts/prepare_kb_eval_teyvat.py`：从本地 Genshin Impact Wiki Markdown 导出目录
  生成本地 manifest 和 seed queries；导出路径通过 `--source` 或
  `KB_EVAL_TEYVAT_SOURCE` 指定，不写入仓库。
- `scripts/eval_kb_retrieval.py`：按 manifest 临时导入当前 KB，实现 doc-level 与
  heading-level recall/MRR smoke eval。

### Phase 1：重建 KB 文档索引

- [ ] 长段落继续按句子/token window 拆分。
- [ ] Markdown parser 保留完整 heading path。
- [ ] 文件名、source id、路径和别名进入 `retrieval_text`。
- [ ] `raw_text` 与 `retrieval_text` 分离。
- [ ] 搜索结果返回稳定 provenance。
- [ ] 增加相邻 chunk 和父级上下文展开。

当前 KB 仍处于早期可重建状态，collection 数据可以作为派生索引丢弃后重导入。
因此 Phase 1 不必为了兼容旧 KB 表结构而牺牲目标 schema；可以直接引入新的
KB 文档节点表/索引表，或替换现有 `DocumentStore` 的 KB 用法。唯一要求是导入
路径可重复、删除旧 collection 有明确工具或自动迁移策略。

**但 `scope_type` 枚举和 `provenance` 的双模结构必须在 Phase 1 就按 §5.1 定死**——
它们是 KB / Memory / #7 三方的公共契约，Phase 3 引入层级时不能回头改这两个字段，
否则触发历史数据回填。可以理解为：Phase 1 把目标 schema 的 scope/provenance 子集
先落到位（`parent_id` / `root_id` / `path` 可暂留空），层级化在 Phase 3 填充。

> 注：`scope_type` 的 `global`/`chat` 已在记忆地基（上方小节）中生效；KB 侧文档节点
> 尚未携带 scope/provenance，仍是扁平 chunk。

Memory 不能按 KB 的方式直接丢弃。后续统一 Context Store 时，`memory_items` /
`memory_candidates` / `memory_embeddings` 需要保留兼容读或提供一次性迁移：
durable memory 回填为 `source_type=conversation|human|tool` 的 ContextNode，保留
原 `item_id`、scope、kind、evidence、confidence 和 timestamps；embedding 仍视为
可重建缓存，迁移时可丢弃后按新 `retrieval_text` 重算。

### Phase 2：统一检索服务

- [x] 抽出 Memory 与 KB 共用的 retrieval request/result 类型。
- [x] 统一 FTS/vector/hybrid、RRF、threshold 和 context packing。
      （dispatch 经 `_select_mode` 统一；RRF 共用 `reciprocal_rank_fusion`；threshold
      为 `RetrievalRequest.min_score`，在 adapter 内、cascade 去重前过滤。**context packing
      暂不统一**：当前仅 Memory 自动注入一个消费者，KB 工具返回原始结果；等 §3.1 自动注入
      落地出现第二个消费者再抽共享 packing，避免单消费者提前抽象违反 §11。）
- [x] KB 工具和 Memory 自动注入调用相同服务。
- [x] 保留不同 scope、预算和触发策略。（scope 经 `RetrievalScope`、预算经 `limit`/`max_chars`；
      触发策略即 §3.1 的软跨 scope 召回，已单列并标注依赖 #7。）
- [x] 修复 embedding 增量维护，避免进程重启后全量重复计算。
      （KB：`embed_documents` 按 `(doc_id, content_hash)` 跳过已持久化项，返回
      `BackfillResult(added, needed)`，调用方以 `added == needed` 判定"已全部嵌入"——重启后
      needed=0 不再全量重算。Memory：`embed_items` / `embed_items_all_scopes` 同样按
      `(item_id, content_hash)` 跳过，定时刷新只嵌新项、不重嵌全量；返回仍为 `int`（新嵌数），
      因 memory 无"标记完成"语义、调用方只用于 debug 日志。）

> 注：Memory 侧的 FTS/vector/hybrid + RRF + scope cascade 已就位（见上方"地基"小节）；
> 本阶段重点是把它与 KB 的检索抽成同一服务。状态：**Phase 2 已完成并验证（2026-06-18）**；
> `uv run pytest` / `ruff` / `pyright` 全绿。

### Phase 3：层级 Context Store

- [ ] 引入 parent/root/path/node_type。
- [ ] 支持 document/section/passage 和 episode/memory/evidence。
- [ ] 实现父级定位后子树检索。
- [ ] 支持按 node id 继续展开父节点、子节点和邻居。
- [ ] 为现有 KB 和 Memory 提供可回滚迁移。

### Phase 4：按评测增加高级能力

按实际收益选择，而不是全部实现：

- [ ] reranker；
- [ ] 可选 LLM contextualizer；
- [ ] Late Chunking adapter；
- [ ] document/section summaries；
- [ ] 轻量 entity links；
- [ ] 大规模语料外部索引。

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
