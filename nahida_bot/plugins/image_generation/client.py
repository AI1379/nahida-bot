"""OpenAI-compatible Images API client for the image generation plugin."""

from __future__ import annotations

import base64
import binascii
import mimetypes
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from nahida_bot.plugins.image_generation.config import (
    MiniMaxBackendConfig,
    OpenAIImagesBackendConfig,
)


@dataclass(slots=True, frozen=True)
class GeneratedImage:
    """One generated image returned by the upstream image API."""

    data: bytes
    mime_type: str
    revised_prompt: str = ""
    source: str = ""


class ImageGenerationError(Exception):
    """Raised when image generation fails in a user-facing way."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class OpenAIImageGenerationClient:
    """Small client for ``POST /images/generations`` compatible backends."""

    def __init__(
        self,
        config: OpenAIImagesBackendConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def close(self) -> None:
        """Close the underlying HTTP client if this object created it."""

        if (
            self._client is not None
            and not self._client.is_closed
            and self._owns_client
        ):
            await self._client.aclose()
        self._client = None

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
        """Generate one or more images from a text prompt."""

        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ImageGenerationError(
                "image_generation_empty_prompt",
                "Image prompt is empty.",
            )
        if self._config.require_api_key and not self._config.api_key:
            raise ImageGenerationError(
                "image_generation_not_configured",
                "Image generation API key is not configured.",
            )

        count = max(1, min(int(n), self._config.max_images_per_request))
        payload = self._build_payload(
            clean_prompt,
            model=model,
            size=size,
            quality=quality,
            n=count,
            response_format=response_format,
            output_format=output_format,
        )
        endpoint = f"{self._config.base_url.rstrip('/')}/images/generations"
        headers = {"Content-Type": "application/json"}
        if self._config.force_close_connections:
            headers["Connection"] = "close"
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        try:
            response = await self._ensure_client().post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ImageGenerationError(
                "image_generation_timeout",
                "Image generation request timed out.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationError(
                "image_generation_transport_error",
                f"Image generation request failed: {exc}",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            raise self._http_error(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation backend returned non-JSON response.",
            ) from exc
        return await self._extract_images(body)

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=0
                    if self._config.force_close_connections
                    else None
                ),
                trust_env=self._config.trust_env,
            )
            self._owns_client = True
        return self._client

    def _build_payload(
        self,
        prompt: str,
        *,
        model: str,
        size: str,
        quality: str,
        n: int,
        response_format: str,
        output_format: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = dict(self._config.extra_body)
        payload["model"] = model.strip() or self._config.model
        payload["prompt"] = prompt
        payload["n"] = n

        for key, value in {
            "size": size.strip() or self._config.size,
            "quality": quality.strip() or self._config.quality,
            "response_format": response_format.strip() or self._config.response_format,
            "output_format": output_format.strip() or self._config.output_format,
        }.items():
            if value:
                payload[key] = value

        return payload

    async def _extract_images(self, body: object) -> list[GeneratedImage]:
        if not isinstance(body, dict):
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation backend returned a non-object response.",
            )
        raw_items = body.get("data")
        if not isinstance(raw_items, list):
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation response is missing a data array.",
            )

        images: list[GeneratedImage] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            revised_prompt = str(item.get("revised_prompt") or "").strip()
            mime_type = str(item.get("mime_type") or "").strip()
            b64_json = item.get("b64_json") or item.get("base64")
            if isinstance(b64_json, str) and b64_json.strip():
                images.append(
                    self._decode_base64_image(
                        b64_json,
                        mime_type=mime_type,
                        revised_prompt=revised_prompt,
                    )
                )
                continue
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                images.append(
                    await self._download_image(
                        url.strip(),
                        revised_prompt=revised_prompt,
                    )
                )

        if not images:
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation response did not contain any images.",
            )
        return images

    def _decode_base64_image(
        self,
        value: str,
        *,
        mime_type: str = "",
        revised_prompt: str = "",
    ) -> GeneratedImage:
        data_text = value.strip()
        if data_text.startswith("data:"):
            header, sep, encoded = data_text.partition(",")
            if not sep:
                raise ImageGenerationError(
                    "image_generation_bad_response",
                    "Image data URL is missing a comma separator.",
                )
            mime_type = mime_type or header.removeprefix("data:").split(";", 1)[0]
            data_text = encoded
        try:
            data = base64.b64decode(data_text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation backend returned invalid base64 image data.",
            ) from exc
        return GeneratedImage(
            data=data,
            mime_type=mime_type or guess_mime_from_bytes(data),
            revised_prompt=revised_prompt,
            source="b64_json",
        )

    async def _download_image(self, url: str, *, revised_prompt: str) -> GeneratedImage:
        if url.startswith("data:"):
            return self._decode_base64_image(url, revised_prompt=revised_prompt)
        try:
            response = await self._ensure_client().get(
                url,
                timeout=self._config.download_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ImageGenerationError(
                "image_download_timeout",
                "Generated image download timed out.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationError(
                "image_download_transport_error",
                f"Generated image download failed: {exc}",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise self._http_error(response, prefix="Generated image download")
        data = response.content
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        mime_type = content_type or _mime_from_url(url) or guess_mime_from_bytes(data)
        return GeneratedImage(
            data=data,
            mime_type=mime_type,
            revised_prompt=revised_prompt,
            source="url",
        )

    def _http_error(
        self,
        response: httpx.Response,
        *,
        prefix: str = "Image generation backend",
    ) -> ImageGenerationError:
        hint = _response_error_hint(response)
        status = response.status_code
        retryable = status == 429 or status >= 500
        if status in (401, 403):
            code = "image_generation_auth_failed"
        elif status == 429:
            code = "image_generation_rate_limited"
        elif status >= 500:
            code = "image_generation_server_error"
        else:
            code = "image_generation_rejected"
        return ImageGenerationError(
            code,
            f"{prefix} rejected request with status {status}: {hint}",
            retryable=retryable,
        )


_SIZE_TO_ASPECT_RATIO: dict[str, str] = {
    "1024x1024": "1:1",
    "1280x720": "16:9",
    "1152x864": "4:3",
    "1248x832": "3:2",
    "832x1248": "2:3",
    "864x1152": "3:4",
    "720x1280": "9:16",
    "1344x576": "21:9",
}


class MiniMaxImageGenerationClient:
    """Client for MiniMax Image Generation API (POST /v1/image_generation)."""

    def __init__(
        self,
        config: MiniMaxBackendConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def close(self) -> None:
        if (
            self._client is not None
            and not self._client.is_closed
            and self._owns_client
        ):
            await self._client.aclose()
        self._client = None

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
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ImageGenerationError(
                "image_generation_empty_prompt",
                "Image prompt is empty.",
            )
        if self._config.require_api_key and not self._config.api_key:
            raise ImageGenerationError(
                "image_generation_not_configured",
                "Image generation API key is not configured.",
            )

        count = max(1, min(int(n), self._config.max_images_per_request))
        payload = self._build_payload(
            clean_prompt,
            model=model,
            size=size,
            n=count,
            response_format=response_format,
        )
        endpoint = f"{self._config.base_url.rstrip('/')}/v1/image_generation"
        headers = {"Content-Type": "application/json"}
        if self._config.force_close_connections:
            headers["Connection"] = "close"
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        try:
            response = await self._ensure_client().post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ImageGenerationError(
                "image_generation_timeout",
                "Image generation request timed out.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationError(
                "image_generation_transport_error",
                f"Image generation request failed: {exc}",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            raise self._http_error(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation backend returned non-JSON response.",
            ) from exc

        self._check_base_resp(body)
        return await self._extract_images(body)

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=0
                    if self._config.force_close_connections
                    else None
                ),
                trust_env=self._config.trust_env,
            )
            self._owns_client = True
        return self._client

    def _build_payload(
        self,
        prompt: str,
        *,
        model: str,
        size: str,
        n: int,
        response_format: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = dict(self._config.extra_body)
        payload["model"] = model.strip() or self._config.model
        payload["prompt"] = prompt
        payload["n"] = n

        resolved_format = response_format.strip() or self._config.response_format
        if resolved_format:
            if resolved_format == "b64_json":
                resolved_format = "base64"
            payload["response_format"] = resolved_format

        if self._config.aspect_ratio:
            payload["aspect_ratio"] = self._config.aspect_ratio
        elif self._config.width > 0 and self._config.height > 0:
            payload["width"] = self._config.width
            payload["height"] = self._config.height

        if size.strip():
            ar = _SIZE_TO_ASPECT_RATIO.get(size.strip())
            if ar:
                payload["aspect_ratio"] = ar

        if self._config.style_type:
            style: dict[str, object] = {"style_type": self._config.style_type}
            if self._config.style_weight:
                style["style_weight"] = self._config.style_weight
            payload["style"] = style

        if self._config.seed is not None:
            payload["seed"] = self._config.seed

        if self._config.prompt_optimizer:
            payload["prompt_optimizer"] = True
        if self._config.aigc_watermark:
            payload["aigc_watermark"] = True

        return payload

    @staticmethod
    def _check_base_resp(body: object) -> None:
        if not isinstance(body, dict):
            return
        base_resp = body.get("base_resp")
        if not isinstance(base_resp, dict):
            return
        status_code = base_resp.get("status_code")
        if status_code is None or status_code == 0:
            return
        status_msg = str(base_resp.get("status_msg", ""))
        code_map: dict[int, tuple[str, bool]] = {
            1002: ("image_generation_rate_limited", True),
            1004: ("image_generation_auth_failed", False),
            1008: ("image_generation_rejected", False),
            1026: ("image_generation_rejected", False),
            2013: ("image_generation_rejected", False),
            2049: ("image_generation_auth_failed", False),
        }
        error_code, retryable = code_map.get(
            status_code, ("image_generation_rejected", False)
        )
        raise ImageGenerationError(
            error_code,
            f"MiniMax API returned error {status_code}: {status_msg}",
            retryable=retryable,
        )

    async def _extract_images(self, body: object) -> list[GeneratedImage]:
        if not isinstance(body, dict):
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation backend returned a non-object response.",
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation response is missing a data object.",
            )

        images: list[GeneratedImage] = []

        urls = data.get("image_urls")
        if isinstance(urls, list):
            for url in urls:
                if isinstance(url, str) and url.strip():
                    images.append(await self._download_image(url.strip()))

        b64_list = data.get("image_base64")
        if isinstance(b64_list, list):
            for b64 in b64_list:
                if isinstance(b64, str) and b64.strip():
                    images.append(self._decode_base64_image(b64.strip()))

        if not images:
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation response did not contain any images.",
            )
        return images

    def _decode_base64_image(
        self,
        value: str,
    ) -> GeneratedImage:
        data_text = value.strip()
        if data_text.startswith("data:"):
            header, sep, encoded = data_text.partition(",")
            if not sep:
                raise ImageGenerationError(
                    "image_generation_bad_response",
                    "Image data URL is missing a comma separator.",
                )
            mime_type = header.removeprefix("data:").split(";", 1)[0]
            data_text = encoded
        else:
            mime_type = ""
        try:
            data = base64.b64decode(data_text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationError(
                "image_generation_bad_response",
                "Image generation backend returned invalid base64 image data.",
            ) from exc
        return GeneratedImage(
            data=data,
            mime_type=mime_type or guess_mime_from_bytes(data),
            source="b64_json",
        )

    async def _download_image(self, url: str) -> GeneratedImage:
        if url.startswith("data:"):
            return self._decode_base64_image(url)
        try:
            response = await self._ensure_client().get(
                url,
                timeout=self._config.download_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ImageGenerationError(
                "image_download_timeout",
                "Generated image download timed out.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationError(
                "image_download_transport_error",
                f"Generated image download failed: {exc}",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise self._http_error(response, prefix="Generated image download")
        data = response.content
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        mime_type = content_type or _mime_from_url(url) or guess_mime_from_bytes(data)
        return GeneratedImage(
            data=data,
            mime_type=mime_type,
            source="url",
        )

    def _http_error(
        self,
        response: httpx.Response,
        *,
        prefix: str = "MiniMax API",
    ) -> ImageGenerationError:
        hint = _response_error_hint(response)
        status = response.status_code
        retryable = status == 429 or status >= 500
        if status in (401, 403):
            code = "image_generation_auth_failed"
        elif status == 429:
            code = "image_generation_rate_limited"
        elif status >= 500:
            code = "image_generation_server_error"
        else:
            code = "image_generation_rejected"
        return ImageGenerationError(
            code,
            f"{prefix} rejected request with status {status}: {hint}",
            retryable=retryable,
        )


def guess_mime_from_bytes(data: bytes) -> str:
    """Best-effort MIME detection for common generated image formats."""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "image/png"


def _mime_from_url(url: str) -> str:
    path = urlparse(url).path
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or ""


def _response_error_hint(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message[:300]
        message = body.get("message")
        if isinstance(message, str) and message:
            return message[:300]
    return str(body)[:300]
