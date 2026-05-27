"""SSE event stream endpoint for real-time UI updates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from nahida_bot.gateway.deps import get_application

router = APIRouter()


@router.get("/api/events/stream")
async def event_stream(request: Request, app=Depends(get_application)):
    """SSE endpoint that streams real-time events to the WebUI."""
    from sse_starlette.sse import EventSourceResponse

    broadcaster = getattr(request.app.state, "event_broadcaster", None)
    if broadcaster is None:
        from sse_starlette.sse import ServerSentEvent

        async def _no_broadcaster():
            yield ServerSentEvent(
                event="error",
                data='{"detail":"Event broadcaster not available"}',
            )

        return EventSourceResponse(_no_broadcaster())

    q = broadcaster.subscribe()

    async def _stream():
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            broadcaster.unsubscribe(q)

    return EventSourceResponse(_stream(), ping=15)
