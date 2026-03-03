# 权限系统使用指南

## 概述

新的权限系统采用**黑名单模型**（ban模型），即：

- 所有功能默认**允许**
- 除非被显式**ban**掉，否则允许使用

## 权限檢查優先級

1. **Superuser** → 全部允许（即使被ban也允许）
2. **用户黑名单** → 如果用户被ban，则拒绝
3. **群黑名单** → 如果群被ban（仅对群消息），则拒绝
4. **默认** → 允许

## API 使用

### 基础函数

```python
import nahida_bot.permission as perm

# 初始化权限系统（在bot.py中已调用）
perm.init()

# 添加/移除 superuser
perm.add_superuser("user_id")
perm.remove_superuser("user_id")

# Ban/Unban 用户的功能
perm.ban_user("user_id", "plugin_name", "feature_name")
perm.unban_user("user_id", "plugin_name", "feature_name")

# Ban/Unban 群的功能
perm.ban_group("group_id", "plugin_name", "feature_name")
perm.unban_group("group_id", "plugin_name", "feature_name")

# 检查权限
result = perm.check_permission(event, "plugin_name", "feature_name")

# 获取权限检查器（用于nonebot rule）
checker = perm.get_checker("plugin_name", "feature_name")

# 获取所有ban记录
bans_info = perm.get_all_bans()
```

### 在插件中使用

```python
from nonebot import on_command
import nahida_bot.permission as perm

# 创建命令
cmd = on_command("mycommand", priority=5)

@cmd.handle()
async def handle_command(event):
    # 检查权限
    if not perm.check_permission(event, "my_plugin", "my_feature"):
        await cmd.finish("您没有权限使用此功能")
    
    # 处理命令...
```

## 私聊命令

仅superuser可以通过私聊使用以下命令来管理权限：

```
/perm ban user <user_id> <plugin.feature>
/perm unban user <user_id> <plugin.feature>
/perm ban group <group_id> <plugin.feature>
/perm unban group <group_id> <plugin.feature>
/perm add_superuser <user_id>
/perm remove_superuser <user_id>
/perm list
```

### 例子

```
/perm ban user 123456 pixiv.search
    → Ban用户123456使用pixiv的search功能

/perm ban group 789123 openai.chat
    → Ban群789123使用openai的chat功能

/perm add_superuser 999888
    → 添加用户999888为superuser

/perm list
    → 列出所有ban和superuser
```

## 实现原理

### 数据存储

权限数据存储在SQLite数据库中：

- **ban_user** 表：user_id, plugin, feature → 用户功能黑名单
- **ban_group** 表：group_id, plugin, feature → 群功能黑名单
- **superusers** 表：user_id → 超级用户列表

### 检查流程

```
事件到达
    ↓
是否是superuser? → 是 → 允许
    ↓ 否
用户被ban该功能? → 是 → 拒绝
    ↓ 否
是群消息且群被ban该功能? → 是 → 拒绝
    ↓ 否
允许
```

## 迁移说明

旧权限系统（allow模型）已完全重写为新的ban模型。不需要进行数据迁移。
