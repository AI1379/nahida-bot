"""Send message endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status

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

    session_id = body.session_id
    if not session_id:
        session_id = app.message_router.get_active_session_id(
            body.platform, body.chat_id
        )

    channel = app.channel_registry.get(body.platform)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{body.platform}' not found or not connected",
        )

    from nahida_bot.plugins.base import OutboundMessage

    await channel.send_message(body.chat_id, OutboundMessage(text=body.text))

    return SendMessageResponse(status="sent", session_id=session_id)
