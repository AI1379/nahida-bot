#
# Created by Renatus Madrigal on 4/14/2025
#

import nonebot
from nonebot.log import logger, LoguruHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging

APS_TIMEZONE = "Asia/Shanghai"

driver = nonebot.get_driver()

scheduler = AsyncIOScheduler()
scheduler.configure(timezone=APS_TIMEZONE)


async def _startup():
    if not scheduler.running:
        scheduler.start()
        logger.opt(colors=True).info("<y>Scheduler started.</y>")


async def _shutdown():
    if scheduler.running:
        scheduler.shutdown()
        logger.opt(colors=True).info("<y>Scheduler stopped.</y>")


driver.on_startup(_startup)
driver.on_shutdown(_shutdown)

aps_logger = logging.getLogger("apscheduler")
aps_logger.setLevel(driver.config.log_level)
aps_logger.handlers.clear()
aps_logger.addHandler(LoguruHandler())
