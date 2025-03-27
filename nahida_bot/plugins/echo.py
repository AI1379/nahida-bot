#
# Created by Renatus Madrigal on 03/24/2025
#

from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg

echo = on_command("echo", rule=to_me(), priority=5, block=True)

@echo.handle()
async def handle_first_receive(args: Message = CommandArg()):
    if msg := args.extract_plain_text():
        await echo.finish(msg)
    else:
        await echo.finish("你好像没有说话喵~")
