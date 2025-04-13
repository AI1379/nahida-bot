#
# Created by Renatus Madrigal on 04/12/2025
#

from pixivpy3 import AppPixivAPI, PixivError
import nonebot
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import MessageEvent, Message, MessageSegment, Bot, GroupMessageEvent
from nonebot.log import logger
import nahida_bot.localstore as localstore
import re
import random
import math


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

/pixiv.request [xN] [sN] [r18] [tags] (tag1 tag2 tag3)

xN: N是要获取的图片数量。默认值为1。
sN: N是图片的健康等级。默认值为2。如果设置了R18为True，则健康等级将被忽略。
r18: 是否包含R18图片。默认值为False。
tags: 要搜索的标签。如果不提供，将获取系统推荐的图片。

举例:

/pixiv.request x5 s2 tags 三月七 - 这将获取5张健康等级为2的图片，标签为三月七。
/pixiv.request s2 r18 tags 三月七 - 这将获取1张R18图片，标签为三月七，并忽略健康等级。

"""

# Regex to parse the /pixiv.request command.
# It parses:
# - An optional x parameter: "x" immediately followed by one or more digits (group "count")
# - An optional s parameter: "s" with one or more digits (group "sanity")
# - An optional "r18" literal (group "r18")
# - An optional "tags" literal followed by one or more tags.
#   Tags are sequences of non-whitespace characters; multiple tags may be separated by whitespace.
ARG_PARSE_REGEX = r"^(?:\s*x(?P<count>\d+))?(?:\s+s(?P<sanity>\d+))?(?:\s+(?P<r18>r18))?(?:\s+tags(?:\s+(?P<tags>\S+(?:\s+\S+)*))?)?\s*$"

REFRESH_TOKEN = nonebot.get_driver().config.pixiv_refresh_token
logger.info(f"Pixiv refresh token: {REFRESH_TOKEN}")

_pixiv_api = AppPixivAPI()
_cache = localstore.register_cache("pixiv")

try:
    auth_result = _pixiv_api.auth(refresh_token=REFRESH_TOKEN)
    logger.success(f"Pixiv authentication successful.")
    logger.success(f"Pixiv account name: {auth_result["user"]["name"]}")
except PixivError as e:
    logger.error(f"Pixiv authentication failed: {e}")
    raise e


def extract_arguments(command: str):
    """Extract arguments from the command using ARG_PARSE_REGEX."""
    match = re.match(ARG_PARSE_REGEX, command)
    if not match:
        return None  # or raise an exception/error if you prefer
    count = int(match.group("count")) if match.group("count") else 1
    sanity = int(match.group("sanity")) if match.group("sanity") else 4
    r18 = bool(match.group("r18"))
    if r18:
        sanity = 6
    tags = match.group("tags").split() if match.group("tags") else []
    return {
        "count": count,
        "sanity": sanity,
        "r18": r18,
        "tags": tags,
    }


def weight_sample(items, weights, k: int) -> list:
    keys = [(-math.log(max(1e-10, random.uniform(0, 1))) / w, i)
            for i, w in enumerate(weights)]
    keys.sort(reverse=True)
    return [items[i] for _, i in keys[:k]]


async def pixiv_request_handler(bot: Bot, event: MessageEvent, args: Message, matcher: Matcher) -> None:
    """Handle the pixiv request command."""
    FACTOR = 10
    raw_arg = args.extract_plain_text()
    if not raw_arg:
        logger.debug(f"Empty argument: {raw_arg}")
        await matcher.finish(HELP_MESSAGE_ZH)
    parsed_args = extract_arguments(raw_arg)
    if not parsed_args:
        logger.debug(f"Invalid argument: {raw_arg}")
        await matcher.finish(HELP_MESSAGE_ZH)
    count = parsed_args["count"]
    sanity = parsed_args["sanity"]  # record["sanity_level"]
    r18 = parsed_args["r18"]  # record["x_restrict"]
    tags = parsed_args["tags"]
    result = []

    def filter_func(rec):
        if r18:
            return 4 <= rec["sanity_level"] <= sanity
        return rec["sanity_level"] <= sanity and not rec["x_restrict"]

    def search_and_filter(tag: str, count: int):
        res = []
        qs = {
            "word": tag,
            "search_target": "partial_match_for_tags",
            "sort": "popular_desc"
        }
        while len(res) < count:
            rec = _pixiv_api.search_illust(**qs)
            try:
                filtered = [record for record in rec["illusts"]
                            if filter_func(record)]
            except KeyError as e:
                logger.error(f"KeyError: {e}")
                logger.error(f"Record: {rec}")
            res.extend(filtered)
            qs = _pixiv_api.parse_qs(rec["next_url"])
        return res

    if tags:
        for tag in tags:
            try:
                filtered = search_and_filter(tag, count * FACTOR)
                logger.debug(f"Find {len(filtered)} after filter")
                weight = [record["total_bookmarks"] for record in filtered]
                result.extend(weight_sample(filtered, weight, count * FACTOR))
            except PixivError as e:
                logger.error(f"Pixiv search failed: {e}")
                await matcher.finish(f"Pixiv search failed: {e}")
            except Exception as e:
                logger.error(f"Error occurred: {e}")
                await matcher.finish(f"Pixiv search failed")
                raise e
    else:
        try:
            rec = _pixiv_api.illust_recommended()
            filtered = [record for record in rec["illusts"]
                        if filter_func(record)]
            weight = [record["total_bookmarks"] for record in filtered]
            result.extend(weight_sample(filtered, weight, count * FACTOR))
        except PixivError as e:
            logger.error(f"Pixiv recommended failed: {e}")
            await matcher.finish(f"Pixiv recommended failed: {e}")

    result = random.sample(result, count)

    messages = []
    group = isinstance(event, GroupMessageEvent)

    for record in result:
        logger.debug(f"illust id: {record["id"]}")
        logger.debug(f"illust title: {record["title"]}")
        logger.debug(f"illust tags: {record["tags"]}")
        logger.debug(f"illust sanity level: {record["sanity_level"]}")
        logger.debug(f"illust x_restrict: {record["x_restrict"]}")
        logger.debug(f"total bookmarks: {record["total_bookmarks"]}")
        msg = construct_message_chain(record)
        if not group:
            await matcher.send(msg)
        else:
            messages.append(msg)

    bot_info = await bot.get_login_info()
    self_id = bot_info["user_id"]
    self_name = bot_info["nickname"]

    def to_json(msg: Message):
        return {
            "type": "node",
            "data": {
                "name": self_name,
                "uin": f"{self_id}",
                "content": msg
            }
        }

    if group:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=event.group_id,
            messages=[to_json(msg) for msg in messages]
        )

    await matcher.finish(f"{count} images are sent.")


def construct_message_chain(record):
    """Construct the message chain for the image."""
    FILE_BASENAME = "pixiv_{}_{}.jpg"
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
    message += MessageSegment.text(f"Id: {img_id}\n")
    message += MessageSegment.text(f"Page count: {record["page_count"]}\n")
    message += MessageSegment.text(f"Bookmarks: {record['total_bookmarks']}\n")

    for i, url in enumerate(image_urls):
        filename = FILE_BASENAME.format(img_id, i)

        def download_image(f):
            _pixiv_api.download(url, fname=f)
            logger.debug(f"Downloaded image: {filename}")
        full_path = _cache.add_file(filename, download_image, mode="wb")

        message += MessageSegment.image(full_path)

    return message
