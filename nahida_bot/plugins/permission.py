#
# 权限管理插件
# 仅superuser可以通过私聊使用这些命令
#

from nonebot import on_command
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.log import logger
import nahida_bot.permission as perm

# 仅在私聊中使用
perm_cmd = on_command("perm", priority=5, block=True)


async def _verify_superuser(event: PrivateMessageEvent) -> bool:
    """验证是否是superuser"""
    if not perm.get_all_bans:  # 检查权限系统是否初始化
        await perm_cmd.finish("权限系统未初始化")
        return False

    bans = perm.get_all_bans()
    superusers = bans.get("superusers", [])
    user_id = str(event.sender.user_id)

    if not any(su["user_id"] == user_id for su in superusers):
        await perm_cmd.finish("只有superuser才能使用此命令")
        return False

    return True


@perm_cmd.handle()
async def handle_permission_command(
    event: PrivateMessageEvent,
    args: Message = CommandArg(),
):
    """处理权限命令

    用法:
    - perm ban user <user_id> <plugin.feature>  # Ban用户的功能
    - perm unban user <user_id> <plugin.feature>  # Unban用户的功能
    - perm ban group <group_id> <plugin.feature>  # Ban群的功能
    - perm unban group <group_id> <plugin.feature>  # Unban群的功能
    - perm add_superuser <user_id>  # 添加superuser
    - perm remove_superuser <user_id>  # 移除superuser
    - perm list  # 列出所有ban和superuser
    """

    if not await _verify_superuser(event):
        return

    args_text = args.extract_plain_text().strip()
    if not args_text:
        await perm_cmd.finish(handle_permission_command.__doc__)
        return

    parts = args_text.split()
    command = parts[0] if parts else None

    try:
        if command == "ban":
            await handle_ban(parts[1:])
        elif command == "unban":
            await handle_unban(parts[1:])
        elif command == "add_superuser":
            await handle_add_superuser(parts[1:])
        elif command == "remove_superuser":
            await handle_remove_superuser(parts[1:])
        elif command == "list":
            await handle_list()
        else:
            await perm_cmd.finish(
                f"未知命令: {command}\n\n{handle_permission_command.__doc__}"
            )
    except Exception as e:
        logger.error(f"权限命令执行失败: {e}")
        await perm_cmd.finish(f"执行失败: {e}")


async def handle_ban(parts: list):
    """处理ban命令"""
    if len(parts) < 3:
        await perm_cmd.finish("用法: perm ban <user|group> <id> <plugin.feature>")
        return

    target_type = parts[0]  # user or group
    target_id = parts[1]
    feature_spec = parts[2]

    if "." not in feature_spec:
        await perm_cmd.finish("功能格式错误，应为: plugin.feature")
        return

    plugin, feature = feature_spec.split(".", 1)

    if target_type == "user":
        perm.ban_user(target_id, plugin, feature)
        await perm_cmd.finish(f"✓ 已ban用户 {target_id} 的功能 {plugin}.{feature}")
    elif target_type == "group":
        perm.ban_group(target_id, plugin, feature)
        await perm_cmd.finish(f"✓ 已ban群 {target_id} 的功能 {plugin}.{feature}")
    else:
        await perm_cmd.finish(f"目标类型错误: {target_type}，应为 user 或 group")


async def handle_unban(parts: list):
    """处理unban命令"""
    if len(parts) < 3:
        await perm_cmd.finish("用法: perm unban <user|group> <id> <plugin.feature>")
        return

    target_type = parts[0]
    target_id = parts[1]
    feature_spec = parts[2]

    if "." not in feature_spec:
        await perm_cmd.finish("功能格式错误，应为: plugin.feature")
        return

    plugin, feature = feature_spec.split(".", 1)

    if target_type == "user":
        perm.unban_user(target_id, plugin, feature)
        await perm_cmd.finish(f"✓ 已unban用户 {target_id} 的功能 {plugin}.{feature}")
    elif target_type == "group":
        perm.unban_group(target_id, plugin, feature)
        await perm_cmd.finish(f"✓ 已unban群 {target_id} 的功能 {plugin}.{feature}")
    else:
        await perm_cmd.finish(f"目标类型错误: {target_type}，应为 user 或 group")


async def handle_add_superuser(parts: list):
    """处理添加superuser"""
    if not parts:
        await perm_cmd.finish("用法: perm add_superuser <user_id>")
        return

    user_id = parts[0]
    perm.add_superuser(user_id)
    await perm_cmd.finish(f"✓ 已添加superuser: {user_id}")


async def handle_remove_superuser(parts: list):
    """处理移除superuser"""
    if not parts:
        await perm_cmd.finish("用法: perm remove_superuser <user_id>")
        return

    user_id = parts[0]
    perm.remove_superuser(user_id)
    await perm_cmd.finish(f"✓ 已移除superuser: {user_id}")


async def handle_list():
    """列出所有ban和superuser"""
    bans = perm.get_all_bans()

    msg = "【权限配置】\n\n"

    # Superusers
    superusers = bans.get("superusers", [])
    if superusers:
        msg += "Superusers:\n"
        for su in superusers:
            msg += f"  - {su['user_id']}\n"
    else:
        msg += "Superusers: 无\n"

    msg += "\n"

    # User bans
    user_bans = bans.get("user_bans", [])
    if user_bans:
        msg += "用户黑名单:\n"
        for ban in user_bans:
            msg += f"  - 用户 {ban['user_id']}: {ban['plugin']}.{ban['feature']}\n"
    else:
        msg += "用户黑名单: 无\n"

    msg += "\n"

    # Group bans
    group_bans = bans.get("group_bans", [])
    if group_bans:
        msg += "群黑名单:\n"
        for ban in group_bans:
            msg += f"  - 群 {ban['group_id']}: {ban['plugin']}.{ban['feature']}\n"
    else:
        msg += "群黑名单: 无\n"

    await perm_cmd.finish(msg)
