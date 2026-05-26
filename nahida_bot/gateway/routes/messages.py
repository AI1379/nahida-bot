"""Send message endpoint."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import SendMessageRequest, SendMessageResponse
from nahida_bot.plugins.base import OutboundMessage

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/api/send", response_model=SendMessageResponse)
async def send_message(
    body: SendMessageRequest,
    app=Depends(get_application),
) -> SendMessageResponse:
    if app.message_router is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Message router not initialized",
        )

    if not body.target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a typed 'target' such as 'milky:group:20001'.",
        )
    try:
        address = ChatAddress.parse(body.target)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target format: {exc}",
        ) from exc
    if not address.is_typed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target must include a chat type, such as private or group.",
        )

    session_id = body.session_id
    if not session_id:
        session_id = app.message_router.get_active_session_id(address)

    channel = app.channel_registry.get(address.channel)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{address.channel}' not found or not connected",
        )

    await channel.send_message(
        address.target_id,
        OutboundMessage(text=body.text, extra={"chat_address": address.chat_key}),
    )

    logger.info(
        "webapi.message_sent",
        target=body.target,
        session_id=session_id,
        text_len=len(body.text),
    )

    return SendMessageResponse(status="sent", session_id=session_id)
