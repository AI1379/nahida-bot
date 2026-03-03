# 权限系统重构总结

## 概述

已完全重构权限系统，从复杂的allow/deny模型改为简洁的**黑名单（ban）模型**。

## 主要改动

### 1. 核心逻辑变更
- **旧模型**：Allow/Deny 三层权限（Feature/Group/User）
- **新模型**：黑名单模型 - 默认允许，除非被ban

### 2. 数据库表结构
```
ban_user: (id, user_id, plugin, feature)
  └─ 用户黑名单

ban_group: (id, group_id, plugin, feature)
  └─ 群黑名单

superusers: (id, user_id)
  └─ 超级用户
```

### 3. 权限检查流程
1. 是superuser？ → 允许
2. 用户被ban？ → 拒绝
3. 群被ban（群消息）？ → 拒绝
4. 其他情况 → 允许

## 新增功能

### 管理命令（私聊，仅superuser可用）
```
/perm ban user <user_id> <plugin.feature>
/perm unban user <user_id> <plugin.feature>
/perm ban group <group_id> <plugin.feature>
/perm unban group <group_id> <plugin.feature>
/perm add_superuser <user_id>
/perm remove_superuser <user_id>
/perm list
```

### API函数
```python
perm.add_superuser(user_id)
perm.remove_superuser(user_id)
perm.ban_user(user_id, plugin, feature)
perm.unban_user(user_id, plugin, feature)
perm.ban_group(group_id, plugin, feature)
perm.unban_group(group_id, plugin, feature)
perm.check_permission(event, plugin, feature)
perm.get_checker(plugin, feature)
perm.get_all_bans()
```

## 改进点

1. **简化复杂性**：从7个函数+多层逻辑简化为6个核心函数
2. **更直观**：Ban模型比Allow/Deny更容易理解
3. **管理更方便**：superuser可以通过私聊实时管理权限
4. **数据兼容性**：使用SQLite.Row支持字典式访问
5. **完整测试**：12个测试用例覆盖所有场景

## 文件修改

### 新增
- `PERMISSION_GUIDE.md` - 使用指南
- 权限管理命令处理

### 修改
- `nahida_bot/permission/__init__.py` - 完全重构
- `nahida_bot/plugins/permission.py` - 完全重写
- `tests/test_permission.py` - 新测试用例
- `nahida_bot/localstore/sqlite3.py` - 添加row_factory支持

## 测试结果

✅ 所有12个测试用例通过
- Superuser功能
- Ban用户功能
- Ban群功能
- 权限优先级
- 权限检查器
- Ban列表获取

## 使用示例

### 在插件中使用
```python
from nonebot import on_command
import nahida_bot.permission as perm

cmd = on_command("mycommand")

@cmd.handle()
async def handle(event):
    if not perm.check_permission(event, "my_plugin", "feature"):
        await cmd.finish("您没有权限使用此功能")
    # 处理命令...
```

### 通过私聊管理
```
向bot发送私聊：
/perm ban user 123456 pixiv.search
  → Ban用户123456的pixiv搜索功能

/perm ban group 789012 openai.chat
  → Ban群789012的openai聊天功能

/perm list
  → 列出所有权限配置
```

## 不需要迁移
旧权限数据无需迁移，新系统完全独立。

## 注意事项
1. Superuser需要在config中配置或通过API添加
2. 用户ID和群ID均需转换为字符串存储
3. 权限检查必须在事件处理中进行
