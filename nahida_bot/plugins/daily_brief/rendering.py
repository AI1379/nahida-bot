"""Human-readable brief items for task and calendar sources."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def source_label(source: str) -> str:
    return {"calendar": "日程", "todo": "待办"}.get(source.partition(":")[0], "事项")


def render_item(item: dict[str, Any], timezone: str) -> str:
    title = str(item["title"])[:200]
    zone = ZoneInfo(timezone)
    detail = ""
    if item.get("start"):
        if item.get("all_day"):
            detail = "全天"
        else:
            start = datetime.fromisoformat(item["start"]).astimezone(zone)
            end = datetime.fromisoformat(item["end"]).astimezone(zone)
            detail = f"{start:%H:%M}–{end:%H:%M}"
    elif item.get("due_at"):
        deadline = datetime.fromisoformat(item["due_at"]).astimezone(zone)
        detail = f"截止 {deadline:%m-%d %H:%M}"
    elif item.get("due_date"):
        detail = f"截止 {item['due_date']}"
    return f"• {title}" + (f"（{detail}）" if detail else "")
