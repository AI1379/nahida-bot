"""FastAPI dependency injection for the WebAPI."""

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from nahida_bot.core.app import Application


def get_application(request: Request) -> "Application":
    return request.app.state.application  # type: ignore[no-any-return]
