"""Materialize channel attachments and resolve their cached media."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import structlog

from nahida_bot.agent.media.resolver import MediaResolver, ResolvedMedia
from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.core.context import current_session
from nahida_bot_sdk import InboundAttachment

logger = structlog.get_logger(__name__)


async def resolve_attachment(
    attachment: InboundAttachment,
    *,
    media_resolver: MediaResolver | None,
    channel_registry: ChannelRegistry | None,
) -> ResolvedMedia:
    """Resolve an attachment via MediaResolver if available."""
    attachment = await download_platform_attachment(
        attachment, channel_registry=channel_registry
    )
    if media_resolver is None:
        return ResolvedMedia(
            media_id=attachment.platform_id,
            mime_type=attachment.mime_type,
            local_path=attachment.path,
            file_size=attachment.file_size,
            width=attachment.width,
            height=attachment.height,
            description=attachment.alt_text,
        )
    return await media_resolver.resolve(attachment)


async def download_platform_attachment(
    attachment: InboundAttachment, *, channel_registry: ChannelRegistry | None
) -> InboundAttachment:
    """Use the current channel service to materialize opaque platform media IDs."""
    if attachment.path and Path(attachment.path).is_file():
        logger.debug(
            "session_runner.platform_media_download_skipped",
            reason="already_resolved",
            media_id=attachment.platform_id,
        )
        return attachment
    if attachment.path:
        logger.debug(
            "session_runner.platform_media_path_expired",
            media_id=attachment.platform_id,
            path=attachment.path,
        )
        attachment = replace(attachment, path="")
    if attachment.url or not attachment.platform_id:
        logger.debug(
            "session_runner.platform_media_download_skipped",
            reason="already_resolved" if attachment.url else "missing_platform_id",
            media_id=attachment.platform_id,
        )
        return attachment
    if channel_registry is None:
        logger.debug(
            "session_runner.platform_media_download_skipped",
            reason="no_channel_registry",
            media_id=attachment.platform_id,
        )
        return attachment
    ctx = current_session.get()
    if ctx is None:
        logger.debug(
            "session_runner.platform_media_download_skipped",
            reason="no_session_context",
            media_id=attachment.platform_id,
        )
        return attachment
    channel = channel_registry.get(ctx.platform)
    if channel is None:
        logger.debug(
            "session_runner.platform_media_download_skipped",
            reason="channel_not_found",
            platform=ctx.platform,
            media_id=attachment.platform_id,
        )
        return attachment
    download = getattr(channel, "download_media", None)
    if download is None:
        logger.debug(
            "session_runner.platform_media_download_skipped",
            reason="download_media_unavailable",
            platform=ctx.platform,
            media_id=attachment.platform_id,
        )
        return attachment

    try:
        logger.debug(
            "session_runner.platform_media_download_start",
            platform=ctx.platform,
            media_id=attachment.platform_id,
        )
        result = await download(attachment.platform_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "session_runner.platform_media_download_failed",
            platform=ctx.platform,
            media_id=attachment.platform_id,
            error=str(exc),
        )
        return attachment

    if result is None or not getattr(result, "path", ""):
        logger.debug(
            "session_runner.platform_media_download_empty",
            platform=ctx.platform,
            media_id=attachment.platform_id,
        )
        return attachment
    logger.debug(
        "session_runner.platform_media_download_success",
        platform=ctx.platform,
        media_id=attachment.platform_id,
        mime_type=result.mime_type or attachment.mime_type,
        file_size=result.file_size or attachment.file_size,
    )
    return replace(
        attachment,
        path=result.path,
        mime_type=result.mime_type or attachment.mime_type,
        file_size=result.file_size or attachment.file_size,
    )
