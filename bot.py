#! /usr/bin/env python3

#
# Created by Renatus Madrigal on 03/24/2025
#

from typing import cast
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OnebotAdapter
from nonebot.log import logger, default_format
from nonebot.utils import escape_tag
from nonebot.compat import model_dump
from nonebot.config import Config as NonebotConfig
import nahida_bot.localstore as localstore
import nahida_bot.permission as permission
from nahida_bot.utils.plugin_registry import plugin_registry
from nahida_bot.config import init_config, get_config, merge_pydantic_models
import os

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OnebotAdapter)

# Load configuration from YAML and environment variables
# nonebot has already loaded .env files automatically
app_config = init_config("config.yaml")

# Set configuration to nonebot driver
# Convert all config fields to uppercase for nonebot compatibility
driver.config = merge_pydantic_models(driver.config, app_config.core)


log_level = app_config.core.log_level

if app_config.core.log_file:
    logger.success(f"Log file: {app_config.core.log_file}")
    logger.add(
        app_config.core.log_file,
        rotation="1 week",
        encoding="utf-8",
        level=log_level,
        format=default_format,
    )
else:
    logger.success("Log file: None")

if not app_config.core.data_dir:
    app_config.core.data_dir = "data"

full_data_dir = os.path.abspath(app_config.core.data_dir)

logger.info("Data path: " + app_config.core.data_dir)
logger.opt(colors=True).debug(
    f"Updated <y><b>Config</b></y>: {escape_tag(str(model_dump(driver.config)))}"
)

superusers = app_config.core.superusers

logger.info(f"Superuser: {superusers}")

localstore.init(full_data_dir)

permission.init()
for superuser in superusers:
    permission.add_superuser(superuser)

nonebot.load_builtin_plugins()

for plugin_name in app_config.plugins:
    try:
        nonebot.load_plugin(f"nahida_bot.plugins.{plugin_name}")
        logger.success(f"Loaded plugin: {plugin_name}")
    except Exception as e:
        logger.error(f"Failed to load plugin {plugin_name}: {e}")

# nonebot.load_plugins("nahida_bot/plugins")


async def notify_superusers():
    """Notify all superusers when the bot is initialized"""
    bot = nonebot.get_bot()
    message = "Bot已初始化完成！\n\n"
    message += "当前加载的插件：\n"

    plugins = plugin_registry.get_plugins()
    for plugin_name, plugin_info in plugins.items():
        message += f"\n{plugin_name}：{plugin_info.description}\n"

    for user_id in superusers:
        try:
            await bot.send_private_msg(user_id=int(user_id), message=message)
            logger.info(f"Sent initialization notification to superuser {user_id}")
        except Exception as e:
            logger.error(
                f"Failed to send initialization notification to superuser {user_id}: {e}"
            )


@driver.on_bot_connect
async def _():
    await notify_superusers()


if __name__ == "__main__":
    nonebot.run()
