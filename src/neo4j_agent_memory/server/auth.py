"""API key authentication middleware for the Neo4j Agent Memory server."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Paths that bypass authentication
_PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware that validates an ``X-API-Key`` header on every request.

    Public paths (health check, docs) are excluded from authentication.
    """

    def __init__(self, app: object, api_key: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key")
        if provided_key != self._api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)
