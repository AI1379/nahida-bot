"""Claimed outbox processing; uncertain sends never switch to another channel."""

from datetime import UTC, datetime, timedelta
from typing import Any

from nahida_bot_sdk.api import BotAPI
from nahida_bot_sdk.messaging import OutboundMessage
from .models import NotificationRequest, RoutingPreferences, owner_key, select_route


class NotificationDelivery:
    def __init__(self, api: BotAPI) -> None:
        self.api = api

    async def tick(self) -> None:
        now = datetime.now(UTC)
        records = await self.api.plugin_data_list("notification:")
        for key, record in records.items():
            if record["status"] in ("accepted", "unknown", "expired"):
                continue
            try:
                await self._process(key, record, now)
            except Exception as exc:
                self.api.logger.warning(
                    "notification.process_failed",
                    notification_id=record.get("id"),
                    error=str(exc),
                )

    async def _process(self, key: str, record: dict, now: datetime) -> None:
        request = NotificationRequest.model_validate(record["request"])
        if now >= request.expires_at:
            await self._save(key, record, status="expired", reason="validity_ended")
            return
        if record["next_attempt_at"] and now < datetime.fromisoformat(
            record["next_attempt_at"]
        ):
            return
        # A crash or cancellation during a channel send has an uncertain outcome.
        if record["status"] == "sending" and not record["target"].startswith(
            "desktop:"
        ):
            await self._save(key, record, status="unknown", reason="send_interrupted")
            return
        preferences = RoutingPreferences.model_validate(
            await self.api.plugin_data_get(f"preferences:{owner_key(record['owner'])}")
            or {},
        )
        try:
            result = await self.api.call_plugin_service(
                "host.desktop:v1", "endpoints", {}, actor_account_key=record["owner"]
            )
            endpoints = result["endpoints"]
        except (ValueError, RuntimeError):
            endpoints = []
        route = select_route(record, endpoints, preferences)
        if route.status == "pending":
            await self._defer(key, record, now, route.reason)
            return
        if route.status == "unknown":
            await self._save(key, record, status="unknown", reason=route.reason)
            return
        node_id = route.node_id
        target = f"desktop:{node_id}" if node_id else "private"
        claimed = {
            **record,
            "status": "sending",
            "target": target,
            "reason": route.reason,
            "next_attempt_at": (now + timedelta(seconds=30)).isoformat(),
            "attempts": [
                *record["attempts"],
                {"at": now.isoformat(), "target": target},
            ],
        }
        if not await self.api.plugin_data_compare_and_set(key, record, claimed):
            return
        if node_id:
            await self._send_desktop(key, claimed, request, now)
        else:
            await self._send_private(key, claimed, request)

    async def _send_desktop(
        self,
        key: str,
        record: dict,
        request: NotificationRequest,
        now: datetime,
    ) -> None:
        try:
            result = await self.api.call_plugin_service(
                "host.desktop:v1",
                "deliver",
                {
                    "node_id": record["target"].removeprefix("desktop:"),
                    "notification_id": record["id"],
                    "title": request.title,
                    "body": request.body,
                },
                actor_account_key=record["owner"],
            )
        except Exception:
            await self._defer(key, record, now, "desktop_result_unknown")
            return
        receipt = result.get("receipt") or {}
        if result.get("ok") and receipt.get("status") == "accepted":
            await self._save(key, record, status="accepted", reason="desktop_accepted")
        elif receipt.get("status") == "suppressed":
            await self._save(
                key, record, status="pending", target="", reason="desktop_suppressed"
            )
        elif result.get("definite") and not result.get("ok"):
            await self._save(
                key, record, status="pending", target="", reason="desktop_unavailable"
            )
        else:
            await self._defer(key, record, now, "desktop_result_unknown")

    async def _send_private(
        self, key: str, record: dict, request: NotificationRequest
    ) -> None:
        channel, separator, account_id = record["owner"].partition(":user:")
        if not separator or not channel or not account_id:
            await self._save(
                key, record, status="unknown", reason="invalid_private_binding"
            )
            return
        try:
            message_id = await self.api.send_message(
                account_id,
                OutboundMessage(
                    text=f"{request.title}\n\n{request.body}",
                    extra={"chat_address": f"{channel}:private:{account_id}"},
                ),
                channel=channel,
            )
        except Exception:
            await self._save(
                key, record, status="unknown", reason="channel_result_unknown"
            )
            return
        await self._save(
            key,
            record,
            status="accepted",
            reason="channel_accepted",
            message_id=message_id,
        )

    async def _defer(self, key: str, record: dict, now: datetime, reason: str) -> None:
        await self._save(
            key,
            record,
            status="pending",
            reason=reason,
            next_attempt_at=(now + timedelta(seconds=30)).isoformat(),
        )

    async def _save(self, key: str, record: dict, **updates: Any) -> None:
        await self.api.plugin_data_compare_and_set(key, record, {**record, **updates})
