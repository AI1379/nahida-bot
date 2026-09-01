"""Adapter from genshin.py models to stable, channel-neutral reports."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Iterable
from typing import Any

from .config import HoyoverseAssistantConfig
from .domain import Game, GameAccount, Report


class HoyoverseClientUnavailable(RuntimeError):
    """The optional genshin.py dependency or credentials are unavailable."""


class HoyoverseQueryError(RuntimeError):
    """A sanitized error safe to return to a chat user."""

    @classmethod
    def from_exception(cls, exc: Exception) -> "HoyoverseQueryError":
        name = type(exc).__name__
        messages = {
            "InvalidCookies": "米游社登录已失效，请重新登录",
            "CookieException": "米游社登录凭据无法使用，请重新登录",
            "DataNotPublic": "该账号未公开战绩数据",
            "AccountNotFound": "没有找到该 UID 对应的游戏账号",
            "TooManyRequests": "米游社查询额度已用尽，请稍后再试",
            "VisitsTooFrequently": "米游社访问过于频繁，请稍后再试",
            "GeetestError": "米游社触发了人机验证，请稍后再试",
            "UserNotesAccessDenied": "该账号的实时便笺不可访问",
        }
        return cls(messages.get(name, "米游社接口暂时不可用，请稍后重试"))


class GenshinPyService:
    """Multi-game service implemented with genshin.py.

    ``client`` is injectable so model adaptation can be unit-tested without
    making network requests or requiring the optional dependency.
    """

    def __init__(
        self,
        config: HoyoverseAssistantConfig,
        *,
        cookies: str = "",
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._owned_uids: dict[Game, set[int]] | None = None
        self._owned_uids_lock = asyncio.Lock()

        if client is not None:
            self._client = client
            return
        if not cookies:
            raise HoyoverseClientUnavailable("尚未登录米游社；请私聊使用 /米游社登录")

        try:
            import genshin
        except ImportError as exc:
            raise HoyoverseClientUnavailable(
                "缺少 genshin.py 依赖；请安装 hoyoverse-assistant 可选依赖"
            ) from exc

        region = (
            genshin.Region.CHINESE if config.region == "cn" else genshin.Region.OVERSEAS
        )
        self._client = genshin.Client(
            cookies,
            lang=config.language,
            region=region,
            proxy=config.proxy or None,
        )

    async def accounts(self) -> tuple[GameAccount, ...]:
        accounts = await self._call(self._client.get_game_accounts())
        result = []
        owned: dict[Game, set[int]] = {}
        for account in accounts:
            game = self._account_game(account)
            if game is None:
                continue
            uid = int(account.uid)
            owned.setdefault(game, set()).add(uid)
            result.append(
                GameAccount(
                    game=game,
                    uid=uid,
                    level=int(getattr(account, "level", 0) or 0),
                    nickname=str(getattr(account, "name", "") or ""),
                )
            )
        self._owned_uids = owned
        return tuple(result)

    async def status(self, game: Game, uid: int) -> Report:
        if game is Game.GENSHIN:
            profile = await self._call(self._client.get_genshin_user(uid))
            lines = self._genshin_profile_lines(profile)
            notes_method = self._client.get_genshin_notes
        elif game is Game.STARRAIL:
            profile = await self._call(self._client.get_starrail_user(uid))
            lines = self._starrail_profile_lines(profile)
            notes_method = self._client.get_starrail_notes
        else:
            profile = await self._call(self._client.get_zzz_user(uid))
            lines = self._zzz_profile_lines(profile)
            notes_method = self._client.get_zzz_notes

        if not self._config.include_real_time_notes:
            lines.append("实时便笺：已由管理员关闭")
        else:
            ownership = await self._owns_uid(game, uid)
            if ownership is None:
                lines.append("实时便笺：账号归属校验暂不可用，请稍后重试")
            elif ownership:
                try:
                    notes = await self._call(notes_method(uid))
                except HoyoverseQueryError as exc:
                    lines.append(f"实时便笺：{exc}")
                else:
                    lines.extend(self._notes_lines(game, notes))
            else:
                lines.append("实时便笺：仅可查询当前 Cookies 所属的 UID")

        return Report(f"{game.label}状态 · UID {uid}", tuple(lines))

    async def challenge(
        self,
        game: Game,
        uid: int,
        *,
        mode: str,
        previous: bool,
    ) -> Report:
        period = "上期" if previous else "本期"
        normalized = mode.strip().lower()

        if game is Game.GENSHIN:
            data = await self._call(
                self._client.get_spiral_abyss(uid, previous=previous)
            )
            lines = self._standard_challenge_lines(data)
            name = "深境螺旋"
        elif game is Game.STARRAIL:
            methods = {
                "混沌": ("混沌回忆", self._client.get_starrail_challenge),
                "混沌回忆": ("混沌回忆", self._client.get_starrail_challenge),
                "虚构": ("虚构叙事", self._client.get_starrail_pure_fiction),
                "虚构叙事": ("虚构叙事", self._client.get_starrail_pure_fiction),
                "末日": ("末日幻影", self._client.get_starrail_apc_shadow),
                "末日幻影": ("末日幻影", self._client.get_starrail_apc_shadow),
            }
            try:
                name, method = methods[normalized]
            except KeyError as exc:
                raise ValueError("铁道挑战必须是：混沌、虚构或末日") from exc
            data = await self._call(method(uid, previous=previous))
            lines = self._standard_challenge_lines(data)
        else:
            methods = {
                "式舆": ("式舆防卫战", self._client.get_shiyu_defense),
                "式舆防卫战": ("式舆防卫战", self._client.get_shiyu_defense),
                "危局": ("危局强袭战", self._client.get_deadly_assault),
                "危局强袭战": ("危局强袭战", self._client.get_deadly_assault),
                "湮灭": ("湮灭模拟战", self._client.get_annihilation_simulacrum),
                "湮灭模拟战": ("湮灭模拟战", self._client.get_annihilation_simulacrum),
            }
            try:
                name, method = methods[normalized]
            except KeyError as exc:
                raise ValueError("绝区零挑战必须是：式舆、危局或湮灭") from exc
            data = await self._call(method(uid, previous=previous))
            lines = self._zzz_challenge_lines(normalized, data)

        return Report(f"{name} · {period} · UID {uid}", tuple(lines))

    async def diary(
        self,
        game: Game,
        uid: int,
        *,
        month: str | None,
    ) -> Report:
        if game is Game.GENSHIN:
            parsed_month = self._month_number(month)
            data = await self._call(
                self._client.get_genshin_diary(uid, month=parsed_month)
            )
            summary = data.data
            lines = [
                f"原石：{summary.current_primogems}",
                f"摩拉：{summary.current_mora}",
                *self._category_lines(summary.categories),
            ]
            title = "原石月报"
        elif game is Game.STARRAIL:
            parsed_month = self._month_number(month)
            data = await self._call(
                self._client.get_starrail_diary(uid, month=parsed_month)
            )
            summary = data.data
            lines = [
                f"星琼：{summary.current_hcoin}",
                f"星轨通票：{summary.current_rails_pass}",
                *self._category_lines(summary.categories),
            ]
            title = "星琼月报"
        else:
            parsed_month = self._zzz_month(month)
            data = await self._call(self._client.get_zzz_diary(uid, month=parsed_month))
            lines = [
                *(f"{item.name}：{item.num}" for item in data.income.currencies),
                *self._category_lines(data.income.polychrome_incomes),
            ]
            title = "菲林月报"

        data_month = getattr(data, "month", None) or getattr(data, "data_month", "")
        suffix = f" · {data_month}" if data_month else ""
        return Report(f"{title}{suffix} · UID {uid}", tuple(lines))

    async def _call(self, request: Awaitable[Any]) -> Any:
        try:
            async with self._semaphore:
                async with asyncio.timeout(self._config.request_timeout_seconds):
                    return await request
        except TimeoutError as exc:
            raise HoyoverseQueryError("米游社请求超时，请稍后重试") from exc
        except Exception as exc:
            raise HoyoverseQueryError.from_exception(exc) from exc

    async def _owns_uid(self, game: Game, uid: int) -> bool | None:
        if self._owned_uids is None:
            async with self._owned_uids_lock:
                if self._owned_uids is None:
                    try:
                        accounts = await self._call(self._client.get_game_accounts())
                    except HoyoverseQueryError:
                        return None
                    else:
                        owned: dict[Game, set[int]] = {}
                        for account in accounts:
                            parsed = self._account_game(account)
                            if parsed is not None:
                                owned.setdefault(parsed, set()).add(int(account.uid))
                        self._owned_uids = owned
        return uid in self._owned_uids.get(game, set())

    @staticmethod
    def _account_game(account: Any) -> Game | None:
        raw = getattr(account, "game", "")
        raw = getattr(raw, "value", raw)
        return {
            "hk4e": Game.GENSHIN,
            "genshin": Game.GENSHIN,
            "hkrpg": Game.STARRAIL,
            "starrail": Game.STARRAIL,
            "nap": Game.ZZZ,
            "zzz": Game.ZZZ,
        }.get(str(raw))

    @staticmethod
    def _genshin_profile_lines(profile: Any) -> list[str]:
        stats = profile.stats
        info = profile.info
        return [
            f"旅行者：{info.nickname} · 冒险等级 {info.level}",
            f"活跃 {stats.days_active} 天 · 成就 {stats.achievements}",
            f"角色 {stats.characters} · 深境螺旋 {stats.spiral_abyss}",
        ]

    @staticmethod
    def _starrail_profile_lines(profile: Any) -> list[str]:
        stats = profile.stats
        info = profile.info
        return [
            f"开拓者：{info.nickname} · 开拓等级 {info.level}",
            f"活跃 {stats.active_days} 天 · 成就 {stats.achievement_num}",
            f"角色 {stats.avatar_num} · 忘却之庭 {stats.abyss_process}",
        ]

    @staticmethod
    def _zzz_profile_lines(profile: Any) -> list[str]:
        stats = profile.stats
        return [
            f"活跃 {stats.active_days} 天 · 成就 {stats.achievement_count}",
            f"代理人 {stats.character_num} · 邦布 {stats.bangboo_obtained}",
            f"绳网声望：{stats.inter_knot_reputation}",
        ]

    @classmethod
    def _notes_lines(cls, game: Game, notes: Any) -> list[str]:
        if game is Game.GENSHIN:
            return [
                (
                    f"原粹树脂：{notes.current_resin}/{notes.max_resin}"
                    f"（{cls._time_hint(notes.resin_recovery_time)}）"
                ),
                f"每日委托：{notes.completed_commissions}/{notes.max_commissions}",
                f"探索派遣：{sum(item.finished for item in notes.expeditions)}/{len(notes.expeditions)} 已完成",
                f"周本减半：剩余 {notes.remaining_resin_discounts}/{notes.max_resin_discounts}",
            ]
        if game is Game.STARRAIL:
            return [
                (
                    f"开拓力：{notes.current_stamina}/{notes.max_stamina}"
                    f"（{cls._time_hint(notes.stamina_recovery_time)}）"
                ),
                f"后备开拓力：{notes.current_reserve_stamina}",
                f"每日实训：{notes.current_train_score}/{notes.max_train_score}",
                f"模拟宇宙积分：{notes.current_rogue_score}/{notes.max_rogue_score}",
                f"历战余响：剩余 {notes.remaining_weekly_discounts}/{notes.max_weekly_discounts}",
            ]

        battery = notes.battery_charge
        lines = [
            (
                f"电量：{battery.current}/{battery.max}"
                f"（{cls._time_hint(battery.full_datetime)}）"
            ),
            f"活跃度：{notes.engagement.current}/{notes.engagement.max}",
            f"刮刮卡：{'已完成' if notes.scratch_card_completed else '未完成'}",
        ]
        bounty = getattr(notes.hollow_zero, "bounty_commission", None)
        if bounty is not None:
            lines.append(f"零号空洞悬赏：{bounty.cur_completed}/{bounty.total}")
        weekly = getattr(notes, "weekly_task", None)
        if weekly is not None:
            lines.append(f"周常积分：{weekly.cur_point}/{weekly.max_point}")
        return lines

    @staticmethod
    def _standard_challenge_lines(data: Any) -> list[str]:
        if (
            getattr(data, "has_data", True) is False
            or getattr(data, "unlocked", True) is False
        ):
            return ["本期暂无挑战记录"]
        stars = int(getattr(data, "total_stars", 0)) + int(
            getattr(data, "starward_stars", 0)
        )
        return [
            f"最高层：{getattr(data, 'max_floor', '—')}",
            f"星数：{stars}",
            f"战斗次数：{getattr(data, 'total_battles', '—')}",
        ]

    @staticmethod
    def _zzz_challenge_lines(mode: str, data: Any) -> list[str]:
        if getattr(data, "has_data", True) is False:
            return ["本期暂无挑战记录"]
        if mode in {"式舆", "式舆防卫战"}:
            brief = getattr(data, "brief_info", None)
            if brief is not None:
                return [
                    f"评分：{brief.rating or '—'}",
                    f"分数：{brief.score}/{brief.max_score}",
                    f"排名：{brief.rank_percent}",
                ]
            ratings = getattr(data, "ratings", {})
            return [
                f"最高防线：{getattr(data, 'max_floor', '—')}",
                "评级："
                + " / ".join(f"{key}×{value}" for key, value in ratings.items()),
            ]
        if mode in {"危局", "危局强袭战"}:
            return [
                f"星数：{getattr(data, 'total_star', 0)}",
                f"总分：{getattr(data, 'total_score', 0)}",
                f"排名：{getattr(data, 'rank_percent', '—')}",
            ]
        if getattr(data, "unlocked", True) is False:
            return ["尚未解锁湮灭模拟战"]
        challenges = tuple(getattr(data, "challenges", ()))
        stars = sum(int(getattr(challenge, "star", 0)) for challenge in challenges)
        return [f"星数：{stars}", f"完成关卡：{len(challenges)}"]

    @staticmethod
    def _category_lines(categories: Iterable[Any]) -> list[str]:
        result = []
        for item in list(categories)[:5]:
            name = getattr(item, "name", None) or getattr(item, "source", "其他")
            name = getattr(name, "name", name)
            amount = getattr(item, "amount", None)
            if amount is None:
                amount = getattr(item, "num", 0)
            percentage = getattr(item, "percentage", None)
            if percentage is None:
                percentage = getattr(item, "percent", 0)
            result.append(f"{name}：{amount}（{percentage}%）")
        return result

    @staticmethod
    def _time_hint(value: dt.datetime) -> str:
        now = dt.datetime.now().astimezone()
        target = value.astimezone()
        if target <= now:
            return "已回满"
        delta = target - now
        total_minutes = max(1, int(delta.total_seconds() // 60))
        hours, minutes = divmod(total_minutes, 60)
        if hours:
            return f"约 {hours} 小时 {minutes} 分后回满"
        return f"约 {minutes} 分后回满"

    @staticmethod
    def _month_number(value: str | None) -> int | None:
        if value is None or not value.strip():
            return None
        try:
            month = int(value)
        except ValueError as exc:
            raise ValueError("月份应为 1 到 12") from exc
        if not 1 <= month <= 12:
            raise ValueError("月份应为 1 到 12")
        return month

    @staticmethod
    def _zzz_month(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        raw = value.strip()
        if raw.isdigit() and 1 <= int(raw) <= 12:
            return f"{dt.datetime.now().year}{int(raw):02d}"
        if len(raw) == 6 and raw.isdigit() and 1 <= int(raw[-2:]) <= 12:
            return raw
        raise ValueError("绝区零月份应为 1 到 12，或 YYYYMM")
