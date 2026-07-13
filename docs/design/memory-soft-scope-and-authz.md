# 记忆软 scope（薄敏感 tag）与动作授权闸（Phase A）

> 记录时间：2026-06-29 · 更新：2026-06-29（实施完成）
> 状态：**已实施（v2 分支，`memory.retrieval.soft_scope` 默认关）**。2026-07-13 增加
> `metadata.portable=false` 轻量边界：不敏感但仅在原上下文成立的 public 记忆不参与跨 scope 召回。本文是 [memory-architecture-exploration.md](memory-architecture-exploration.md)
> §8.3（薄敏感 tag）与 [person-identity-system.md](person-identity-system.md) §2.5/§8.4（Phase A
> 授权闸）的落地设计；两块均已落地并通过两轮代码评审。图引擎、独立库拆分、
> 意图触发 scope 扩张等大改动**继续推迟**。
>
> **已落地（v2）**：Phase A 授权闸 `e54f9a1`；A0 `sensitivity_source` 列 `a1aea06`；A1 回填脚本 + A3 dreaming 自动打标 `cdd5a64`；A2 软 scope 召回过滤 + A4 显式打标 `2fcea96`；评审修复 `96860d7`、`6909773`。启用 A2 前必须先跑 `scripts/migrate_memory_sensitivity.py`（A1 回填），否则存量 legacy `private` 不会软化。A2.1（当事人到场放松 `private`）仍 deferred。
> 关联 issue：#22（tracking）、#7（身份系统 Phase A）。注：#24（记忆失真）是独立轨道，不属本设计。

## 1. 背景与决策记录（2026-06-29）

exploration §9 的两个前置问题已由用户拍板：

1. **部署信任边界**：nahida 所在群里**会有不那么熟的人**（半公开）。但在"更像人"与"默认隔离"
   之间，用户**选择几乎全软 scope**，靠**薄敏感 tag**兜底——接受"保护强度 = 打标可靠性"的取舍。
2. **admin 定义**：新增独立的 `identity.admins`（account_key 列表），**与 `identity.people` 解耦**。

由此解除推迟的两块：

- **Piece A（记忆侧）**：几乎全软 scope + 薄敏感 tag（§8.3）。
- **Piece B（授权侧）**：`AuthorizationGate`（Phase A）。

**贯穿两块的硬约束（用户两次强调）**：admin/授权是**纯工具权限**，**不得以任何方式干扰 memory**。
memory 子系统不得 import 或分支依赖 admin 状态。详见 §4.4。

## 2. 范围

**做**：Piece A 的 sensitivity 默认翻转 + 召回过滤 + dreaming 自动打标 + 显式打标；Piece B 的
`identity.admins` + `AuthorizationGate` + 工具执行边界接入 + 解耦测试。

**不做（继续推迟）**：property-graph 引擎、memory 独立库拆分、意图触发 scope 扩张（§8 step1 余项）、
极简关系边。`memory_write` **不**纳入 admin 闸（§4.3）。

## 3. Piece A：几乎全软 scope + 薄敏感 tag

### 3.1 现状（代码核实）

- `memory_items.sensitivity` 列**已存在**（`db/engine.py` 迁移 008），值域 `public | private | secret_like`，
  **默认 `'private'`**（`agent/memory/models.py`）。
- 该字段被存、被查、被透传到检索结果（`agent/retrieval/adapters.py`），**但从未作为召回过滤条件**——
  `MemoryStoreRetrievalAdapter._cascade` 只按 `scope_type/scope_id` 过滤。
- dreaming/consolidation **完全不设 sensitivity**，所有记忆都是默认 `private`。

### 3.2 决策：翻转默认

当前默认 `private` 与"几乎全软"**直接冲突**（默认隔离 = 回到硬 scope）。故：

| 值 | 新语义 | 来源 |
|----|--------|------|
| `public`（**新默认**） | 软，可跨 scope 召回 | 绝大多数记忆 |
| `private` | 受限：不在来源 scope 之外召回，**除非来源当事人到场且在问** | dreaming 打 / 用户显式要求 |
| `secret_like` | 严格：仅来源 scope，**永不跨处** | dreaming 打 / 用户显式要求 |

**回填**：存量记忆全是默认 `private`。迁移脚本把存量 `private`（未被显式设过、即 consolidation 默认写出）
回填为新默认 `public`；只有 dreaming/用户**显式**标过的才保留受限。需在 `memory_items` 增一个
`sensitivity_source`（`default | dream | explicit`）以区分"默认 private"与"显式 private"，回填只动前者。
schema 默认值改为 `public`。

### 3.3 召回过滤规则

实现在 `_cascade` 之上、跨 scope 召回时：

- 召回集来源放宽为**全局池**（不再只 cascade 当前 chat scope），即"软"。
- 对每条候选，若 `sensitivity == secret_like` 且来源 scope ≠ 当前召回上下文 → **排除**。
- 若 `sensitivity == private` 且来源 scope ≠ 当前上下文 → **排除，除非**来源当事人（person/account）
  在当前会话中在场且是提问对象。
- `public` 不受限。

补充维度：`public` 只表示内容不敏感；若 item metadata 明确 `portable=false`，它仍只在自己的
主 scope 内召回。典型例子是群内外号：可在原群公开使用，但不应带到另一个群。SQLite 的
all-scopes public 查询同时过滤 sensitivity 与 portable，避免过滤发生在 LIMIT 之后造成结果饥饿。

当前上下文 = 当前 `chat_address`；来源 scope = 记忆的 `scope_type/scope_id`（已有）。当事人到场判断
复用 identity 解析（当前会话的 sender 是否命中记忆来源的 person/account）。

### 3.4 dreaming 自动打标

在 `agent/memory/consolidation.py` 的写入路径加一步**敏感分类**：

- 输入：候选记忆内容 + 来源 turn 上下文（是否私聊、是否含个人隐私/秘密/吐槽他人）。
- 规则初版（偏保守，宁可多标）：私聊来源且含个人隐私信号 → `private`；涉密/敏感 → `secret_like`；
  否则 `public`。可后续接 LLM 分类。
- 写入时设 `sensitivity_source='dream'`。

### 3.5 显式打标

聊天对象可在对话中要求："这个别在别处提" / "这是私下的" → 把对应记忆标 `private`/`secret_like`
（`sensitivity_source='explicit'`）。落地路径：`memory_write` 工具增加 `sensitivity` 参数 +
turn 上的轻量 NLU/关键词触发（"别告诉"/"私下"）。显式标记优先级 > dreaming。

### 3.6 残留风险与缓解

群里有不熟的人时，**保护强度 = 打标可靠性**。dreaming 漏标一条私密内容 → 它会被软召回进群。

缓解：dreaming 分类**偏保守**（私聊来源默认倾向 `private`）；显式打标易于使用；预留"私聊来源→群"
默认受限的可选旋钮（默认关，符合"几乎全软"；用户需要更强保护时开）。

### 3.7 代码触点

- `db/engine.py`：`memory_items.sensitivity` 默认 `public` + 新增 `sensitivity_source` 列（迁移）。
- `db/repositories/sqlite_memory_repo.py`：读写 `sensitivity_source`。
- `agent/memory/models.py` / `sqlite.py`：字段 + 默认值。
- `agent/retrieval/adapters.py` `_cascade`：跨 scope 召回的 sensitivity 过滤。
- `agent/memory/consolidation.py`：敏感分类 + 写 `sensitivity_source='dream'`。
- `plugins/api_bridge.py` / `plugins/builtin/commands.py`：`memory_write` 的 `sensitivity` 参数 + 显式打标。
- `scripts/migrate_memory_sensitivity.py`（新）：存量回填。

## 4. Piece B：AuthorizationGate（Phase A）

### 4.1 config

`IdentityConfig` 新增（与 `people` 解耦）：

```python
admins: list[IdentityAccountSeed] = Field(default_factory=list)
```

复用 `IdentityAccountSeed`（`channel` + `platform_account_id`）形态，与 `AccountKey.from_parts`
的派生方式一致。启动时解析成 admin account_key 集合。

### 4.2 模块

新 `nahida_bot/identity/authorization.py`：

```python
class AuthorizationGate:
    def __init__(self, admin_account_keys: frozenset[str]): ...
    def is_admin(self, sender_account_key: str) -> bool: ...
    def assert_authorized(self, action: str, sender_account_key: str) -> None:
        """Raise NotAuthorized if sender not in admin set."""
```

- 查 `sender_account_key ∈ admin_account_keys`，**只认账号**（平台已认证），不认 person、不查记忆。
- `identity.enabled=False` 时 gate 退化为"全通过"或"按 config admins"——默认行为零变化（见 §6 回滚）。

### 4.3 闸门工具清单

| 工具/动作 | 闸门 | 说明 |
|-----------|------|------|
| `exec`（任意 shell） | ✅ admin | 系统侧危险 |
| 跨会话 `message` | ✅ admin | 跨会话影响他人 |
| `workspace_write` | ✅ admin | 写文件系统 |
| 管理命令（`/identity link` 等） | ✅ admin | 管理动作 |
| **`memory_write`** | ❌ **不闸** | 属 memory 侧、写自己 scope；闸它违背"授权不干扰 memory" |
| 其余（读、检索、附件等） | ❌ 不闸 | — |

### 4.4 解耦保证（硬约束）

- `authorization.py` 是**独立模块**。
- `nahida_bot/agent/memory/*`、`nahida_bot/identity/policy.py`、`nahida_bot/agent/retrieval/*`
  **不得 import 或分支依赖 `identity.authorization` / admin 状态**。
- admin 只决定"某次工具**调用**准不准"，从不影响读/写/召回哪条记忆。
- **用 import-layer 测试锁死**：断言上述 memory/scope/retrieval 模块不依赖 authorization（静态扫 import 图）。

### 4.5 插入点

- 特权工具实现边界：`plugins/api_bridge.py` 的 `RealBotAPI` 方法（已有 `_permissions.check_*`，
  在此并列加 `authorization.assert_authorized`）。
- 管理命令：`plugins/builtin/commands.py`。
- 需要 `sender_account_key` 在工具执行时可取：从 `SessionContext` 经 tool-execution context 传入
  （若当前未贯通，补一条 plumbing；不污染 memory 路径）。

### 4.6 代码触点

- `core/config.py`：`IdentityConfig.admins`。
- `identity/authorization.py`（新）。
- `plugins/api_bridge.py` / `tool_executor.py` / `commands.py`：闸门接入 + sender_account_key plumbing。
- `app.py` / 装配处：构造 `AuthorizationGate` 并注入。

## 5. 实施顺序

1. **Piece B 先**（更独立、风险低、解锁安全洞）：config → authorization.py → 装配 → 工具边界接入 → 解耦测试。
2. **Piece A 后**（改动面大、依赖 schema 翻转 + 回填）：schema 默认 + `sensitivity_source` → 回填脚本 →
   召回过滤 → dreaming 自动打标 → 显式打标 → 评测。

两块互不阻塞，可分别评审/发布。Piece B 不触碰 Piece A 任何代码。

## 6. 配置示例

```yaml
identity:
  enabled: true
  people:
    - person_id: owner
      display_name: "浅色"
      accounts:
        - { channel: milky, platform_account_id: "10001", label: "owner" }
  admins:
    - { channel: milky, platform_account_id: "10001" }
```

## 7. 风险与回滚

- **Piece B**：`identity.enabled=false`（默认，identity 子系统关）时 gate 为 no-op，行为零变化；
  `enabled=true` 时**强制 fail-closed**——`admins` 为空则**所有**特权工具被拒（开启 identity 必须声明
  admin，否则 owner 立刻发现并修 config，安全且响亮，绝不静默放行）。回滚 = 关 `identity.enabled`。
- **Piece A**：sensitivity 默认翻转 + 回填**有数据影响**——回填脚本必须 dry-run + 备份
  （复用 `migrate_memory_scope.py` 的 inspect→backup→apply 模式）。回滚 = 恢复备份 + 还原默认值。
- **打标可靠性**：Piece A 上线后观察"群内是否出现本不该浮现的私聊内容"，作为打标质量的真实信号。
