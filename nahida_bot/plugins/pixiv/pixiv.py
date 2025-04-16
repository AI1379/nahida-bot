#
# Created by Renatus Madrigal on 04/12/2025
#

from pixivpy3 import AppPixivAPI, PixivError, ByPassSniApi
import nonebot
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import MessageEvent, Message, MessageSegment, Bot, GroupMessageEvent
from nonebot.log import logger
import nahida_bot.localstore as localstore
from nahida_bot.utils.command_parser import split_arguments
from nahida_bot.plugins.pixiv.pixiv_pool import PixivPool
import asyncio
import random
from typing import Callable, Union, Any, Coroutine, Literal, Type

HELP_MESSAGE = """
/pixiv.request [xN] [sN] [r18] [tags] (tag1 tag2 tag3)

xN: N is the number of images to fetch. Default is 1.
sN: N is the sanity level. Default is 2. Note that if R18 is set to True, the sanity level will be ignored.
r18: Whether to include R18 images. Default is False.
tags: Tags to search for. If not provided, it will fetch the system's recommended images.

Example:

/pixiv.request x5 s2 tags March7th - This will fetch 5 images with a sanity level of 2 with the tag "March7th".
/pixiv.request s2 r18 tags March7th - This will fetch 1 R18 image with the tag "March7th" and ignore the sanity level.
"""

HELP_MESSAGE_ZH = """

/pixiv.request [xN] [sN] [r18] [ban-ai] [tags] (tag1 tag2 tag3)

xN: N是要获取的图片数量。默认值为1。
sN: N是图片的健康等级。默认值为2。如果设置了R18为True，则健康等级将被忽略。
r18: 是否包含R18图片。默认值为False。
ban-ai: 是否避免AI生成的图片。默认值为False。
tags: 要搜索的标签。如果不提供，将获取系统推荐的图片。

举例:

/pixiv.request x5 s2 tags 三月七 - 这将获取5张健康等级为2的图片，标签为三月七。
/pixiv.request s2 r18 tags 三月七 - 这将获取1张R18图片，标签为三月七，并忽略健康等级。

"""

COUNT_FACTOR = 2
MAX_IMAGE_PER_PAGE = 5
REFRESH_TOKENS = nonebot.get_driver().config.pixiv_refresh_tokens
BYPASS_GFW = False

logger.info(f"Pixiv bypass GFW: {BYPASS_GFW}")
logger.info(f"Pixiv max image per page: {MAX_IMAGE_PER_PAGE}")
logger.info(f"Pixiv refresh tokens: {REFRESH_TOKENS}")

_cache = localstore.register_cache("pixiv")

_pixiv_pool = PixivPool(refresh_tokens=REFRESH_TOKENS)
_api_generator = _pixiv_pool.all_api()
_current_token, _pixiv_api = next(_api_generator)
logger.info(f"Pixiv current token: {_current_token}")


def extract_arguments(command: str, **kwargs):
    """Extract arguments from the command using ARG_PARSE_REGEX."""
    args = split_arguments(command)
    if len(args) == 0:
        return None
    if "help" in args:
        return None
    result = {
        "count": 1,
        "sanity": 4,
        "r18": False,
        # Because non-AI generated images are much less than AI generated images,
        # we set the default to True to avoid missing out on good images.
        "ai": True,
        "tags": [],
        "related_id": None,
    }
    in_tags = False
    for arg in args:
        if arg.startswith("x"):
            try:
                result["count"] = int(arg[1:])
            except ValueError:
                logger.error(f"Invalid count argument: {arg}")
                return None
        elif arg.startswith("s"):
            try:
                result["sanity"] = int(arg[1:])
            except ValueError:
                logger.error(f"Invalid sanity argument: {arg}")
                return None
        elif arg == "r18":
            result["r18"] = True
        elif arg == "ban-ai":
            result["ai"] = False
        elif arg.startswith("tags"):
            in_tags = True
        elif in_tags:
            result["tags"].append(arg)
        elif kwargs["related"]:
            result["related_id"] = arg
        else:
            return None
    return result


def weight_sample(items, k: int) -> list:
    weights = [item["total_bookmarks"] for item in items]
    keys = [(random.normalvariate(1, 0.2) * w, i)
            for i, w in enumerate(weights)]
    keys.sort(reverse=True)
    return [items[i] for _, i in keys[:k]]


class PixivErrorInResponse(Exception):
    """Custom exception for Pixiv API errors in response."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return self.message


def get_and_filter(count: int,
                   filter_func: Callable,
                   get_type: Literal["recommend", "search", "related"],
                   tag: str = None,
                   ai: bool = False,
                   **kwargs) -> list:
    """Get and filter Pixiv images based on the specified tag and count."""
    global _current_token, _pixiv_api
    res = []
    init_qs_table = {
        "search": {
            "word": kwargs["tag"] if "tag" in kwargs else None,
            "search_target": "partial_match_for_tags",
            "sort": "popular_desc",
            "search_ai_type": 1 if ai else 0,
        },
        "recommend": {},
        "related": {
            "illust_id": kwargs["id"],
        }
    }
    qs = init_qs_table[get_type]
    first_attempt_token = _current_token
    while len(res) < count:
        if get_type == "recommend":
            rec = _pixiv_api.illust_recommended(**qs)
        elif get_type == "related":
            rec = _pixiv_api.illust_related(**qs)
        else:
            rec = _pixiv_api.search_illust(**qs)
        if 'error' in rec:
            _current_token, _pixiv_api = next(_api_generator)
            qs = init_qs_table[get_type]
            logger.warning(f"Pixiv API error: {rec['error']}")
            logger.warning(f"Switching to next token: {_current_token}")
            if _current_token == first_attempt_token:
                raise PixivErrorInResponse(rec['error']['message'])
            continue
        try:
            filtered = [record for record in rec["illusts"]
                        if filter_func(record)]
            res.extend(filtered)
            if "next_url" not in rec:
                break
            qs = _pixiv_api.parse_qs(rec["next_url"])
        except KeyError as err:
            logger.error(f"KeyError: {err}")
            logger.error(f"Record: {rec}")
    logger.info(f"Using token: {_current_token}")
    return res


def get_filter(r18: bool, ai: bool, sanity: int) -> Callable[[Any], bool]:
    def filter_func(record):
        if r18:
            return 4 <= record["sanity_level"] <= sanity
        # The GOD DAMN Pixiv API returns 2 for AI generated images and 1 for non-AI generated images.
        if not ai and record["illust_ai_type"] == 2:
            return False
        return record["sanity_level"] <= sanity and not record["x_restrict"]

    return filter_func


async def pixiv_request_handler(bot: Bot,
                                event: MessageEvent,
                                args: Message,
                                matcher: Type[Matcher],
                                related: bool = False) -> None:
    """Handle the pixiv request command."""
    raw_arg = args.extract_plain_text()
    if not raw_arg:
        logger.debug(f"Empty argument: {raw_arg}")
        await matcher.finish(HELP_MESSAGE_ZH)
    parsed_args = extract_arguments(raw_arg, related=related)
    if not parsed_args:
        logger.debug(f"Invalid argument: {raw_arg}")
        await matcher.finish(HELP_MESSAGE_ZH)
    logger.info(f"Parsed arguments: {parsed_args}")
    count = parsed_args["count"]
    sanity = parsed_args["sanity"]  # record["sanity_level"]
    r18 = parsed_args["r18"]  # record["x_restrict"]
    ai = parsed_args["ai"]
    tags = parsed_args["tags"]
    related_id = parsed_args["related_id"] if related else None
    result = []
    filter_func = get_filter(r18, ai, sanity)
    try:
        if tags:
            for tag in tags:
                filtered = get_and_filter(count * COUNT_FACTOR, filter_func, "search", tag=tag, ai=ai)
                result.extend(weight_sample(filtered, count * COUNT_FACTOR))
        elif related_id:
            filtered = get_and_filter(count * COUNT_FACTOR, filter_func, "related", id=related_id, ai=ai)
            result.extend(weight_sample(filtered, count * COUNT_FACTOR))
        else:
            filtered = get_and_filter(count * COUNT_FACTOR, filter_func, "recommend", ai=ai)
            result.extend(weight_sample(filtered, count * COUNT_FACTOR))
    except PixivError as err:
        logger.error(f"Pixiv internal error: {err}")
        await matcher.finish(f"Pixiv internal error: {err}")
    except PixivErrorInResponse as err:
        logger.error(f"Pixiv failed in response: {err}")
        await matcher.send(f"Pixiv failed in response: {err}")
        raise err
    except Exception as err:
        logger.error(f"Error occurred: {err}")
        await matcher.send(f"Pixiv search failed")
        raise err

    result = random.sample(result, min(len(result), count))
    bot_info = await bot.get_login_info()

    def to_json(raw_msg: MessageSegment):
        self_id = bot_info["user_id"]
        self_name = bot_info["nickname"]
        return {
            "type": "node",
            "data": {
                "name": self_name,
                "uin": f"{self_id}",
                "content": raw_msg
            }
        }

    tasks = []
    for record in result:
        logger.debug(f"illust id: {record['id']}")
        logger.debug(f"illust title: {record['title']}")
        logger.debug(f"illust tags: {record['tags']}")
        logger.debug(f"illust sanity level: {record['sanity_level']}")
        logger.debug(f"illust x_restrict: {record['x_restrict']}")
        logger.debug(f"total bookmarks: {record['total_bookmarks']}")
        tasks.append(construct_message_chain(record))

    messages: list[Union[MessageSegment, BaseException]] = await asyncio.gather(*tasks, return_exceptions=True)
    exception_msg = [msg for msg in messages if isinstance(msg, BaseException)]
    filtered_msg = [msg for msg in messages if not isinstance(msg, BaseException)]
    group = isinstance(event, GroupMessageEvent)
    if group:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=event.group_id,
            messages=[to_json(msg_elem) for msg_elem in filtered_msg]
        )
    else:
        await bot.call_api(
            "send_private_forward_msg",
            user_id=event.user_id,
            messages=[to_json(msg_elem) for msg_elem in filtered_msg]
        )
    for msg in exception_msg:
        if isinstance(msg, PixivErrorInResponse):
            logger.error(f"Pixiv failed in response: {msg}")
        else:
            logger.error(f"Error occurred: {msg}")

    count = len(filtered_msg)
    err_count = len(exception_msg)
    err_msg = f"Failed to send {err_count} images." if err_count else ""

    await matcher.finish(f"{count} images are sent. {err_msg}")


async def async_pixiv_download(url: str, filename: str) -> str:
    with _cache.get_file_handler(filename, mode="wb") as file:
        await asyncio.to_thread(lambda: _pixiv_api.download(url, fname=file.get_raw()))
        logger.success(f"Downloaded image: {filename}")
        path = file.file_path

    return path


async def construct_message_chain(record) -> MessageSegment:
    """Construct the message chain for the image."""
    file_basename = "pixiv_{}_{}.jpg"
    img_id = record["id"]
    if record["page_count"] > 1:
        image_urls = [page["image_urls"]["original"]
                      for page in record["meta_pages"]]
    else:
        image_urls = [record["meta_single_page"]["original_image_url"]]

    logger.debug(f"Image URLs: {image_urls}")

    message = MessageSegment.text(f"Title: {record['title']}\n")
    message += MessageSegment.text(
        f"Tags: {', '.join([tag["name"] for tag in record["tags"]])}\n")
    message += MessageSegment.text(f"Sanity Level: {record['sanity_level']}\n")
    message += MessageSegment.text(f"Page count: {record["page_count"]}\n")
    message += MessageSegment.text(f"Bookmarks: {record['total_bookmarks']}\n")
    message += MessageSegment.text(f"Total bookmarks: {record['total_bookmarks']}\n")
    message += MessageSegment.text(f"URL: https://www.pixiv.net/artworks/{img_id}\n")
    message += MessageSegment.text(f"Date: {record['create_date']}\n")
    message += MessageSegment.text(f"Is AI: {"Yes" if record['illust_ai_type'] == 2 else "No"}\n")

    async def download_image(f: str, url: str):
        full_path = _cache.get_file(f)
        if not full_path:
            full_path = await async_pixiv_download(url, f)
            logger.info(f"Downloaded image: {full_path}")
        else:
            logger.info(f"Cache hit: {full_path}")
        return full_path

    tasks = [
        download_image(file_basename.format(img_id, i), url)
        for i, url in enumerate(image_urls) if i < MAX_IMAGE_PER_PAGE
    ]

    result: list[Union[str, BaseException]] = await asyncio.gather(*tasks, return_exceptions=True)

    for i, res in enumerate(result):
        if isinstance(res, BaseException):
            logger.error(f"Error downloading image {img_id}_{i}: {res}")
            continue
        message += MessageSegment.image(res)

    return message
