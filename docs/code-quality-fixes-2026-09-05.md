# 代码质量修复记录（2026-09-05）

对应 [审查报告](D:/Projects/nahida-bot/docs/code-quality-review-2026-09-05.md) 的七步计划；基于 `6b089cc`。本轮完成行为契约修复与第一阶段职责拆分，不包含部署。

## 改动

| 计划 | 实施结果 |
|---|---|
| 插件导入 | 普通 load 不再立即 reload；显式重载单独控制；普通导入异常统一进入 PluginLoadError/ERROR 路径；本地同名模块冲突报错，不驱逐别人的模块 |
| 依赖注入 | 已加载 bridge 同步 model_router、speech_service；共享 unset 标记，区分省略参数与显式 None；分阶段更新不再意外清空其他服务 |
| KB 检索 | 内部 `retrieve_documents` 保留 RetrievalResult；公开 `search_documents` 在出口投影 SearchResult，保留分数与邻居 metadata；自动召回报告实际检索模式 |
| mention | 解析与文案归 channels；渠道按当前配置注册 supplement，只在群聊注入；关闭 mention 不再教模型输出 token，提示限额与出站的不同目标计数一致 |
| SessionKey | SDK 提供统一聊天归属转换，授权、检索、历史工具、gateway 复用；保留 typed/legacy 的键形式，派生后缀不再混入旧聊天键 |
| pending / runner | 具名 PendingMessage + deque + 可配置接纳/过期策略；知识召回与附件解析分别移到独立模块，runner 保留调度入口 |
| workspace / 生成插件 | SkillCatalog 归 workspace；manager 提供 list/read，router/gateway 经它访问；两个生成插件共用滚动配额组件，后台任务交由框架归属管理；补上 TaskManager 在启动前取消和创建失败时的协程清理 |

职责归属的关键变化：

- [knowledge_context.py](D:/Projects/nahida-bot/nahida_bot/core/knowledge_context.py)：自动知识召回的检索、筛选与 prompt 投影；不再在 runner 中访问 adapter 私有转换函数。
- [attachments.py](D:/Projects/nahida-bot/nahida_bot/core/attachments.py)：渠道附件下载与媒体解析；各条视觉路径复用该入口。
- [mentions.py](D:/Projects/nahida-bot/nahida_bot/channels/mentions.py)：渠道共用 mention 解析和按配置生成的提示；移除旧 core 实现。
- [workspace/skills.py](D:/Projects/nahida-bot/nahida_bot/workspace/skills.py)：skill 发现、frontmatter 和内容读取；返回文本/元数据，不依赖 agent 的 ContextMessage。
- [rolling_quota.py](D:/Projects/nahida-bot/nahida_bot/plugins/rolling_quota.py)：配额预留、释放与窗口过期；额度单位仍由各插件决定。

## 排队策略

新增配置：

```yaml
router:
  pending_messages:
    max_messages: 20
    ttl_seconds: 900
```

每会话先移除已过期消息，再接纳新消息。满时保留已接纳消息、拒绝最新消息；普通用户消息会收到队列已满的提示，主动插话只记录拒绝日志。消费保持 FIFO；超过 TTL 的排队输入不再执行，并记录 `router.pending_messages_expired`。默认值只定义在 PendingMessagesConfig 中，应用装配传入有效配置。

这是每会话的待处理输入上限，不是全局会话数量上限，也不是执行中任务的超时设置。

## 兼容与范围

- 公开文档搜索的字段形状保持不变；内部检索结果不再退回 raw 后重建。
- SessionKey 的第四段按会话后缀解释；没有修改 ChatAddress 的 thread ID 协议。
- 模型 prompt、渠道注册随插件现有生命周期生效；没有额外引入配置热更新机制。
- 配额仍是内存中的滚动窗口；重启后的额度持久化不在本轮范围。
- runner 的首次拆分减少约 275 行；模型路由、视觉策略、转录持久化等后续还可继续独立，不宣称已经完成整个大类的重构。
- 未推进报告中不在七步计划内的 persona voice 路由、诊断脚手架、全局 sys.path 隔离或其他需求型 TODO。

## 验证

新增跨模块回归覆盖：typed/legacy/derived 聊天键一致性；milky/feishu 开关和非默认 mention 限额；KB 结果的 mode、neighbor_of、score 经过插件与自动召回后不丢失；队列满载、过期和请求字段保留。插件侧另覆盖首次模块执行次数、异常状态、已有 bridge 更新，以及配额与任务取消。

最终验证结果：

| 检查 | 结果 |
|---|---|
| 完整离线 pytest | **2532 passed，15 deselected**，90.16 秒；JUnit 为 0 failures / 0 errors |
| 插件与任务专项 | 130 passed，覆盖 loader、manager、API bridge、生成插件、滚动配额和 TaskManager |
| 核心关联回归 | 184 passed，含新增跨模块契约测试与 WebAPI |
| Ruff / Pyright | Ruff 通过；Pyright 分析 315 文件，0 errors / 0 warnings |
| 格式与补丁 | 改动文件已格式化；格式化前后 Python AST 一致；git diff --check 通过 |
| 额外失败探针 | TaskManager 的 create_task 被拒绝时，输入和包装协程均关闭，任务账本为空 |

完整离线测试命令：

```text
python -m pytest -m "not network" -o "addopts=-ra --strict-markers --strict-config --tb=short" -q --timeout=30 --basetemp=<新建独立临时路径> --junitxml=<临时结果文件>
```

测试结果已完整输出并写入 JUnit，但整个 pytest 进程随后仍未自动退出；本轮确认归属的 sleep/emit 子进程及已完成的测试运行器已清理。上述通过数来自完整测试报告，不代表测试进程的退出残留问题已修复。

初次直接运行完整 pytest 时，live LLM 契约用例连接已配置外部后端触发 `httpx.ConnectError`；没有据此调整生产逻辑。最终离线回归使用 `-m 'not network'`。本轮测试产生的已确认 sleep 子进程已单独清理。

质量 skill 同时复跑作为辅助：分析 302 个 Python 文件，参考分 83.43。它仍缺少 Python Tree-sitter WASM，使用正则解析，分数不作为修复完成标准。
