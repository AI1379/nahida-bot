#
# Created by Renatus Madrigal on 03/27/2025
#

import nonebot
from nonebot import on_command, CommandGroup
from nonebot.rule import to_me
import psutil
import os
import platform
import time
import datetime

server_monitor_group = CommandGroup("server", priority=5, block=True)
all_status = server_monitor_group.command(tuple())
info = server_monitor_group.command("info")
usage = server_monitor_group.command("usage")
bot_config = server_monitor_group.command("config")


@info.handle()
async def handle_info():
    await info.send(f"Server Info:\n"
                      f"System: {platform.system()} {platform.release()} {platform.version()}\n"
                      f"Machine: {platform.machine()}\n"
                      f"Processor: {platform.processor()}")


@usage.handle()
async def handle_usage():
    cpu_usage = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot_time = datetime.datetime.fromtimestamp(
        psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
    await usage.send(f"CPU Usage: {cpu_usage}%\n"
                       f"Memory Usage: {memory.percent}%\n"
                       f"Disk Usage: {disk.percent}%\n"
                       f"Boot Time: {boot_time}")


@bot_config.handle()
async def handle_config():
    await bot_config.send(f"Command start: {nonebot.get_driver().config.command_start}\n"
                            f"Command separator: {nonebot.get_driver().config.command_sep}")

@all_status.handle()
async def handle_all_status():
    await handle_info()
    await handle_usage()
    await handle_config()


