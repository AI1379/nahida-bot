# KB 方向与边界决策记录

> 状态：**已拍板（2026-08-25，owner 确认）**。本文件是 #22 后续讨论 + #49 复盘 +
> #26 重构讨论的结论落档，作为后续 KB 侧工作的约束依据。
> 涉及：#22（tracking）、#26（KB 存储分离）、#49（检索质量，已修复）、
> `dreaming-to-kb.md`（A3 提案，仍待拍板三个决策点）。

## 1. 两条边界规则（宪法级）

1. **通用检索能力不进本体**：凡是"作为通用检索能力、其他项目也会想用"的东西
   （图引擎、通用 reranker 平台、为复用而复用的抽象），一律不进本仓库——
   要么不做，要么独立仓库（先例：实体共现图走独立 MCP server 仓库）。
2. **投资判据**：每个 KB 侧改动必须能回答"让她哪一类对话变好了？能用 eval 集
   里的哪条查询证明？"答不上来的不做。eval 集是唯一的裁判（见 §4）。

## 2. KB 定位修订：从"wiki 镜像"到"她的教科书"

#49 的复盘结论：三个执行层错误（全量镜像 wiki 而非按问题分布策展、无评测回路、
维护责任全在人）。据此调整定位：

- **wiki 大库降为参考资料层**：只在 agent 显式 `kb_search` 深挖时使用，不作为
  auto-recall 的主力语料。
- **auto-recall 主力让给小型策展库**：首要生产路径是做梦通道（A3，
  `dreams` collection——AI 产出的高置信对话知识）；她的 workspace 蒸馏物
  （设定卡片等）用现有工具写、值得入库的走同一道闸，不建新路径。
- **KB 与 memory 的区分是策略层不是管道层**：检索底座已统一
  （RetrievalService / hybrid RRF / retrieve_fused 多源融合）；长期保留的区分
  是权限（scope/sensitivity/portable）、权威（外部事实 vs AI 断言）、
  生命周期（版本化 vs 遗忘）。两套存储不合并（§5 统一 Context Store 无限期搁置）。

## 3. 向量引擎决策：留 sqlite-vec，换库条件明文化

**决定（2026-08-25）：继续 sqlite-vec（当前 0.1.9），本轮不引入 LanceDB /
ChromaDB 等专用向量库。**

理由：量级（1.4 万向量）下暴力 KNN 已是毫秒级，专用库优势不咬合；#49 六个根因
全在胶水层不在引擎；单人维护的部署现实（sqlite-vec 是 160KB C 扩展，
LanceDB 拖 pyarrow 重依赖）；"JSON embedding 为权威源、索引可零 API 成本重建"
的安全网建在现 substrate 上。ChromaDB 直接排除：依赖重、版本间 API 不稳、
"库拥有数据"模型与零成本纪律相抵触。

**换库触发条件（命中任何一条再评估，后继者优先 LanceDB）**：

1. KB 语料向百万级向量增长，或探针计时显示 KNN 延迟实际可感；
2. 实体图/独立检索仓库立项——那个仓库自己选引擎，本体不受牵连；
3. sqlite-vec 停滞（需要的修复/特性迟迟不落地）；
4. 出现必须库内原生 hybrid 的需求（现有 FTS5+领域词表+别名层是资产，别处无）。

工程约束：所有向量访问继续收在 `SQLiteVecIndex` / `DocumentStoreManager` 接口
后面，将来换引擎是收敛的适配器改动。

## 4. eval 集先行

从生产聊天记录抽取她真实被问过的知识类问题，构建 ≥20 条金标查询（查询 +
理想命中），进 `scripts/probe_kb_retrieval.py` 回归框架。作用：验证 #49 修复的
真实命中率、给 A3 放量节奏提供依据、充当 #26 迁移的回归网、裁决未来一切
KB 侧投资（§1.2 的裁判）。

## 5. #26 重构：KB 存储整体分离（取代原 issue 设计）

核心不是"拆 vec 表"，而是 **KB 存储与 bot 核心存储分开**：

- KB 全部（docs / FTS / embedding JSON / vec0 / vec_map）按 collection 一库一文件
  （`data/kb/{collection}.db`）；原设计的"map 表留主库 + 双连接"作废——map 与
  docs 同文件后 join 全本地。
- bot 核心（turns / memory_items / memory_embeddings / identity / chat_metadata /
  plugin_data）留主库，瘦身后 ~0.3GB，备份即备份"她的记忆与身份"。
- `metric=cosine` 随 vec0 重建修复（0.1.9 支持）。
- 迁移：从主库 `{collection}_docs` + JSON embedding 直接构建新库文件，
  FTS 以当前分词器重建，零 API 成本；eval 探针前后对照为验收。
- 回退：`knowledge_base.storage_dir` 为空时保持现状（表在主库），过渡期保留。

## 6. 执行顺序

```
① 本落档（规则成文）
② eval 集（生产快照抽取 + 金标集成 + 基线报告）
③ #26 KB 存储分离（含 metric 修复 + 迁移脚本 + 快照演练）
④ A3 阶段二（DreamPromoter；仍待 owner 拍板三决策点）
⑤ 其余按住：A2 等 SessionRunner 拆刀；reranker/查询改写等 eval 证据；
   图引擎在独立仓库轨道
```
