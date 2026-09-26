"""GitHub Channel plugin (webhook events in, issue comments out)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.channels.github.client import GitHubClient, GitHubClientError
from nahida_bot.channels.github.config import GitHubChannelConfig, parse_github_config
from nahida_bot.channels.github.event_converter import GitHubEventConverter
from nahida_bot.channels.github.tools import register_issue_tools
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import MessageObserved, MessagePayload, MessageReceived
from nahida_bot.core.group_policy import GroupInteractionPolicy
from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.base import (
    OutboundMessage,
    Plugin,
    WebhookRequest,
    WebhookResponse,
)

if TYPE_CHECKING:
    from nahida_bot.plugins.base import BotAPI as BotAPIProtocol
    from nahida_bot.plugins.manifest import PluginManifest

logger = structlog.get_logger(__name__)

_DEDUP_LRU_SIZE = 1024
_ORG_CACHE_MAX = 512
_ORG_CACHE_TTL = 1800.0
_ORG_CACHE_ERROR_TTL = 60.0
_TARGET_PATTERN = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)#(?P<number>\d+)$"
)


class _OrgMembership:
    """Cached org-membership answer for one sender login."""

    __slots__ = ("is_member", "expires_at")

    def __init__(self, is_member: bool, expires_at: float) -> None:
        self.is_member = is_member
        self.expires_at = expires_at


class GitHubChannelPlugin(Plugin):
    """GitHub channel plugin (repository webhooks + REST issue comments)."""

    def __init__(self, api: BotAPIProtocol, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._channel_id = manifest.id
        self._config = parse_github_config(manifest.config)
        self._client: GitHubClient | None = None
        self._org_member_cache: OrderedDict[str, _OrgMembership] = OrderedDict()
        self._converter = GitHubEventConverter(
            self._config, org_member_checker=self._org_member_allowed
        )
        self._seen_delivery_ids: OrderedDict[str, None] = OrderedDict()
        self._webhook_handle: Any = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._bot_info_task: asyncio.Task[None] | None = None

    # ── ChannelService surface ────────────────────────────────────

    @property
    def channel_id(self) -> str:
        """Unique identifier used by the channel registry."""
        return self._channel_id

    @property
    def config(self) -> GitHubChannelConfig:
        """Parsed GitHub plugin configuration."""
        return self._config

    @property
    def bot_login(self) -> str:
        """Bot account login (config override or discovered via /user)."""
        return self._converter.bot_login

    @property
    def reply_to_inbound(self) -> bool | None:
        """Optional channel override for router default reply-to behavior."""
        return self.config.reply_to_inbound

    # ── lifecycle ─────────────────────────────────────────────────

    async def on_load(self) -> None:
        """Create the API client, best-effort bot identity, register surfaces."""
        self._ensure_client()
        await self._refresh_bot_login_once(log_failure=True)
        if not self.config.webhook_secret:
            logger.warning(
                "github.webhook_secret_missing",
                path=self.config.webhook_path,
                note="non-ping webhook deliveries will be rejected (fail-closed)",
            )
        self._webhook_handle = self.api.register_webhook_endpoint(
            self.config.webhook_path,
            self._handle_webhook,
            methods=("POST",),
        )
        self.api.register_channel(self)
        if self.config.enable_issue_tools:
            register_issue_tools(self.api, self._config, self._ensure_client)
        self.api.register_prompt_supplement(
            key="markdown_rendering",
            instruction=(
                "The current channel renders full GitHub Flavored Markdown "
                "including tables, task lists, nested lists, and fenced code "
                "blocks. Keep replies in Markdown."
            ),
            channel=self.channel_id,
        )
        logger.info(
            "github.loaded",
            channel=self.channel_id,
            webhook_path=self.config.webhook_path,
            bot_login=self.bot_login,
            group_trigger_mode=self.config.group_trigger_mode,
            allowed_repos=len(self.config.allowed_repos),
            allowed_orgs=len(self.config.allowed_orgs),
        )

    async def on_enable(self) -> None:
        """Retry bot identity discovery when it failed at load time."""
        if not self.bot_login:
            self._start_bot_login_retry()

    async def on_disable(self) -> None:
        """Stop background work and close HTTP client resources."""
        await self._stop_bot_login_retry()
        await self._stop_background_tasks()
        if self._webhook_handle is not None:
            self._webhook_handle.unsubscribe()
            self._webhook_handle = None
        if self._client is not None:
            await self._client.close()
            self._client = None
        logger.info("github.stopped", channel=self.channel_id)

    # ── webhook intake ────────────────────────────────────────────

    async def _handle_webhook(self, request: WebhookRequest) -> WebhookResponse:
        content_type = request.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            logger.warning(
                "github.webhook_rejected",
                reason="unsupported_content_type",
                content_type=content_type,
                client_host=request.client_host,
            )
            return WebhookResponse(status_code=415, body="Expected application/json")

        event_name = request.headers.get("x-github-event", "")
        delivery_id = request.headers.get("x-github-delivery", "")

        if event_name == "ping":
            logger.info(
                "github.webhook_ping", delivery_id=delivery_id, channel=self.channel_id
            )
            return WebhookResponse(status_code=204)

        if not self.config.webhook_secret:
            logger.warning(
                "github.webhook_rejected",
                reason="secret_not_configured",
                github_event=event_name,
                delivery_id=delivery_id,
            )
            return WebhookResponse(
                status_code=403, body="Webhook secret is not configured"
            )
        if not self._verify_signature(request):
            logger.warning(
                "github.webhook_rejected",
                reason="invalid_signature",
                github_event=event_name,
                delivery_id=delivery_id,
                client_host=request.client_host,
            )
            return WebhookResponse(status_code=403, body="Invalid signature")

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning(
                "github.webhook_rejected",
                reason="invalid_json",
                github_event=event_name,
                delivery_id=delivery_id,
            )
            return WebhookResponse(status_code=400, body="Invalid JSON payload")
        if not isinstance(payload, dict):
            logger.warning(
                "github.webhook_rejected",
                reason="non_object_payload",
                github_event=event_name,
                delivery_id=delivery_id,
            )
            return WebhookResponse(status_code=400, body="Payload must be an object")

        if delivery_id:
            if self._delivery_seen(delivery_id):
                logger.debug(
                    "github.webhook_ignored",
                    reason="duplicate_delivery",
                    github_event=event_name,
                    delivery_id=delivery_id,
                )
                return WebhookResponse(status_code=204)
            self._remember_delivery(delivery_id)

        self._spawn_task(
            self.handle_inbound_event(
                {"event": event_name, "payload": payload, "delivery": delivery_id}
            )
        )
        return WebhookResponse(status_code=202)

    def _verify_signature(self, request: WebhookRequest) -> bool:
        signature = request.headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            return False
        expected = (
            "sha256="
            + hmac.new(
                self.config.webhook_secret.encode("utf-8"),
                request.body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)

    def _delivery_seen(self, delivery_id: str) -> bool:
        return delivery_id in self._seen_delivery_ids

    def _remember_delivery(self, delivery_id: str) -> None:
        self._seen_delivery_ids[delivery_id] = None
        self._seen_delivery_ids.move_to_end(delivery_id)
        while len(self._seen_delivery_ids) > _DEDUP_LRU_SIZE:
            self._seen_delivery_ids.popitem(last=False)

    # ── inbound ───────────────────────────────────────────────────

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Normalize one GitHub webhook event and publish a bot event."""
        try:
            await self._handle_inbound_event_inner(event)
        except Exception as exc:  # noqa: BLE001 - webhook task must never crash
            logger.exception(
                "github.inbound_event_failed",
                error=str(exc),
                channel=self.channel_id,
            )

    async def _handle_inbound_event_inner(self, event: dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        converted = await self._converter.to_inbound(event_name, payload)
        if converted is None:
            logger.debug(
                "github.event_filtered",
                github_event=event_name,
                action=str(payload.get("action") or ""),
                delivery=str(event.get("delivery") or ""),
                channel=self.channel_id,
            )
            return

        inbound = converted.inbound
        if converted.kind == "context":
            await self._publish_message_event(inbound, MessageObserved, "lifecycle")
            return

        decision = GroupInteractionPolicy(
            mode=self.config.group_trigger_mode,
            observe_untriggered=self.config.group_context_capture,
        ).decide(inbound)
        logger.debug(
            "github.message_decision",
            channel=self.channel_id,
            reason=decision.reason,
            observe=decision.observe,
            respond=decision.respond,
            chat_id=inbound.chat_id,
            mentions_bot=inbound.mentions_bot,
        )
        if not decision.observe:
            return
        await self._publish_message_event(
            inbound,
            MessageReceived if decision.respond else MessageObserved,
            decision.reason,
        )

    async def _publish_message_event(
        self,
        inbound: Any,
        event_cls: type[MessageReceived] | type[MessageObserved],
        decision_reason: str,
    ) -> None:
        address = ChatAddress.from_inbound(
            inbound.platform, inbound.chat_id, chat_type="group"
        )
        if not address.is_typed:
            logger.warning("github.chat_type_missing", chat_id=inbound.chat_id)
            return
        session_id = MessageRouter.make_session_id(address)
        await self.api.publish_event(
            event_cls(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="github",
            )
        )
        logger.debug(
            "github.message_published",
            channel=self.channel_id,
            emitted_event=event_cls.__name__,
            session_id=session_id,
            decision_reason=decision_reason,
        )

    # ── org membership (allowed_orgs) ──────────────────────────────

    async def _org_member_allowed(self, login: str) -> bool:
        """Whether ``login`` belongs to any allowed organization.

        Definitive answers (member / not a member) are cached for
        ``_ORG_CACHE_TTL``; probe failures deny the sender but only for
        ``_ORG_CACHE_ERROR_TTL`` so a transient API error self-heals.
        """
        now = time.monotonic()
        cached = self._org_member_cache.get(login)
        if cached is not None and now < cached.expires_at:
            self._org_member_cache.move_to_end(login)
            return cached.is_member

        client = self._ensure_client()
        for org in self._config.allowed_orgs:
            try:
                member = await client.check_org_membership(org, login)
            except GitHubClientError as exc:
                logger.warning(
                    "github.org_membership_probe_failed",
                    org=org,
                    login=login,
                    error=str(exc),
                    channel=self.channel_id,
                )
                self._cache_org_membership(login, False, now + _ORG_CACHE_ERROR_TTL)
                return False
            if member:
                self._cache_org_membership(login, True, now + _ORG_CACHE_TTL)
                return True
        self._cache_org_membership(login, False, now + _ORG_CACHE_TTL)
        return False

    def _cache_org_membership(
        self, login: str, is_member: bool, expires_at: float
    ) -> None:
        self._org_member_cache[login] = _OrgMembership(is_member, expires_at)
        self._org_member_cache.move_to_end(login)
        while len(self._org_member_cache) > _ORG_CACHE_MAX:
            self._org_member_cache.popitem(last=False)

    # ── outbound ──────────────────────────────────────────────────

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Post one normalized outbound message as an issue/PR comment."""
        logger.debug(
            "github.send_start",
            channel=self.channel_id,
            target=target,
            text_chars=len(message.text),
            attachment_count=len(message.attachments),
        )
        resolved = self._resolve_target(target, message)
        if resolved is None:
            logger.warning(
                "github.target_invalid", target=target, channel=self.channel_id
            )
            return ""
        owner, repo, number = resolved

        body = self._comment_body(message)
        if not body:
            logger.warning(
                "github.send_empty",
                target=target,
                channel=self.channel_id,
                attachment_count=len(message.attachments),
            )
            return ""
        if message.attachments:
            logger.warning(
                "github.attachments_unsupported",
                target=target,
                attachment_count=len(message.attachments),
                channel=self.channel_id,
            )

        result = await self._ensure_client().add_comment(owner, repo, number, body=body)
        comment_id = str(result.get("id") or "")
        logger.debug(
            "github.send_done",
            channel=self.channel_id,
            target=target,
            comment_id=comment_id,
        )
        return comment_id

    def _resolve_target(
        self, target: str, message: OutboundMessage
    ) -> tuple[str, str, int] | None:
        """Resolve a send target into ``(owner, repo, number)``."""
        candidate = target
        chat_address = message.extra.get("chat_address")
        if isinstance(chat_address, str) and chat_address:
            try:
                address = ChatAddress.parse(chat_address)
            except ValueError:
                address = None
            if address is not None and address.channel == self.channel_id:
                candidate = address.target_id
        match = _TARGET_PATTERN.match(candidate.strip())
        if match is None:
            return None
        return (
            match.group("owner"),
            match.group("repo"),
            int(match.group("number")),
        )

    def _comment_body(self, message: OutboundMessage) -> str:
        text = message.text.rstrip()
        if self.config.include_reasoning and message.reasoning:
            thinking = f"[💭 思考过程]\n{message.reasoning}"
            text = f"{thinking}\n\n{text}" if text else thinking
        limit = self.config.max_comment_length
        if len(text) > limit:
            text = text[: limit - 20].rstrip() + "\n\n…(truncated)"
        return text

    # ── bot identity ──────────────────────────────────────────────

    async def _refresh_bot_login_once(self, *, log_failure: bool) -> bool:
        if self.config.bot_login:
            self._converter = self._converter.with_bot_login(self.config.bot_login)
            return True
        try:
            info = await self._ensure_client().get_authenticated_user()
        except Exception as exc:  # noqa: BLE001 - startup must not crash on API
            if log_failure:
                logger.warning(
                    "github.bot_identity_unavailable",
                    error=str(exc),
                    channel=self.channel_id,
                )
            return False
        login = str(info.get("login") or "").strip()
        if not login:
            if log_failure:
                logger.warning("github.bot_identity_missing_login", keys=list(info)[:8])
            return False
        self._converter = self._converter.with_bot_login(login)
        logger.info(
            "github.bot_identity_loaded", channel=self.channel_id, bot_login=login
        )
        return True

    def _start_bot_login_retry(self) -> None:
        if self._bot_info_task is not None and not self._bot_info_task.done():
            return
        self._bot_info_task = asyncio.create_task(self._bot_login_retry_loop())

    async def _stop_bot_login_retry(self) -> None:
        task = self._bot_info_task
        self._bot_info_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _bot_login_retry_loop(self) -> None:
        delay = 2.0
        while not self.bot_login:
            if await self._refresh_bot_login_once(log_failure=False):
                return
            logger.info("github.bot_identity_retry_scheduled", delay=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    # ── task management ───────────────────────────────────────────

    def _spawn_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _discard(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.exception("github.background_task_failed", error=str(exc))

        task.add_done_callback(_discard)

    async def _stop_background_tasks(self) -> None:
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    # ── wiring ────────────────────────────────────────────────────

    def _ensure_client(self) -> GitHubClient:
        if self._client is None:
            self._client = GitHubClient(self._config)
        return self._client
