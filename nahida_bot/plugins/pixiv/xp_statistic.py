#
# Created by Renatus Madrigal on 04/12/2025
#

from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.matcher import Matcher
from typing import Dict, List, Tuple
import json
from pathlib import Path
from datetime import datetime, timedelta

# Statistics storage
TAG_STATS_FILE = Path("data/pixiv/tag_stats.json")
TAG_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)

# Initialize statistics if file doesn't exist
if not TAG_STATS_FILE.exists():
    TAG_STATS_FILE.write_text(json.dumps({
        "total_requests": 0,
        "tag_counts": {},
        "daily_stats": {},
        "user_stats": {}
    }))


def load_stats() -> Dict:
    """Load statistics from file"""
    return json.loads(TAG_STATS_FILE.read_text(encoding="utf-8"))


def save_stats(stats: Dict):
    """Save statistics to file"""
    TAG_STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def update_tag_stats(tags: List[str], user_id: str):
    """Update statistics for given tags"""
    stats = load_stats()
    today = datetime.now().strftime("%Y-%m-%d")

    # Update total requests
    stats["total_requests"] += 1

    # Update tag counts
    for tag in tags:
        if tag not in stats["tag_counts"]:
            stats["tag_counts"][tag] = 0
        stats["tag_counts"][tag] += 1

    # Update daily stats
    if today not in stats["daily_stats"]:
        stats["daily_stats"][today] = {"total": 0, "tags": {}}
    stats["daily_stats"][today]["total"] += 1
    for tag in tags:
        if tag not in stats["daily_stats"][today]["tags"]:
            stats["daily_stats"][today]["tags"][tag] = 0
        stats["daily_stats"][today]["tags"][tag] += 1

    # Update user stats
    if user_id not in stats["user_stats"]:
        stats["user_stats"][user_id] = {"total": 0, "tags": {}}
    stats["user_stats"][user_id]["total"] += 1
    for tag in tags:
        if tag not in stats["user_stats"][user_id]["tags"]:
            stats["user_stats"][user_id]["tags"][tag] = 0
        stats["user_stats"][user_id]["tags"][tag] += 1

    save_stats(stats)


def get_top_tags(limit: int = 10) -> List[Tuple[str, int]]:
    """Get top N most used tags"""
    stats = load_stats()
    return sorted(
        stats["tag_counts"].items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]


def get_user_top_tags(user_id: str, limit: int = 5) -> List[Tuple[str, int]]:
    """Get top N most used tags by a specific user"""
    stats = load_stats()
    if user_id not in stats["user_stats"]:
        return []
    return sorted(
        stats["user_stats"][user_id]["tags"].items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]


async def handle_tag_stats(event: MessageEvent, matcher: Matcher):
    """Handle tag statistics command"""
    stats = load_stats()
    user_id = str(event.user_id)

    # Get top tags
    top_tags = get_top_tags()
    user_top_tags = get_user_top_tags(user_id)

    # Format message
    message = "Pixiv标签统计:\n"
    message += f"总请求数: {stats['total_requests']}\n\n"

    message += "全局热门标签:\n"
    for tag, count in top_tags:
        message += f"{tag}: {count}次\n"

    if user_top_tags:
        message += f"\n你的热门标签:\n"
        for tag, count in user_top_tags:
            message += f"{tag}: {count}次\n"

    await matcher.finish(message)


# Function to be called from pixiv.py when tags are used


def record_tag_usage(tags: List[str], user_id: str):
    """Record tag usage for statistics"""
    update_tag_stats(tags, user_id)
