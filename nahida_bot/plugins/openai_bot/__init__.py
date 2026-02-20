#
# Created by Renatus Madrigal on 03/24/2025
#

import nonebot
from nonebot import on_command, on_message, CommandGroup
from nonebot.rule import to_me
from nonebot.adapters import Message, Event
from nonebot.adapters.onebot.v11 import (
    MessageEvent,
    PrivateMessageEvent,
    GroupMessageEvent,
    MessageSegment,
)
from nonebot.params import EventMessage, EventParam, Command, CommandArg
from nonebot.log import logger
from openai import OpenAI, AsyncOpenAI
from nahida_bot.localstore import register
from nahida_bot.localstore.sqlite3 import SQLite3DB, PRIMARY_KEY_TYPE, TEXT, REAL
from nahida_bot.config import get_config
import nahida_bot.permission as permission
from nahida_bot.utils.plugin_registry import plugin_registry
import time
import random
import asyncio

# Register the plugin
openai_plugin = plugin_registry.register_plugin(
    name="OpenAI插件", description="提供AI对话、提示词管理等功能"
)

# Register features
plugin_registry.add_feature(
    plugin_name="OpenAI插件",
    feature_name="AI对话",
    description="与AI进行对话",
    commands=["@机器人 对话内容"],
)

plugin_registry.add_feature(
    plugin_name="OpenAI插件",
    feature_name="提示词管理",
    description="管理AI对话的提示词",
    commands=["/prompt", "/reset_prompt", "/show_prompt"],
)

plugin_registry.add_feature(
    plugin_name="OpenAI插件",
    feature_name="模型管理",
    description="管理AI模型",
    commands=["/get_models", "/current_model", "/set_model"],
)

plugin_registry.add_feature(
    plugin_name="OpenAI插件",
    feature_name="记忆管理",
    description="管理对话记忆",
    commands=["/clear_memory"],
)

plugin_name = "openai"


def checker(feature: str):
    # FIXME: This is a temporary fix for the permission system
    # return permission.get_checker(plugin_name, feature) & to_me()
    return to_me()


logger.info("Loading openai_bot.py")

# Load configuration from OpenAIConfig
app_config = get_config()
openai_config = app_config.openai

if not openai_config:
    logger.error("OpenAI configuration not found, please check your config.yaml")
    raise ValueError("OpenAI configuration not found")

OPENAI_URL = openai_config.api_url
OPENAI_TOKEN = openai_config.api_token
DEFAULT_OPENAI_MODEL = openai_config.model_name
OPENAI_MODEL = DEFAULT_OPENAI_MODEL

DATA_PATH = app_config.core.data_dir

FIXED_PROMPT = "DO NOT use markdown in your reply. Always reply in Simplified Chinese unless requested."

if openai_config.default_prompt:
    DEFAULT_PROMPT = openai_config.default_prompt + " " + FIXED_PROMPT
else:
    DEFAULT_PROMPT = """You are a helpful AI assistant. DO NOT use markdown in your reply. Always reply in Simplified Chinese unless requested. """

MESSAGE_TIMEOUT = openai_config.message_timeout
MAX_MEMORY = openai_config.max_memory

# Segmentation settings - minimum length before splitting by punctuation/newline
MIN_SEGMENT_LENGTH = 200
# Chinese punctuation marks
CHINESE_PUNCTUATION = "。！？；"
# English punctuation marks
ENGLISH_PUNCTUATION = ".!?;"
# All punctuation marks
ALL_PUNCTUATION = CHINESE_PUNCTUATION + ENGLISH_PUNCTUATION
# Message sending speed (characters per second) for delay calculation
MESSAGE_SEND_SPEED = 50
# Random delay variance (0.8 - 1.2 means ±20%)
DELAY_VARIANCE = 0.2

store: SQLite3DB = register(plugin_name, SQLite3DB)

logger.info(f"OpenAI API URL: {OPENAI_URL}")
logger.info(f"OpenAI API Token: {OPENAI_TOKEN}")
logger.info(f"OpenAI Model Name: {OPENAI_MODEL}")
logger.info(f"OpenAI DB Path: {store.db_path}")
logger.success("OpenAI plugin loaded successfully")

openai = on_message(rule=checker("chat"), priority=10)
openai_setting = CommandGroup("openai", priority=5, block=True)

prompt_setting = openai_setting.command(
    "prompt", rule=checker("prompt"), aliases={"prompt"}
)
clear_memory = openai_setting.command(
    "clear_memory", rule=checker("prompt"), aliases={"clear_memory"}
)
reset_prompt = openai_setting.command(
    "reset_prompt", rule=checker("prompt"), aliases={"reset_prompt"}
)
show_prompt = openai_setting.command(
    "show_prompt", rule=checker("prompt"), aliases={"show_prompt"}
)
get_models = openai_setting.command(
    "get_models", rule=checker("prompt"), aliases={"get_models"}
)
current_model = openai_setting.command(
    "current_model", rule=checker("prompt"), aliases={"current_model"}
)
set_model = openai_setting.command(
    "set_model", rule=checker("prompt"), aliases={"set_model"}
)

store.create_table(
    "prompts",
    {
        "id": PRIMARY_KEY_TYPE,
        "chat_identifier": TEXT,
        "prompt": TEXT,
    },
)


def get_chat_identifier(msg_type: str, chat_id: str) -> str:
    return f"{msg_type}_{chat_id}"


def get_memory_table_name(msg_type: str, chat_id: str) -> str:
    return f"{msg_type}_{chat_id}_memory"


@openai.handle()
async def handle_message(
    args: Message = EventMessage(), event: MessageEvent = EventParam()
):
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


async def should_send_content(content: str) -> bool:
    """
    Determine if content should be sent based on:
    1. Length >= MIN_SEGMENT_LENGTH
    2. Ends with punctuation or newline
    
    Args:
        content: The content to check
        
    Returns:
        True if content should be sent, False otherwise
    """
    if not content:
        return False
    
    # If content is less than minimum length, don't send
    if len(content) < MIN_SEGMENT_LENGTH:
        return False
    
    # If reaches minimum length, check if it ends with punctuation or newline
    return content[-1] in ALL_PUNCTUATION or content.endswith("\n")


def split_at_last_punctuation(content: str) -> tuple[str | None, str]:
    """
    Find the last punctuation or newline in content and split at that point.
    Only splits if the content before the punctuation is reasonably long.
    
    Args:
        content: The content to split
        
    Returns:
        A tuple of (segment_to_send, remaining_content) or (None, content) if no good split point found
    """
    if not content or len(content) < MIN_SEGMENT_LENGTH:
        return (None, content)
    
    # Find the last punctuation mark from the end, but not too close to the end
    # We want to ensure there's some content left in the buffer
    min_keep_length = 50  # Keep at least 50 chars in buffer for next segment
    search_end = max(len(content) - min_keep_length, MIN_SEGMENT_LENGTH)
    
    # Search backwards from search_end
    last_punct_pos = -1
    for i in range(search_end - 1, -1, -1):
        if content[i] in ALL_PUNCTUATION or content[i] == "\n":
            last_punct_pos = i
            break
    
    # If found a punctuation mark, split there (include the punctuation)
    if last_punct_pos != -1:
        split_pos = last_punct_pos + 1
        return (content[:split_pos], content[split_pos:])
    
    # No good split point found
    return (None, content)


async def calculate_send_delay(content_length: int) -> float:
    """
    Calculate delay based on message length with randomization.
    Delay = (length / MESSAGE_SEND_SPEED) * random(1 - DELAY_VARIANCE, 1 + DELAY_VARIANCE)
    
    Args:
        content_length: Length of content being sent
        
    Returns:
        Delay in seconds
    """
    base_delay = content_length / MESSAGE_SEND_SPEED
    variance = random.uniform(1.0 - DELAY_VARIANCE, 1.0 + DELAY_VARIANCE)
    return base_delay * variance


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

    store.create_table(
        memory_table,
        {"id": PRIMARY_KEY_TYPE, "role": TEXT, "content": TEXT, "timestamp": REAL},
    )

    row = store.select("prompts", {"chat_identifier": chat_identifier})
    if not row:
        store.insert(
            "prompts", {"chat_identifier": chat_identifier, "prompt": DEFAULT_PROMPT}
        )
        row = store.select("prompts", {"chat_identifier": chat_identifier})
    row = row[0] if row else None

    logger.debug(f"Row: {row}")

    prompt = row[2] if row else DEFAULT_PROMPT
    logger.debug(f"Prompt: {prompt}")

    store.delete(memory_table, {"timestamp": time.time() - MESSAGE_TIMEOUT}, "{} < ?")
    # Delete the oldest messages if memory exceeds MAX_MEMORY
    where_clause = (
        f"""SELECT id FROM {memory_table} ORDER BY id DESC LIMIT {MAX_MEMORY}"""
    )
    store.get_cursor().execute(
        f"""
        DELETE FROM {memory_table} WHERE id NOT IN ({where_clause})
        """
    ).connection.commit()
    store.insert(
        memory_table,
        {"role": "user", "content": msg.extract_plain_text(), "timestamp": time.time()},
    )

    messages = [{"role": "system", "content": prompt}]

    db_memory = store.select(memory_table)

    for row in db_memory:
        logger.debug(f"Database memory row: {row}")

    for _, role, content, _ in db_memory:
        messages.append({"role": role, "content": content})

    logger.debug(f"Messages: {messages}")

    async_client = AsyncOpenAI(api_key=OPENAI_TOKEN, base_url=OPENAI_URL)

    response = await async_client.chat.completions.create(
        model=OPENAI_MODEL, messages=messages, stream=True
    )

    all_content = ""
    current_content = ""
    token_count = 0
    
    async for chunk in response:
        if chunk.choices[0].delta.content:
            delta_content = chunk.choices[0].delta.content
            all_content += delta_content
            current_content += delta_content
            
            logger.debug(f"Current buffer length: {len(current_content)}")
            
            # If content is long enough, try to find a good split point
            if len(current_content) >= MIN_SEGMENT_LENGTH:
                segment_to_send, remaining = split_at_last_punctuation(current_content)
                
                # If we found a good split point, send the segment and keep the rest
                if segment_to_send:
                    if segment_to_send.strip():  # Only send non-empty segments
                        logger.debug(f"Sending content: {repr(segment_to_send)}")
                        await openai.send(segment_to_send.strip())
                        
                        # Calculate delay based on segment length with randomization
                        delay = await calculate_send_delay(len(segment_to_send))
                        logger.debug(f"Sending delay for {len(segment_to_send)} chars: {delay:.2f}s")
                        await asyncio.sleep(delay)
                    
                    # Keep remaining content in buffer
                    current_content = remaining
        
        token_count = chunk.usage.total_tokens
    
    # Send any remaining content in the buffer
    logger.debug(f"Final buffer content: {repr(current_content)}")
    if current_content.strip():
        await openai.send(current_content)
        # Add delay for final message too
        delay = await calculate_send_delay(len(current_content))
        logger.debug(f"Final message delay for {len(current_content)} chars: {delay:.2f}s")
        await asyncio.sleep(delay)

    store.insert(
        memory_table,
        {"role": "assistant", "content": all_content, "timestamp": time.time()},
    )

    logger.info(f"Token count: {token_count}")


@prompt_setting.handle()
async def openai_setting_handler(
    args: Message = CommandArg(), event: MessageEvent = EventParam()
):
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

    store.update("prompts", {"prompt": args_msg}, {"chat_identifier": chat_identifier})
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

    store.update(
        "prompts", {"prompt": DEFAULT_PROMPT}, {"chat_identifier": chat_identifier}
    )
    store.delete(get_memory_table_name(msg_type, chat_id))

    await reset_prompt.finish("Prompt has been reset to default")


@show_prompt.handle()
async def show_prompt_handler(event: MessageEvent = EventParam()):
    msg_type = "private" if isinstance(event, PrivateMessageEvent) else "group"
    chat_id = event.get_user_id() if msg_type == "private" else event.group_id
    chat_identifier = get_chat_identifier(msg_type, chat_id)

    logger.debug(f"Chat ID: {chat_id}")
    logger.debug(f"Chat Identifier: {chat_identifier}")

    row = store.select("prompts", {"chat_identifier": chat_identifier})
    if not row:
        await show_prompt.finish("No prompt found")
    row = row[0] if row else None

    logger.debug(f"Row: {row}")

    prompt = row[2] if row else DEFAULT_PROMPT
    logger.debug(f"Prompt: {prompt}")

    await show_prompt.send(prompt)


@get_models.handle()
async def get_models_handler():
    async_client = AsyncOpenAI(api_key=OPENAI_TOKEN, base_url=OPENAI_URL)
    models = await async_client.models.list()
    models_list = [model.id for model in models.data]
    await get_models.finish("\n".join(models_list))


@current_model.handle()
async def current_model_handler():
    await current_model.finish(OPENAI_MODEL)


@set_model.handle()
async def set_model_handler(args: Message = CommandArg()):
    global OPENAI_MODEL
    model_name = args.extract_plain_text().strip()
    if not model_name:
        await set_model.finish("Please provide a model name")

    client = AsyncOpenAI(api_key=OPENAI_TOKEN, base_url=OPENAI_URL)
    all_models = await client.models.list()
    if model_name not in [model.id for model in all_models.data]:
        message = MessageSegment.text(
            f"Model {model_name} not found. Available models are:\n"
        )
        for model in all_models.data:
            message += MessageSegment.text(f"{model.id}\n")
        await set_model.finish(message)
    OPENAI_MODEL = model_name
    await set_model.finish(f"Model has been set to {model_name}")
