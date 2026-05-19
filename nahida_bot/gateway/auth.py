"""Token-based authentication for the WebAPI."""

from fastapi import HTTPException, Request, status


def _extract_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return request.query_params.get("token")


def require_token(request: Request) -> None:
    configured: str = request.app.state.auth_token
    if not configured:
        return
    provided = _extract_token(request)
    if provided != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
        )
