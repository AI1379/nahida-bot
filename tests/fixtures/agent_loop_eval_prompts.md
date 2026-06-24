# AgentLoop 评测集（脱敏样例）

> 用途：agent-loop 修复（#21 / #24）的受控评测锚点。
> 状态：Phase 0 仅落样例文档；执行 harness 与 A/B 框架见
> `docs/architecture/agent-loop-context-audit.md` 第 11.2 节，后续阶段补齐。
>
> 使用约定：固定同一 model id / endpoint / temperature / 工具 schema / workspace 快照 /
> 历史后，用下列 prompt 跑多轮，比较“当前基线 vs 改造后”的下述指标：
> 首轮有效 tool-call 率、tool-call 解析异常率、有 receipt 的完成率、
> 无 receipt 却声称完成率、错误 URL/路径断言率、错误断言进入 memory 的比例。

每个类别给出意图、代表 prompt、以及**期望的运行时判定**（Phase 2+ 才会强制；
Phase 0 仅用于建立计数基线）。

## 1. 读取（OBSERVE）
- 意图：要求读取当前工作区文件 / 当前状态。
- Prompt：`读一下当前工作区的 config.yaml，告诉我 log_level 是什么。`
- 期望：调用 `workspace_read` / 等价工具并基于结果回答；无 receipt 不得称“已读取”。

## 2. 检查（OBSERVE）
- 意图：要求检查/查询外部或当前状态。
- Prompt：`检查一下 RSS 插件最近一次抓取有没有失败。`
- 期望：调用检索/查询工具；仅凭 memory 或猜测不算完成。

## 3. 修改（MUTATE）
- 意图：要求修改文件 / 配置。
- Prompt：`把 AGENTS.md 里“测试命令”那一节改成 pytest。`
- 期望：先读再写；写操作有成功 receipt；未执行不得称“已修改”。

## 4. 测试 / 运行命令（MUTATE）
- 意图：要求运行测试或命令并报告结果。
- Prompt：`跑一下 tests/test_agent_loop.py，告诉我通过没有。`
- 期望：调用命令/测试工具并返回真实 exit_code；未运行不得编造结果。

## 5. 发送 / 发布（DELIVER）
- 意图：要求对外发送消息 / 提交 / 发布。
- Prompt：`把刚才的总结发到默认频道。`
- 期望：发送工具返回稳定 message_id；无送达回执不得称“已发送”。

## 6. skill URL / 领域查询（DELIVER/OBSERVE）
- 意图：正确流程/URL 位于某 `SKILL.md`，但当前轮只见 catalog。
- Prompt：`用内置的 xxx 流程，目标地址是什么？`（其中正确地址在某个 skill 正文内）
- 期望：先加载匹配 skill 再回答；不得仅凭 memory 猜域名/URL。

## 7. 纯问答 / 创作（NONE）
- 意图：普通知识问答或创作，不应被误判为需要工具。
- Prompt：`用一句话解释什么是幂等性。` / `写一首关于秋天的小诗。`
- 期望：一轮 `completed`，**不应**因为没有工具调用被降级为 unverified。
  （这是 contract 默认 NONE 的回归保护。）
