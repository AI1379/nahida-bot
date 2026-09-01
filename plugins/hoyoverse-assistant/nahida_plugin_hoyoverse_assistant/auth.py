"""Miyoushe QR login adapter for genshin.py."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from http.cookies import BaseCookie
from typing import Any

from .client import HoyoverseClientUnavailable, HoyoverseQueryError
from .config import HoyoverseAssistantConfig


class QRLoginStatus(str, Enum):
    CREATED = "created"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"


@dataclass(slots=True, frozen=True)
class QRLoginSession:
    ticket: str
    url: str


@dataclass(slots=True, frozen=True)
class QRLoginResult:
    status: QRLoginStatus
    cookies: str = ""


class GenshinPyQRAuthenticator:
    """Expose genshin.py's CN QR primitives without opening a local window."""

    def __init__(
        self,
        config: HoyoverseAssistantConfig,
        *,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        if config.region != "cn":
            raise HoyoverseClientUnavailable("海外服暂不支持二维码登录")
        if client is not None:
            self._client = client
            return
        try:
            import genshin
        except ImportError as exc:
            raise HoyoverseClientUnavailable(
                "缺少 genshin.py 依赖；请安装 hoyoverse-assistant 可选依赖"
            ) from exc
        self._client = genshin.Client(
            lang=config.language,
            region=genshin.Region.CHINESE,
            proxy=config.proxy or None,
        )

    async def start(self) -> QRLoginSession:
        create = getattr(self._client, "_create_qrcode", None)
        if create is None:
            raise HoyoverseClientUnavailable("当前 genshin.py 版本不支持二维码登录")
        creation = await self._call(create())
        ticket = getattr(creation, "ticket", None)
        url = getattr(creation, "url", None)
        if ticket is None or url is None:
            try:
                ticket, url = creation
            except (TypeError, ValueError) as exc:
                raise HoyoverseQueryError("米游社返回了无法识别的二维码") from exc
        return QRLoginSession(ticket=str(ticket), url=str(url))

    async def check(self, ticket: str) -> QRLoginResult:
        check = getattr(self._client, "_check_qrcode", None)
        if check is None:
            raise HoyoverseClientUnavailable("当前 genshin.py 版本不支持二维码登录")
        status, cookie = await self._call(check(ticket))
        normalized = str(getattr(status, "value", status)).lower()
        if normalized.endswith("confirmed"):
            return QRLoginResult(
                QRLoginStatus.CONFIRMED,
                self._serialize_cookies(cookie),
            )
        if normalized.endswith("scanned"):
            return QRLoginResult(QRLoginStatus.SCANNED)
        return QRLoginResult(QRLoginStatus.CREATED)

    async def _call(self, request: Any) -> Any:
        try:
            async with self._semaphore:
                async with asyncio.timeout(self._config.request_timeout_seconds):
                    return await request
        except TimeoutError as exc:
            raise HoyoverseQueryError("米游社请求超时，请稍后重试") from exc
        except Exception as exc:
            raise HoyoverseQueryError.from_exception(exc) from exc

    @staticmethod
    def _serialize_cookies(cookie: BaseCookie[str] | Any) -> str:
        items = []
        for key, morsel in cookie.items():
            value = getattr(morsel, "value", morsel)
            items.append(f"{key}={value}")
        if not items:
            raise HoyoverseQueryError("二维码确认成功，但未收到登录凭据")
        return "; ".join(items)
