# Workspace 文件说明

每个 workspace 的根目录下会有一组特殊的 Markdown 文件，它们是 Agent 的"上下文说明书"——工作指引、人格定义、用户偏好、长期记忆，都靠这些文件驱动。理解每个文件的作用，才能让 Agent 按你的预期工作。

## 文件总览

<div class="file-tree">

```
workspaces/<id>/
├── AGENTS.md            # 🧭 工作指引——启动流程、工具使用规则
├── SOUL.md              # 🎭 灵魂定义——人格、语气、行为边界
├── USER.md              # 👤 用户画像——语言偏好、回复风格、长期背景
├── MEMORY.md            # 🧠 长期记忆——偏好、项目上下文、决策记录
├── memory_summary.md    # 📋 记忆摘要——由结构化记忆自动编译
├── memory/              # 📅 每日笔记目录
│   ├── 2026-06-01.md
│   └── 2026-05-31.md
└── skills/              # 🛠 自定义技能目录
    └── <name>/
        └── SKILL.md
```

</div>

## 各文件详解

### AGENTS.md — 工作指引

**这是 Agent 最先读取的 workspace 指令文件。** 它告诉 Agent 如何在这个 workspace 里工作。

默认内容包括：

- **启动流程**：按顺序读取 SOUL.md → USER.md → 加载 skills → 使用 memory 工具
- **工具使用规则**：编辑前先读取、用 `workspace_write` 写持久化笔记、用 `memory_write` 只存稳定偏好
- **回复风格**：简洁可执行，除非用户要求更多细节
- **安全提示**：不存储密钥，除非用户明确要求

::: tip 你可以修改它
AGENTS.md 的内容完全由你控制。比如你可以加上：
```markdown
## 特殊规则
- 所有代码输出必须使用中文注释
- 生成文件前先确认目标目录
```
:::

### SOUL.md — 灵魂定义

**定义 Agent 的人格、语气和行为边界。** 这是 Agent 的"性格设定"。

默认内容包括：

- **语气**：直接、冷静、技术精准；不做空洞鼓励
- **边界**：只在阻碍安全推进时才提问；保护用户文件；将 workspace memory 视为参考而非绝对真理

::: tip 调整人格
想让 Agent 更幽默？更学术？更简洁？直接改 SOUL.md 即可：
```markdown
## Tone
- 用轻松、友好的语气回复
- 适当使用 emoji 增加亲和力
- 用「你」而非「您」
```
:::

### USER.md — 用户画像

**存放关于用户的持久化偏好和长期背景。** 这是让 Agent 越来越懂你的关键文件。

默认内容包括：

- **语言偏好**：默认语言、术语习惯
- **回复风格偏好**：详细程度、是否要代码注释、是否要解释
- **重要约束**：不能做什么、必须做什么
- **长期上下文**：当前在做什么项目、需要记住的事情

::: tip 示例
```markdown
## Preferences
- Language: 中文优先，代码用英文
- Preferred response style: 先给结论再解释，代码要完整可运行
- Important constraints: 永远不要修改数据库 schema 除非我明确要求

## Long-Running Context
- Current projects: nahida-bot 的 WebUI 重构
- Things to remember: 我习惯用 pnpm 而非 npm
```
:::

### MEMORY.md — 长期记忆

**持久化 workspace 记忆的主文件。** 它是 Agent 自动写入和读取记忆的锚点。

#### 结构说明

MEMORY.md 包含两部分：

1. **用户可编辑区域**：你手动维护的记忆，Agent 不会覆盖
2. **自动生成区域**：Agent 通过 `memory_write` 工具写入的结构化记忆，位于标记之间：
   ```
   <!-- nahida-memory-generated:start -->
   ...
   <!-- nahida-memory-generated:end -->
   ```

#### 记忆分类

自动生成区域会按类型分组：

| 类型 | 用途 | 来源 |
|------|------|------|
| **Preferences** | 用户偏好 | `memory_write` kind=preference |
| **Project Context** | 项目上下文 | `memory_write` kind=fact/procedure/warning |
| **Decisions** | 决策记录 | `memory_write` kind=decision |
| **Tasks** | 任务追踪 | `memory_write` kind=task |
| **Notes** | 一般笔记 | 其他所有 kind |

#### 上下文注入

MEMORY.md 会在每次对话时注入到 Agent 的上下文中，上限 **6000 字符**。超出部分会被截断，并在截断处标记 `... (memory truncated)`。

### memory_summary.md — 记忆摘要

**由结构化持久记忆自动编译的轻量摘要。** 相比 MEMORY.md 更紧凑，只保留最近 20 条记忆的关键信息。

它和 MEMORY.md 一起在上下文中注入，帮助 Agent 在预算紧张时仍保留关键记忆。

### memory/YYYY-MM-DD.md — 每日笔记

**按日期组织的记忆流水。** 通过 `memory_write` 写入，通过 `memory_read` 读取。

特点：

- 每天一个文件，格式为 `memory/YYYY-MM-DD.md`
- 上下文注入时只加载最近 **3 天** 的笔记
- 适合记录临时但近期有用的信息（今天做了什么、刚学到的技巧等）

### skills/\<name\>/SKILL.md — 自定义技能

**教 Agent 掌握新的工作流程或领域知识。** 每个 skill 是一个目录，包含一个 `SKILL.md` 文件。

Skill 文件格式：

```markdown
---
name: my-skill-name
description: 一句话描述这个技能的用途
---
# 技能标题

技能的详细说明、步骤、规则……

## 规则
- 规则 1
- 规则 2
```

:::: details 内置 skills

workspace 初始化时自动创建两个 skill：

- **workspace-files** — 教 Agent 使用 `workspace_read` / `workspace_write` 工具进行文件读写
- **memory** — 教 Agent 使用 `memory_read` / `memory_write` 工具管理记忆

你可以在 `skills/` 目录下创建更多自定义 skill。

::::

## 上下文注入顺序

这些文件被组装到 Agent 的系统提示中时，遵循**固定的优先级顺序**：

```
1. 系统基线提示（硬编码）
2. AGENTS.md          ← 工作指引
3. SOUL.md            ← 人格定义
4. USER.md            ← 用户画像
5. skills/*/SKILL.md  ← 自定义技能
6. MEMORY.md          ← 长期记忆
7. memory_summary.md  ← 记忆摘要
8. memory/最近3天.md   ← 每日笔记
9. 对话历史
10. 工具调用回填
11. 受保护消息（当前用户轮次）
```

**为什么这个顺序重要？**

- AGENTS.md 在最前面，因为它定义了后续文件如何被使用
- SOUL.md 在 USER.md 之前，因为人格基调应该先于具体偏好
- 记忆在技能之后，因为技能教会 Agent "怎么用工具"，记忆提供"工具产出了什么"
- 历史对话在最后，因为它是动态变化的，优先级低于所有静态指令

## 文件保护规则

Workspace 初始化时会自动创建这些文件的默认版本，但：

- **已有的非空文件不会被覆盖**。如果你编辑了 AGENTS.md 或 SOUL.md，升级 bot 后你的修改会保留。
- **空文件或仅为空白字符的文件会被重新填充默认内容**。这就是"秒删大法"的妙用——删掉内容重启就能恢复到默认。

## 与其他配置的关系

| 配置方式 | 作用范围 | 适用场景 |
|----------|----------|----------|
| `config.yaml` | 全局 | LLM 密钥、Provider、Channel、系统级参数 |
| workspace `AGENTS.md` | 当前 workspace | 工作流程、工具策略、跨项目一致的行为 |
| workspace `SOUL.md` | 当前 workspace | 不同 bot 人格（如：工作 bot vs 闲聊 bot） |
| workspace `USER.md` | 当前 workspace | 个人偏好、语言、项目背景 |
| workspace `MEMORY.md` | 当前 workspace | 跨会话的持久记忆 |

::: tip 多 workspace 场景
不同的 workspace 可以有不同的 AGENTS.md 和 SOUL.md。比如：
- `work` workspace 的 SOUL.md 定义严谨、正式的工作助手
- `creative` workspace 的 SOUL.md 定义有创意、轻松的风格
切换 workspace 就切换了 Agent 的行为模式。
:::
