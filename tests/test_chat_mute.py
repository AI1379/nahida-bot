"""Mute deadlines, administrator checks, expiry claims and restart persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.chat_mute import ChatMuteService
from nahida_bot.core.config import MuteConfig, Settings
from nahida_bot.core.config_schema import build_config_schema
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_chat_mute_repo import SQLiteChatMuteRepository
from nahida_bot.identity.authorization import AuthorizationGate

ADDRESS = ChatAddress(channel="test", target_type="group", target_id="c1")


@pytest.fixture
async def mute_service(tmp_path: Path):
    engine = DatabaseEngine(tmp_path / "mute.db")
    await engine.initialize()
    clock = [1000.0]
    service = ChatMuteService(
        SQLiteChatMuteRepository(engine),
        MuteConfig(enabled=True),
        AuthorizationGate(frozenset({"test:user:admin"})),
        clock=lambda: clock[0],
    )
    try:
        yield service, clock, engine
    finally:
        await engine.close()


@pytest.mark.parametrize(
    "duration,seconds",
    [("", 3600), ("30m", 1800), ("2H", 7200), ("1d", 86400), ("1s", 1)],
)
async def test_mute_duration_and_current_chat_only(mute_service, duration, seconds):
    service, _, _ = mute_service
    result = await service.command("mute", duration, ADDRESS, "test:user:admin")
    assert result.stop_agent
    assert (await service.status(ADDRESS)).muted_until == 1000 + seconds
    other = ChatAddress(channel="test", target_type="private", target_id="c1")
    assert not (await service.status(other)).muted


@pytest.mark.parametrize(
    "duration", ["0m", "-1h", "30", "forever", "1h 30m", "366d", "9" * 5000 + "d"]
)
async def test_invalid_duration_never_changes_state(mute_service, duration):
    service, _, _ = mute_service
    assert not (
        await service.command("mute", duration, ADDRESS, "test:user:admin")
    ).stop_agent
    assert not (await service.status(ADDRESS)).muted


async def test_disabled_feature_does_not_modify_or_enforce_saved_state(mute_service):
    service, clock, engine = mute_service
    await service.command("mute", "1h", ADDRESS, "test:user:admin")
    disabled = ChatMuteService(
        SQLiteChatMuteRepository(engine), MuteConfig(), clock=lambda: clock[0]
    )
    assert (
        "未启用"
        in (await disabled.command("unmute", "", ADDRESS, "test:user:admin")).text
    )
    assert not (await disabled.status(ADDRESS)).muted
    assert (await service.status(ADDRESS)).muted


@pytest.mark.parametrize("actor", ["", "test:user:other", "person:admin"])
async def test_non_admin_cannot_mute_or_unmute(mute_service, actor):
    service, _, _ = mute_service
    assert "管理员" in (await service.command("mute", "1h", ADDRESS, actor)).text
    assert not (await service.status(ADDRESS)).muted
    await service.command("mute", "1h", ADDRESS, "test:user:admin")
    assert "管理员" in (await service.command("unmute", "", ADDRESS, actor)).text
    assert (await service.status(ADDRESS)).muted


async def test_expiry_is_claimed_once_and_renewal_replaces_deadline(mute_service):
    service, clock, _ = mute_service
    await service.command("mute", "1s", ADDRESS, "test:user:admin")
    clock[0] = 1001
    results = await asyncio.gather(*(service.status(ADDRESS) for _ in range(5)))
    assert sum(result.expired for result in results) == 1
    assert all(not result.muted for result in results)
    await service.command("mute", "1h", ADDRESS, "test:user:admin")
    clock[0] += 10
    await service.command("mute", "30m", ADDRESS, "test:user:admin")
    assert (await service.status(ADDRESS)).muted_until == clock[0] + 1800


async def test_status_is_public_and_unmute_uses_custom_notice(mute_service):
    service, clock, engine = mute_service
    custom = ChatMuteService(
        SQLiteChatMuteRepository(engine),
        MuteConfig(enabled=True, notice={"offline": "暂停", "online": "恢复"}),
        AuthorizationGate(frozenset({"test:user:admin"})),
        clock=lambda: clock[0],
    )
    assert (
        "暂停" in (await custom.command("mute", "30m", ADDRESS, "test:user:admin")).text
    )
    status = await custom.command("mute", "status", ADDRESS, "test:user:other")
    assert "30m" in status.text and not status.stop_agent
    assert (
        await custom.command("unmute", "", ADDRESS, "test:user:admin")
    ).text == "恢复"
    assert not (await service.status(ADDRESS)).muted


async def test_restart_retains_deadline(tmp_path):
    db_path = tmp_path / "saved.db"
    engine = DatabaseEngine(db_path)
    await engine.initialize()
    await SQLiteChatMuteRepository(engine).set_until(
        ADDRESS.chat_key, 2000, "admin", 1000
    )
    await engine.close()
    restarted = DatabaseEngine(db_path)
    await restarted.initialize()
    try:
        status = await SQLiteChatMuteRepository(restarted).status(
            ADDRESS.chat_key, 1500
        )
        assert status.muted_until == 2000
    finally:
        await restarted.close()


def test_mute_config_defaults_validation_and_schema():
    assert not Settings().mute.enabled
    with pytest.raises(ValidationError):
        MuteConfig(default_duration_seconds=0)
    entries = {
        entry.path for entry in build_config_schema(section="mute", show_plugins=False)
    }
    assert {
        "mute.enabled",
        "mute.default_duration_seconds",
        "mute.notice.offline",
        "mute.notice.online",
    } <= entries


async def test_legacy_schema_is_upgraded_without_changing_sessions(tmp_path):
    engine = DatabaseEngine(tmp_path / "legacy.db")
    await engine.initialize()
    await engine.execute("DROP TABLE chat_mutes")
    await engine.execute("UPDATE schema_version SET version=28")
    await engine.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        ("kept", None, "created", "active", None),
    )
    await engine.db.commit()
    await engine.close()
    await engine.initialize()
    try:
        assert await engine.fetch_one("SELECT 1 FROM sessions WHERE session_id='kept'")
        repository = SQLiteChatMuteRepository(engine)
        await repository.set_until(ADDRESS.chat_key, 2000, "admin", 1000)
        assert (await repository.status(ADDRESS.chat_key, 1000)).muted
    finally:
        await engine.close()
