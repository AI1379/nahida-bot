# 结构化社交记忆与跨 channel 身份

2026-10-31。基于记忆归属重构继续实现；本文描述本分支行为。部署状态需单独核对。

## 记忆组织

仍以 `memory_items` 为事实来源，通过 `MemoryService` 读写。内容类型、主体、来源和读取范围分别表达：

| 维度 | 内容 |
|---|---|
| kind | fact、preference、impression、task、decision 等；impression 是可修正的印象 |
| subject | bot、environment、account/person、chat、relationship、unknown |
| evidence | 原始 turn、发言账号、消息 ID、来源聊天与时间 |
| scope | chat/account/person/global 的读取范围 |
| sensitivity / portable | 是否私密、能否离开原上下文 |

`MemorySubject.category` 将 account/person 统一展示为人物；account 仍是人物在一个 channel 内的稳定标识。
关系主体携带 2–4 个明确参与者，参与者排序后产生稳定关系 ID。具体关系保存在记忆正文，
不进行关系传递推断。后台整理及显式写入都验证参与者和引用证据。

bot 与环境的公开、可迁移记忆可显式使用 global；人物和群聊的事实不会因此升级为全局记忆。
未知旧条目保持 unknown，不根据昵称、存储桶或当前读者猜测主体。共享 Markdown 仅用于导出。
`memory_read(category=...)` 可以分类浏览；省略 category 保留原有检索。
WebUI Memory 页面增加分类过滤及主体展示。过滤在 SQL LIMIT 之前执行。

## 跨 channel 关联

同一个 Person 新增绑定只能来自不同 channel；两个 Milky 账号不能通过声明合并。
channel 目前沿用平台名，尚未引入同平台多个 bot 实例的独立命名空间。

1. 用户明确指出自己在另一个 channel 的完整账号，模型调用 `identity_claim(propose)`。
2. 保存候选、当前用户原始声明、双方原有身份快照；候选不改变身份解析或私密记忆读取。
3. 用户切换到候选的目标账号，发送 `/identity confirm <candidate_id>`。
4. 校验真实 actor、原始命令、候选状态、身份快照和 channel 冲突，在同一事务内完成绑定、候选状态和审计。

模型工具只提供 propose/list/reject，不能代替用户调用 confirm。主动插话、cron 和子代理不能创建候选。
不自动向另一账号发送通知。`/identity pending` 查看自己的候选；任一涉及账号可 reject；
`/identity revoke` 解除当前账号绑定。管理员现有管理入口保留，同样遵守跨 channel 约束及原子审计。
双方已有不同 Person 时不自动合并整个人物库，须管理员明确处理冲突。身份已变化的旧候选不能继续确认。

管理员权限依旧查询 `authorization.admins` 中的认证账号。Person、候选、昵称和记忆均不授予权限；
现有 standard/relaxed/unsafe 运行策略不由本功能改变。

## 旧记忆与读取边界

确认后的私聊级联：Person → 当前 account → 同一 Person 的其他 channel account → 当前 chat → global。
旧 account 记忆无需批量搬迁，即可被确认后的另一个账号读取。附加 account 读取过滤
`secret_like` 与 `portable=false`；chat 范围的内容保持原范围。共享账号补充检索使用 FTS/字面匹配。
群聊的基础级联仍是 chat → global，人物出现在群里不会展开其私密库。
soft scope 只补充公开、可迁移内容；人物资料与关系摘要只检索当前相关参与者。

解绑后的新读取重新核对数据库映射，包括已经构造的 SessionContext。身份读取等待正在进行的写事务，
防止共享 SQLite 连接暴露尚未提交、最终回滚的关联。既往已展示到聊天历史中的内容不自动抹除。

## 情绪接入

现有 `SocialContinuity` 保持聊天内短期场面、心情和最多三个未表达想法，按原有 TTL 和半衰期衰减。
不增加独立的定时思考或额外 LLM 调用。在既有 appraisal 调用之前获取当前受众可见的：

```text
人物印象／人物关系／群聊背景／bot 自我记忆／环境记忆
  → 按类别轮流选取、限制条数和字符预算
  → 当前消息证据 + 长期记忆背景
  → 场面／心情／未表达想法
```

每条情绪记录仍必须引用消息 `evidence_ids`；长期记忆不能单独创造新的社会事件。
受记忆影响时还需引用可用的 `memory_ids`，并把主体、来源与原始证据带入快照。
引用不存在或不可见的记忆会使更新无效；后台读取失败降级为只有当前消息的判断。
情绪记录不会写回长期记忆，避免把模型推测循环固化成事实。

配置位于 `conversation_joiner.inner_thoughts`：`memory_enabled=true`、`max_memory_items=6`、
`max_memory_chars=2400`。整体情绪启用、私聊开关、冷却、模型与情绪衰减沿用现有配置。

## 数据兼容与验证

schema 31 增加身份候选表、主体分类索引及跨 channel 约束触发器。没有删除或批量改写旧记忆，
没有重算 embedding；旧 attribution v1 仍可读取。旧数据库中已有的同 channel 映射不在迁移时自动拆散，
但不能新增同类绑定；配置中的重复 channel 会明确报错，避免启动时部分写入。

回归覆盖候选隔离、反向候选去重、目标账号确认、拒绝与撤销、身份变化冲突、跨 channel 约束、
旧私密记忆承接、群聊隔离、审计失败回滚、未提交身份不可见、关系证据校验、分类查询和情绪引用。
