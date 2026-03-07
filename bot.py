#! /usr/bin/env python3

#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OnebotAdapter
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, PrivateMessageEvent
from nonebot.log import logger, default_format
from nonebot.utils import escape_tag
from nonebot.compat import model_dump
from nonebot.rule import Rule
import nahida_bot.localstore as localstore
import nahida_bot.permission as permission
from nahida_bot.utils.plugin_registry import plugin_registry
from json import load as json_load
import os

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OnebotAdapter)

json_config_path = getattr(driver.config, "json_config_path", "config.json")

logger.info(f"Loading config from: {json_config_path}")

with open(json_config_path, "r", encoding="utf-8") as f:
    json_config = json_load(f)
    for key, value in json_config.items():
        driver.config.__setattr__(key.lower(), value)

if not hasattr(driver.config, 'nickname') or not driver.config.nickname:
    driver.config.nickname = ["浅色阴影风", "浅色", "大小姐", "分身"]

logger.info(f"Bot nickname: {driver.config.nickname}")

# 移除了 group_checker 函数及其设置

log_level = getattr(driver.config, "log_level", "INFO")

if hasattr(driver.config, "log_file") and driver.config.log_file:
    logger.success(f"Log file: {driver.config.log_file}")
    logger.add(driver.config.log_file,
               rotation="1 week",
               encoding="utf-8",
               level=log_level,
               format=default_format)
else:
    logger.success("Log file: None")

if not hasattr(driver.config, "data_dir") or not driver.config.data_dir:
    driver.config.data_dir = "data"

full_data_dir = os.path.abspath(driver.config.data_dir)

logger.info("Data path: " + driver.config.data_dir)
logger.opt(colors=True).debug(
    f"Updated <y><b>Config</b></y>: {escape_tag(str(model_dump(driver.config)))}"
)

superusers = getattr(driver.config, "superusers", [])

logger.info(f"Superuser: {superusers}")

localstore.init(full_data_dir)

permission.init()
for superuser in superusers:
    permission.add_superuser(superuser)

nonebot.load_builtin_plugins()
nonebot.load_plugins("nahida_bot/plugins")

async def notify_superusers():
    """Notify all superusers when the bot is initialized"""
    bot = nonebot.get_bot()
    message = "Bot已初始化完成！\n\n"
    message += "当前加载的插件：\n"
    
    plugins = plugin_registry.get_plugins()
    for plugin_name, plugin_info in plugins.items():
        message += f"\n{plugin_name}：{plugin_info.description}\n"
    
    # 移除白名单显示，改为提示无限制
    message += "\n白名单群组: 未设置（允许所有群组）"

    for user_id in superusers:
        try:
            await bot.send_private_msg(user_id=int(user_id), message=message)
            logger.info(f"Sent initialization notification to superuser {user_id}")
        except Exception as e:
            logger.error(f"Failed to send initialization notification to superuser {user_id}: {e}")

@driver.on_bot_connect
async def _():
    await notify_superusers()

if __name__ == "__main__":
    nonebot.run()
