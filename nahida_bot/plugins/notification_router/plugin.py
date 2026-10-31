"""Persistent outbox and routing policy; legacy cron and chat remain independent."""

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any

from nahida_bot_sdk.plugin import Plugin
from nahida_bot_sdk.services import PluginServiceContext

from .delivery import NotificationDelivery
from .models import NotificationRequest, RoutingPreferences, owner_key


class NotificationRouterPlugin(Plugin):
    async def on_load(self) -> None:
        self.delivery = NotificationDelivery(self.api)
        self.api.register_plugin_service("notifications.v1", self.handle, desktop=True)
        self.api.register_tool(
            "notification_preferences",
            "Read or update your personal notification routing. "
            "fallback_enabled sends to your own private account; no group fallback. "
            "Omitted preferences are preserved.",
            RoutingPreferences.model_json_schema(),
            self.preferences_tool,
        )

    async def on_enable(self) -> None:
        self.api.spawn_interval_task("deliver-outbox", self.tick, interval_seconds=10)

    async def preferences_tool(self, **kwargs: Any) -> str:
        try:
            result = await self.handle(
                "configure" if kwargs else "preferences",
                kwargs,
                PluginServiceContext(self.api.actor_account_key),
            )
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, PermissionError) as exc:
            return f"Error: {exc}"

    async def handle(
        self, operation: str, arguments: dict[str, Any], context: PluginServiceContext
    ) -> dict[str, Any]:
        owner = context.actor_account_key
        prefix = owner_key(owner)
        preferences_key = f"preferences:{prefix}"
        if operation in ("preferences", "configure"):
            old = await self.api.plugin_data_get(preferences_key)
            preferences = RoutingPreferences.model_validate(
                {**(old or {}), **arguments}
            )
            if operation == "configure":
                if not await self.api.plugin_data_compare_and_set(
                    preferences_key, old, preferences.model_dump()
                ):
                    raise ValueError("Preferences changed; retry")
            return preferences.model_dump()
        if operation == "list":
            records = await self.api.plugin_data_list(f"notification:{prefix}:")
            return {
                "items": sorted(
                    records.values(), key=lambda item: item["created_at"], reverse=True
                )[:100]
            }
        if operation != "submit" or not context.caller_plugin_id:
            raise PermissionError("Only server plugins may submit notification intents")
        request = NotificationRequest.model_validate(arguments)
        digest = sha256(
            f"{context.caller_plugin_id}:{request.key}".encode()
        ).hexdigest()
        notification_id = f"{prefix}:{digest}"
        key = f"notification:{notification_id}"
        record = {
            "id": notification_id,
            "owner": owner,
            "source": context.caller_plugin_id,
            "request": request.model_dump(mode="json"),
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "next_attempt_at": "",
            "target": "",
            "attempts": [],
            "reason": "queued",
        }
        created = await self.api.plugin_data_compare_and_set(key, None, record)
        if not created:
            existing = await self.api.plugin_data_get(key)
            if existing is None or existing["request"] != record["request"]:
                raise ValueError("Notification key already used with different content")
        return {"notification_id": notification_id, "created": created}

    async def tick(self) -> None:
        await self.delivery.tick()
