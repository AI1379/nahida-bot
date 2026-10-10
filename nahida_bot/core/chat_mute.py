"""Admin commands and lazy expiry for persistent chat-scoped mute mode (#47)."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import structlog

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.config import MuteConfig
from nahida_bot.db.repositories.sqlite_chat_mute_repo import (
    ChatMuteStatus,
    SQLiteChatMuteRepository,
)
from nahida_bot.identity.authorization import AuthorizationGate

logger = structlog.get_logger(__name__)
_DURATION = re.compile(r"([0-9]{1,9})([smhd])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MAX_DURATION_SECONDS = 31_536_000


@dataclass(frozen=True, slots=True)
class MuteCommandResult:
    text: str
    stop_agent: bool = False


class ChatMuteService:
    def __init__(
        self,
        repository: SQLiteChatMuteRepository,
        config: MuteConfig,
        authorization: AuthorizationGate | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self.config = config
        self._authorization = authorization
        self._clock = clock

    async def status(self, address: ChatAddress) -> ChatMuteStatus:
        if not self.config.enabled or not address.is_typed:
            return ChatMuteStatus()
        status = await self._repository.status(address.chat_key, self._clock())
        if status.expired:
            logger.info("chat_mute.expired", chat_address=address.chat_key)
        return status

    async def command(
        self,
        action: Literal["mute", "unmute"],
        args: str,
        address: ChatAddress,
        actor: str,
    ) -> MuteCommandResult:
        if not self.config.enabled:
            return MuteCommandResult("会话禁言功能未启用（mute.enabled=false）。")
        if not address.is_typed:
            return MuteCommandResult("无法确定当前聊天入口，未修改禁言状态。")
        raw = args.strip().lower()
        if action == "mute" and raw == "status":
            status = await self.status(address)
            text = self.describe(status)
            if status.expired:
                text = f"{self.config.notice.online}\n{text}"
            return MuteCommandResult(text)
        if self._authorization is None or not self._authorization.is_admin(actor):
            return MuteCommandResult(
                "只有 authorization.admins 中的管理员可以修改禁言状态。"
            )
        if action == "unmute":
            if raw:
                return MuteCommandResult("用法：/unmute")
            changed = await self._repository.clear(address.chat_key)
            if changed:
                logger.info(
                    "chat_mute.cleared", chat_address=address.chat_key, actor=actor
                )
            return MuteCommandResult(
                self.config.notice.online if changed else "当前会话未处于静默状态。"
            )
        duration = self._parse_duration(raw)
        if duration is None:
            return MuteCommandResult(
                "用法：/mute [30m|2h|1d] 或 /mute status；时长需在 1 秒到 365 天之间。"
            )
        now = self._clock()
        await self._repository.set_until(address.chat_key, now + duration, actor, now)
        logger.info(
            "chat_mute.set",
            chat_address=address.chat_key,
            actor=actor,
            duration_seconds=duration,
            muted_until=now + duration,
        )
        return MuteCommandResult(
            f"{self.config.notice.offline}（{self._format_duration(duration)}）",
            stop_agent=True,
        )

    def _parse_duration(self, raw: str) -> int | None:
        if not raw:
            return self.config.default_duration_seconds
        match = _DURATION.fullmatch(raw)
        if match is None:
            return None
        duration = int(match[1]) * _UNIT_SECONDS[match[2].lower()]
        return duration if 1 <= duration <= _MAX_DURATION_SECONDS else None

    def describe(self, status: ChatMuteStatus) -> str:
        if not status.muted or status.muted_until is None:
            return "当前会话未处于静默状态。"
        remaining = max(1, math.ceil(status.muted_until - self._clock()))
        return f"当前会话已静默，剩余 {self._format_duration(remaining)}。"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        for suffix, unit in reversed(tuple(_UNIT_SECONDS.items())):
            if seconds % unit == 0:
                return f"{seconds // unit}{suffix}"
        return f"{seconds}s"
