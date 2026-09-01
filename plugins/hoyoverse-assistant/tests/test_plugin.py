"""Tests for the external HoYoverse assistant plugin."""

from __future__ import annotations

import datetime as dt
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from nahida_bot_sdk import (  # noqa: E402
    CommandResult,
    ChatContext,
    InboundMessage,
    PluginManifest,
    parse_manifest,
)
from nahida_bot_sdk.testing import (  # noqa: E402
    RecordingMockBotAPI,
    load_plugin_for_test,
)
from nahida_plugin_hoyoverse_assistant.client import (  # noqa: E402
    GenshinPyService,
    HoyoverseQueryError,
)
from nahida_plugin_hoyoverse_assistant.auth import (  # noqa: E402
    GenshinPyQRAuthenticator,
    QRLoginResult,
    QRLoginSession,
    QRLoginStatus,
)
from nahida_plugin_hoyoverse_assistant.config import (  # noqa: E402
    HoyoverseAssistantConfig,
)
from nahida_plugin_hoyoverse_assistant.domain import (  # noqa: E402
    Game,
    GameAccount,
    Report,
)
from nahida_plugin_hoyoverse_assistant.plugin import (  # noqa: E402
    HoyoverseAssistantPlugin,
)


class _FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.owned_accounts = (
            GameAccount(Game.GENSHIN, 123456789, 60, "荧"),
            GameAccount(Game.STARRAIL, 123456789, 70, "三月"),
            GameAccount(Game.STARRAIL, 987654321, 60, "星"),
            GameAccount(Game.ZZZ, 10000001, 60, "铃"),
        )

    async def accounts(self) -> tuple[GameAccount, ...]:
        return self.owned_accounts

    async def status(self, game: Game, uid: int) -> Report:
        self.calls.append(("status", game, uid))
        return Report("status", (game.value, str(uid)))

    async def challenge(
        self,
        game: Game,
        uid: int,
        *,
        mode: str,
        previous: bool,
    ) -> Report:
        self.calls.append(("challenge", game, uid, mode, previous))
        return Report("challenge", (mode, str(previous)))

    async def diary(
        self,
        game: Game,
        uid: int,
        *,
        month: str | None,
    ) -> Report:
        self.calls.append(("diary", game, uid, month))
        return Report("diary", (month or "current",))


def _manifest(**config: Any) -> PluginManifest:
    return PluginManifest(
        id="hoyoverse-assistant",
        name="HoYoverse Assistant",
        version="0.1.0",
        entrypoint=(
            "nahida_plugin_hoyoverse_assistant.plugin:HoyoverseAssistantPlugin"
        ),
        config=config,
    )


def _inbound(
    user_id: str = "u1",
    *,
    is_group: bool = True,
    chat_context: ChatContext | None = None,
) -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform="milky",
        chat_id="g1",
        user_id=user_id,
        text="",
        raw_event={},
        is_group=is_group,
        chat_context=chat_context,
    )


async def _invoke(
    api: RecordingMockBotAPI,
    command: str,
    args: str = "",
    *,
    user_id: str = "u1",
    is_group: bool = True,
) -> CommandResult:
    handler = api.registered_commands[command]["handler"]
    result = await handler(
        args=args,
        inbound=_inbound(user_id, is_group=is_group),
        session_id=f"milky:{'group' if is_group else 'private'}:g1",
    )
    assert isinstance(result, CommandResult)
    return result


async def _loaded_plugin() -> tuple[
    HoyoverseAssistantPlugin, RecordingMockBotAPI, _FakeService
]:
    api = RecordingMockBotAPI()
    plugin = HoyoverseAssistantPlugin(api=api, manifest=_manifest())
    service = _FakeService()
    plugin._service = service
    await load_plugin_for_test(plugin)
    return plugin, api, service


async def test_registers_commands_with_native_argument_metadata() -> None:
    _plugin, api, _service = await _loaded_plugin()

    assert {
        "米游社绑定",
        "米游社登录",
        "米游社登录确认",
        "米游社退出",
        "米游社解绑",
        "我的米游社",
        "原神状态",
        "铁道状态",
        "绝区零状态",
        "原神深渊",
        "铁道挑战",
        "绝区零挑战",
        "原石月报",
        "星琼月报",
        "菲林月报",
    }.issubset(api.registered_commands)
    arguments = api.registered_commands["米游社绑定"]["arguments"]
    assert [argument.name for argument in arguments] == ["game", "uid"]
    assert [
        argument.name for argument in api.registered_commands["菲林月报"]["arguments"]
    ] == ["month"]


def test_manifest_requests_secret_store_without_static_cookie_config() -> None:
    manifest = parse_manifest(PLUGIN_ROOT / "plugin.yaml")

    assert manifest.permissions.plugin_secrets.read is True
    assert manifest.permissions.plugin_secrets.write is True
    assert "cookies" not in manifest.config
    assert "cookies" not in manifest.config_schema["properties"]


async def test_binding_is_scoped_by_platform_user_and_drives_status() -> None:
    _plugin, api, service = await _loaded_plugin()

    result = await _invoke(api, "米游社绑定", "铁道 123456789")
    assert result.message is not None
    assert "已绑定崩坏：星穹铁道 UID 123456789" in result.message.text

    status = await _invoke(api, "铁道状态")
    assert status.message is not None
    assert status.message.text == "status\nstarrail\n123456789"
    assert service.calls == [("status", Game.STARRAIL, 123456789)]

    stored = await api.plugin_data_list()
    assert "milky:user:u1" not in repr(stored)

    other = await _invoke(api, "铁道状态", user_id="u2")
    assert other.message is not None
    assert "尚未绑定崩坏：星穹铁道 UID" in other.message.text


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("未知 123456789", "游戏必须是"),
        ("原神 abc", "UID 应为"),
        ("原神 123", "UID 应为"),
    ],
)
async def test_binding_validates_game_and_uid(args: str, expected: str) -> None:
    _plugin, api, _service = await _loaded_plugin()

    result = await _invoke(api, "米游社绑定", args)

    assert result.message is not None
    assert expected in result.message.text


async def test_binding_rejects_channels_without_a_stable_user_id() -> None:
    _plugin, api, _service = await _loaded_plugin()

    result = await _invoke(api, "米游社绑定", "原神 123456789", user_id="")

    assert result.message is not None
    assert "稳定的用户 ID" in result.message.text


async def test_challenge_and_diary_reuse_the_bound_uid() -> None:
    _plugin, api, service = await _loaded_plugin()
    await _invoke(api, "米游社绑定", "绝区零 10000001")

    challenge = await _invoke(api, "绝区零挑战", "危局 上期")
    diary = await _invoke(api, "菲林月报", "202608")

    assert challenge.message is not None
    assert challenge.message.text == "challenge\n危局\nTrue"
    assert diary.message is not None
    assert diary.message.text == "diary\n202608"
    assert service.calls == [
        ("challenge", Game.ZZZ, 10000001, "危局", True),
        ("diary", Game.ZZZ, 10000001, "202608"),
    ]


async def test_unbind_one_game_preserves_other_bindings() -> None:
    _plugin, api, _service = await _loaded_plugin()
    await _invoke(api, "米游社绑定", "原神 123456789")
    await _invoke(api, "米游社绑定", "铁道 987654321")

    await _invoke(api, "米游社解绑", "原神")
    accounts = await _invoke(api, "我的米游社")

    assert accounts.message is not None
    assert "987654321" in accounts.message.text
    assert "123456789" not in accounts.message.text


class _FakeAuthenticator:
    def __init__(self) -> None:
        self.result = QRLoginResult(
            QRLoginStatus.CONFIRMED,
            "ltoken_v2=private-token; ltuid_v2=10001",
        )
        self.checked_tickets: list[str] = []

    async def start(self) -> QRLoginSession:
        return QRLoginSession("ticket-u1", "https://example.invalid/login")

    async def check(self, ticket: str) -> QRLoginResult:
        self.checked_tickets.append(ticket)
        return self.result


class _FakeGenshinQRClient:
    async def _create_qrcode(self) -> Any:
        return SimpleNamespace(
            ticket="upstream-ticket", url="https://example.invalid/qr"
        )

    async def _check_qrcode(self, ticket: str) -> tuple[Any, SimpleCookie[str]]:
        assert ticket == "upstream-ticket"
        cookies: SimpleCookie[str] = SimpleCookie()
        cookies["ltoken_v2"] = "token"
        cookies["ltuid_v2"] = "10001"
        return SimpleNamespace(value="Confirmed"), cookies


async def test_qr_adapter_matches_genshin_py_creation_and_cookie_models() -> None:
    auth = GenshinPyQRAuthenticator(
        HoyoverseAssistantConfig(), client=_FakeGenshinQRClient()
    )

    session = await auth.start()
    result = await auth.check(session.ticket)

    assert session == QRLoginSession("upstream-ticket", "https://example.invalid/qr")
    assert result.status is QRLoginStatus.CONFIRMED
    assert result.cookies == "ltoken_v2=token; ltuid_v2=10001"


async def test_login_is_private_only_and_never_accepts_cookie_arguments() -> None:
    plugin, api, _service = await _loaded_plugin()
    plugin._authenticator = _FakeAuthenticator()

    group = await _invoke(api, "米游社登录")
    unsafe = await _invoke(
        api,
        "米游社登录",
        "ltoken_v2=must-not-enter-command-logs",
        is_group=False,
    )

    assert group.message is not None
    assert "私聊" in group.message.text
    assert unsafe.message is not None
    assert "不要在命令中粘贴 Cookies" in unsafe.message.text
    assert not api._plugin_secrets


async def test_login_rejects_non_private_chat_context_even_without_group_flag() -> None:
    plugin, api, _service = await _loaded_plugin()
    plugin._authenticator = _FakeAuthenticator()
    handler = api.registered_commands["米游社登录"]["handler"]
    inbound = _inbound(
        is_group=False,
        chat_context=ChatContext(
            platform="milky", chat_type="channel", platform_chat_id="c1"
        ),
    )

    result = await handler(args="", inbound=inbound, session_id="milky:channel:c1")

    assert result.message is not None
    assert "私聊" in result.message.text


async def test_private_qr_login_binds_user_and_works_from_group() -> None:
    plugin, api, service = await _loaded_plugin()
    auth = _FakeAuthenticator()
    plugin._authenticator = auth

    login = await _invoke(api, "米游社登录", is_group=False)
    assert login.message is not None
    assert login.message.attachments
    qr_path = Path(login.message.attachments[0].path)
    assert qr_path.read_bytes().startswith(b"\x89PNG")

    confirmed = await _invoke(api, "米游社登录确认", is_group=False)
    assert confirmed.message is not None
    assert "米游社登录成功" in confirmed.message.text
    assert "崩坏：星穹铁道：123456789" in confirmed.message.text
    assert auth.checked_tickets == ["ticket-u1"]

    status = await _invoke(api, "铁道状态", is_group=True)
    assert status.message is not None
    assert status.message.text == "status\nstarrail\n123456789"
    assert service.calls == [("status", Game.STARRAIL, 123456789)]

    plugin_data = await api.plugin_data_list()
    assert "private-token" not in repr(plugin_data)
    assert "milky:user:u1" not in repr(api._plugin_secrets)
    qr_path.unlink(missing_ok=True)


async def test_login_confirmation_tracks_scan_state_and_user_isolation() -> None:
    plugin, api, _service = await _loaded_plugin()
    auth = _FakeAuthenticator()
    plugin._authenticator = auth
    login = await _invoke(api, "米游社登录", is_group=False)
    assert login.message is not None
    qr_path = Path(login.message.attachments[0].path)
    auth.result = QRLoginResult(QRLoginStatus.SCANNED)

    scanned = await _invoke(api, "米游社登录确认", is_group=False)
    other_user = await _invoke(api, "米游社登录确认", user_id="u2", is_group=False)

    assert scanned.message is not None
    assert "尚未在米游社 App 中确认" in scanned.message.text
    assert other_user.message is not None
    assert "没有待确认的二维码" in other_user.message.text
    qr_path.unlink(missing_ok=True)


async def test_logout_deletes_credentials_and_bindings() -> None:
    plugin, api, _service = await _loaded_plugin()
    plugin._authenticator = _FakeAuthenticator()
    login = await _invoke(api, "米游社登录", is_group=False)
    assert login.message is not None
    qr_path = Path(login.message.attachments[0].path)
    await _invoke(api, "米游社登录确认", is_group=False)

    result = await _invoke(api, "米游社退出", is_group=False)
    accounts = await _invoke(api, "我的米游社")

    assert result.message is not None
    assert "删除登录凭据和 UID 绑定" in result.message.text
    assert accounts.message is not None
    assert "尚未绑定" in accounts.message.text
    assert not api._plugin_secrets
    qr_path.unlink(missing_ok=True)


class _FakeGenshinClient:
    async def get_game_accounts(self) -> list[Any]:
        return [
            SimpleNamespace(
                uid=123456789,
                game=SimpleNamespace(value="hkrpg"),
            )
        ]

    async def get_starrail_user(self, uid: int) -> Any:
        assert uid == 123456789
        return SimpleNamespace(
            info=SimpleNamespace(nickname="三月", level=70),
            stats=SimpleNamespace(
                active_days=99,
                achievement_num=777,
                avatar_num=42,
                abyss_process="12-3",
            ),
        )

    async def get_starrail_notes(self, uid: int) -> Any:
        assert uid == 123456789
        return SimpleNamespace(
            current_stamina=200,
            max_stamina=300,
            stamina_recovery_time=dt.datetime.now().astimezone()
            + dt.timedelta(hours=8),
            current_reserve_stamina=12,
            current_train_score=500,
            max_train_score=500,
            current_rogue_score=12000,
            max_rogue_score=14000,
            remaining_weekly_discounts=2,
            max_weekly_discounts=3,
        )


async def test_genshin_py_adapter_includes_owned_realtime_notes() -> None:
    service = GenshinPyService(
        HoyoverseAssistantConfig(),
        client=_FakeGenshinClient(),
    )

    report = await service.status(Game.STARRAIL, 123456789)

    rendered = report.render()
    assert "开拓者：三月" in rendered
    assert "开拓力：200/300" in rendered
    assert "每日实训：500/500" in rendered


class _FailingClient:
    async def get_zzz_user(self, uid: int) -> Any:
        del uid
        error = type("DataNotPublic", (Exception,), {})
        raise error()


async def test_genshin_py_adapter_sanitizes_upstream_errors() -> None:
    service = GenshinPyService(
        HoyoverseAssistantConfig(),
        client=_FailingClient(),
    )

    with pytest.raises(HoyoverseQueryError, match="未公开"):
        await service.status(Game.ZZZ, 10000001)


async def test_missing_user_login_is_reported() -> None:
    api = RecordingMockBotAPI()
    plugin = HoyoverseAssistantPlugin(api=api, manifest=_manifest())
    await load_plugin_for_test(plugin)
    result = await _invoke(api, "米游社绑定", "原神 123456789")

    assert result.message is not None
    assert "尚未登录米游社" in result.message.text


def test_legacy_static_cookie_config_is_discarded() -> None:
    config = HoyoverseAssistantConfig.model_validate(
        {"cookies": "ltoken_v2=must-not-survive-upgrade"}
    )

    assert "must-not-survive-upgrade" not in repr(config)
    assert "cookies" not in config.model_dump()


class _TransientOwnershipClient(_FakeGenshinClient):
    def __init__(self) -> None:
        self.account_calls = 0

    async def get_game_accounts(self) -> list[Any]:
        self.account_calls += 1
        if self.account_calls == 1:
            error = type("TooManyRequests", (Exception,), {})
            raise error()
        return await super().get_game_accounts()


async def test_transient_ownership_failure_is_not_cached() -> None:
    client = _TransientOwnershipClient()
    service = GenshinPyService(
        HoyoverseAssistantConfig(),
        client=client,
    )

    first = await service.status(Game.STARRAIL, 123456789)
    second = await service.status(Game.STARRAIL, 123456789)

    assert "归属校验暂不可用" in first.render()
    assert "开拓力：200/300" in second.render()
    assert client.account_calls == 2


class _DeniedNotesClient(_FakeGenshinClient):
    async def get_starrail_notes(self, uid: int) -> Any:
        del uid
        error = type("UserNotesAccessDenied", (Exception,), {})
        raise error()


async def test_notes_failure_keeps_the_public_profile() -> None:
    service = GenshinPyService(
        HoyoverseAssistantConfig(),
        client=_DeniedNotesClient(),
    )

    report = await service.status(Game.STARRAIL, 123456789)

    assert "开拓者：三月" in report.render()
    assert "实时便笺：该账号的实时便笺不可访问" in report.render()


def test_zzz_annihilation_summary_uses_challenge_stars() -> None:
    data = SimpleNamespace(
        unlocked=True,
        challenges=[SimpleNamespace(star=3), SimpleNamespace(star=2)],
    )

    assert GenshinPyService._zzz_challenge_lines("湮灭", data) == [
        "星数：5",
        "完成关卡：2",
    ]
