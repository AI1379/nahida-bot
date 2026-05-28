"""Authentication dependencies for the WebAPI."""

import hmac

import structlog
from fastapi import HTTPException, Request, status

logger = structlog.get_logger(__name__)


def _extract_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return request.query_params.get("token")


def _has_valid_bearer_token(request: Request) -> bool:
    configured: str = request.app.state.auth_token
    if not configured:
        return False
    provided = _extract_token(request)
    return hmac.compare_digest(provided or "", configured)


def _has_valid_webui_session(request: Request) -> bool:
    auth_service = getattr(request.app.state, "webui_auth", None)
    if auth_service is None or not auth_service.password_configured:
        return False
    return auth_service.get_request_session(request) is not None


def _auth_required(request: Request) -> bool:
    if getattr(request.app.state, "auth_token", ""):
        return True
    auth_service = getattr(request.app.state, "webui_auth", None)
    return bool(auth_service is not None and auth_service.password_configured)


def require_token(request: Request) -> None:
    if not _auth_required(request):
        return

    if _has_valid_bearer_token(request) or _has_valid_webui_session(request):
        return

    provided = _extract_token(request)
    logger.warning(
        "webapi.auth_failed",
        path=request.url.path,
        has_token=provided is not None,
        has_session_cookie="nahida_webui_session" in request.cookies,
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials",
    )
