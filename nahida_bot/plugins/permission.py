#
# Created by Renatus Madrigal on 04/03/2025
#

from nonebot import on_command, CommandGroup
from nonebot.adapters.onebot.v11 import Message, GroupMessageEvent
from nonebot.params import CommandArg, ArgPlainText, EventParam
from nonebot.rule import to_me
from nonebot.log import logger
import nahida_bot.permission as permission

perm_plugin_name = "permission_plugin"


def checker(feature: str):
    return permission.get_checker(perm_plugin_name, feature) & to_me()


perm = CommandGroup("perm", priority=5, block=True, rule=checker("perm"))
# Feature should only be set by plugins, not in chat
# feature_set = perm.command("feat", rule=checker("feat"))
group_set = perm.command("group", rule=checker("group"))
user_set = perm.command("user", rule=checker("user"))

permission.update_feature_permission(
    perm_plugin_name,
    feature="group",
    admin=permission.ALLOW,
    group=permission.ALLOW,
    user=permission.ALLOW
)
permission.update_feature_permission(
    perm_plugin_name,
    feature="user",
    admin=permission.ALLOW,
    group=permission.DENY,
    user=permission.DENY
)


@group_set.handle()
async def handle_group_set(
    args: Message = CommandArg(),
    event: GroupMessageEvent = EventParam()
):
    """Set the permission level of a plugin in a group."""
    logger.debug(f"Group set args: {args}")
    logger.debug(f"Argument list {args.extract_plain_text().split(' ')}")
    arg_list = args.extract_plain_text().split(' ')
    if len(arg_list) < 2:
        await group_set.finish("Please provide a feature and a permission level.")
    if arg_list[0].find(".") == -1:
        await group_set.finish("Please provide a valid feature.")
    plugin = arg_list[0].split(".")[0]
    feature = arg_list[0].split(".")[1]
    perm = permission.check_yes(arg_list[1])
    if perm is None:
        await group_set.finish("Please provide a valid permission level.")

    permission.update_group_permission(plugin, feature, event.group_id, perm)
    logger.debug(
        f"Plugin {plugin} feature {feature} group {event.group_id} permission {perm}")

    await group_set.finish(f"{plugin}.{feature} permission in group {event.group_id} set to {perm}")
