"""Browser WebUI authentication endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status

from nahida_bot.gateway.schemas import (
    AuthLoginRequest,
    AuthLoginResponse,
    AuthSessionResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _auth_service(request: Request):
    return getattr(request.app.state, "webui_auth", None)


@router.post("/api/auth/login", response_model=AuthLoginResponse)
async def login(
    body: AuthLoginRequest,
    request: Request,
    response: Response,
) -> AuthLoginResponse:
    auth = _auth_service(request)
    if auth is None or not auth.password_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WebUI password login is not configured",
        )

    if not auth.is_login_allowed(request):
        logger.warning(
            "webui.login_rate_limited",
            client_host=request.client.host if request.client else "",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts",
        )

    if not auth.verify_password(body.password):
        auth.record_login_failure(request)
        logger.warning(
            "webui.login_failed",
            client_host=request.client.host if request.client else "",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    auth.record_login_success(request)
    session = auth.create_session(request)
    auth.set_session_cookie(response, request, session)
    logger.info(
        "webui.login_succeeded",
        client_host=request.client.host if request.client else "",
        expires_at=session.expires_at.isoformat(),
    )
    return AuthLoginResponse(
        authenticated=True,
        expires_at=session.expires_at.isoformat(),
    )


@router.post("/api/auth/logout", response_model=AuthSessionResponse)
async def logout(request: Request, response: Response) -> AuthSessionResponse:
    auth = _auth_service(request)
    if auth is not None:
        auth.destroy_request_session(request)
        auth.clear_session_cookie(response, request)
    return AuthSessionResponse(
        authenticated=False,
        auth_required=bool(auth is not None and auth.password_configured),
        mode=auth.mode if auth is not None else "none",
    )


@router.get("/api/auth/session", response_model=AuthSessionResponse)
async def session(request: Request) -> AuthSessionResponse:
    auth = _auth_service(request)
    if auth is None or not auth.password_configured:
        return AuthSessionResponse(
            authenticated=True,
            auth_required=False,
            mode="none",
        )

    current = auth.get_request_session(request)
    return AuthSessionResponse(
        authenticated=current is not None,
        auth_required=True,
        mode=auth.mode,
        expires_at=current.expires_at.isoformat() if current is not None else "",
    )
