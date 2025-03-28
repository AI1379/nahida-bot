#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter
from nonebot.adapters.onebot.v11 import Adapter as OnebotAdapter
from nonebot.log import logger

logger.level("DEBUG")

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OnebotAdapter)

logger.info("Data path: " + driver.config.data_dir)

nonebot.load_builtin_plugins()
nonebot.load_plugins("nahida_bot/plugins")

if __name__ == "__main__":
    nonebot.run()