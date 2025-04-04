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
from nahida_bot.localstore import register
from nahida_bot.localstore.sqlite3 import SQLite3DB, PRIMARY_KEY_TYPE, TEXT, REAL
import time

logger.info("Loading openai.py")

OPENAI_URL = nonebot.get_driver().config.openai_api_url
OPENAI_TOKEN = nonebot.get_driver().config.openai_api_token
OPENAI_MODEL = nonebot.get_driver().config.openai_model_name

DATA_PATH = nonebot.get_driver().config.data_dir

FIXED_PROMPT = "DO NOT use markdown in your reply. Always reply in Simplified Chinese unless requested."

if hasattr(nonebot.get_driver().config, "openai_default_prompt"):
    DEFAULT_PROMPT = nonebot.get_driver().config.openai_default_prompt + FIXED_PROMPT
else:
    DEFAULT_PROMPT = """You are a helpful AI assistant. DO NOT use markdown in your reply. Always reply in Simplified Chinese unless requested. """

if hasattr(nonebot.get_driver().config, "openai_message_timeout"):
    MESSAGE_TIMEOUT = nonebot.get_driver().config.openai_message_timeout
else:
    MESSAGE_TIMEOUT = 60 * 60 * 24 * 7  # 7 days

if hasattr(nonebot.get_driver().config, "openai_max_memory"):
    MAX_MEMORY = nonebot.get_driver().config.openai_max_memory
else:
    MAX_MEMORY = 50  # Max context message

store: SQLite3DB = register("openai", SQLite3DB)

logger.info(f"OpenAI API URL: {OPENAI_URL}")
logger.info(f"OpenAI API Token: {OPENAI_TOKEN}")
logger.info(f"OpenAI Model Name: {OPENAI_MODEL}")
logger.info(f"OpenAI DB Path: {store.db_path}")
logger.success("OpenAI plugin loaded successfully")

openai = on_message(rule=to_me(), priority=10)
openai_setting = CommandGroup("openai", priority=5, block=True)
prompt_setting = openai_setting.command("prompt", aliases={"prompt"})
clear_memory = openai_setting.command("clear_memory", aliases={"clear_memory"})
reset_prompt = openai_setting.command("reset_prompt", aliases={"reset_prompt"})

store.create_table("prompts", {
    "id": PRIMARY_KEY_TYPE,
    "chat_identifier": TEXT,
    "prompt": TEXT,
})


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

    store.create_table(memory_table, {
        "id": PRIMARY_KEY_TYPE,
        "role": TEXT,
        "content": TEXT,
        "timestamp": REAL
    })

    row = store.select("prompts", {
        "chat_identifier": chat_identifier
    })
    if not row:
        store.insert("prompts", {
            "chat_identifier": chat_identifier,
            "prompt": DEFAULT_PROMPT
        })
        row = store.select("prompts", {
            "chat_identifier": chat_identifier
        })
    row = row[0] if row else None

    logger.debug(f"Row: {row}")

    prompt = row[2] if row else DEFAULT_PROMPT
    logger.debug(f"Prompt: {prompt}")

    store.delete(memory_table, {
                 "timestamp": time.time() - MESSAGE_TIMEOUT
                 },
                 "{} < ?")
    # Delete oldest messages if memory exceeds MAX_MEMORY
    where_clause = f"""SELECT id FROM {memory_table} ORDER BY id DESC LIMIT {MAX_MEMORY}"""
    store.get_cursor().execute(
        f"""
        DELETE FROM {memory_table} WHERE id NOT IN ({where_clause})
        """).connection.commit()
    store.insert(memory_table, {
        "role": "user",
        "content": msg.extract_plain_text(),
        "timestamp": time.time()
    })

    messages = []
    messages.append({
        "role": "system",
        "content": prompt
    })

    db_memory = store.select(memory_table)

    for row in db_memory:
        logger.debug(f"Database memory row: {row}")

    for _, role, content, _ in db_memory:
        messages.append({
            "role": role,
            "content": content
        })

    logger.debug(f"Messages: {messages}")

    client = OpenAI(api_key=OPENAI_TOKEN,
                    base_url=OPENAI_URL)

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages
    )

    res = response.choices[0].message

    store.insert(memory_table, {
        "role": res.role,
        "content": res.content,
        "timestamp": time.time()
    })

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
    memory_table = get_memory_table_name(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    if not args_msg:
        await prompt_setting.finish("Please provide a prompt")

    args_msg = args_msg.strip() + " " + FIXED_PROMPT

    store.update("prompts", {
        "prompt": args_msg
    }, {
        "chat_identifier": chat_identifier
    })
    store.delete(memory_table)

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

    store.delete(memory_table)

    await clear_memory.finish("Memory has been cleared")


@reset_prompt.handle()
async def reset_prompt_handler(event: MessageEvent = EventParam()):
    msg_type = "private" if isinstance(event, PrivateMessageEvent) else "group"
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    chat_identifier = get_chat_identifier(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    store.update("prompts", {
        "prompt": DEFAULT_PROMPT
    }, {
        "chat_identifier": chat_identifier
    })
    store.delete(get_memory_table_name(msg_type, chat_id))

    await reset_prompt.finish("Prompt has been reset to default")
