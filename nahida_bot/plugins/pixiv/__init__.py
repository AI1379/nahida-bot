#
# Created by Renatus Madrigal on 04/12/2025
#

from nonebot import CommandGroup, get_driver
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Message, MessageEvent, Bot
from nonebot.params import CommandArg, EventParam
import nahida_bot.permission as permission
from nahida_bot.plugins.pixiv.pixiv import pixiv_request_handler
from nahida_bot.plugins.pixiv.xp_statistic import handle_tag_stats
from nahida_bot.utils.plugin_registry import plugin_registry
from nonebot.log import logger

# Register the plugin
pixiv_plugin = plugin_registry.register_plugin(
    name="Pixiv插件",
    description="提供Pixiv图片请求、标签统计等功能"
)

# Register features
plugin_registry.add_feature(
    plugin_name="Pixiv插件",
    feature_name="图片请求",
    description="从Pixiv获取图片",
    commands=["/pixiv.request", "/pixiv.related"]
)

plugin_registry.add_feature(
    plugin_name="Pixiv插件",
    feature_name="标签统计",
    description="统计和分析标签使用情况",
    commands=["/pixiv.statistic", "/pixiv.ranking"]
)

pixiv_plugin_name = "pixiv_plugin"


def checker(feature: str):
    # return permission.get_checker(pixiv_plugin_name, feature) & to_me()
    return to_me()


pixiv_group = CommandGroup("pixiv", priority=5, block=True)
# Fetch an image from pixiv
pixiv_request = pixiv_group.command(
    "request", aliases={"pixiv_request", "setu"}, priority=5, rule=checker("request")
)

# Get related images of a specific image on pixiv
pixiv_related = pixiv_group.command(
    "related", aliases={"pxrelated"}, priority=5, rule=checker("related")
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


@pixiv_related.handle()
async def handle_pixiv_related(
        bot: Bot,
        event: MessageEvent = EventParam(),
        args: Message = CommandArg()
):
    await pixiv_request_handler(bot, event, args, pixiv_related, related=True)


@pixiv_statistic.handle()
async def handle_pixiv_statistic(
        event: MessageEvent = EventParam(),
):
    await handle_tag_stats(event, pixiv_statistic)



