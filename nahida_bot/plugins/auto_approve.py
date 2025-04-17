#
# Created by Renatus Madrigal on 4/15/2025
#

import nonebot
from nonebot import on_request
from nonebot.adapters.onebot.v11.event import FriendRequestEvent, GroupRequestEvent
from nahida_bot.utils.plugin_registry import plugin_registry

# Register the plugin
auto_approve_plugin = plugin_registry.register_plugin(
    name="自动审批",
    description="自动处理好友和群组请求"
)

# Register features
plugin_registry.add_feature(
    plugin_name="自动审批",
    feature_name="好友请求",
    description="自动审批好友添加请求",
    commands=["自动处理"]
)

plugin_registry.add_feature(
    plugin_name="自动审批",
    feature_name="群组请求",
    description="自动审批群组加入请求",
    commands=["自动处理"]
)

friend_request = on_request(priority=1)
group_request = on_request(priority=1)


@friend_request.handle()
async def handle_friend_request(event: FriendRequestEvent):
    bot = nonebot.get_bot()
    add_nickname: str = (await bot.get_stranger_info(user_id=event.user_id))["nickname"] or "Unknown"
    await event.approve(bot)
    for su in bot.config.superusers:
        await bot.send_private_msg(
            user_id=su,
            message=f"Friend request from {add_nickname}[{event.user_id}] has been approved.",
        )


@group_request.handle()
async def handle_group_request(event: GroupRequestEvent):
    bot = nonebot.get_bot()
    group_id = event.group_id
    inviter_id = event.user_id
    group_name = (await bot.get_group_info(group_id=group_id))["group_name"] or "Unknown"
    inviter_name = (await bot.get_stranger_info(user_id=inviter_id))["nickname"] or "Unknown"
    await event.approve(bot)
    for su in bot.config.superusers:
        await bot.send_private_msg(
            user_id=su,
            message=f"Group request from {inviter_name}[{inviter_id}] to join {group_name}[{group_id}] has been approved.",
        )
