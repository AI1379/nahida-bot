"""Send message endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status

from nahida_bot.core.chat_address import ChatAddress, VALID_TARGET_TYPES
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import SendMessageRequest, SendMessageResponse

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

    # Resolve platform and chat_id from target or legacy fields
    address: ChatAddress | None = None
    target_is_canonical = False
    if body.target:
        try:
            address = ChatAddress.parse(body.target)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid target format: {exc}",
            ) from exc
        platform = address.channel
        chat_id = address.target_id
        target_is_canonical = _is_canonical_target(body.target)
    elif body.platform and body.chat_id:
        platform = body.platform
        chat_id = body.chat_id
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide 'target' or both 'platform' and 'chat_id'.",
        )

    session_id = body.session_id
    if not session_id:
        if address is not None and target_is_canonical:
            session_id = app.message_router.get_active_session_id(address)
        else:
            session_id = app.message_router.get_active_session_id(platform, chat_id)

    channel = app.channel_registry.get(platform)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{platform}' not found or not connected",
        )

    from nahida_bot.plugins.base import OutboundMessage

    await channel.send_message(chat_id, OutboundMessage(text=body.text))

    return SendMessageResponse(status="sent", session_id=session_id)


def _is_canonical_target(value: str) -> bool:
    parts = value.split(":")
    return len(parts) >= 3 and parts[1] in VALID_TARGET_TYPES
