# Code Quality Review

## Summary

任务和日程共用受限的 CalDAV 传输层，各自使用独立账户绑定；日程读取、重复事件展开和写入已经拆分，业务逻辑保留在插件中。

## Overall Assessment

2026-11-05 使用 `fuck-u-code-analysis` 技能与 `fuck-u-code 2.2.2` 扫描本轮涉及的完整实现文件。
整体包含已有的大型 `core/app.py`，该文件本轮只新增两个插件发现项；不是全仓库评分。

| Metric | Score |
|--------|-------|
| Overall | 77.48/100 |
| Plugin and script weighted score | 88.64/100 |
| Files Analyzed | 22 |
| Critical Issues | 16 个扫描器指标标记；未确认本轮新增阻断问题 |

异常上抛到工具/服务边界也会被扫描器判为缺少错误处理。对网络异常、权限失败、版本冲突、
XML 实体及不完整查询已做行为验证，没有为了提高分数吞掉异常或返回空数据。

## Key Issues (sorted by severity)

- **`EventProvider.handle`**：初版文件 67.20 分、单函数最长 137 行。已拆为读取、展开和写入模块，协调模块提升至 90.42 分。
- **`task_values`**：初版分支复杂度较高。时间归一化、截止时间与提醒解析已分成独立函数，记录模块提升至 84.52 分。
- **`DavClient.request`**：实际 Radicale 返回压缩响应。修复解压后重建响应时重复解压的问题，真实 HTTP 集成测试覆盖此路径。
- **`expanded_values`**：真实服务对 DST 边界的重复日程可能漏报。读取增加一天候选边界，再按原请求窗口精确展开和过滤；跨 DST 回归已通过。
- **`DavClient.query`**：多状态响应缺少日程数据时必须失败，不能把不可读取的记录解释为“空闲”。已有针对性测试。

## Refactoring Plan

1. 已完成：拆分 `events.py`、`event_reads.py` 与 `event_writes.py`，缩短业务分派函数。
2. 已完成：抽出 `daily_brief/rendering.py`，统一待办和日程的简报显示。
3. 已完成：TODO 增加来源路由，保留本地条目与现有 ID，外部条目使用不透明引用。
4. 后续按真实账户结果扩展认证和客户端兼容性；避免把提供商特例加入核心 Agent。

## Security Concerns

- Agent 工具不接收任意 URL、账号或密码；身份来自当前会话，集合由可信配置绑定。
- 默认要求 HTTPS；明示的 HTTP 测试例外只允许回环地址。
- 跨域发现和跳转受来源限制；iCloud 仅自动允许标准数字 CalDAV 分片域名。
- 资源引用按账户、连接和集合验证，修改/删除使用强 ETag。
- XML 实体扩展被拒绝；响应大小、查询窗口、集合和展开数量有上限。
- 写入失败不伪造成功，工具错误返回操作键供查询和重试。外部客户端并发预约仍不具备跨服务事务保证。

真实 iCloud 账户与 iPhone 客户端尚未验收。测试使用两个仅监听本机、不同凭据和集合的
Radicale 实例；测试结束清理进程，没有部署生产 CalDAV 服务或修改真实日历。
