#
# Created by Renatus Madrigal on 02/19/2026
#

import json
import time
from typing import Any, cast

import nonebot
from nonebot import on_message, on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageSegment,
)
from nonebot.adapters.onebot.v11 import (
    Message as OneBotMessage,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import (
    BotParam,
    Command,
    CommandArg,
    EventMessage,
    EventParam,
    MatcherParam,
)
from nonebot.rule import to_me
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from nahida_bot.config import OpenAIConfig, get_config
from nahida_bot.localstore import register
from nahida_bot.localstore.sqlite3_v2 import SQLite3DBv2Store
from nahida_bot.utils.plugin_registry import plugin_registry
from nahida_bot.utils.llm_message_builder import LLMMessageBuilder
from nahida_bot.utils.unwrap import unwrap, unwrap_or, unwrap_or_throw

store = register("chat_summarizer", SQLite3DBv2Store)

chat_summarizer_plugin = plugin_registry.register_plugin(
    "chat_summarizer", "自动总结聊天内容"
)

logger.info("Loading chat_summarizer plugin...")

config = get_config()
openai_config: OpenAIConfig = config.openai  # type: ignore

if openai_config is None:
    logger.error("OpenAI configuration is missing. Please set the OpenAI API token.")
    raise ImportError(
        "OpenAI configuration is missing. Please set the OpenAI API token."
    )


def checker(feature: str):
    # TODO: Waiting for the Permission system to be implemented
    return to_me()


msg_recorder = on_message(
    priority=1, block=False
)  # Highest priority to ensure it runs before other plugins

msg_summarize = on_command(
    "summarize",
    aliases={"总结"},
    rule=checker("chat_summarizer"),
    priority=5,
    block=True,
)


# TODO: Image handling
class MessageModel(BaseModel):
    time: int
    msg_id: int
    nickname: str
    content: str


SYSTEM_PROMPT = """你是一个聊天总结助手，负责总结群聊中的消息内容。请根据以下要求进行总结：
1. 只总结文本内容，忽略图片、视频等非文本消息。
2. 如果消息中包含链接或视频，提取其中的文本描述或标题进行总结。
3. 保持总结简洁明了，突出关键信息。
4. 不要添加任何主观评论或个人观点，只总结客观内容。
5. 对于群成员的昵称，在总结中使用尖括号括起来，例如 <昵称>，以示区分。
6. 对于不同的聊天话题，请在总结中使用分段的方式进行区分，每个话题单独成段。
7. 不要使用 Markdown 或其他格式化语法，保持纯文本格式，并且保持语义连贯，不要用分点等形式。
请根据以上要求对以下消息进行总结：
"""

USER_MESSAGE_PROMPT = """消息ID: {msg_id}
用户: {nickname}
时间：{time}
内容: {content}
"""


async def message_summarize(messages: list[MessageModel]) -> str:
    builder = LLMMessageBuilder()
    builder.add_system_message(SYSTEM_PROMPT)
    for msg in messages:
        builder.add_user_message(
            USER_MESSAGE_PROMPT.format(
                msg_id=msg.msg_id,
                nickname=msg.nickname,
                time=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(msg.time)),
                content=msg.content,
            )
        )
    llm = AsyncOpenAI(
        api_key=openai_config.api_token,
        base_url=openai_config.api_url,
    )
    response = await llm.chat.completions.create(
        model=openai_config.model_name,
        messages=builder.build(),
    )
    if not response.choices:
        logger.error("LLM response has no choices: %s", response)
        raise ValueError("LLM response has no choices")
    summary = response.choices[0].message.content | unwrap_or_throw(
        ValueError("LLM response message content is None")
    )
    logger.debug(f"LLM summary response: {summary}")
    return summary


def parse_forward_msg(msg: dict[str, Any]) -> MessageModel:
    return MessageModel(
        time=msg.get("time", int(time.time())),
        msg_id=msg.get("message_id", 0),
        nickname=msg.get("sender", {}).get("nickname", "未知用户"),
        content=msg.get("content", "[转发消息]"),
    )


async def get_forward_messages(bot: OneBotV11Bot, id: int) -> list[MessageModel]:
    forward_msg = await bot.get_forward_msg(id=str(id))
    msg_count = len(forward_msg.get("messages", []))
    try:
        raise NotImplementedError("Forward message parsing is not implemented yet")
    except Exception as e:
        logger.error(f"Error fetching forward messages: {e}")
        return [
            MessageModel(
                time=int(time.time()),
                msg_id=0,
                nickname="未知用户",
                content="[转发消息]",
            )
            for _ in range(msg_count)
        ]


def json_message_to_text(json_data: dict) -> str:
    json_string = json.dumps(json_data, ensure_ascii=False)
    is_bilibili = "bilibili" in json_string or "哔哩哔哩" in json_string
    prompt = json_data.get("prompt", "")
    if is_bilibili:
        return f"[视频] {prompt}"
    else:
        return f"[链接] {prompt}"


# TODO: Implement recursive summarization for long forward messages
ENABLE_FORWARD_SUMMARIZATION = False


async def segment_to_text(bot: OneBotV11Bot, seg: MessageSegment, group_id: int) -> str:
    match seg.type:
        case "text":
            return seg.data.get("text", "")
        case "image":
            return seg.data.get("summary", "[图片]")
        case "json":
            return json_message_to_text(json.loads(seg.data.get("data", "{}")))
        case "forward":
            forward_id = seg.data.get("id")
            if forward_id:
                try:
                    forward_messages = await get_forward_messages(bot, int(forward_id))
                    if ENABLE_FORWARD_SUMMARIZATION:
                        forwarded_summary = await message_summarize(forward_messages)
                        return f"[转发消息: {len(forward_messages)} 条，摘要: {forwarded_summary}]"
                    return f"[转发消息: {len(forward_messages)} 条]"
                except Exception as e:
                    logger.error(f"Error fetching forward messages: {e}")
                    return "[转发消息]"
            else:
                return "[转发消息]"
        case "face":
            raw_dict = seg.data.get("raw", {})
            text = raw_dict.get("faceText", "")
            return f"[表情 {text}]"
        case "at":
            qq = seg.data.get("qq", "")
            user_id = int(qq) if qq.isdigit() else None
            if user_id:
                info = await bot.get_group_member_info(
                    group_id=group_id, user_id=user_id
                )
                nickname = info.get("card", qq)
                if nickname == "":
                    nickname = info.get("nickname", qq)
                return f"@{nickname}"
            else:
                return "@未知用户"
        case _:
            return str(seg)


async def onebot_message_to_text(
    bot: OneBotV11Bot, message: OneBotMessage, group_id: int
) -> str:
    """Convert a OneBotMessage to plain text for summarization."""
    seg_texts = []
    for seg in message:
        seg_texts.append(await segment_to_text(bot, seg, group_id))
    return "".join(seg_texts)


async def onebot_message_to_model(
    bot: OneBotV11Bot,
    message: OneBotMessage,
    group_id: int,
    msg_id: int,
    user_id: int,
) -> MessageModel:
    content = await onebot_message_to_text(bot, message, group_id)
    try:
        if user_id:
            if group_id:
                member_info = await bot.get_group_member_info(
                    group_id=group_id, user_id=user_id
                )
                nickname = member_info.get("card", str(user_id))
                if nickname == "":
                    nickname = member_info.get("nickname", str(user_id))
            else:
                user_info = await bot.get_stranger_info(user_id=user_id)
                nickname = user_info.get("nickname", str(user_id))
        else:
            nickname = str(user_id)
    except Exception as e:
        logger.error(
            f"Error fetching member info for user {user_id} in group {group_id}: {e}"
        )
        nickname = str(user_id)
    return MessageModel(
        time=int(time.time()), msg_id=msg_id, nickname=nickname, content=content
    )


@msg_recorder.handle()
async def record_message(
    event: Event = EventParam(),  # type: ignore
    message: Message = EventMessage(),
    bot: OneBotV11Bot = BotParam(),  # type: ignore
):
    # TODO: Reply handling
    logger.debug(f"Received message: {message} from event: {event}")

    if not isinstance(event, GroupMessageEvent):
        logger.debug("Message is not a group message, ignoring.")
        return

    logger.debug("Message is a group message, processing...")
    logger.debug(
        f"Group ID: {event.group_id}, User ID: {event.user_id}, Message: {message}"
    )
    logger.debug(f"Group Name: {event.group_id}, User Name: {event.sender.nickname}")

    forward_id = None
    if isinstance(message, OneBotMessage):
        logger.debug(f"Total segments in message: {len(message)}")
        for seg in message:
            logger.debug(f"Segment type: {seg.type}, data: {seg.data}")
            if seg.type == "forward":
                forward_id = seg.data.get("id")
                logger.debug(f"Found forward message with ID: {forward_id}")
                break

    if isinstance(bot, OneBotV11Bot):
        logger.debug(f"Bot ID: {bot.self_id}")
        member_info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=event.user_id
        )
        logger.debug(f"Member info: {member_info}")
        if forward_id:
            try:
                forward_messages = await bot.get_forward_msg(id=str(forward_id))
                logger.debug(f"Forward messages: {forward_messages}")
            except Exception as e:
                logger.error(f"Error fetching forward messages: {e}")
    else:
        logger.warning("Bot is not an instance of OneBotV11Bot, cannot log bot ID.")

    if isinstance(message, OneBotMessage) and isinstance(bot, OneBotV11Bot):
        group_id = event.group_id
        with store.get_or_create_table(MessageModel, f"group_{group_id}") as table:
            msg_model = await onebot_message_to_model(
                bot, message, group_id, event.message_id, event.user_id
            )
            try:
                table.insert(msg_model)
                logger.debug(f"Inserted message model into database: {msg_model}")
            except Exception as e:
                logger.error(f"Error inserting message model into database: {e}")

            # Remove old messages to keep only the latest 10000 messages
            KEEP_ONLY_LATEST_MESSAGES = 10000
            try:
                deleted_count = table.keep_top_k(KEEP_ONLY_LATEST_MESSAGES, "time")
                logger.debug(
                    f"Deleted {deleted_count} old messages from table {table.table_name}"
                )
            except Exception as e:
                logger.error(
                    f"Error deleting old messages from table {table.table_name}: {e}"
                )


@msg_summarize.handle()
async def message_summarize_handler(
    event: Event = EventParam(),  # type: ignore
    args: Message = CommandArg(),
    matcher: Matcher = MatcherParam(),  # type: ignore
):
    if not isinstance(event, GroupMessageEvent):
        await msg_summarize.finish("This command can only be used in group chats.")
        return

    group_id = event.group_id
    with store.get_or_create_table(MessageModel, f"group_{group_id}") as table:
        k = int(args.extract_plain_text().strip() or "100")
        messages = table.top_k(k, "time")
        logger.debug(f"Fetched top {k} messages for summarization: {messages}")
        if not messages:
            await msg_summarize.finish("No messages to summarize.")
            return

        messages.reverse()  # Reverse to have oldest messages first for better summarization context

        try:
            summary = await message_summarize(messages)
            logger.debug(f"Generated summary: {summary}")
        except Exception as e:
            logger.error(f"Error during message summarization: {e}")
            await msg_summarize.finish("An error occurred while summarizing messages.")
        await msg_summarize.finish(summary)
