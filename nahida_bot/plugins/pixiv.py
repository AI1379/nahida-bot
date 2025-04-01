#
# Created by Renatus Madrigal on 03/30/2025
#

from pixivpy3 import AppPixivAPI, PixivError
from nonebot import on_command, CommandGroup, get_driver
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import MessageEvent, PrivateMessageEvent, GroupMessageEvent, Message

pixiv_api = AppPixivAPI()

cmd = CommandGroup("pixiv", priority=5, block=True)
account = cmd.command("account", aliases={"pixivlogin"}, priority=5)


@account.handle()
async def set_pixiv_account(event: GroupMessageEvent, args: Message = CommandArg()):
    if isinstance(event, PrivateMessageEvent):
        await account.finish("This command can only be used in group chats.")
    if not args:
        await account.finish("Please provide your Pixiv account credentials in the format: `username password`")

    credentials = args.extract_plain_text().split()
    if len(credentials) != 2:
        await account.finish("Invalid format. Please provide your Pixiv account credentials in the format: `username password`")

    username, password = credentials
    try:
        pixiv_api.login(username, password)
        await account.send("Pixiv account logged in successfully.")
    except PixivError as e:
        await account.finish(f"Failed to log in: {e}")
