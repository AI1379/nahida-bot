#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot import on_command, on_message
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg, Command, EventMessage, EventParam
from nonebot.log import logger
from nahida_bot.scheduler import scheduler
from nahida_bot.utils.plugin_registry import plugin_registry
import os
import __main__

# Register the plugin
utility_plugin = plugin_registry.register_plugin(
    name="工具插件",
    description="提供基础工具和系统功能"
)

# Register features
plugin_registry.add_feature(
    plugin_name="工具插件",
    feature_name="回显功能",
    description="回显用户发送的消息",
    commands=["/echo"]
)

plugin_registry.add_feature(
    plugin_name="工具插件",
    feature_name="文档查看",
    description="查看项目文档",
    commands=["/readme"]
)

plugin_registry.add_feature(
    plugin_name="工具插件",
    feature_name="定时任务",
    description="系统定时任务",
    commands=["自动运行"]
)

echo = on_command("echo", rule=to_me(), priority=5, block=True)
readme = on_command("readme", rule=to_me(), priority=5, block=True)


@echo.handle()
async def handle_first_receive(args: Message = CommandArg()):
    if msg := args.extract_plain_text():
        await echo.finish(msg)
    else:
        await echo.finish("你好像没有说话喵~")


@scheduler.scheduled_job("cron", hour="*")
async def scheduled_job():
    bot = nonebot.get_bot()
    if superusers := nonebot.get_driver().config.superusers:
        for superuser in superusers:
            await bot.send_private_msg(
                user_id=superuser,
                message="定时任务运行了喵~"
            )
    else:
        nonebot.logger.info("Heartbeat: No superuser found, skipping scheduled job.")


@readme.handle()
async def handle_readme():
    base_dir = os.path.dirname(os.path.abspath(__main__.__file__))
    readme_path = os.path.join(base_dir, "README.md")
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            readme_content = f.read()
        await readme.finish(readme_content)
    else:
        logger.warning(f"README.md not found at {readme_path}")
        await readme.finish("README.md not found.")
