"""Small domain model shared by commands, storage, and the API adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Game(str, Enum):
    """Games supported by the first plugin release."""

    GENSHIN = "genshin"
    STARRAIL = "starrail"
    ZZZ = "zzz"

    @property
    def label(self) -> str:
        return {
            Game.GENSHIN: "原神",
            Game.STARRAIL: "崩坏：星穹铁道",
            Game.ZZZ: "绝区零",
        }[self]

    @classmethod
    def parse(cls, value: str) -> "Game":
        normalized = value.strip().lower().replace("：", "").replace(":", "")
        aliases = {
            "原神": cls.GENSHIN,
            "genshin": cls.GENSHIN,
            "ys": cls.GENSHIN,
            "铁道": cls.STARRAIL,
            "星铁": cls.STARRAIL,
            "星穹铁道": cls.STARRAIL,
            "崩坏星穹铁道": cls.STARRAIL,
            "starrail": cls.STARRAIL,
            "hsr": cls.STARRAIL,
            "绝区零": cls.ZZZ,
            "zzz": cls.ZZZ,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError("游戏必须是：原神、铁道或绝区零") from exc


@dataclass(slots=True, frozen=True)
class Report:
    """A channel-neutral text report."""

    title: str
    lines: tuple[str, ...]

    def render(self) -> str:
        if not self.lines:
            return self.title
        return "\n".join((self.title, *self.lines))


@dataclass(slots=True, frozen=True)
class GameAccount:
    """One game account owned by the logged-in Miyoushe user."""

    game: Game
    uid: int
    level: int = 0
    nickname: str = ""


class HoyoverseService(Protocol):
    """API surface consumed by command handlers and replaced by fakes in tests."""

    async def status(self, game: Game, uid: int) -> Report: ...

    async def challenge(
        self,
        game: Game,
        uid: int,
        *,
        mode: str,
        previous: bool,
    ) -> Report: ...

    async def diary(
        self,
        game: Game,
        uid: int,
        *,
        month: str | None,
    ) -> Report: ...

    async def accounts(self) -> tuple[GameAccount, ...]: ...
