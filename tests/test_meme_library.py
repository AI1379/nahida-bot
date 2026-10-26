"""Curated library indexing, routing, import isolation and delivery behavior."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session, current_attachments
from nahida_bot.plugins.base import InboundAttachment
from nahida_bot.core.config_schema import build_config_schema
from nahida_bot.plugins.manifest import PluginManifest, parse_manifest
from nahida_bot.plugins.meme_library.config import MemeLibraryConfig
from nahida_bot.plugins.meme_library.library import MemeLibrary, MemeMetadata
from nahida_bot.plugins.meme_library.plugin import MemeLibraryPlugin

from .helpers import RecordingMockBotAPI


GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "l9sAAAAASUVORK5CYII="
)


def _image(root: Path, name: str = "开心/纳西妲.gif", data: bytes = GIF) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _library(root: Path, **kwargs) -> MemeLibrary:
    return MemeLibrary(
        root,
        max_image_bytes=kwargs.get("max_image_bytes", 1024),
        max_items=kwargs.get("max_items", 100),
    )


def _session(key: str = "milky:group:20001") -> SessionContext:
    address = ChatAddress.parse(key)
    return SessionContext(
        address.channel, address.target_id, key, chat_address=address, user_id="10001"
    )


def test_categories_chinese_tags_and_descriptions_are_searchable(tmp_path):
    image = _image(tmp_path)
    Path(f"{image}.json").write_text(
        json.dumps(
            {"title": "好耶", "tags": ["纳西妲", "庆祝"], "description": "笑着挥手"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _image(tmp_path, "伤心/哭哭.png", PNG)
    library = _library(tmp_path)
    library.refresh()
    assert len(library.items) == 2
    meme = library.search("纳西妲 庆祝", "开心", 8)[0]
    assert meme.title == "好耶"
    assert library.search("挥手", "", 8) == [meme]
    assert library.search("庆祝 哭哭", "", 8) == []
    assert "path" not in meme.public()


def test_content_ids_deduplicate_and_survive_rename(tmp_path):
    source = _image(tmp_path)
    _image(tmp_path, "duplicate.gif")
    library = _library(tmp_path)
    library.refresh()
    meme_id = next(iter(library.items))
    assert len(library.items) == 1
    source.rename(source.with_name("新名字.gif"))
    library.refresh()
    assert set(library.items) == {meme_id}


def test_refresh_observes_metadata_changes_and_deletions(tmp_path):
    image = _image(tmp_path)
    library = _library(tmp_path)
    library.refresh()
    Path(f"{image}.json").write_text('{"tags":["新的标签"]}', encoding="utf-8")
    library.refresh()
    assert library.search("新的标签", "", 1)
    image.unlink()
    library.refresh()
    assert library.items == {}


def test_bad_assets_do_not_break_catalog_and_reads_are_bounded(tmp_path):
    _image(tmp_path)
    _image(tmp_path, "invalid.png", b"not an image")
    _image(tmp_path, "too-big.gif", GIF + b"x" * 1024)
    broken = _image(tmp_path, "broken.png", PNG)
    Path(f"{broken}.json").write_text("{broken", encoding="utf-8")
    library = _library(tmp_path)
    library.refresh()
    assert len(library.items) == 1
    assert library.skipped == 3


def test_capacity_limit_reports_incomplete_index(tmp_path):
    _image(tmp_path)
    _image(tmp_path, "哭哭.png", PNG)
    library = _library(tmp_path, max_items=1)
    library.refresh()
    assert len(library.items) == 1
    assert library.truncated is True


def test_changed_asset_is_rejected_before_send(tmp_path):
    image = _image(tmp_path)
    library = _library(tmp_path)
    library.refresh()
    meme_id = next(iter(library.items))
    image.write_bytes(PNG)
    with pytest.raises(ValueError, match="changed"):
        library.resolve(meme_id)


def test_import_preserves_gif_and_cannot_escape_root(tmp_path):
    source = _image(tmp_path / "workspace")
    library = _library(tmp_path / "library")
    library.refresh()
    first = library.import_file(
        source, category="开心", metadata=MemeMetadata(tags=["好耶"])
    )
    second = library.import_file(source, category="重复", metadata=MemeMetadata())
    assert first.id == second.id
    assert first.path.read_bytes() == GIF
    assert len(library.items) == 1
    with pytest.raises(ValueError, match="Category"):
        library.import_file(source, category="../escape", metadata=MemeMetadata())


def test_symlinks_outside_library_are_never_indexed_or_sent(tmp_path):
    outside = _image(tmp_path / "outside")
    root = tmp_path / "library"
    root.mkdir()
    link = root / "escape.gif"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not allow symlink creation")
    library = _library(root)
    library.refresh()
    assert library.items == {}
    assert library.skipped == 1
    link.unlink()
    image = _image(root)
    library.refresh()
    meme_id = next(iter(library.items))
    image.unlink()
    image.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        library.resolve(meme_id)


@pytest.fixture
async def plugin(tmp_path):
    root = tmp_path / "memes"
    _image(root)
    api = RecordingMockBotAPI()
    api.send_message = AsyncMock(return_value="sent-1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def resolve(path):
        from nahida_bot.workspace.sandbox import WorkspaceSandbox

        return str(WorkspaceSandbox(workspace).resolve_safe_path(path))

    api.resolve_workspace_path = resolve
    instance = MemeLibraryPlugin(
        api,
        PluginManifest(
            id="meme_library",
            name="Memes",
            version="0.1.0",
            entrypoint="nahida_bot.plugins.meme_library.plugin:MemeLibraryPlugin",
            config={"directory": str(root)},
        ),
    )
    token = current_session.set(_session())
    await instance.on_load()
    yield instance, api, workspace
    current_session.reset(token)


@pytest.mark.asyncio
async def test_search_then_concurrent_send_routes_once_to_typed_current_chat(plugin):
    instance, api, _workspace = plugin
    catalog = json.loads(
        await api.registered_tools["meme_search"]["handler"](query="开心")
    )
    meme_id = catalog["memes"][0]["id"]
    results = await asyncio.gather(instance.send(meme_id), instance.send(meme_id))
    assert sum('"sent": true' in result for result in results) == 1
    assert any("cooldown" in result for result in results)
    api.send_message.assert_awaited_once()
    call = api.send_message.call_args
    assert call.args[0] == "20001"
    assert call.kwargs["channel"] == "milky"
    assert call.args[1].extra == {"chat_address": "milky:group:20001"}
    assert Path(call.args[1].attachments[0].path).read_bytes() == GIF


@pytest.mark.asyncio
async def test_unknown_id_or_missing_session_cannot_send(plugin):
    instance, api, _workspace = plugin
    assert "Unknown meme" in await instance.send("../../private-file")
    current_session.set(None)
    assert "No active session" in await instance.search()
    assert "No active session" in await instance.send("anything")
    api.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowlist_applies_to_search_send_and_import(plugin):
    instance, api, _workspace = plugin
    instance._config.allowed_chats = ["milky:group:20002"]
    assert "not enabled" in await instance.search()
    assert "not enabled" in await instance.send("id")
    assert "not enabled" in await instance.import_image("x.gif")
    api.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_import_uses_workspace_policy_and_is_searchable(plugin):
    instance, api, workspace = plugin
    assert api.registered_tools["meme_import"]["requires_admin"] is True
    _image(workspace, "哭哭.png", PNG)
    result = json.loads(
        await instance.import_image("哭哭.png", category="伤心", tags=["委屈"])
    )
    assert result["imported"] is True
    assert (
        json.loads(await instance.search("委屈"))["memes"][0]["id"]
        == result["meme"]["id"]
    )
    assert "escapes the workspace" in await instance.import_image("../private.png")
    assert "workspace-relative" in await instance.import_image(
        str(workspace / "哭哭.png")
    )


@pytest.mark.asyncio
async def test_failed_delivery_does_not_claim_success_and_retains_cooldown(plugin):
    instance, api, _workspace = plugin
    meme_id = next(iter(instance._library.items))
    api.send_message.side_effect = RuntimeError("channel offline")
    assert "uncertain" in await instance.send(meme_id)
    assert "cooldown" in await instance.send(meme_id)
    api.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_search_and_send_share_the_same_library(plugin):
    instance, api, _workspace = plugin
    result = await api.registered_commands["meme"]["handler"](
        args="开心", inbound=None, session_id="unused"
    )
    meme_id = next(iter(instance._library.items))
    assert meme_id in result
    assert '"sent": true' in await instance._command(
        args=f"send {meme_id}", inbound=None, session_id="unused"
    )


@pytest.mark.asyncio
async def test_empty_delivery_id_does_not_claim_success(plugin):
    instance, api, _workspace = plugin
    api.send_message.return_value = ""
    meme_id = next(iter(instance._library.items))
    assert "not confirmed" in await instance.send(meme_id)
    assert "cooldown" in await instance.send(meme_id)


def test_manifest_defaults_and_config_validation():
    manifest = parse_manifest(Path("nahida_bot/plugins/meme_library/plugin.yaml"))
    assert manifest.enabled is False
    assert MemeLibraryConfig(**manifest.config).directory == "./data/memes"
    for config in (
        {"max_items": 0},
        {"allowed_chats": ["milky:20001"]},
        {"send_cooldown_seconds": -1},
    ):
        with pytest.raises(ValidationError):
            MemeLibraryConfig(**config)


def test_library_is_discovered_by_config_schema():
    entries = {
        entry.path: entry
        for entry in build_config_schema("meme_library", show_providers=False)
    }
    assert entries["meme_library.enabled"].default_ == "False"
    assert entries["meme_library.directory"].default_ == "./data/memes"


@pytest.mark.asyncio
async def test_application_discovers_the_builtin_library():
    from unittest.mock import MagicMock

    from nahida_bot.core.app import Application
    from nahida_bot.core.config import Settings

    app = Application(
        settings=Settings(plugin_paths=[], discover_builtin_channels=False)
    )
    manager = MagicMock()
    manager.discover = AsyncMock()
    app.plugin_manager = manager
    await app._discover_plugins()
    discovered = {
        path.resolve()
        for call in manager.discover.call_args_list
        for path in call.args[0]
    }
    assert Path("nahida_bot/plugins/meme_library").resolve() in discovered


@pytest.mark.asyncio
async def test_collect_selects_only_actual_turn_images_and_records_private_provenance(
    plugin, tmp_path
):
    instance, api, _workspace = plugin
    instance._config.collect_enabled = True
    instance._collector.register()
    cached = _image(tmp_path / "cached", "received.png", PNG)
    token = current_attachments.set(
        (
            InboundAttachment(kind="audio", platform_id="voice"),
            InboundAttachment(
                kind="image", platform_id="first", path=str(_image(tmp_path / "cached"))
            ),
            InboundAttachment(kind="image", platform_id="chosen", path=str(cached)),
        )
    )
    try:
        result = json.loads(
            await api.registered_tools["meme_collect"]["handler"](
                image_index=2, title="委屈", tags=["哭哭"]
            )
        )
        assert result["saved"] is True
        meme = instance._library.items[result["meme"]["id"]]
        assert meme.path.read_bytes() == PNG
        assert meme.path != cached and cached.exists()
        assert meme.source_chat == "milky:group:20001"
        assert meme.source_media_id == "chosen"
        assert meme.source_user_id == "10001" and meme.collected_at > 0
        assert (
            "source_chat" not in json.loads(await instance.search("哭哭"))["memes"][0]
        )
        admin = await api.page_handlers["gallery"]("list", {"query": "哭哭"})
        assert admin["memes"][0]["source_chat"] == "milky:group:20001"
        edited = await api.page_handlers["gallery"](
            "update", {"meme_id": meme.id, "title": "修改标题", "tags": ["新标签"]}
        )
        assert edited["meme"]["source_media_id"] == "chosen"
        assert edited["meme"]["collected_at"] == meme.collected_at
        assert "cooldown" in await instance._collector.collect(image_index=2)
    finally:
        current_attachments.reset(token)


@pytest.mark.asyncio
async def test_collection_is_opt_in_and_cannot_reach_other_images(plugin):
    instance, api, _workspace = plugin
    assert "meme_collect" not in api.registered_tools
    assert "disabled" in await instance._collector.collect()
    instance._config.collect_enabled = True
    token = current_attachments.set(())
    try:
        assert "on this turn" in await instance._collector.collect()
        assert "on this turn" in await instance._collector.collect(image_index=-1)
        instance._config.allowed_chats = ["milky:group:other"]
        assert "not enabled" in await instance._collector.collect()
        assert len(instance._library.items) == 1
    finally:
        current_attachments.reset(token)


@pytest.mark.asyncio
async def test_collection_size_and_concurrent_cooldown(plugin, tmp_path):
    instance, api, _workspace = plugin
    instance._config.collect_enabled = True
    path = _image(tmp_path / "cache", "new.png", PNG)
    token = current_attachments.set(
        (InboundAttachment(kind="image", platform_id="new", path=str(path)),)
    )
    try:
        results = await asyncio.gather(
            instance._collector.collect(), instance._collector.collect()
        )
        assert sum('"saved": true' in result for result in results) == 1
        assert any("cooldown" in result for result in results)
        instance._config.max_image_bytes = 1
        current_attachments.set(
            (InboundAttachment(kind="image", platform_id="huge", file_size=100),)
        )
        api.resolve_current_attachment = AsyncMock()
        assert "max_image_bytes" in await instance._collector.collect()
        api.resolve_current_attachment.assert_not_awaited()
    finally:
        current_attachments.reset(token)


@pytest.mark.asyncio
async def test_admin_preview_update_and_delete_cover_all_duplicate_copies(plugin):
    instance, api, _workspace = plugin
    meme_id = next(iter(instance._library.items))
    handler = api.page_handlers["gallery"]
    preview = await handler("preview", {"meme_id": meme_id})
    assert base64.b64decode(preview["data_url"].split(",", 1)[1]) == GIF
    assert preview["data_url"].startswith("data:image/gif;base64,")
    result = await handler(
        "update", {"meme_id": meme_id, "title": "新的标题", "tags": ["新标签"]}
    )
    assert result["meme"]["title"] == "新的标题"
    assert json.loads(await instance.search("新标签"))["memes"][0]["id"] == meme_id
    _image(instance._library.root, "重复.gif")
    deleted = await handler("delete", {"meme_id": meme_id})
    assert deleted == {"deleted": True, "removed_files": 2}
    assert instance._library.items == {}


@pytest.mark.asyncio
async def test_admin_actions_reject_arbitrary_paths_and_invalid_metadata(plugin):
    _instance, api, _workspace = plugin
    handler = api.page_handlers["gallery"]
    with pytest.raises(ValueError):
        await handler("preview", {"meme_id": "../../secret"})
    with pytest.raises(ValueError):
        await handler("exec", {"command": "anything"})
    with pytest.raises(ValueError):
        await handler("list", {"limit": 100000})
    with pytest.raises(ValueError):
        await handler("update", {"meme_id": "0" * 20, "title": "x", "path": "secret"})


def test_large_utf8_metadata_is_rejected_before_publishing_an_asset(tmp_path):
    source = _image(tmp_path / "source", "test.png", PNG)
    library = _library(tmp_path / "library")
    library.refresh()
    metadata = MemeMetadata(
        title="😀" * 120,
        tags=["😀" * 80] * 32,
        description="😀" * 500,
        source_chat="😀" * 256,
        source_user_id="😀" * 256,
        source_media_id="😀" * 512,
    )
    with pytest.raises(ValueError, match="16 KiB"):
        library.import_file(source, category="test", metadata=metadata)
    assert list(library.root.rglob("*.png")) == []
