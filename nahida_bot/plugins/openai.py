#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot import on_command, on_message, CommandGroup
from nonebot.rule import to_me
from nonebot.adapters import Message, Event
from nonebot.adapters.onebot.v11 import Event as OnebotEvent
from nonebot.adapters.onebot.v11 import MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.params import EventMessage, EventParam, Command, CommandArg
from nonebot.log import logger
from openai import OpenAI
from typing import Tuple
import sqlite3
import time

logger.info("Loading openai.py")

openai_url = nonebot.get_driver().config.openai_api_url
openai_token = nonebot.get_driver().config.openai_api_token
openai_model = nonebot.get_driver().config.openai_model_name

data_path = nonebot.get_driver().config.data_dir

DEFAULT_PROMPT = """You are a helpful AI assistant. DO NOT use markdown in your reply. Always reply in Simplified Chinese unless requested. """
# TODO: Make this configurable
MESSAGE_TIMEOUT = 3600  # Currently set it to 1 hour
MAX_MEMORY = 50  # Max context message

db_path = f"{data_path}/openai.db"

logger.info(f"OpenAI API URL: {openai_url}")
logger.info(f"OpenAI API Token: {openai_token}")
logger.info(f"OpenAI Model Name: {openai_model}")
logger.info(f"OpenAI DB Path: {db_path}")
logger.success("OpenAI plugin loaded successfully")

openai = on_message(rule=to_me(), priority=10)
openai_setting = CommandGroup("openai", priority=5, block=True)
prompt_setting = openai_setting.command("prompt", aliases={"prompt"})
clear_memory = openai_setting.command("clear_memory", aliases={"clear_memory"})
reset_prompt = openai_setting.command("reset_prompt", aliases={"reset_prompt"})

db = sqlite3.connect(db_path)
cursor = db.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS prompts (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            chat_identifier TEXT NOT NULL,
                            prompt TEXT NOT NULL
                        )""")
db.commit()


def get_chat_identifier(msg_type: str, chat_id: str) -> str:
    return f"{msg_type}_{chat_id}"


def get_memory_table_name(msg_type: str, chat_id: str) -> str:
    return f"{msg_type}_{chat_id}_memory"


@openai.handle()
async def handle_message(args: Message = EventMessage(), event: MessageEvent = EventParam()):
    logger.debug(f"Received message: {args}")
    logger.debug(f"Received event: {event}")
    logger.debug(f"Received event message: {event.get_message()}")
    logger.debug(f"Received event type: {event.get_type()}")
    logger.debug(f"Received user id: {event.get_user_id()}")
    logger.debug(f"Message type: {event.message_type}")
    logger.debug(f"Message sender: {event.sender}")

    msg_type = ""

    if isinstance(event, PrivateMessageEvent):
        logger.debug(f"Received private message from {event.sender}")
        msg_type = "private"
    elif isinstance(event, GroupMessageEvent):
        logger.debug(f"Received group message from {event.sender}")
        grp_event = event
        logger.debug(f"Received group id: {grp_event.group_id}")
        msg_type = "group"
    elif args.extract_plain_text() == "":
        await openai.finish("你好像没有说话喵~")
    await get_openai_response(args, event, msg_type)


async def get_openai_response(msg: Message, event: PrivateMessageEvent, msg_type: str):
    if msg_type != "private" and msg_type != "group":
        return
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id

    # For SQLite3
    chat_identifier = get_chat_identifier(msg_type, chat_id)
    memory_table = get_memory_table_name(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Message type: {msg_type}")
    logger.debug(f"Chat identifier: {chat_identifier}")
    logger.debug(f"Memory table: {memory_table}")

    cursor.execute(f"""CREATE TABLE IF NOT EXISTS {memory_table} (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL,
                            timestamp REAL NOT NULL
                        )""")
    db.commit()

    row = cursor.execute(f"""SELECT * FROM prompts WHERE chat_identifier = ?""",
                         (chat_identifier,)).fetchone()
    if row is None:
        cursor.execute(f"""INSERT INTO prompts (chat_identifier, prompt) VALUES (?, ?)""",
                       (chat_identifier, DEFAULT_PROMPT))
        db.commit()
    logger.debug(f"Row: {row}")

    prompt = row[2] if row else DEFAULT_PROMPT
    logger.debug(f"Prompt: {prompt}")

    # Update memory
    cursor.execute(f"""DELETE FROM {memory_table} WHERE timestamp < ?""",
                   (time.time() - MESSAGE_TIMEOUT,))
    db.commit()

    cursor.execute(f"""INSERT INTO {memory_table} (role, content, timestamp) VALUES (?, ?, ?)""",
                   ("user", msg.extract_plain_text(), time.time()))
    db.commit()

    messages = []
    messages.append({
        "role": "system",
        "content": prompt
    })

    db_memory = cursor.execute(f"""SELECT * FROM {memory_table}""").fetchall()
    for row in db_memory:
        logger.debug(f"Database memory row: {row}")

    for _, role, content, _ in db_memory:
        messages.append({
            "role": role,
            "content": content
        })

    logger.debug(f"Messages: {messages}")

    client = OpenAI(api_key=openai_token,
                    base_url=openai_url)

    response = client.chat.completions.create(
        model=openai_model,
        messages=messages
    )

    res = response.choices[0].message

    cursor.execute(f"""INSERT INTO {memory_table} (role, content, timestamp) VALUES (?, ?, ?)""",
                   (res.role, res.content, time.time()))
    db.commit()
    
    logger.success(f"OpenAI response: {res.content}")

    await openai.send(res.content)


@prompt_setting.handle()
async def openai_setting_handler(args: Message = CommandArg(),
                                 event: MessageEvent = EventParam()):
    args_msg = args.extract_plain_text()

    logger.debug(f"Received args: {args_msg}")

    msg_type = "private" if isinstance(event, PrivateMessageEvent) else "group"
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    chat_identifier = get_chat_identifier(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    if not args_msg:
        await prompt_setting.finish("Please provide a prompt")

    cursor.execute(f"""UPDATE prompts SET prompt = ? WHERE chat_identifier = ?""",
                   (args_msg, chat_identifier))
    
    memory_table = get_memory_table_name(msg_type, chat_id)
    # remove old memory
    cursor.execute(f"""DELETE FROM {memory_table}""")
    db.commit()

    await prompt_setting.finish("Prompt has been set")


@clear_memory.handle()
async def clear_memory_handler(event: MessageEvent = EventParam()):
    msg_type = "private" if isinstance(event, PrivateMessageEvent) else "group"
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    chat_identifier = get_chat_identifier(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    memory_table = get_memory_table_name(msg_type, chat_id)
    logger.debug(f"Memory table: {memory_table}")

    cursor.execute(f"""DELETE FROM {memory_table}""")
    db.commit()

    await clear_memory.finish("Memory has been cleared")


@reset_prompt.handle()
async def reset_prompt_handler(event: MessageEvent = EventParam()):
    msg_type = "private" if isinstance(event, PrivateMessageEvent) else "group"
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    chat_identifier = get_chat_identifier(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    cursor.execute(f"""UPDATE prompts SET prompt = ? WHERE chat_identifier = ?""",
                   (DEFAULT_PROMPT, chat_identifier))
    db.commit()

    await reset_prompt.finish("Prompt has been reset to default")
