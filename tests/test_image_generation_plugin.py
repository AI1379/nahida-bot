"""Tests for the first-party image generation plugin."""

from __future__ import annotations

import base64
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from nahida_bot.core.app import Application
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.config import Settings
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.core.tasks import TaskManager
from nahida_bot.plugins.base import ChatContext, InboundMessage, OutboundMessage
from nahida_bot.plugins.image_generation.client import (
    GeneratedImage,
    OpenAIImageGenerationClient,
)
from nahida_bot.plugins.image_generation.config import OpenAIImagesBackendConfig
from nahida_bot.plugins.image_generation.plugin import ImageGenerationPlugin
from nahida_bot.plugins.manifest import PluginManifest
from nahida_bot_sdk.testing import RecordingMockBotAPI, load_plugin_for_test


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class _ImageAPI(RecordingMockBotAPI):
    def __init__(self, workspace_root: Path) -> None:
        super().__init__()
        self.workspace_root = workspace_root
        self.sent_messages: list[tuple[str, OutboundMessage, str]] = []
        self.task_manager = TaskManager()
        self.tasks: dict[str, asyncio.Task[Any]] = {}

    def spawn_task(
        self,
        name: str,
        coro: Any,
        *,
        kind: str = "oneshot",
    ) -> None:
        self.spawned_tasks[name] = {"kind": kind}
        self.tasks[name] = self.task_manager.spawn(
            name,
            coro,
            owner="image_generation",
            kind=kind,  # type: ignore[arg-type]
        )

    def cancel_task(self, name: str) -> bool:
        return self.task_manager.cancel(f"image_generation:{name}")

    async def send_message(
        self,
        target: str,
        message: OutboundMessage,
        *,
        channel: str = "",
    ) -> str:
        self.sent_messages.append((target, message, channel))
        return f"msg-{len(self.sent_messages)}"

    def get_workspace_root(self, workspace_id: str | None = None) -> str:
        return str(self.workspace_root)

    def resolve_workspace_path(self, path: str) -> str:
        return str(self.workspace_root / path)


class _RejectingTaskAPI(_ImageAPI):
    def spawn_task(self, name: str, coro: Any, *, kind: str = "oneshot") -> None:
        raise RuntimeError("task manager unavailable")


class _FakeImageClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        size: str = "",
        quality: str = "",
        n: int = 1,
        response_format: str = "",
        output_format: str = "",
    ) -> list[GeneratedImage]:
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "size": size,
                "quality": quality,
                "n": n,
                "response_format": response_format,
                "output_format": output_format,
            }
        )
        return [
            GeneratedImage(
                data=PNG_1X1,
                mime_type="image/png",
                revised_prompt=f"{prompt} revised",
                source="b64_json",
            )
            for _ in range(n)
        ]

    async def close(self) -> None:
        self.closed = True


class _BlockingImageClient(_FakeImageClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        size: str = "",
        quality: str = "",
        n: int = 1,
        response_format: str = "",
        output_format: str = "",
    ) -> list[GeneratedImage]:
        self.started.set()
        await self.release.wait()
        return await super().generate(
            prompt,
            model=model,
            size=size,
            quality=quality,
            n=n,
            response_format=response_format,
            output_format=output_format,
        )


def _manifest(config: dict[str, Any] | None = None) -> PluginManifest:
    base_config: dict[str, Any] = {
        "provider": "default",
        "backends": {
            "default": {
                "type": "openai-images",
                "api_key": "test-key",
                "base_url": "https://images.example/v1",
                "model": "image-model",
                "size": "1024x1024",
                "quality": "auto",
                "max_images_per_request": 3,
            }
        },
        "output_dir": "generated/images",
        "command_names": ["draw", "生图"],
        "auto_send": True,
    }
    if config:
        base_config.update(config)
    return PluginManifest(
        id="image_generation",
        name="Image Generation",
        version="0.1.0",
        entrypoint="nahida_bot.plugins.image_generation.plugin:ImageGenerationPlugin",
        config=base_config,
    )


def _inbound() -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform="telegram",
        chat_id="123",
        user_id="u1",
        text="/draw cat",
        raw_event={},
        chat_context=ChatContext(
            platform="telegram",
            chat_type="private",
            platform_chat_id="123",
        ),
    )


async def _load_plugin(
    api: _ImageAPI,
    config: dict[str, Any] | None = None,
) -> tuple[ImageGenerationPlugin, _FakeImageClient]:
    plugin = ImageGenerationPlugin(api=api, manifest=_manifest(config))
    fake_client = _FakeImageClient()
    plugin._clients["default"] = fake_client  # type: ignore[assignment]
    await load_plugin_for_test(plugin)
    return plugin, fake_client


@pytest.mark.asyncio
async def test_client_decodes_base64_image_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "b64_json": base64.b64encode(PNG_1X1).decode("ascii"),
                        "revised_prompt": "revised",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAIImageGenerationClient(
            OpenAIImagesBackendConfig(
                base_url="https://images.example/v1",
                api_key="key",
                model="image-model",
                max_images_per_request=3,
            ),
            client=http,
        )
        images = await client.generate("a cat", n=3)

    assert images[0].data == PNG_1X1
    assert images[0].mime_type == "image/png"
    assert images[0].revised_prompt == "revised"
    assert requests[0].url == "https://images.example/v1/images/generations"
    assert requests[0].headers["authorization"] == "Bearer key"
    assert requests[0].headers["connection"] == "close"
    payload = json.loads(requests[0].content)
    assert payload["prompt"] == "a cat"
    assert payload["model"] == "image-model"
    assert payload["n"] == 3


@pytest.mark.asyncio
async def test_client_downloads_url_image_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/images/generations":
            return httpx.Response(
                200,
                json={"data": [{"url": "https://cdn.example/image.png"}]},
            )
        return httpx.Response(
            200,
            content=PNG_1X1,
            headers={"content-type": "image/png"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAIImageGenerationClient(
            OpenAIImagesBackendConfig(
                base_url="https://images.example/v1",
                api_key="key",
            ),
            client=http,
        )
        images = await client.generate("a cat")

    assert images[0].data == PNG_1X1
    assert images[0].mime_type == "image/png"
    assert images[0].source == "url"


@pytest.mark.asyncio
async def test_plugin_registers_command_alias_and_tool(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    await _load_plugin(api)

    assert "draw" in api.registered_commands
    assert "生图" in api.registered_commands
    assert "image_generate" in api.registered_tools


def test_task_creation_failure_closes_pending_coroutine(tmp_path: Path) -> None:
    api = _RejectingTaskAPI(tmp_path)
    plugin = ImageGenerationPlugin(api=api, manifest=_manifest())

    async def pending() -> None:
        await asyncio.sleep(60)

    coro = pending()
    with pytest.raises(RuntimeError, match="task manager unavailable"):
        plugin._spawn_task("draw", coro)
    assert coro.cr_frame is None


@pytest.mark.asyncio
async def test_draw_command_generates_saves_and_sends(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    plugin, fake_client = await _load_plugin(api)
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="123",
            session_id="telegram:private:123",
            workspace_id="default",
            chat_address=ChatAddress(
                channel="telegram",
                target_type="private",
                target_id="123",
            ),
        )
    )
    try:
        result = await plugin._cmd_draw(
            args="a small green house",
            inbound=_inbound(),
            session_id="telegram:private:123",
        )
    finally:
        current_session.reset(token)

    assert (
        result
        == "Image generation started. Generated image will be sent to this chat when ready."
    )
    tasks = list(api.tasks.values())
    assert len(tasks) == 1
    await asyncio.gather(*tasks)

    assert fake_client.calls[0]["prompt"] == "a small green house"
    generated_dir = tmp_path / "generated" / "images"
    generated_files = list(generated_dir.glob("*.png"))
    assert len(generated_files) == 1
    assert generated_files[0].read_bytes() == PNG_1X1
    assert len(api.sent_messages) == 1
    target, outbound, channel = api.sent_messages[0]
    assert target == "123"
    assert channel == "telegram"
    assert outbound.extra["chat_address"] == "telegram:private:123"
    assert outbound.attachments[0].path == str(generated_files[0])
    assert outbound.attachments[0].type == "photo"


@pytest.mark.asyncio
async def test_tool_can_generate_without_sending(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    plugin, fake_client = await _load_plugin(api)
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="123",
            session_id="telegram:private:123",
            workspace_id="default",
            chat_address=ChatAddress(
                channel="telegram",
                target_type="private",
                target_id="123",
            ),
        )
    )
    try:
        raw = await plugin._tool_image_generate(
            "a blue tree",
            n=2,
            size="1536x1024",
            quality="high",
            send=False,
        )
    finally:
        current_session.reset(token)

    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert len(payload["images"]) == 2
    assert len(payload["media"]) == 2
    assert payload["media"][0]["kind"] == "image"
    assert "sent_message_ids" not in payload
    assert api.sent_messages == []
    assert fake_client.calls[0]["n"] == 2
    assert fake_client.calls[0]["size"] == "1536x1024"
    assert fake_client.calls[0]["quality"] == "high"


@pytest.mark.asyncio
async def test_tool_enforces_24h_image_limit(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    plugin, fake_client = await _load_plugin(api, {"max_images_per_24h": 1})

    raw_ok = await plugin._tool_image_generate("a quiet garden", send=False)
    ok_payload = json.loads(raw_ok)
    assert ok_payload["status"] == "ok"

    raw_error = await plugin._tool_image_generate("another quiet garden", send=False)
    error_payload = json.loads(raw_error)

    assert error_payload["status"] == "error"
    assert "quota exceeded" in error_payload["error"]
    assert len(fake_client.calls) == 1


@pytest.mark.asyncio
async def test_quota_released_when_send_fails(tmp_path: Path, monkeypatch) -> None:
    api = _ImageAPI(tmp_path)
    plugin, _ = await _load_plugin(api, {"max_images_per_24h": 1})

    async def fail_send(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("send failed")

    monkeypatch.setattr(plugin, "_send_images", fail_send)
    with pytest.raises(RuntimeError, match="send failed"):
        await plugin._generate_and_maybe_send(
            prompt="first",
            n=1,
            send=True,
            caption="",
        )

    monkeypatch.undo()
    result = json.loads(await plugin._tool_image_generate("second", send=False))
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_quota_released_when_generation_is_cancelled(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    plugin, _ = await _load_plugin(api, {"max_images_per_24h": 1})
    blocking = _BlockingImageClient()
    plugin._clients["default"] = blocking  # type: ignore[assignment]

    task = asyncio.create_task(
        plugin._generate_and_maybe_send(
            prompt="hold",
            n=1,
            send=False,
            caption="",
        )
    )
    await blocking.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    plugin._clients["default"] = _FakeImageClient()  # type: ignore[assignment]
    result = json.loads(await plugin._tool_image_generate("after", send=False))
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_output_dir_is_created_with_parents(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    plugin, _ = await _load_plugin(
        api,
        {"output_dir": "nested/generated/images"},
    )

    raw = await plugin._tool_image_generate(
        "a small lantern",
        send=False,
    )

    payload = json.loads(raw)
    assert payload["status"] == "ok"
    generated_dir = tmp_path / "nested" / "generated" / "images"
    assert generated_dir.is_dir()
    generated_files = list(generated_dir.glob("*.png"))
    assert len(generated_files) == 1
    assert payload["images"][0]["path"].startswith("nested/generated/images/")


@pytest.mark.asyncio
async def test_tool_can_select_configured_provider(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    plugin, _ = await _load_plugin(
        api,
        {
            "backends": {
                "default": {
                    "type": "openai-images",
                    "api_key": "test-key",
                    "base_url": "https://images.example/v1",
                    "model": "default-model",
                },
                "alt": {
                    "type": "openai-images",
                    "api_key": "test-key",
                    "base_url": "https://alt-images.example/v1",
                    "model": "alt-model",
                },
            }
        },
    )
    alt_client = _FakeImageClient()
    plugin._clients["alt"] = alt_client  # type: ignore[assignment]

    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="123",
            session_id="telegram:private:123",
            workspace_id="default",
            chat_address=ChatAddress(
                channel="telegram",
                target_type="private",
                target_id="123",
            ),
        )
    )
    try:
        raw = await plugin._tool_image_generate(
            "a silver forest",
            provider="alt",
            send=False,
        )
    finally:
        current_session.reset(token)

    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["provider"] == "alt"
    assert payload["backend_type"] == "openai-images"
    assert payload["model"] == "alt-model"
    assert alt_client.calls[0]["prompt"] == "a silver forest"


@pytest.mark.asyncio
async def test_framework_enabled_is_not_a_business_config_gate(tmp_path: Path) -> None:
    api = _ImageAPI(tmp_path)
    await _load_plugin(api, {"enabled": False})

    assert "draw" in api.registered_commands
    assert "image_generate" in api.registered_tools


@pytest.mark.asyncio
async def test_application_discovers_image_generation_builtin(tmp_path: Path) -> None:
    settings = Settings(
        db_path=":memory:",
        workspace_base_dir=str(tmp_path / "workspace"),
        plugin_paths=[],
        discover_builtin_channels=False,
    )
    app = Application(settings=settings)
    await app.initialize()
    try:
        assert app.plugin_manager is not None
        assert app.plugin_manager.get_record("image_generation") is not None
    finally:
        await app.stop()
