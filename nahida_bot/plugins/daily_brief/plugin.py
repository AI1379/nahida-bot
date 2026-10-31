"""Recoverable daily snapshot creation, separate from delivery and live tasks."""

from datetime import UTC, date, datetime
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from nahida_bot_sdk.desktop import DesktopSurfaceContext, DesktopSurfaceView
from nahida_bot_sdk.plugin import Plugin
from nahida_bot_sdk.services import PluginServiceContext

from .models import BriefPreferences


def owner_key(owner: str) -> str:
    if not owner:
        raise PermissionError("An authenticated account is required")
    return sha256(owner.encode()).hexdigest()


class DailyBriefPlugin(Plugin):
    async def on_load(self) -> None:
        self.sources = self.manifest.config.get("sources", ["todo:tasks.v1"])
        if not isinstance(self.sources, list) or not all(
            isinstance(s, str) for s in self.sources
        ):
            raise ValueError("sources must be a list of versioned services")
        self.api.register_plugin_service("briefs.v1", self.handle, desktop=True)
        for name in ("brief", "drawer"):
            self.api.register_desktop_surface_provider(name, self.surface)
        self.api.register_tool(
            "daily_brief_preferences",
            "Read or update your fixed-time daily brief. "
            "Enable explicitly; omitted settings are preserved. Desktop long text is always silent.",
            BriefPreferences.model_json_schema(),
            self.preferences_tool,
        )

    async def on_enable(self) -> None:
        self.api.spawn_interval_task(
            "daily-brief", self.tick, interval_seconds=15, initial_delay=15
        )

    async def preferences_tool(self, **kwargs: Any) -> str:
        try:
            return json.dumps(
                await self.handle(
                    "configure" if kwargs else "preferences",
                    kwargs,
                    PluginServiceContext(self.api.actor_account_key),
                ),
                ensure_ascii=False,
            )
        except (ValueError, PermissionError) as exc:
            return f"Error: {exc}"

    async def handle(
        self, operation: str, arguments: dict[str, Any], context: PluginServiceContext
    ) -> dict[str, Any]:
        key = f"preferences:{owner_key(context.actor_account_key)}"
        stored = await self.api.plugin_data_get(key)
        if operation in ("preferences", "configure"):
            preferences = BriefPreferences.model_validate(
                {**((stored or {}).get("preferences", {})), **arguments}
            )
            if operation == "configure":
                value = {
                    "owner": context.actor_account_key,
                    "preferences": preferences.model_dump(),
                }
                if not await self.api.plugin_data_compare_and_set(key, stored, value):
                    raise ValueError("Preferences changed; retry")
            return preferences.model_dump()
        if operation == "today":
            preferences = BriefPreferences.model_validate(
                (stored or {}).get("preferences", {})
            )
            day = datetime.now(ZoneInfo(preferences.timezone)).date()
            value = await self.api.plugin_data_get(
                self.brief_key(context.actor_account_key, day)
            )
            return {"brief": value}
        raise ValueError("Unknown briefing operation")

    @staticmethod
    def brief_key(owner: str, day: date) -> str:
        return f"brief:{owner_key(owner)}:{day.isoformat()}"

    async def tick(self) -> None:
        now = datetime.now(UTC)
        for entry in (await self.api.plugin_data_list("preferences:")).values():
            try:
                await self.run_due(
                    entry["owner"],
                    BriefPreferences.model_validate(entry["preferences"]),
                    now,
                )
            except Exception as exc:
                self.api.logger.warning("daily_brief.run_failed", error=str(exc))
        self.refresh()

    async def run_due(
        self, owner: str, preferences: BriefPreferences, now: datetime
    ) -> None:
        due, expiry = preferences.window(now)
        if not preferences.enabled or not due <= now < expiry:
            return
        key = self.brief_key(owner, due.date())
        brief = await self.api.plugin_data_get(key)
        if brief is None:
            body = await self.build_body(owner, due.date())
            proposed = {
                "owner": owner,
                "date": due.date().isoformat(),
                "body": body,
                "created_at": now.isoformat(),
                "expires_at": expiry.isoformat(),
                "submitted": False,
            }
            await self.api.plugin_data_compare_and_set(key, None, proposed)
            brief = await self.api.plugin_data_get(key)
        if brief is None:
            raise RuntimeError("Brief storage did not retain the snapshot")
        if brief["submitted"]:
            return
        # Durable reconciliation across plugin stores: a restart between these
        # writes resubmits the same immutable key/content to the router.
        await self.api.call_plugin_service(
            "notification-router:notifications.v1",
            "submit",
            {
                "key": key,
                "title": f"今日简报 · {brief['date']}",
                "body": brief["body"],
                "expires_at": brief["expires_at"],
            },
            actor_account_key=owner,
        )
        await self.api.plugin_data_compare_and_set(
            key, brief, {**brief, "submitted": True}
        )
        self.refresh()

    async def build_body(self, owner: str, day: date) -> str:
        lines = [f"{day.isoformat()} 的安排", ""]
        for source in self.sources:
            try:
                result = await self.api.call_plugin_service(
                    source, "today", {"date": day.isoformat()}, actor_account_key=owner
                )
                items = result["items"]
                lines.append(f"【{source}】")
                if not items:
                    lines.append("今天没有安排。")
                for item in items[:100]:
                    title = str(item["title"])[:200]
                    deadline = (
                        f"（截止 {item['due_date']}）" if item.get("due_date") else ""
                    )
                    lines.append(f"• {title}{deadline}")
            except Exception:
                lines.append(f"【{source}】暂时无法读取，请稍后查看今日面板。")
            lines.append("")
        body = "\n".join(lines).strip()
        return (
            body if len(body) <= 12000 else body[:11950] + "\n…完整事项请查看任务列表。"
        )

    def refresh(self) -> None:
        for name in ("brief", "drawer"):
            self.api.request_desktop_surface_refresh(name)

    async def surface(
        self, context: DesktopSurfaceContext
    ) -> DesktopSurfaceView | None:
        if not context.actor_account_key:
            return None
        result = await self.handle(
            "today", {}, PluginServiceContext(context.actor_account_key)
        )
        brief = result["brief"]
        if not brief:
            return None
        return DesktopSurfaceView(
            title="今日简报",
            status=brief["date"],
            text="打开阅读当天简报；任务面板显示最新完成状态。",
            body=brief["body"],
        )
