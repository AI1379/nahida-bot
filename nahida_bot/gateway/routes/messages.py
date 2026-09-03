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

    message_id = await channel.send_message(
        address.target_id,
        OutboundMessage(text=body.text, extra={"chat_address": address.chat_key}),
    )
    delivery_store = getattr(app, "message_delivery_store", None)
    if not message_id:
        if delivery_store is not None:
            await delivery_store.record(
                target_chat_address=address.chat_key,
                platform=address.channel,
                target_type=address.target_type,
                target_id=address.target_id,
                source_session_id=session_id,
                source_chat_address=address.chat_key,
                source_user_id="webapi",
                source="webapi_send",
                delivery_mode="notify",
                status="failed",
                text=body.text,
                error="channel returned no message id",
                metadata={"requested_session_id": body.session_id or ""},
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Channel did not confirm message delivery",
        )

    delivery_id: str | None = None
    if delivery_store is not None:
        delivery = await delivery_store.record(
            target_chat_address=address.chat_key,
            platform=address.channel,
            target_type=address.target_type,
            target_id=address.target_id,
            source_session_id=session_id,
            source_chat_address=address.chat_key,
            source_user_id="webapi",
            source="webapi_send",
            delivery_mode="notify",
            status="sent",
            message_id=message_id,
            text=body.text,
            metadata={"requested_session_id": body.session_id or ""},
        )
        stored_delivery_id = getattr(delivery, "delivery_id", "")
        if isinstance(stored_delivery_id, str) and stored_delivery_id:
            delivery_id = stored_delivery_id

    logger.info(
        "webapi.message_sent",
        target=body.target,
        session_id=session_id,
        text_len=len(body.text),
    )

    return SendMessageResponse(
        status="sent",
        session_id=session_id,
        message_id=message_id,
        delivery_id=delivery_id,
    )
