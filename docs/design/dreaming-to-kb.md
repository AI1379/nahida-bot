# 做梦 → 知识节点写路径（dreaming-to-kb）设计提案

> 状态：**提案，等待 owner 拍板三个决策点**（见 §8）。拍板前不动代码。
> 日期：2026-08-25
> 来源：#22（A 线任务 A3）。主设计依据：`docs/design/knowledge-base.md` §4（职责边界）、
> `docs/a-line-memory-plan.md` 任务 A3。
> 现状基线：`v2` @ `b176136`。
>
> **定位修订（2026-08-25，见 `kb-direction.md` §2）**：本提案的地位从"补
> memory↔KB 统一的最后一环"升格为**新 KB 战略的地基**——`dreams` collection
> 不只是隔离/回滚措施，而是 auto-recall 主力语料（策展库）的第一个试点；
> wiki 大库降为显式 `kb_search` 的参考资料层。结构不变，但 §4.5 的放量节奏
> 与抽查设计因此更重要：它直接决定策展库的生长质量。

## 1. 背景与问题

consolidation（dreaming）目前只写 `memory_items` + `memory_candidates`
（`nahida_bot/agent/memory/consolidation.py`，无任何 KB 写入）。对话中获得的知识
永远进不了知识库：memory 与 KB 的统一（`knowledge-base.md` §5 的统一 Context Store
愿景）缺最后一环——**对话 → 长期记忆 → 可公共引用的知识**的晋升通道。

风险也因此集中：没有这条通道，agent 被迫把「世界事实」也塞进社交记忆
（scope 错位），或干脆丢失。

## 2. 现状核实（2026-08-25）

- dreaming 有两个触发点：每轮后的 `session_runner.py:3271`（在线）与
  `scheduler/service.py:633`（后台批量）。两者都只调
  `MemoryConsolidator.consolidate_turn`。
- `ExtractedMemory` 已带 `confidence`（LLM dream 输出 clamp 到 [0,1]，默认 0.65；
  规则抽取：显式「记住」0.95、群内称呼 0.9、偏好/决策 0.78、任务 0.68、
  assistant 决策 0.6）与 `audience`（current/global）。
- sensitivity 分类（`classify_sensitivity`）：`secret_like` / `private(explicit)` /
  `private(dream, PII 推断)` / `public(default)`，写入时已持久化。
- KB 插件有程序化写入口 `import_content(collection, source_id, content, ...)`
  （`plugins/knowledge_base/plugin.py:690`）：走 ingestion pipeline
  （切分、FTS、可选异步 embedding），按需自动建 collection。

## 3. 设计原则

来自 `knowledge-base.md` §4「应保持独立的能力」：**Memory consolidation/dreaming
保持独立**。写路径设计不得让 dreaming 与 KB 生命周期耦合——KB 插件禁用、collection
被删、embedding 关闭时，dreaming 本身必须完全不受影响。

由此定下架构基线：**promoter 是独立组件，消费已落地的 memory item，而不是挂在
consolidator 内部**。dreaming 继续只写 `memory_items`；「晋升为知识节点」是事后
的、可关闭的、可整体回滚的第二跳。

## 4. 提案

### 4.1 置信门槛（问题 1）

首版只放行**显式高置信**条目，全部条件 AND：

| 条件 | 阈值/白名单 | 理由 |
|---|---|---|
| `confidence` | ≥ **0.9** | 现有字段直接复用；规则显式「记住」0.95 天然过线，LLM dream 需自标 ≥0.9 |
| `kind` | ∈ {`fact`, `procedure`, `decision`} | preference 是个人偏好、task 瞬态、summary 是复述，都不构成公共知识 |
| `sensitivity` | = `public` 且 `sensitivity_source` = `default` | 被 PII/隐私标记推断过的内容（`dream`/`explicit` 来源）绝不进 KB |
| `scope_type` | = `global` | 见 §4.2 |
| `portable` | ≠ false | 与跨域召回同门槛 |

不新增字段；`confidence` 的语义已经是「该条记忆的可信度」，够用。

### 4.2 scope 纪律（问题 2）

- **个人观察留在 memory**：preference、群内称呼（alias，portable=false）、
  任何 person/chat/account scope 条目，永不被提升。它们已有正确的归宿。
- **KB 只收可公共引用的事实**：首版白名单仅 `scope=global ∧ public ∧ portable`
  的条目——即 dreaming 时已被判为「cross-chat bot-wide 适用」的那一小撮。
- **误写回滚**：每个晋升节点在 KB metadata 写
  `dream_promotion=true` + `promoted_from_item_id` + `promoted_at`。
  回滚 = 按 `dream_promotion` 标记反查并删除（提供 `/kb` 子命令或脚本，
  按 source_id 删除即可，不需要新机制）。

### 4.3 写入路径（问题 3）

**走 KB 插件的 ingestion pipeline**（`import_content`），不直写 DocumentStore：

- 享受切分、FTS 索引、层级 retrieval text 构建、异步 embedding——这些能力
  重造没有意义；
- 但调用方是独立的 `DreamPromoter`（建议落
  `nahida_bot/plugins/knowledge_base/promoter.py`，KB 插件自有组件），由
  **scheduler 周期任务**驱动（复用后台 dreaming 的节拍），扫描
  `memory_items` 中「过 §4.1 门槛且未被提升过」的条目；
- 解耦保障：promoter 通过 ledger（去重记录，见 §4.4）+ KB 插件可用性探测工作，
  KB 禁用/异常时静默跳过并告警一次，**不影响** consolidator 的任何行为——
  consolidator 代码零改动。

### 4.4 去重与冲突（问题 4）

- **同一 item 只提升一次**：promotion ledger（KB 插件 collection meta 或
  item metadata 二选一，建议前者——`kb_meta` 已有持久化机制），键 =
  `item_id` + 内容哈希。item 被更新（memory_update 生成新 id）视为新条目。
- **与既有 KB 文档去重**：写入前在目标 collection 内做 hybrid 检索（top-5），
  归一化内容相等（复用 `_normalize_for_dedupe` 语义）或命中同
  `promoted_from_item_id` 即跳过。
- **冲突不合并**：首版遇到「KB 已有相关但不同」的文档时**跳过并在观测里计数**，
  不做自动合并/改写——版本合并留给人工（`/kb` 导入与删除命令已存在）。
- **防自我强化环**：晋升节点的 retrieval text 会被 auto-recall 命中、再进入
  对话、再被 dream 提取。防线：dream 系统提示词追加一条——
  「上下文中标注为知识库召回（含 dream_promotion 来源）的内容不是新证据，
  不得据此再次生成记忆条目」。同时 ledger 保证内容哈希不变时不会二次写入。

### 4.5 可关闭与观测（问题 5）

- 配置（KB 插件 config 下，默认全关）：

  ```yaml
  knowledge_base:
    dream_promotion:
      enabled: false          # 总开关，默认关
      collection: dreams      # 专用目标 collection，不混入 wiki 库
      min_confidence: 0.9
      daily_limit: 5          # 放量节奏阀
  ```

- 专用 `dreams` collection 的意义：与 Teyvat 等 wiki 库物理隔离，便于统计
  召回占比、整体回滚、以及 `scripts/probe_kb_retrieval.py` 探针互不干扰。
- 观测指标：提升写入量 / 跳过原因分布（去重、冲突、超限）/
  `dreams` collection 在 auto-recall 结果中的占比。前两者 promoter 日志 +
  周报，后者检索侧已有 collection 元数据可直接聚合。
- embedding 成本：走 KB 既有 `embed_after_import` 异步通道，与手动导入同
  预算；默认关 = 零新增成本。开启后可用 `embedding.enabled=false` 组合出
  「只建 FTS 不 embed」的保守档。

## 5. 首版范围与明确不做

做：promoter + ledger + 配置 + 观测 + 回滚命令 + 测试。

不做（留待验证后）：

- 冲突自动合并 / supersede 链；
- 多 collection 路由（按主题分流）；
- 从 `conversation_turns` 直接抽取进 KB（必须先经过 memory_items 这一跳，
  保留 sensitivity/portability 审核面）；
- 完整的「记忆置信度衰变/提升」动态模型。

## 6. 评测与验收

- `scripts/probe_kb_retrieval.py` 两个探针理想答案排名**不得退化**（专用
  collection 天然隔离，回归照跑）；
- 单测：门槛过滤矩阵（confidence/kind/sensitivity/scope/portable 各维度）、
  ledger 幂等、KB 禁用时的降级行为、回滚命令；
- 集成测试：构造一条过门槛 item → promoter 写入 → `kb_search` 可命中 →
  回滚后消失。

## 7. 与 #26 / 后续的关系

- vec 索引拆库（#26）与本提案正交；promoter 的 embedding 走 KB 现有通道，
  拆库后无需改动。
- 若未来做 §5 统一 Context Store，promoter 的 ledger 与标记
  （`promoted_from_item_id`）就是 memory↔KB 双向链接的第一批真实数据。

## 8. 决策点（owner 拍板）

- [ ] **置信门槛具体值与字段来源**
      建议：0.9 + 复用现有 `confidence` 字段，不新增 schema。
- [ ] **首版 scope 白名单**
      建议：仅 `global ∧ public ∧ portable ∧ kind∈{fact,procedure,decision}`。
- [ ] **人工抽查窗口期与放量节奏**
      建议：默认 `daily_limit=5`；前两周逐条人工抽查（webui 按
      `dream_promotion` 过滤），零严重误写后提到 20/日，再观察两周后放开上限。
