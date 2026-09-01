"""Nahida Bot command surface for the HoYoverse assistant."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable
from typing import Any

from nahida_bot_sdk import (
    CommandArgument,
    CommandResult,
    InboundMessage,
    OutboundMessage,
    Plugin,
    register_command,
)

from .bindings import BindingStore
from .auth import GenshinPyQRAuthenticator, QRLoginStatus
from .client import (
    GenshinPyService,
    HoyoverseClientUnavailable,
    HoyoverseQueryError,
)
from .config import HoyoverseAssistantConfig
from .credentials import CredentialStore
from .domain import Game, HoyoverseService, Report

_GAME_ARGUMENT = CommandArgument(
    name="game",
    description="原神、铁道或绝区零",
    required=True,
    choices=("原神", "铁道", "绝区零"),
)
_UID_ARGUMENT = CommandArgument(
    name="uid",
    description="游戏 UID",
    type="int",
    required=True,
)
_PERIOD_ARGUMENT = CommandArgument(
    name="period",
    description="本期或上期",
    choices=("本期", "上期"),
)
_MONTH_ARGUMENT = CommandArgument(
    name="month",
    description="留空为本月，也可填写 1 到 12",
    type="int",
)
_ZZZ_MONTH_ARGUMENT = CommandArgument(
    name="month",
    description="留空为本月，也可填写 1 到 12 或 YYYYMM",
)


class HoyoverseAssistantPlugin(Plugin):
    """Query Genshin, Star Rail, and ZZZ through one shared plugin."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._config = HoyoverseAssistantConfig.model_validate(manifest.config or {})
        self._bindings = BindingStore(api)
        self._credentials = CredentialStore(
            api, qr_ttl_seconds=self._config.qr_login_ttl_seconds
        )
        self._service: HoyoverseService | None = None
        self._services: dict[str, tuple[str, HoyoverseService]] = {}
        self._authenticator: Any | None = None

    @register_command(
        "米游社登录",
        description="在私聊中生成米游社扫码登录二维码。",
    )
    async def _cmd_login(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        if args.strip():
            return CommandResult.text("用法：/米游社登录（不要在命令中粘贴 Cookies）")
        private_error = _require_private(inbound)
        if private_error is not None:
            return private_error
        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            return actor_key
        try:
            session = await self._get_authenticator().start()
            temp_file = await self.api.create_temp_file(
                suffix=".png",
                prefix="miyoushe-login",
                purpose="Miyoushe QR login",
                ttl_seconds=self._config.qr_login_ttl_seconds,
            )
            self._write_qr_image(session.url, temp_file.path)
            await self._credentials.set_pending_qr(actor_key, session.ticket)
        except (HoyoverseClientUnavailable, HoyoverseQueryError) as exc:
            return CommandResult.text(str(exc))
        return CommandResult.outbound(
            OutboundMessage(
                text=(
                    "请使用米游社 App 扫码并确认登录，然后在本私聊发送 "
                    "/米游社登录确认。二维码会在短时间后失效。"
                ),
                attachments=[
                    temp_file.as_attachment(
                        type="photo",
                        filename="miyoushe-login.png",
                        mime_type="image/png",
                    )
                ],
            )
        )

    @register_command(
        "米游社登录确认",
        description="确认私聊中发起的米游社扫码登录。",
    )
    async def _cmd_login_confirm(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        if args.strip():
            return CommandResult.text("用法：/米游社登录确认")
        private_error = _require_private(inbound)
        if private_error is not None:
            return private_error
        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            return actor_key
        pending = await self._credentials.get_pending_qr(actor_key)
        if pending is None:
            return CommandResult.text(
                "没有待确认的二维码，或二维码已过期。请重新使用 /米游社登录。"
            )
        try:
            result = await self._get_authenticator().check(pending.ticket)
        except (HoyoverseClientUnavailable, HoyoverseQueryError) as exc:
            return CommandResult.text(str(exc))
        if result.status is QRLoginStatus.CREATED:
            return CommandResult.text(
                "二维码尚未扫码，请扫码确认后再发送 /米游社登录确认。"
            )
        if result.status is QRLoginStatus.SCANNED:
            return CommandResult.text("二维码已扫码，但尚未在米游社 App 中确认。")
        if not result.cookies:
            return CommandResult.text("二维码确认成功，但未收到登录凭据，请重新登录。")

        await self._credentials.set_cookies(actor_key, result.cookies)
        await self._credentials.delete_pending_qr(actor_key)
        self._services.pop(actor_key, None)
        try:
            service = await self._get_service(inbound)
            accounts = await service.accounts()
        except (HoyoverseClientUnavailable, HoyoverseQueryError) as exc:
            return CommandResult.text(
                f"米游社登录成功，但读取游戏账号失败：{exc}。可稍后使用 /米游社绑定。"
            )

        selected: dict[Game, int] = {}
        for account in sorted(accounts, key=lambda item: item.level, reverse=True):
            selected.setdefault(account.game, account.uid)
        await self._bindings.replace(actor_key, selected)
        if not selected:
            return CommandResult.text(
                "米游社登录成功，但没有发现原神、星穹铁道或绝区零账号。"
            )
        lines = ["米游社登录成功，已自动绑定："]
        lines.extend(f"{game.label}：{uid}" for game, uid in selected.items())
        return CommandResult.text("\n".join(lines))

    @register_command("米游社退出", description="删除当前用户保存的米游社登录凭据。")
    async def _cmd_logout(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        if args.strip():
            return CommandResult.text("用法：/米游社退出")
        private_error = _require_private(inbound)
        if private_error is not None:
            return private_error
        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            return actor_key
        removed = await self._credentials.delete_cookies(actor_key)
        await self._credentials.delete_pending_qr(actor_key)
        await self._bindings.unbind(actor_key)
        self._services.pop(actor_key, None)
        if not removed:
            return CommandResult.text("当前用户没有已保存的米游社登录。")
        return CommandResult.text("已退出米游社，并删除登录凭据和 UID 绑定。")

    @register_command(
        "米游社绑定",
        description="绑定一个游戏 UID。用法：/米游社绑定 <游戏> <UID>",
        arguments=(_GAME_ARGUMENT, _UID_ARGUMENT),
    )
    async def _cmd_bind(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        parts = args.split()
        if len(parts) != 2:
            return CommandResult.text("用法：/米游社绑定 <原神|铁道|绝区零> <UID>")
        try:
            game = Game.parse(parts[0])
            uid = _parse_uid(parts[1])
        except ValueError as exc:
            return CommandResult.text(str(exc))

        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            return actor_key
        try:
            service = await self._get_service(inbound)
            accounts = await service.accounts()
        except (HoyoverseClientUnavailable, HoyoverseQueryError) as exc:
            return CommandResult.text(str(exc))
        if not any(account.game is game and account.uid == uid for account in accounts):
            return CommandResult.text(
                f"UID {uid} 不属于当前登录的米游社账号，拒绝绑定。"
            )
        await self._bindings.bind(actor_key, game, uid)
        return CommandResult.text(f"已绑定{game.label} UID {uid}。")

    @register_command(
        "米游社解绑",
        description="解绑一个或全部游戏 UID。用法：/米游社解绑 [游戏]",
        arguments=(
            CommandArgument(
                name="game",
                description="留空时解绑全部",
                choices=("原神", "铁道", "绝区零"),
            ),
        ),
    )
    async def _cmd_unbind(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        try:
            game = Game.parse(args) if args.strip() else None
        except ValueError as exc:
            return CommandResult.text(str(exc))

        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            return actor_key
        removed = await self._bindings.unbind(actor_key, game)
        if not removed:
            return CommandResult.text("没有找到对应的 UID 绑定。")
        if game is None:
            return CommandResult.text("已解绑全部游戏 UID。")
        return CommandResult.text(f"已解绑{game.label} UID。")

    @register_command("我的米游社", description="查看当前账号绑定的游戏 UID。")
    async def _cmd_accounts(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del args, session_id
        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            return actor_key
        bindings = await self._bindings.get_all(actor_key)
        if not bindings:
            return CommandResult.text(
                "尚未绑定游戏 UID。使用 /米游社绑定 <游戏> <UID>。"
            )
        lines = ["当前 UID 绑定："]
        for game in Game:
            if game in bindings:
                lines.append(f"{game.label}：{bindings[game]}")
        return CommandResult.text("\n".join(lines))

    @register_command("原神状态", description="查询原神概览与实时便笺。")
    async def _cmd_genshin_status(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return await self._status(Game.GENSHIN, args, inbound, session_id)

    @register_command("铁道状态", description="查询星穹铁道概览与实时便笺。")
    async def _cmd_starrail_status(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return await self._status(Game.STARRAIL, args, inbound, session_id)

    @register_command("绝区零状态", description="查询绝区零概览与实时便笺。")
    async def _cmd_zzz_status(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return await self._status(Game.ZZZ, args, inbound, session_id)

    @register_command(
        "原神深渊",
        description="查询本期或上期深境螺旋。",
        arguments=(_PERIOD_ARGUMENT,),
    )
    async def _cmd_genshin_abyss(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return await self._challenge(Game.GENSHIN, "深渊", args, inbound, session_id)

    @register_command(
        "铁道挑战",
        description="查询混沌回忆、虚构叙事或末日幻影。",
        arguments=(
            CommandArgument(
                name="mode",
                description="挑战模式",
                required=True,
                choices=("混沌", "虚构", "末日"),
            ),
            _PERIOD_ARGUMENT,
        ),
    )
    async def _cmd_starrail_challenge(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        parts = args.split()
        if not parts:
            return CommandResult.text("用法：/铁道挑战 <混沌|虚构|末日> [本期|上期]")
        period = parts[1] if len(parts) > 1 else "本期"
        return await self._challenge(
            Game.STARRAIL, parts[0], period, inbound, session_id
        )

    @register_command(
        "绝区零挑战",
        description="查询式舆防卫战、危局强袭战或湮灭模拟战。",
        arguments=(
            CommandArgument(
                name="mode",
                description="挑战模式",
                required=True,
                choices=("式舆", "危局", "湮灭"),
            ),
            _PERIOD_ARGUMENT,
        ),
    )
    async def _cmd_zzz_challenge(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        parts = args.split()
        if not parts:
            return CommandResult.text("用法：/绝区零挑战 <式舆|危局|湮灭> [本期|上期]")
        period = parts[1] if len(parts) > 1 else "本期"
        return await self._challenge(Game.ZZZ, parts[0], period, inbound, session_id)

    @register_command(
        "原石月报",
        description="查询原神月度原石统计。",
        arguments=(_MONTH_ARGUMENT,),
    )
    async def _cmd_genshin_diary(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return await self._diary(Game.GENSHIN, args, inbound, session_id)

    @register_command(
        "星琼月报",
        description="查询星穹铁道月度星琼统计。",
        arguments=(_MONTH_ARGUMENT,),
    )
    async def _cmd_starrail_diary(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return await self._diary(Game.STARRAIL, args, inbound, session_id)

    @register_command(
        "菲林月报",
        description="查询绝区零月度菲林统计。",
        arguments=(_ZZZ_MONTH_ARGUMENT,),
    )
    async def _cmd_zzz_diary(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        return await self._diary(Game.ZZZ, args, inbound, session_id)

    async def _status(
        self,
        game: Game,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> CommandResult:
        del session_id
        if args.strip():
            command_game = "铁道" if game is Game.STARRAIL else game.label
            return CommandResult.text(f"用法：/{command_game}状态")
        uid = await self._bound_uid(inbound, game)
        if isinstance(uid, CommandResult):
            return uid
        try:
            service = await self._get_service(inbound)
            request = service.status(game, uid)
        except HoyoverseClientUnavailable as exc:
            return CommandResult.text(str(exc))
        return await self._run_report(request)

    async def _challenge(
        self,
        game: Game,
        mode: str,
        period: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> CommandResult:
        del session_id
        try:
            previous = _parse_period(period)
        except ValueError as exc:
            return CommandResult.text(str(exc))
        uid = await self._bound_uid(inbound, game)
        if isinstance(uid, CommandResult):
            return uid
        try:
            service = await self._get_service(inbound)
            request = service.challenge(game, uid, mode=mode, previous=previous)
        except HoyoverseClientUnavailable as exc:
            return CommandResult.text(str(exc))
        return await self._run_report(request)

    async def _diary(
        self,
        game: Game,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> CommandResult:
        del session_id
        uid = await self._bound_uid(inbound, game)
        if isinstance(uid, CommandResult):
            return uid
        try:
            service = await self._get_service(inbound)
            request = service.diary(game, uid, month=args.strip() or None)
        except HoyoverseClientUnavailable as exc:
            return CommandResult.text(str(exc))
        return await self._run_report(request)

    async def _bound_uid(
        self, inbound: InboundMessage, game: Game
    ) -> int | CommandResult:
        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            return actor_key
        bindings = await self._bindings.get_all(actor_key)
        uid = bindings.get(game)
        if uid is None:
            return CommandResult.text(
                f"尚未绑定{game.label} UID。使用 /米游社绑定 {game.label} <UID>。"
            )
        return uid

    async def _get_service(self, inbound: InboundMessage) -> HoyoverseService:
        if self._service is not None:
            return self._service
        actor_key = _actor_key(inbound)
        if isinstance(actor_key, CommandResult):
            raise HoyoverseClientUnavailable("当前渠道没有提供稳定的用户 ID")
        cookies = await self._credentials.get_cookies(actor_key)
        if not cookies:
            raise HoyoverseClientUnavailable("尚未登录米游社；请私聊使用 /米游社登录")
        fingerprint = hashlib.sha256(cookies.encode("utf-8")).hexdigest()
        cached = self._services.get(actor_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        service = GenshinPyService(self._config, cookies=cookies)
        self._services[actor_key] = (fingerprint, service)
        return service

    def _get_authenticator(self) -> Any:
        if self._authenticator is None:
            self._authenticator = GenshinPyQRAuthenticator(self._config)
        return self._authenticator

    @staticmethod
    def _write_qr_image(url: str, path: str) -> None:
        try:
            import qrcode
        except ImportError as exc:
            raise HoyoverseClientUnavailable(
                "缺少 qrcode 依赖；请安装 hoyoverse-assistant 可选依赖"
            ) from exc
        with open(path, "wb") as stream:
            qrcode.make(url).save(stream)

    @staticmethod
    async def _run_report(request: Awaitable[Report]) -> CommandResult:
        try:
            report = await request
        except (HoyoverseClientUnavailable, HoyoverseQueryError, ValueError) as exc:
            return CommandResult.text(str(exc))
        return CommandResult.text(report.render())


def _actor_key(inbound: InboundMessage) -> str | CommandResult:
    account_key = inbound.sender_account_key
    if not account_key:
        return CommandResult.text("当前渠道没有提供稳定的用户 ID，无法使用 UID 绑定")
    return account_key


def _require_private(inbound: InboundMessage) -> CommandResult | None:
    chat_type = inbound.chat_context.chat_type if inbound.chat_context else ""
    if inbound.is_group or (chat_type and chat_type != "private"):
        return CommandResult.text("为保护账号安全，请私聊机器人使用这个命令。")
    return None


def _parse_uid(value: str) -> int:
    raw = value.strip()
    if not raw.isdigit() or not 6 <= len(raw) <= 12:
        raise ValueError("UID 应为 6 到 12 位数字")
    return int(raw)


def _parse_period(value: str) -> bool:
    normalized = value.strip() or "本期"
    if normalized in {"本期", "当前", "current"}:
        return False
    if normalized in {"上期", "previous", "prev"}:
        return True
    raise ValueError("期数必须是：本期或上期")
