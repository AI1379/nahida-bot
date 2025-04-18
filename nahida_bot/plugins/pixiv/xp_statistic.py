#
# Created by Renatus Madrigal on 04/12/2025
#

from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.matcher import Matcher
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import nahida_bot.localstore as localstore

# Initialize statistics handler
stats_handler = localstore.get_json("pixiv", "tag_stats")

# Initialize statistics if empty
if not stats_handler:
    stats_handler.update({
        "total_requests": 0,
        "tag_counts": {},
        "daily_stats": {},
        "user_stats": {}
    })


def update_tag_stats(tags: List[str], user_id: str):
    """Update statistics for given tags"""
    today = datetime.now().strftime("%Y-%m-%d")

    # Update total requests
    stats_handler["total_requests"] += 1

    # Update tag counts
    for tag in tags:
        if tag not in stats_handler["tag_counts"]:
            stats_handler["tag_counts"][tag] = 0
        stats_handler["tag_counts"][tag] += 1

    # Update daily stats
    if today not in stats_handler["daily_stats"]:
        stats_handler["daily_stats"][today] = {"total": 0, "tags": {}}
    stats_handler["daily_stats"][today]["total"] += 1
    for tag in tags:
        if tag not in stats_handler["daily_stats"][today]["tags"]:
            stats_handler["daily_stats"][today]["tags"][tag] = 0
        stats_handler["daily_stats"][today]["tags"][tag] += 1

    # Update user stats
    if user_id not in stats_handler["user_stats"]:
        stats_handler["user_stats"][user_id] = {"total": 0, "tags": {}}
    stats_handler["user_stats"][user_id]["total"] += 1
    for tag in tags:
        if tag not in stats_handler["user_stats"][user_id]["tags"]:
            stats_handler["user_stats"][user_id]["tags"][tag] = 0
        stats_handler["user_stats"][user_id]["tags"][tag] += 1


def get_top_tags(limit: int = 10) -> List[Tuple[str, int]]:
    """Get top N most used tags"""
    return sorted(
        stats_handler["tag_counts"].items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]


def get_user_top_tags(user_id: str, limit: int = 5) -> List[Tuple[str, int]]:
    """Get top N most used tags by a specific user"""
    if user_id not in stats_handler["user_stats"]:
        return []
    return sorted(
        stats_handler["user_stats"][user_id]["tags"].items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]


async def handle_tag_stats(event: MessageEvent, matcher: Matcher):
    """Handle tag statistics command"""
    user_id = str(event.user_id)

    # Get top tags
    top_tags = get_top_tags()
    user_top_tags = get_user_top_tags(user_id)

    # Format message
    message = "Pixiv标签统计:\n"
    message += f"总请求数: {stats_handler['total_requests']}\n\n"

    message += "全局热门标签:\n"
    for tag, count in top_tags:
        message += f"{tag}: {count}次\n"

    if user_top_tags:
        message += f"\n你的热门标签:\n"
        for tag, count in user_top_tags:
            message += f"{tag}: {count}次\n"

    await matcher.finish(message)


def record_tag_usage(tags: List[str], user_id: str):
    """Record tag usage for statistics"""
    update_tag_stats(tags, user_id)
