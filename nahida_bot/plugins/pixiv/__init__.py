#
# Created by Renatus Madrigal on 04/12/2025
#

from nonebot import CommandGroup
from nonebot.log import logger
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Message, MessageEvent, Bot
from nonebot.params import CommandArg, EventParam
import nahida_bot.permission as permission
from nahida_bot.plugins.pixiv.pixiv import pixiv_request_handler

pixiv_plugin_name = "pixiv_plugin"


def checker(feature: str):
    return permission.get_checker(pixiv_plugin_name, feature) & to_me()


permission.update_feature_permission(
    pixiv_plugin_name,
    feature="request",
    admin=permission.ALLOW,
    group=permission.ALLOW,
    user=permission.ALLOW
)
permission.update_feature_permission(
    pixiv_plugin_name,
    feature="statistic",
    admin=permission.ALLOW,
    group=permission.ALLOW,
    user=permission.ALLOW
)
permission.update_feature_permission(
    pixiv_plugin_name,
    feature="ranking",
    admin=permission.ALLOW,
    group=permission.ALLOW,
    user=permission.ALLOW
)

pixiv_group = CommandGroup("pixiv", priority=5, block=True)
# Fetch an image from pixiv
pixiv_request = pixiv_group.command(
    "request", aliases={"pixiv_request", "setu"}, priority=5, rule=checker("request")
)

# Get a word cloud of the favorite tags of members in the group
pixiv_statistic = pixiv_group.command(
    "statistic", aliases={"xp_stat"}, priority=5, rule=checker("statistic")
)

# Get a ranking of the request statistics of the group members
pixiv_ranking = pixiv_group.command(
    "ranking", aliases={"xp_rank"}, priority=5, rule=checker("ranking")
)


@pixiv_request.handle()
async def handle_pixiv_request(
    bot: Bot,
    event: MessageEvent = EventParam(),
    args: Message = CommandArg()
):
    await pixiv_request_handler(bot, event, args, pixiv_request)
