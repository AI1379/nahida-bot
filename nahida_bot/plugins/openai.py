#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot import on_command, on_message, CommandGroup
from nonebot.rule import to_me
from nonebot.adapters import Message, Event
from nonebot.adapters.onebot.v11 import Event as OnebotEvent
from nonebot.adapters.onebot.v11 import MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.params import EventMessage, EventParam, Command, CommandArg, RawCommand
from nonebot.log import logger
from openai import OpenAI
from typing import Tuple
from collections import deque
import time

logger.info("Loading openai.py")

openai_url = nonebot.get_driver().config.openai_api_url
openai_token = nonebot.get_driver().config.openai_api_token
openai_model = nonebot.get_driver().config.openai_model_name

DEFAULT_PROMPT = "You are a helpful AI assistant. DO NOT use markdown in your reply"
MESSAGE_TIMEOUT = 120  # Currently set it to 2min to debug
MAX_MEMORY = 20 # Max context message

config = {
    "group": {
        "prompt": {
        },
        "memory": {
        }
    },
    "private": {
        "prompt": {
        },
        "memory": {
        }
    }
}

logger.info(f"OpenAI API URL: {openai_url}")
logger.info(f"OpenAI API Token: {openai_token}")
logger.info(f"OpenAI Model Name: {openai_model}")

openai = on_message(rule=to_me(), priority=10)
openai_setting_group = CommandGroup("openai_setting", priority=5, block=True)
prompt_setting = openai_setting_group.command("prompt")

@openai.handle()
async def handle_message(args: Message = EventMessage(), event: MessageEvent = EventParam()):
    logger.info(f"Received message: {args}")
    logger.info(f"Received event: {event}")
    logger.info(f"Received event message: {event.get_message()}")
    logger.info(f"Received event type: {event.get_type()}")
    logger.info(f"Received user id: {event.get_user_id()}")
    logger.info(f"Message type: {event.message_type}")
    logger.info(f"Message sender: {event.sender}")

    msg_type = ""

    if isinstance(event, PrivateMessageEvent):
        logger.info(f"Received private message from {event.sender}")
        msg_type = "private"
        await get_openai_response(args, event, msg_type)
    elif isinstance(event, GroupMessageEvent):
        logger.info(f"Received group message from {event.sender}")
        grp_event = event
        logger.info(f"Received group id: {grp_event.group_id}")
        msg_type = "group"
    else:
        await openai.finish("你好像没有说话喵~")


async def get_openai_response(msg: Message, event: PrivateMessageEvent, msg_type: str):
    if msg_type != "private" and msg_type != "group":
        return
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    if chat_id not in config[msg_type]["memory"]:
        config[msg_type]["memory"][chat_id] = deque(maxlen=MAX_MEMORY)
    prompts = config[msg_type]["prompt"]
    prompt = prompts[chat_id] if chat_id in prompts else DEFAULT_PROMPT
    
    memory = config[msg_type]["memory"][chat_id]
    
    while memory and time.time() - memory[0]["timestamp"] > MESSAGE_TIMEOUT:
        memory.popleft()
    
    memory.append({
        "role": "user",
        "content": msg.extract_plain_text(),
        "timestamp": time.time()
    })

    messages = []
    messages.append({
        "role": "system",
        "content": prompt
    })
    
    for m in memory:
        messages.append({
            "role": m["role"],
            "content": m["content"]
        })

    logger.info(f"Messages: {messages}")

    client = OpenAI(api_key=openai_token,
                    base_url=openai_url)

    response = client.chat.completions.create(
        model=openai_model,
        messages=messages
    )

    res = response.choices[0].message

    memory.append({
        "role": res.role,
        "content": res.content,
        "timestamp": time.time()
    })

    await openai.send(res.content)

@prompt_setting.handle()
async def openai_setting_handler(cmd: Tuple[str, str] = Command(),
                         args: Message = CommandArg(),
                         event: MessageEvent = EventParam()):
    _, argument = cmd
    args_msg = args.extract_plain_text()
    logger.info(f"Received command: {cmd}")
    logger.info(f"Received config: {argument}")
    logger.info(f"Received args: {args_msg}")
    if argument == "prompt":
        if isinstance(event, PrivateMessageEvent):
            config["private"]["prompt"][event.get_user_id()] = args_msg
        elif isinstance(event, GroupMessageEvent):
            config["group"]["prompt"][event.group_id] = args_msg
        logger.info(f"Current config: {config}")
        await prompt_setting.finish("Prompt has been set")
