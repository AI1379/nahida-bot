#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot import on_command, on_message, CommandGroup
from nonebot.rule import to_me
from nonebot.adapters import Message, Event
from nonebot.adapters.onebot.v11 import MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.params import EventMessage, EventParam, Command, CommandArg
from nonebot.log import logger
from openai import OpenAI, AsyncOpenAI
from nahida_bot.localstore import register
from nahida_bot.localstore.sqlite3 import SQLite3DB, PRIMARY_KEY_TYPE, TEXT, REAL
import nahida_bot.permission as permission
import time

plugin_name = "openai"


def checker(feature: str):
    # FIXME: This is a temporary fix for the permission system
    # return permission.get_checker(plugin_name, feature) & to_me()
    return to_me()


permission.update_feature_permission(
    plugin_name,
    feature="chat",
    admin=permission.ALLOW,
    group=permission.ALLOW,
    user=permission.ALLOW
)
permission.update_feature_permission(
    plugin_name,
    feature="prompt",
    admin=permission.ALLOW,
    group=permission.ALLOW,
    user=permission.ALLOW
)

logger.info("Loading openai_bot.py")

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

store: SQLite3DB = register(plugin_name, SQLite3DB)

logger.info(f"OpenAI API URL: {OPENAI_URL}")
logger.info(f"OpenAI API Token: {OPENAI_TOKEN}")
logger.info(f"OpenAI Model Name: {OPENAI_MODEL}")
logger.info(f"OpenAI DB Path: {store.db_path}")
logger.success("OpenAI plugin loaded successfully")

openai = on_message(rule=checker("chat"), priority=10)
openai_setting = CommandGroup("openai", priority=5, block=True)
prompt_setting = openai_setting.command(
    "prompt", rule=checker("prompt"), aliases={"prompt"})
clear_memory = openai_setting.command(
    "clear_memory", rule=checker("prompt"), aliases={"clear_memory"})
reset_prompt = openai_setting.command(
    "reset_prompt", rule=checker("prompt"), aliases={"reset_prompt"})
show_prompt = openai_setting.command(
    "show_prompt", rule=checker("prompt"), aliases={"show_prompt"})

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


async def get_openai_response(msg: Message, event: MessageEvent, msg_type: str):
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
    }, "{} < ?")
    # Delete the oldest messages if memory exceeds MAX_MEMORY
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

    messages = [{
        "role": "system",
        "content": prompt
    }]

    db_memory = store.select(memory_table)

    for row in db_memory:
        logger.debug(f"Database memory row: {row}")

    for _, role, content, _ in db_memory:
        messages.append({
            "role": role,
            "content": content
        })

    logger.debug(f"Messages: {messages}")

    async_client = AsyncOpenAI(api_key=OPENAI_TOKEN,
                               base_url=OPENAI_URL)

    response = await async_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        stream=True
    )

    all_content = ""
    current_content = ""
    token_count = 0
    async for chunk in response:
        if chunk.choices[0].delta.content:
            all_content += chunk.choices[0].delta.content
            lines = chunk.choices[0].delta.content.splitlines()
            if len(lines) == 1:
                current_content += chunk.choices[0].delta.content
            else:
                current_content += lines[0]
                await openai.send(current_content)
                for line in lines[1:-1]:
                    await openai.send(line)
                current_content = lines[-1]
        token_count = chunk.usage.total_tokens
    await openai.send(current_content)

    store.insert(memory_table, {
        "role": "assistant",
        "content": all_content,
        "timestamp": time.time()
    })

    logger.info(f"Token count: {token_count}")


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

    store.update("prompts",
                 {"prompt": args_msg},
                 {"chat_identifier": chat_identifier})
    try:
        store.delete(memory_table)
    except Exception as e:
        logger.error(f"Error deleting memory table: {e}")

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

    try:
        store.delete(memory_table)
    except Exception as e:
        logger.error(f"Error deleting memory table: {e}")

    await clear_memory.finish("Memory has been cleared")


@reset_prompt.handle()
async def reset_prompt_handler(event: MessageEvent = EventParam()):
    msg_type = "private" if isinstance(event, PrivateMessageEvent) else "group"
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    chat_identifier = get_chat_identifier(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    store.update("prompts", {"prompt": DEFAULT_PROMPT}, {"chat_identifier": chat_identifier})
    store.delete(get_memory_table_name(msg_type, chat_id))

    await reset_prompt.finish("Prompt has been reset to default")


@show_prompt.handle()
async def show_prompt_handler(event: MessageEvent = EventParam()):
    msg_type = "private" if isinstance(event, PrivateMessageEvent) else "group"
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    chat_identifier = get_chat_identifier(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    row = store.select("prompts", {
        "chat_identifier": chat_identifier
    })
    if not row:
        await show_prompt.finish("No prompt found")
    row = row[0] if row else None

    logger.debug(f"Row: {row}")

    prompt = row[2] if row else DEFAULT_PROMPT
    logger.debug(f"Prompt: {prompt}")

    await show_prompt.send(prompt)
