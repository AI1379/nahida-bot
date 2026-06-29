"""GitHub issue and pull request notifier plugin."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

import httpx
from nahida_bot_sdk import (
    ChatAddress,
    CommandResult,
    InboundMessage,
    OutboundMessage,
    Plugin,
    WebhookRequest,
    WebhookResponse,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DELIVERY_CACHE_KEY = "deliveries"
DYNAMIC_TARGETS_KEY = "dynamic_targets"
ITEM_STATE_KEY = "items"
POLLING_INITIALIZED_KEY = "polling_initialized"


class GitHubWebhookConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    path: str = "github"
    secret: str = ""


class GitHubPollingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=10)
    api_base_url: str = "https://api.github.com"
    token: str = ""


class GitHubRegistrationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False


class GitHubNotifierConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    repo: str = "OWNER/REPO"
    target_chat_addresses: list[str] = Field(default_factory=list)
    webhook: GitHubWebhookConfig = Field(default_factory=GitHubWebhookConfig)
    polling: GitHubPollingConfig = Field(default_factory=GitHubPollingConfig)
    registration: GitHubRegistrationConfig = Field(
        default_factory=GitHubRegistrationConfig
    )

    @field_validator("repo")
    @classmethod
    def _strip_repo(cls, value: str) -> str:
        return value.strip()

    @field_validator("target_chat_addresses")
    @classmethod
    def _strip_targets(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]

    @model_validator(mode="after")
    def _validate_modes(self) -> "GitHubNotifierConfig":
        if self.is_repo_configured and not (
            self.webhook.enabled or self.polling.enabled
        ):
            raise ValueError(
                "At least one of webhook.enabled or polling.enabled is required"
            )
        return self

    @property
    def is_repo_configured(self) -> bool:
        return bool(self.repo and self.repo != "OWNER/REPO" and "/" in self.repo)


@dataclass(slots=True, frozen=True)
class _GitHubItem:
    kind: str
    number: int
    title: str
    url: str
    state: str
    user: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.number}"


class GitHubNotifierPlugin(Plugin):
    """Push GitHub issue and PR open/close events to configured chats."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._config = GitHubNotifierConfig.model_validate(manifest.config or {})
        self._polling_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._warned_missing_secret = False

    async def on_load(self) -> None:
        if not self._config.is_repo_configured:
            self.api.logger.warning(
                "github_notifier.disabled_unconfigured_repo",
                repo=self._config.repo,
            )
            return

        self.api.logger.info(
            "github_notifier.loaded",
            repo=self._config.repo,
            webhook_enabled=self._config.webhook.enabled,
            webhook_path=self._config.webhook.path,
            polling_enabled=self._config.polling.enabled,
            polling_interval_seconds=self._config.polling.interval_seconds,
            configured_targets=len(self._config.target_chat_addresses),
            registration_enabled=self._config.registration.enabled,
        )

        if self._config.registration.enabled:
            self.api.register_command(
                "github_watch",
                self._cmd_watch,
                description="Subscribe the current chat to GitHub notifications.",
            )
            self.api.register_command(
                "github_unwatch",
                self._cmd_unwatch,
                description="Unsubscribe the current chat from GitHub notifications.",
            )
            self.api.register_command(
                "github_watch_status",
                self._cmd_watch_status,
                description="Show GitHub notification subscription status.",
            )

        if self._config.webhook.enabled:
            if not self._config.webhook.secret:
                self.api.logger.warning(
                    "github_notifier.webhook_secret_missing",
                    path=self._config.webhook.path,
                )
                self._warned_missing_secret = True
            self.api.register_webhook_endpoint(
                self._config.webhook.path,
                self._handle_webhook,
                methods=("POST",),
            )
            self.api.logger.info(
                "github_notifier.webhook_registered",
                path=self._config.webhook.path,
                repo=self._config.repo,
            )

    async def on_enable(self) -> None:
        if not self._config.is_repo_configured:
            return
        if self._config.polling.enabled and self._polling_task is None:
            self._polling_task = asyncio.create_task(
                self._polling_loop(),
                name="github-notifier-polling",
            )
            self.api.logger.info(
                "github_notifier.polling_started",
                repo=self._config.repo,
                interval_seconds=self._config.polling.interval_seconds,
                api_base_url=self._config.polling.api_base_url,
            )

    async def on_disable(self) -> None:
        await self._stop_background_tasks()

    async def on_unload(self) -> None:
        await self._stop_background_tasks()

    async def _handle_webhook(self, request: WebhookRequest) -> WebhookResponse:
        content_type = request.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            self.api.logger.warning(
                "github_notifier.webhook_rejected",
                reason="unsupported_content_type",
                content_type=content_type,
                client_host=request.client_host,
            )
            return WebhookResponse(status_code=415, body="Expected application/json")

        if self._config.webhook.secret and not self._verify_signature(request):
            self.api.logger.warning(
                "github_notifier.webhook_rejected",
                reason="invalid_signature",
                delivery_id=request.headers.get("x-github-delivery", ""),
                client_host=request.client_host,
            )
            return WebhookResponse(status_code=403, body="Invalid signature")

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.api.logger.warning(
                "github_notifier.webhook_rejected",
                reason="invalid_json",
                delivery_id=request.headers.get("x-github-delivery", ""),
                client_host=request.client_host,
            )
            return WebhookResponse(status_code=400, body="Invalid JSON payload")

        if not isinstance(payload, dict):
            self.api.logger.warning(
                "github_notifier.webhook_rejected",
                reason="non_object_payload",
                delivery_id=request.headers.get("x-github-delivery", ""),
                client_host=request.client_host,
            )
            return WebhookResponse(status_code=400, body="Payload must be an object")

        event_name = request.headers.get("x-github-event", "")
        delivery_id = request.headers.get("x-github-delivery", "")
        action = str(payload.get("action", ""))
        payload_repo = _nested_str(payload, "repository", "full_name")
        self.api.logger.info(
            "github_notifier.webhook_received",
            github_event=event_name,
            action=action,
            delivery_id=delivery_id,
            payload_repo=payload_repo,
            client_host=request.client_host,
        )
        if delivery_id:
            if await self._delivery_seen(delivery_id):
                self.api.logger.info(
                    "github_notifier.webhook_ignored",
                    reason="duplicate_delivery",
                    github_event=event_name,
                    delivery_id=delivery_id,
                )
                return WebhookResponse(status_code=204)
            await self._remember_delivery(delivery_id)

        if event_name == "ping":
            self.api.logger.info(
                "github_notifier.webhook_ping",
                delivery_id=delivery_id,
                repo=payload_repo,
            )
            return WebhookResponse(status_code=204)
        if event_name not in {"issues", "pull_request"}:
            self.api.logger.debug(
                "github_notifier.webhook_ignored",
                reason="unsupported_event",
                github_event=event_name,
                delivery_id=delivery_id,
            )
            return WebhookResponse(status_code=204)

        if action not in {"opened", "closed"}:
            self.api.logger.debug(
                "github_notifier.webhook_ignored",
                reason="unsupported_action",
                github_event=event_name,
                action=action,
                delivery_id=delivery_id,
            )
            return WebhookResponse(status_code=204)

        if payload_repo.lower() != self._config.repo.lower():
            self.api.logger.info(
                "github_notifier.webhook_ignored",
                reason="repo_mismatch",
                github_event=event_name,
                action=action,
                delivery_id=delivery_id,
                payload_repo=payload_repo,
                configured_repo=self._config.repo,
            )
            return WebhookResponse(status_code=204)

        self.api.logger.info(
            "github_notifier.webhook_accepted",
            github_event=event_name,
            action=action,
            delivery_id=delivery_id,
            repo=self._config.repo,
        )
        self._spawn_task(
            self._process_github_payload(
                event_name,
                action,
                payload,
                source=f"webhook:{delivery_id or 'unknown'}",
            )
        )
        return WebhookResponse(status_code=202)

    async def _process_github_payload(
        self,
        event_name: str,
        action: str,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> None:
        item = _item_from_webhook(event_name, payload)
        if item is None:
            self.api.logger.warning(
                "github_notifier.webhook_item_ignored",
                reason="unsupported_or_invalid_item",
                github_event=event_name,
                action=action,
                source=source,
            )
            return
        await self._store_item_state(item)
        await self._notify(item, action, source=source)

    async def _polling_loop(self) -> None:
        try:
            while True:
                try:
                    await self._poll_once()
                except Exception as exc:  # noqa: BLE001
                    self.api.logger.exception(
                        "github_notifier.poll_failed",
                        error=str(exc),
                    )
                await asyncio.sleep(self._config.polling.interval_seconds)
        except asyncio.CancelledError:
            self.api.logger.info(
                "github_notifier.polling_stopped",
                repo=self._config.repo,
            )
            pass

    async def _poll_once(self) -> None:
        items = await self._fetch_polling_items()
        known_states = await self._load_item_states()
        initialized = bool(await self.api.plugin_data_get(POLLING_INITIALIZED_KEY))
        changes = 0

        for item in items:
            old = known_states.get(item.key)
            old_state = old.get("state", "") if isinstance(old, dict) else ""
            if initialized and item.state in {"open", "closed"}:
                if old_state and old_state != item.state:
                    action = "opened" if item.state == "open" else "closed"
                    changes += 1
                    self.api.logger.info(
                        "github_notifier.polling_state_changed",
                        repo=self._config.repo,
                        kind=item.kind,
                        number=item.number,
                        old_state=old_state,
                        new_state=item.state,
                    )
                    await self._notify(item, action, source="polling")
                elif not old_state:
                    action = "opened" if item.state == "open" else "closed"
                    changes += 1
                    self.api.logger.info(
                        "github_notifier.polling_new_item",
                        repo=self._config.repo,
                        kind=item.kind,
                        number=item.number,
                        state=item.state,
                    )
                    await self._notify(item, action, source="polling")
            known_states[item.key] = _state_record(item)

        await self.api.plugin_data_set(ITEM_STATE_KEY, known_states)
        if not initialized:
            await self.api.plugin_data_set(POLLING_INITIALIZED_KEY, True)
            self.api.logger.info(
                "github_notifier.polling_baseline_initialized",
                item_count=len(items),
                repo=self._config.repo,
            )
        else:
            self.api.logger.debug(
                "github_notifier.polling_completed",
                repo=self._config.repo,
                item_count=len(items),
                changes=changes,
            )

    async def _fetch_polling_items(self) -> list[_GitHubItem]:
        owner, repo = self._config.repo.split("/", 1)
        base_url = self._config.polling.api_base_url.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "nahida-bot-github-notifier",
        }
        if self._config.polling.token:
            headers["Authorization"] = f"Bearer {self._config.polling.token}"

        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            issues_resp = await client.get(
                f"{base_url}/repos/{owner}/{repo}/issues",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                },
            )
            issues_resp.raise_for_status()
            pulls_resp = await client.get(
                f"{base_url}/repos/{owner}/{repo}/pulls",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                },
            )
            pulls_resp.raise_for_status()

        items: list[_GitHubItem] = []
        for raw in issues_resp.json():
            if isinstance(raw, dict) and "pull_request" not in raw:
                item = _item_from_api("issue", raw)
                if item is not None:
                    items.append(item)
        for raw in pulls_resp.json():
            if isinstance(raw, dict):
                item = _item_from_api("pr", raw)
                if item is not None:
                    items.append(item)
        self.api.logger.debug(
            "github_notifier.polling_fetched",
            repo=self._config.repo,
            item_count=len(items),
        )
        return items

    async def _cmd_watch(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        address = _address_from_inbound(inbound).chat_key
        targets = await self._load_dynamic_targets()
        if address in targets:
            return CommandResult.text(f"Already watching {self._config.repo} here.")
        targets.append(address)
        await self.api.plugin_data_set(DYNAMIC_TARGETS_KEY, targets)
        self.api.logger.info(
            "github_notifier.dynamic_target_added",
            repo=self._config.repo,
            address=address,
            dynamic_targets=len(targets),
        )
        return CommandResult.text(f"Watching {self._config.repo} in {address}.")

    async def _cmd_unwatch(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        address = _address_from_inbound(inbound).chat_key
        targets = await self._load_dynamic_targets()
        if address not in targets:
            return CommandResult.text("This chat is not watching GitHub events.")
        targets = [target for target in targets if target != address]
        await self.api.plugin_data_set(DYNAMIC_TARGETS_KEY, targets)
        self.api.logger.info(
            "github_notifier.dynamic_target_removed",
            repo=self._config.repo,
            address=address,
            dynamic_targets=len(targets),
        )
        return CommandResult.text(f"Stopped watching {self._config.repo} here.")

    async def _cmd_watch_status(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        address = _address_from_inbound(inbound).chat_key
        dynamic_targets = await self._load_dynamic_targets()
        watching = address in set(self._config.target_chat_addresses + dynamic_targets)
        text = (
            f"GitHub notifier: {self._config.repo}\n"
            f"Current chat: {address}\n"
            f"Watching here: {'yes' if watching else 'no'}\n"
            f"Configured targets: {len(self._config.target_chat_addresses)}\n"
            f"Dynamic targets: {len(dynamic_targets)}\n"
            f"Webhook: {'on' if self._config.webhook.enabled else 'off'}\n"
            f"Polling: {'on' if self._config.polling.enabled else 'off'}"
        )
        return CommandResult.text(text)

    async def _notify(self, item: _GitHubItem, action: str, *, source: str) -> None:
        targets = await self._target_addresses()
        if not targets:
            self.api.logger.warning(
                "github_notifier.no_targets",
                repo=self._config.repo,
                source=source,
            )
            return
        text = _format_notification(item, action)
        self.api.logger.info(
            "github_notifier.notify_started",
            repo=self._config.repo,
            kind=item.kind,
            number=item.number,
            action=action,
            source=source,
            targets=len(targets),
        )
        for target in targets:
            try:
                address = ChatAddress.parse(target)
                if not address.is_typed:
                    raise ValueError("target must be a typed chat address")
                await self.api.send_message(
                    address.target_id,
                    OutboundMessage(
                        text=text,
                        extra={"chat_address": address.chat_key},
                    ),
                    channel=address.channel,
                )
                self.api.logger.info(
                    "github_notifier.notify_sent",
                    repo=self._config.repo,
                    kind=item.kind,
                    number=item.number,
                    action=action,
                    target=address.chat_key,
                    source=source,
                )
            except Exception as exc:  # noqa: BLE001
                self.api.logger.exception(
                    "github_notifier.notify_failed",
                    target=target,
                    source=source,
                    error=str(exc),
                )

    async def _target_addresses(self) -> list[str]:
        return _dedupe(
            [*self._config.target_chat_addresses, *(await self._load_dynamic_targets())]
        )

    async def _load_dynamic_targets(self) -> list[str]:
        raw = await self.api.plugin_data_get(DYNAMIC_TARGETS_KEY)
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    async def _load_item_states(self) -> dict[str, Any]:
        raw = await self.api.plugin_data_get(ITEM_STATE_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    async def _store_item_state(self, item: _GitHubItem) -> None:
        states = await self._load_item_states()
        states[item.key] = _state_record(item)
        await self.api.plugin_data_set(ITEM_STATE_KEY, states)

    async def _delivery_seen(self, delivery_id: str) -> bool:
        raw = await self.api.plugin_data_get(DELIVERY_CACHE_KEY)
        return isinstance(raw, list) and delivery_id in raw

    async def _remember_delivery(self, delivery_id: str) -> None:
        raw = await self.api.plugin_data_get(DELIVERY_CACHE_KEY)
        deliveries = [str(item) for item in raw] if isinstance(raw, list) else []
        if delivery_id not in deliveries:
            deliveries.append(delivery_id)
        await self.api.plugin_data_set(DELIVERY_CACHE_KEY, deliveries[-500:])

    def _verify_signature(self, request: WebhookRequest) -> bool:
        signature = request.headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            return False
        expected = (
            "sha256="
            + hmac.new(
                self._config.webhook.secret.encode("utf-8"),
                request.body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)

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
                self.api.logger.exception(
                    "github_notifier.background_task_failed",
                    error=str(exc),
                )

        task.add_done_callback(_discard)

    async def _stop_background_tasks(self) -> None:
        tasks = list(self._background_tasks)
        if self._polling_task is not None:
            tasks.append(self._polling_task)
            self._polling_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self.api.logger.info(
                "github_notifier.background_tasks_stopped",
                repo=self._config.repo,
                task_count=len(tasks),
            )
        self._background_tasks.clear()


def _item_from_webhook(event_name: str, payload: dict[str, Any]) -> _GitHubItem | None:
    if event_name == "issues":
        raw = payload.get("issue")
        if not isinstance(raw, dict) or "pull_request" in raw:
            return None
        return _item_from_api(
            "issue", raw, fallback_user=_nested_str(payload, "sender", "login")
        )
    if event_name == "pull_request":
        raw = payload.get("pull_request")
        if not isinstance(raw, dict):
            return None
        return _item_from_api(
            "pr", raw, fallback_user=_nested_str(payload, "sender", "login")
        )
    return None


def _item_from_api(
    kind: str, raw: dict[str, Any], *, fallback_user: str = ""
) -> _GitHubItem | None:
    try:
        number = int(raw.get("number"))
    except (TypeError, ValueError):
        return None
    title = str(raw.get("title") or "").strip()
    url = str(raw.get("html_url") or "").strip()
    state = str(raw.get("state") or "").strip().lower()
    user = fallback_user or _nested_str(raw, "user", "login")
    if not title or not url or state not in {"open", "closed"}:
        return None
    return _GitHubItem(
        kind=kind,
        number=number,
        title=title,
        url=url,
        state=state,
        user=user,
    )


def _format_notification(item: _GitHubItem, action: str) -> str:
    label = "Issue" if item.kind == "issue" else "PR"
    kind_icon = "📌" if item.kind == "issue" else "🔀"
    action_label = "Opened" if action == "opened" else "Closed"
    action_icon = "🟢" if action == "opened" else "🔴"
    lines = [
        f"{action_icon} GitHub {label} {action_label}",
        f"{kind_icon} #{item.number} {item.title}",
        "",
    ]
    if item.user:
        lines.append(f"👤 {item.user}")
    lines.append(f"🔗 {item.url}")
    return "\n".join(lines)


def _state_record(item: _GitHubItem) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "number": item.number,
        "title": item.title,
        "url": item.url,
        "state": item.state,
        "user": item.user,
    }


def _address_from_inbound(inbound: InboundMessage) -> ChatAddress:
    target_type = "group" if inbound.is_group else "private"
    return ChatAddress(
        channel=inbound.platform,
        target_type=target_type,
        target_id=inbound.chat_id,
    )


def _nested_str(raw: dict[str, Any], *keys: str) -> str:
    current: Any = raw
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current or "")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
