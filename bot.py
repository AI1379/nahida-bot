#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter
from nonebot.adapters.onebot.v11 import Adapter as OnebotAdapter
from nonebot.log import logger, default_format
from nonebot.utils import escape_tag
from nonebot.compat import model_dump
import nahida_bot.localstore as localstore
from json import load as json_load

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OnebotAdapter)

json_config_path = driver.config.json_config_path if hasattr(
    driver.config, "json_config_path") else "config.json"

with open(json_config_path, "r", encoding="utf-8") as f:
    json_config = json_load(f)
    for key, value in json_config.items():
        driver.config.__setattr__(key.lower(), value)

log_level = driver.config.log_level if hasattr(
    driver.config, "log_level") else "INFO"

if hasattr(driver.config, "log_file") and driver.config.log_file:
    logger.add(driver.config.log_file,
               rotation="1 week",
               encoding="utf-8",
               level=log_level,
               format=default_format)

if not driver.config.data_dir:
    driver.config.data_dir = "data"

logger.info("Data path: " + driver.config.data_dir)
logger.opt(colors=True).debug(
    f"Updated <y><b>Config</b></y>: {escape_tag(str(model_dump(driver.config)))}"
)

localstore.init(driver.config.data_dir)

nonebot.load_builtin_plugins()
nonebot.load_plugins("nahida_bot/plugins")

if __name__ == "__main__":
    nonebot.run()
