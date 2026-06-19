# 人员身份与账号映射系统设计

> 记录时间：2026-06-07
> 最近修订：2026-06-19（架构调整：**信任边界移到"动作授权"，与"记忆身份"解耦**，据此降级软/硬身份、sensitivity/visibility 访问控制、OTP 自助链接；Phase 3 落地：identity-aware 记忆写入）
> 状态：Phase 0-3 已实现并验证；Phase A（动作授权闸）**按用户决定暂缓**——先完成记忆相关部分，授权闸之后做（记忆侧已与之解耦，见 §2.5）；Phase 4-5（管理命令、迁移）待实现
> 相关文档：
>
> - [memory-scoping.md](memory-scoping.md) — 当前 `chat` / `global` 记忆隔离设计
> - [chat-address-and-session-id.md](chat-address-and-session-id.md) — ChatAddress / SessionKey 边界
> - [memory-system.md](memory-system.md) — 长期记忆系统总体设计
> - [../architecture/runtime-flows.md](../architecture/runtime-flows.md) — 消息上下文与运行流程

## 1. 背景

`memory-scoping.md` 的 V1 方案用 `chat` scope 堵住了不同聊天之间的长期记忆串线，但它没有解决一个更底层的问题：Bot 看到的是聊天入口和平台账号，不知道哪些账号背后是同一个真实聊天对象。

典型场景：

1. 同一个人同时使用 QQ 私聊、Telegram 私聊和 OneBot 群聊身份与 bot 互动。
2. 群聊中多个人共享同一个 `ChatAddress`，但每条消息的发送者不同。
3. 用户在私聊里告诉 bot 的偏好，换到另一个平台后应能被识别为同一个人的偏好。
4. 用户在群里说“我喜欢 Python”，这条记忆应属于发言者，而不是属于整个群。
5. 管理员需要显式声明“这个 QQ 号、这个 Telegram 账号、这个群成员身份是同一个人”，并能审计和撤销。
6. **最危险的场景**：bot 把别人当成管理员，以管理员身份在服务器上执行危险操作（shell、文件、跨会话发消息、改配置）。相比之下，bot 在**记忆**里认错人本身无害——最坏只是回错话。

当前系统已有这些基础：

- `ChatAddress` 表示外部聊天地址，例如 `milky:private:10001`、`telegram:group:-100123`。
- `SessionKey` 表示一段对话历史，不等同于真实人。
- `InboundMessage.user_id` / `SenderContext.platform_user_id` 能表示一条消息的发送者平台账号。
- `MessageContext.sender_id` 已持久化到 `memory_turns.metadata_json.message_context`。
- `memory_items.scope_type` / `scope_id` 已是通用字符串，可以扩展新 scope 类型。

缺口是：没有一个统一的 `person` / `account` 身份层，把“聊天入口”“平台账号”“真实聊天对象”“长期记忆归属”解耦。但——见 §2.5——身份层的**安全**职责和**记忆**职责必须分开。

## 2. 目标与非目标

### 2.1 目标

- **信任边界落在动作授权**：所有危险操作（特权工具、管理命令、跨会话消息）只允许“声明的管理员账号”执行，这道闸独立于记忆（§2.5）。
- **记忆身份松耦合**：用 `person` / `account` scope 提供跨账号记忆连续性；记忆认错人无害，记忆被当作软上下文。
- **统一人员身份**：用稳定的 `person_id` 表示 bot 本地认识的一个人。
- **账号映射**：把多个平台账号映射到同一个 `person_id`（纯记忆连续性用途，零安全权重）。
- **群聊发言者识别**：群聊消息的个人事实归属发言者，而不是整个群。
- **会话解耦**：session/history 仍按 `SessionKey` 存储，长期个人记忆按 `person` 或 `account` scope 存储。
- **可审计可撤销**：管理员账号声明、解除、迁移都有明确记录和管理员入口。

### 2.2 非目标

- **不做运行时身份验证**（OTP / 自助链接）。管理员账号在部署时由管理员自己声明进配置；平台已认证账号，声明即充分可靠。
- **不维护软/硬身份**。bot 不做“账号 B 可能是 person A”的概率假设，也不需要验证闸把软身份升级为硬身份。
- **不把记忆当作访问控制边界**。记忆不做 sensitivity/visibility 硬过滤——那是错位（§2.5）。
- 不把不同账号的 `memory_turns` 聊天历史自动合并。跨平台最近历史检索可后续单独做。
- 不用显示名做稳定身份 key。显示名只能作为 observation 线索。
- 不要求所有账号必须链接。未链接账号仍应正常工作（走 chat scope）。

## 2.5 核心原则：记忆身份 ≠ 动作授权

本系统最危险的失败模式**不是记忆错乱**，而是 bot 把别人当成管理员、然后以管理员身份在服务器上执行危险操作（shell、文件、跨会话发消息、改配置……）。bot 在**记忆**里认错人本身无害——最坏只是回错话。因此整套设计围绕一个原则：

> **信任边界在“动作授权”上，不在“记忆身份”上。两者必须解耦。**

| | 记忆身份（identity-for-memory） | 动作授权（authorization-for-action） |
|---|---|---|
| 风险 | 低：认错只是回错话 | **高：冒充管理员执行危险操作** |
| 该多严 | 松、声明式、无验证 | 硬闸 |
| 依据 | `person` / `account` scope，可模糊 | sender 的**账号** ∈ 声明过的管理员账号集 |
| 认错的后果 | 无所谓 | 必须不可能发生 |

推论：

1. **记忆侧不需要验证机制。** 没有 OTP、没有软/硬身份之分、没有 sensitivity/visibility 访问控制。记忆被当作软上下文（system prompt 已声明“memory 是参考而非权威”），认错无害。
2. **多账号 link 零安全权重**，纯粹是记忆连续性的便利。link 不上的唯一代价是记忆稍差，无害。所以 link 可以很松，甚至自动/不做。
3. **安全由一道独立的硬闸保证**：每个危险动作前，查 `sender_account_key` 是否在配置声明的管理员账号集里。这道闸**不依赖记忆是否正确**——就算记忆全乱、person 全认错，陌生人也无法触发特权操作，因为闸查的是账号（平台已认证），不是“回忆出的身份”。
4. **为什么不需要运行时验证（OTP/self-link）？** 因为平台本身已认证账号：QQ 保证“这条消息来自账号 10001”。管理员只要在部署时把账号声明进配置（admin 物理控制自己的配置，无冒充窗口），就既充分又可靠。验证只在“不预先声明、却要运行时认定同一人”时才需要——而本系统不需要。

此原则取代了早期设计里的软/硬身份（原 §4.5）、sensitivity 分级与 visibility 访问控制（原 §5.3）、OTP 自助链接（原 §9.3）。相关章节已据此改写或降级。

## 3. 核心概念

### 3.1 ChatAddress

`ChatAddress` 是可发送、可接收的聊天入口。

```text
milky:private:10001
milky:group:20001
telegram:private:12345
telegram:group:-10012345
```

它回答的是“消息来自或要发到哪里”，不是“谁是这个人”。

### 3.2 SessionKey

`SessionKey` 是内部对话历史和运行状态 key。

```text
milky:group:20001
milky:group:20001:cron:daily
telegram:private:12345:abcd1234
```

它回答的是“这段上下文和运行状态属于哪条对话 lane”，不是“真实用户是谁”。

### 3.3 AccountKey

`AccountKey` 表示一个平台上可观察到的账号身份，也是**动作授权的原子单位**。

推荐格式：

```text
{channel}:user:{platform_user_id}
```

例子：

```text
milky:user:10001
telegram:user:12345
onebot:user:10001
```

约束：

- `channel` 必须是配置后的 channel 实例 id，而不是含糊的平台名。多 OneBot / 多 QQ 机器人账号部署时，应使用不同 channel id，例如 `onebot-main`、`onebot-alt`。
- `platform_user_id` 来自 `SenderContext.platform_user_id`，缺失时退回 `InboundMessage.user_id`。
- `AccountKey` 不包含群号。群内显示名、角色和成员状态进入 participant observation，而不是账号 key。

### 3.4 Person

`Person` 是 bot 本地维护的真实聊天对象。**注意：`Person` 只服务于记忆连续性，不承载任何安全权重**（§2.5）——授权认账号，不认 person。

```text
person_id: owner
display_name: Arendellian
accounts:
  - milky:user:10001
  - telegram:user:12345
```

`person_id` 是本地稳定 ID，可由管理员命名或系统生成：

```text
person_owner
person_01HZX...
```

一个 `Person` 可以关联多个 `AccountKey`。一个 `AccountKey` 同一时刻只能归属一个 active `Person`。

### 3.5 Participant Observation

`ParticipantObservation` 记录“某个账号在某个聊天里如何出现”。

例子：

```text
chat_address: milky:group:20001
account_key: milky:user:10001
display_name: 群名片(昵称)
role_tags: admin
first_seen_at: ...
last_seen_at: ...
```

它用于审计、显示和辅助人工链接，不作为自动身份判定依据。

## 4. 身份解析

每条入站消息都应解析出一个 `IdentityResolution`：

```python
@dataclass(frozen=True)
class IdentityResolution:
    chat_address: ChatAddress
    session_id: str
    sender_account_key: str
    person_id: str | None
    confidence: Literal["linked", "unlinked", "unknown"]
    source: Literal["manual_link", "self_verified", "config_seed", "none"]
```

解析流程：

```text
InboundMessage
  -> ChatAddress.from_inbound(...)
  -> sender_account_key = AccountKey.from_message(...)
  -> identity_store.resolve_account(sender_account_key)
  -> IdentityResolution
```

规则：

1. 私聊和群聊都使用同一套 sender account 解析。
2. 群聊的 `chat_address` 是群，`sender_account_key` 是发言者账号。
3. 未链接账号不会被归并到任何 person，但仍可用 `account` 或 `chat` scope 隔离个人记忆。
4. 显示名变化只更新 observation，不改变链接。
5. LLM 不参与身份解析。`account_key` 由平台消息**确定性导出**（无概率成分）；`person_id` 由管理员声明的 account→person 映射**查表**得到。没有“软身份假设”或“验证升级”——见 §2.5 与 §4.5。

### 4.5 软身份与硬身份（已弃用）

> **2026-06-19 起不再采用。** 原因见 §2.5：记忆不是信任边界，“bot 认错人”本身无害，所以
> 不需要把身份分成“软（概率假设）”和“硬（验证过）”两层，也不需要验证闸防止软身份静默
> 解锁 sensitive 记忆——因为根本没有 sensitive 记忆访问控制（见 §5.3）。
>
> 保留下来的事实：`account_key` 确定性导出（来自平台消息）；`person_id` 由管理员声明
> （config seed）。两者都直接、确定，没有“假设”层，也没有“静默升级”路径需要堵。

## 5. 记忆 Scope 模型

身份系统引入新的长期记忆 scope：

| scope_type | scope_id | 用途 |
|------------|----------|------|
| `global` | `__global__` | 全局共享知识、系统决策、通用流程 |
| `chat` | `{chat_key}` | 群/频道/私聊入口本身的约定、上下文、共享任务 |
| `person` | `{person_id}` | 同一个真实人的偏好、个人事实、长期任务 |
| `account` | `{account_key}` | 未链接账号的个人记忆，或平台账号特有事实 |

### 5.1 默认写入规则

| 内容类型 | 已解析 person | 未解析账号 | 说明 |
|----------|---------------|------------|------|
| 发送者自述偏好 | `person` | `account` | “我喜欢 Python”属于发言者 |
| 发送者个人事实 | `person` | `account` | “我住在上海”属于发言者 |
| 发送者个人待办 | `person` | `account` | “提醒我明天...”属于发言者 |
| 平台账号事实 | `account` | `account` | “我的 Telegram 用户名是...” |
| 群/频道规则 | `chat` | `chat` | “这个群里默认用中文” |
| 当前私聊局部约定 | `chat` 或 `person` | `chat` 或 `account` | 取决于是聊天入口约定还是人的偏好 |
| 项目/系统决策 | `global` | `global` | 与具体人无关 |

关键变化：`preference` / `fact` / `task` 不再无条件写入 `chat` scope。对群聊来说，个人记忆必须优先写入 `person` 或 `account`，否则会把某个群成员的个人信息变成整个群共享记忆。

### 5.2 读取 Cascade

读取由 `identity.policy.resolve_memory_read_scopes` 根据当前 turn 的身份解析结果生成 scope 序列。

私聊默认：

```text
1. person:{person_id}      # 如果已链接
2. account:{account_key}   # 平台账号特有事实或未链接 fallback
3. chat:{private_chat_key}
4. global:__global__
```

群聊默认：

```text
1. chat:{group_chat_key}
2. 若 sender 是声明的 Person：person:{sender_person_id} → account:{sender_account_key}
3. global:__global__
```

群聊规则由“sender 是否解析到声明的 Person”自动决定，**不需要** `group_person_memory` 配置旋钮：管理员是可信人，在其出现的群里也注入其个人 scope（这是管理员的 bot，记忆越权无害，见 §2.5）；访客 sender 不是声明 Person，只走 `chat → global`（即 V1 行为）。Identity 关闭或 sender 未链接时，cascade 退化为 V1 的 `chat → global`（legacy 仅 `global`），默认行为零变化。

> **意图触发的跨 scope 召回**（未来可选）：用户在群里说“还记得我们私下聊的吗”这类自然
> 语言触发，需要额外的意图判断层把本轮可读集扩张到请求者本人 person/account scope。由于
> 记忆是软上下文、不承载安全（§2.5），这里的扩张不涉及硬过滤，只影响召回的相关性。该触发层
> 与 KB 触发不对称问题统一处理，见 [knowledge-base.md §3.1](knowledge-base.md)。

### 5.3 可见性（已简化为软约束）

> **2026-06-19 起，记忆不再做 sensitivity / visibility 硬访问控制。** 记忆是软上下文
> （system prompt 已声明“memory 是参考而非权威”），认错人或越场合召回的代价很低；真正的
> 访问控制在动作授权层（§2.5、§10），不在记忆层。
>
> 唯一保留的软约束是 §10.2 的回复礼貌：在群聊里不要主动复述私密来源（“你上次在私聊
> 告诉我...”）。这是 UX 提示，不是安全边界。

旧设计中“每条 personal memory 带 `visibility` / `allowed_chats` / `sensitivity` 并硬过滤”的机制不再实现。`MemoryItem` 上的 `sensitivity` 字段保留为信息性元数据，不参与访问决策。

## 6. Consolidation 设计

### 6.1 LLM 不发明 scope ID

Extractor 只输出固定集合内的 `kind` + 内容；**scope 完全由系统从 kind + 当前 sender 身份推导**，LLM 无法指定 `person_id` / `account_key` / scope string：

```json
{
  "kind": "preference",
  "content": "用户偏好使用 Python。"
}
```

系统映射（`nahida_bot/identity/policy.py:resolve_memory_write_scope`）：

| kind | 身份 | → scope |
|------|------|---------|
| `preference` / `fact` / `task` | 已链接 person | `person:{person_id}` |
| `preference` / `fact` / `task` | 未链接（有 account_key） | `account:{account_key}` |
| `preference` / `fact` / `task` | identity 关闭 / 无 account | `chat:{chat_key}`（V1） |
| `decision` / `procedure` / `warning` / `summary` | 任意 | `global:__global__` |

旧设计里 extractor 输出受控 `subject`（`current_sender`/`current_chat`/`global`）的方案已简化：kind + identity 足以决定 scope，不需要 subject 字段，也不需要 LLM 选 subject。

### 6.2 写入流程

```text
current_session + InboundMessage
  -> IdentityResolution (person_id / sender_account_key)
  -> MemoryExtractor extracts content + kind（无 subject 字段）
  -> resolve_memory_write_scope(identity, kind) -> (scope_type, scope_id)
  -> MemoryStore.append_item(scope_type, scope_id, metadata)
```

（旧流程里的 `VisibilityPolicy assigns visibility` 步骤已移除——记忆不做访问控制，见 §5.3。）

### 6.3 Duplicate 检测

重复检测必须在目标 scope 内执行：

- `person:owner` 内检查 owner 的偏好是否已有重复。
- `account:telegram:user:12345` 内检查平台账号事实是否已有重复。
- `chat:milky:group:20001` 内检查群约定是否已有重复。

不同 person/account 之间不能互相判重，否则会误删别人的记忆。

## 7. 数据模型

### 7.1 新增表

```sql
CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_accounts (
    account_key TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'user',
    platform_account_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    verification TEXT NOT NULL DEFAULT 'manual',
    linked_by TEXT NOT NULL DEFAULT '',
    linked_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
);

CREATE INDEX IF NOT EXISTS idx_person_accounts_person
    ON person_accounts(person_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_person_accounts_unique_active
    ON person_accounts(channel, account_type, platform_account_id)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS account_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    chat_address TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role_tags_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_message_id TEXT NOT NULL DEFAULT '',
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_observations_account
    ON account_observations(account_key, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_account_observations_chat
    ON account_observations(chat_address, last_seen_at);
```

> `person_accounts.verification` 在新模型下取值收窄为 `config_seed`（启动 seed）或
> `manual_link`（管理员命令）；`self_verified` 不再产生（OTP 已弃用，见 §9.3）。
> 旧设计里的 `identity_link_requests`（OTP 一次性码）表**不建**。

### 7.2 Memory metadata

新增或规范化这些**审计性** metadata（不含访问控制字段）：

```json
{
  "subject_type": "person",
  "subject_id": "owner",
  "origin_account_key": "telegram:user:12345",
  "origin_chat_address": "telegram:private:12345",
  "origin_session_id": "telegram:private:12345"
}
```

`scope_type` / `scope_id` 仍是查询过滤的主字段，metadata 用于审计、迁移和 UI 展示。旧的 `visibility` / `allowed_chats` 字段不再写入（见 §5.3）。

## 8. API 与代码边界

### 8.1 新模块

| 文件 | 责任 |
|------|------|
| `nahida_bot/identity/models.py` | `AccountKey`、`Person`、`IdentityResolution` 数据模型 |
| `nahida_bot/identity/store.py` | 身份存储协议 |
| `nahida_bot/identity/sqlite.py` | SQLite 实现 |
| `nahida_bot/identity/resolver.py` | 从 inbound/context 解析 person/account（确定性） |
| `nahida_bot/identity/policy.py` | 记忆读取 cascade 与群聊注入策略（基于“sender 是否声明 Person”） |
| `nahida_bot/identity/authorization.py` | **动作授权闸**：`sender_account_key` 是否在管理员账号集（危险操作前调用） |
| `nahida_bot/agent/memory/identity_scope.py` | 根据 IdentityResolution 生成读写 scope |

### 8.2 SessionContext 扩展

建议扩展：

```python
@dataclass(slots=True, frozen=True)
class SessionContext:
    ...
    user_id: str = ""
    sender_display_name: str = ""
    sender_account_key: str = ""
    person_id: str | None = None
```

`user_id` 保持兼容，新的代码优先用 `sender_account_key` / `person_id`。

### 8.3 Router 接入点

`MessageRouter` 在设置 `current_session` 前执行身份解析：

```text
inbound -> ChatAddress -> IdentityResolution -> SessionContext
```

同时记录 `account_observations`：

```text
account_key + chat_address + display_name + role_tags + last_seen
```

### 8.4 Memory 与授权接入点

| 路径 | 改动 |
|------|------|
| `SessionRunner._load_relevant_memory` | 使用 identity-aware cascade（已实现，Phase 2） |
| `MemoryConsolidator.consolidate_turn` | 接收 `IdentityResolution`，按 §6 写 scope |
| `scheduler/service.py` | cron run 带创建者 account/person 和投递 chat |
| `plugins/api_bridge.py` | `memory_search/store` 走 identity cascade（读取已实现） |
| **`AuthorizationGate`** | **特权工具 / 管理命令 / 跨会话消息执行前查 `sender_account_key ∈ 管理员账号集`（Phase A）** |
| `gateway/routes` | WebUI 增加身份与账号管理页面 |

## 9. 管理与账号声明

### 9.1 管理员配置（声明式）

管理员账号在部署时声明进配置——这是**唯一的信任来源**，也是“无感接入”的实现：

```yaml
identity:
  enabled: true
  people:
    - person_id: owner
      display_name: Arendellian
      accounts:
        - channel: milky
          account_type: user
          platform_account_id: "10001"
          label: QQ
        - channel: telegram
          account_type: user
          platform_account_id: "12345"
          label: Telegram
```

启动时将配置 upsert 到 DB（`verification=config_seed`，只增改不删除）。声明即充分：平台已认证账号，admin 物理控制自己的配置，无冒充窗口（§2.5）。

### 9.2 管理命令

建议命令：

```text
/identity whoami
/identity accounts <person_id>
/identity account <account_key>
/identity observations [chat_address]
/identity link <person_id> <account_key>      # 可选：运行时补充声明（仅 admin）
/identity unlink <account_key>                # 仅 admin
```

权限：

- `link/unlink/observations` 默认仅 admin（且受 §8.4 授权闸保护）。
- `whoami/accounts` 可所有用户使用。
- **没有任何验证/OTP 命令**（见 §9.3）。

### 9.3 自助验证（不实现）

> **2026-06-19 起不再实现 OTP / 自助链接。** 管理员账号在部署时声明（§9.1），平台已认证账号，
> 无需运行时验证（见 §2.5）。`identity_link_requests` 表不建。若未来需要让非管理员用户自助
> 绑定多账号，再单独设计——但当前 personal-bot 模式不需要。

## 10. 群聊策略

群聊真正的风险不是“记忆泄漏”（管理员的 bot，记忆越权无害，见 §2.5），而是**群里的陌生人触发管理员动作**。因此群聊策略以**授权**为核心。

### 10.1 授权（硬约束）

1. 群里任何人发消息都不能默认触发特权工具 / 管理命令 / 跨会话消息。
2. 只有当 sender 是声明的管理员账号时，才放开管理员能力（§8.4 授权闸）。
3. 访客在群里只能用普通对话能力——即使 prompt injection 诱导，授权闸仍按账号拦截。

### 10.2 记忆（软约束）

1. 管理员在群里也召回其 person scope（无感连续性）。
2. 访客在群里只召回 `chat + global`。
3. 软提示：不在群里主动复述私密来源。这是 UX 礼貌，不是安全边界：

```text
When using identity-linked personal memory in a group chat, do not reveal
private origins unless the user explicitly asks. (This is a courtesy, not
a security control — the real boundary is the authorization gate.)
```

## 11. 迁移策略

### 11.1 从 chat-scoped personal memory 迁移

迁移脚本应支持 `inspect` / `apply`：

1. 扫描 `memory_items` 中 `scope_type="chat"` 的 `preference` / `fact` / `task`。
2. 从 `evidence_json` / `metadata_json` 读取 `origin_session_id`、`message_context.sender_id`、`origin_chat_address`。
3. 构造 `account_key`。
4. 如果 account 已链接 person，建议迁移到 `person:{person_id}`。
5. 如果 account 未链接，建议迁移到 `account:{account_key}`。
6. 如果无法确定 sender，保留 `chat` scope 并标记 review。
7. 迁移只调整 `scope_type` / `scope_id`，不涉及 visibility 标签（已弃用，见 §5.3）。

### 11.2 从 global-scoped personal memory 迁移

沿用 `memory-scoping.md` Phase 4 的思路，但目标 scope 改为：

```text
person/account > chat > global
```

只有证据足够确定“这条记忆属于哪个发送者”时才迁移到 person/account。无法确定时保留 global 并进入人工 review。

### 11.3 链接变更后的记忆处理

当 `account` 链接到 `person`：

- 不要求立即重写所有 `account` scoped memory。
- 读取时 cascade 同时包含 `person` 和 `account`。
- 后台 compaction 可逐步把长期稳定的 `account` memory 合并到 `person` scope。

当账号从 person 解绑：

- 不自动删除历史 person memory。
- 解绑后该 account 不再读取旧 person scope。
- 管理员可选择把该 account 来源的记忆迁出或归档。

## 12. 实施路线

### Phase 0：模型与文档

- [x] 新增 `AccountKey`、`IdentityResolution` 模型。（`nahida_bot/identity/models.py`，含 `Person` / `AccountLink` / `ParticipantObservation`。）
- [x] 明确 account key 格式和 channel instance id 约束。（`{channel}:user:{platform_user_id}`；channel 取自 typed ChatAddress，**不**回退到 platform 名，避免多实例 namespace 碰撞。）
- [x] 更新 memory scope 文档，声明 personal memory 不应默认写入 group chat scope。

### Phase 1：配置驱动身份映射

> 状态：**已实现并验证（2026-06-18）**；`uv run pytest` / `ruff` / `pyright` 全绿。整层 gated 在 `identity.enabled`（默认 `False`），关闭时 resolver no-op、`SessionContext` 身份字段为空，现有行为零变化。

- [x] 新增 identity 配置 seed。（`IdentityConfig` / `IdentityPersonSeed` / `IdentityAccountSeed`，启动时 upsert，`verification=config_seed`，只增改不删除。）
- [x] 新增 `persons`、`person_accounts`、`account_observations` migration。（migration 016。）
- [x] Router 解析 current sender account 并写入 `SessionContext`。（`IdentityResolver` 在 `_build_session_context` 解析，三个 handler 共用；同时记录 `account_observations`。）
- [x] `whoami` 命令显示当前 account/person。（`/identity whoami`，读 `current_session`。）

### Phase 2：identity-aware memory read

> 状态：**已实现并验证（2026-06-18）**；`uv run pytest` / `ruff` / `pyright` 全绿（仅 live/network 集成测试因沙箱无网络被跳过）。群聊规则现为“sender 是声明 Person 才注入其 scope，否则 chat → global”，由 resolver 结果自动决定，无需配置旋钮。

- [x] `SessionRunner._load_relevant_memory` 使用 identity-aware cascade。（`nahida_bot/identity/policy.py` 生成 scope 序列，`MemoryStoreRetrievalAdapter` 泛化为 N 级 cascade；`RetrievalRequest.scopes` 驱动。）
- [x] 私聊加载 person/account/chat/global。（policy：`person → account → chat → global`，按优先级填满 budget 并按 `result_id` 去重。）
- [x] 群聊：管理员 sender 注入 person/account scope，访客只 chat → global。
- [x] `api_bridge.memory_search` 支持相同策略。（同一 policy + budget-fill cascade。）

### Phase A：动作授权闸（暂缓）

> **按用户决定暂缓（2026-06-19）**：先完成记忆相关部分（Phase 3/4/5），授权闸之后做。记忆侧已与之解耦（§2.5）——两者都只读 `SessionContext.sender_account_key`/`person_id`，记忆用它选 scope，授权（将来）用它判权限，互不依赖，加闸是纯增量。当前权宜：LLM 自身判断充当半层；但 prompt injection 可绕过，授权闸仍是真正必要的。
>
> 审计结论（2026-06-19）：bot 现在对任何能发消息的人开放——`exec`（任意 shell）、`message`（跨会话/跨平台发消息）、`workspace_write`、`memory_write`、管理命令（`/reset` `/model` 等）均无 sender 级闸。`PermissionChecker` 只是插件 manifest 沙箱（问"插件有没有能力"，不问"发送者是谁"）。无 admin 账号集、无 `is_admin`。

- [ ] 新增 `nahida_bot/identity/authorization.py`：`is_admin(sender_account_key)` 查声明的管理员账号集。
- [ ] 把所有特权工具 / 管理命令 / 跨会话消息统一收口到这道闸。
- [ ] 测试：非管理员账号**无法**触发特权动作，即使记忆/person 解析出错或被 prompt injection 诱导。

### Phase 3：identity-aware memory write

> 状态：**已实现并验证（2026-06-19）**；`uv run pytest` / `ruff` / `pyright` 全绿。`nahida_bot/identity/policy.py` 新增 `resolve_memory_write_scope`：personal kind（preference/fact/task）→ `person`（已链接）/ `account`（未链接有 account_key）/ `chat`（identity 关闭，V1）；global kind → `global`。consolidator 接收 `person_id`/`sender_account_key`，按 turn 写入对应 scope；duplicate 检测复用既有 scope-aware `_has_duplicate`，无需改动。后台 dreaming 暂保持 V1（chat/global），identity-aware dreaming 留作 follow-up。

- [x] Consolidator 接收身份（`person_id`/`sender_account_key`）。
- [x] personal memory 写入 person（已链接）/ account（未链接）/ chat（identity 关闭）scope。
- [x] duplicate 检测按目标 scope 过滤（`_has_duplicate` 已 scope-aware）。
> 无 subject-allowlist、无 visibility policy——记忆不是信任边界（§5.3）。Extractor 仍只输出 kind（不需要 subject），scope = identity + kind。

### Phase 4：管理命令与 WebUI

- [ ] 管理员命令：whoami / accounts / observations / link / unlink。
- [ ] WebUI 身份页面：person 列表、账号、观察记录、记忆 scope。
- [ ] 所有声明/解除操作写审计日志。
> 无 OTP、无自助链接命令（§9.3）。

### Phase 5：迁移

- [ ] 迁移脚本 inspect/apply（scope 调整，无 visibility 标签）。
- [ ] 后台 compaction 合并 account scope 到 person scope。

## 13. 测试计划

核心测试：

- **非管理员账号无法触发特权工具 / 管理命令 / 跨会话消息（即使记忆解析错乱或被 prompt injection 诱导）。**
- **管理员账号（已声明）能触发特权动作。**
- 两个账号链接到同一 person 后，私聊召回同一 person memory。
- 管理员在群里召回 person scope；访客只召回 `chat + global`。
- 群聊规则写入 chat scope，对所有群成员可见。
- account unlink 后，该账号不再读取 person scope。
- cron job 使用创建者 identity 和投递目标 chat。
- 迁移脚本 dry-run 不修改数据库，apply 前备份。
> （旧的“群聊不注入 Alice 私密记忆”类测试降级为 §10.2 软约束提示测试，不是安全测试。）

## 14. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **冒充管理员触发危险操作** | **服务器被以管理员身份执行危险命令** | **授权闸：`sender_account_key` ∈ 声明管理员账号集；独立于记忆；危险动作统一收口（Phase A）** |
| 错误声明管理员账号 | 权限过大 | 配置由管理员物理控制；可审计可撤销；声明是显式行为 |
| 记忆认错人 | 回错话（低危） | 可接受——记忆是软上下文；不做硬过滤（§5.3） |
| 显示名碰撞 | 记忆归错人（低危） | 显示名只做 observation；`AccountKey` 用平台 id |
| 多 bot 账号 namespace 碰撞 | account key 错误 | channel id 必须表示配置实例；多账号部署使用不同 channel id |
| 解绑后旧记忆残留 | 访客误以为完全隔离 | 解绑只改变读取；提供迁出/归档工具并在 UI 明示 |

## 15. 结论

`chat` scope 只能回答“这段聊天入口是什么”，不能回答“这个发言者是谁”。完整方案引入 `Person` / `AccountKey` / `ParticipantObservation` 三层：

```text
ChatAddress  -> 消息在哪里
SessionKey   -> 对话历史是哪条 lane
AccountKey   -> 这条消息由哪个平台账号发出（授权的原子单位）
Person       -> 哪些账号属于同一个真实聊天对象（记忆连续性，零安全权重）
```

长期个人记忆归属 `person` 或 `account`，群/频道约定归属 `chat`，系统知识归属 `global`。

但最关键的设计决定是：**信任边界在“动作授权”，不在“记忆身份”。** 管理员账号在部署时声明，平台已认证，无需运行时验证；危险动作由一道独立于记忆的硬闸把关。记忆侧因此可以完全无感——没有 OTP、没有软/硬身份、没有 sensitivity 分级——因为 bot 在记忆里认错人无害，真正不能认错的是“能不能执行危险操作”。
