"""Bearer token authentication middleware."""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """
    Protects state-modifying endpoints with a Bearer token.

    Token resolution order:
      1. AUTH_TOKEN environment variable
      2. app.state.container.config (not yet — kept simple)

    GET / OPTIONS / HEAD requests are always allowed.
    When no token is configured (dev mode), all requests pass through.
    """

    SAFE_METHODS = {"GET", "OPTIONS", "HEAD"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        token = self._resolve_token(request)

        # No token configured — dev mode, allow everything
        if not token:
            return await call_next(request)

        # Safe methods always allowed
        if request.method in self.SAFE_METHODS:
            return await call_next(request)

        # WebSocket upgrade requests are authenticated at the handler level
        if request.url.path.startswith("/ws"):
            return await call_next(request)

        # Check bearer token
        auth_header = request.headers.get("Authorization", "")
        provided = auth_header.removeprefix("Bearer ").strip()
        if provided != token:
            return JSONResponse(
                {"detail": "Unauthorized — provide Authorization: Bearer <token>"},
                status_code=401,
            )

        return await call_next(request)

    @staticmethod
    def _resolve_token(request: Request) -> str:
        """Get the configured auth token."""
        # Environment variable takes priority
        env_token = os.environ.get("AUTH_TOKEN", "")
        if env_token:
            return env_token

        # Fall back to container config if available
        container = getattr(request.app.state, "container", None)
        if container:
            return getattr(container.config, "auth_token", "")

        return ""
