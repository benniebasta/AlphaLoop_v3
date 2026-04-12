"""Bearer token authentication middleware and login endpoint."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


def get_operator_id(request: Request) -> str:
    """Return a stable, non-spoofable operator identifier derived from the
    bearer token (set by BearerAuthMiddleware). Never use a client-supplied
    body field as operator identity — this function is the canonical source.

    Returns a short hash prefix like ``op-a3f2c1b8`` that is unique per token
    but does not expose the token value. Operators can configure a human-readable
    alias by setting ``operator_alias_<hash>`` in app_settings.
    """
    return getattr(request.state, "operator_id", "op-anonymous")

# Paths that bypass auth completely (health probes, static assets, auth login)
_PUBLIC_PREFIXES = ("/health", "/static", "/favicon", "/api/auth", "/api/events/ingest")


# ── Login endpoint ───────────────────────────────────────────────────────────

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    token: str


@auth_router.post("/login")
async def login(body: LoginRequest, request: Request) -> dict:
    """Validate a token against the server-side configured AUTH_TOKEN.

    Returns ``{"ok": true, "required": true}`` on success so the frontend
    can store the token in localStorage for subsequent requests.
    """
    server_token = await BearerAuthMiddleware._resolve_token(request)
    if not server_token:
        return {"ok": True, "required": False}
    if body.token == server_token:
        return {"ok": True, "required": True}
    return JSONResponse({"ok": False, "detail": "Invalid token"}, status_code=401)


@auth_router.get("/status")
async def auth_status(request: Request) -> dict:
    """Check whether AUTH_TOKEN is configured (without revealing it)."""
    server_token = await BearerAuthMiddleware._resolve_token(request)
    return {"required": bool(server_token)}


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """
    Protects state-modifying endpoints with a Bearer token.

    Token resolution order:
      1. AUTH_TOKEN environment variable
      2. app.state.container.config

    In production mode, all mutating requests are REJECTED if no token is configured.
    In dev mode, all requests pass through when no token is set.
    """

    SAFE_METHODS = {"GET", "OPTIONS", "HEAD"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Public paths always allowed
        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        token = await self._resolve_token(request)
        is_production = os.environ.get("ENVIRONMENT", "dev").lower() in (
            "production", "prod",
        )

        # Phase 7E: Sensitive mutation paths require auth even in dev mode
        _SENSITIVE_PREFIXES = (
            "/api/bots", "/api/settings", "/api/strategies",
            "/api/live", "/api/canary",
        )
        _is_sensitive_mutation = (
            request.method not in self.SAFE_METHODS
            and any(path.startswith(p) for p in _SENSITIVE_PREFIXES)
        )

        # No token configured
        if not token:
            if is_production and request.method not in self.SAFE_METHODS:
                logger.critical(
                    "[auth] REJECTED %s %s — no AUTH_TOKEN configured in production",
                    request.method, path,
                )
                return JSONResponse(
                    {"detail": "AUTH_TOKEN must be configured in production mode"},
                    status_code=503,
                )
            if _is_sensitive_mutation:
                # Phase 7E: even in dev mode, block sensitive mutations without auth
                logger.warning(
                    "[auth] REJECTED %s %s — sensitive endpoint requires AUTH_TOKEN "
                    "even in dev mode",
                    request.method, path,
                )
                return JSONResponse(
                    {"detail": "Sensitive endpoint requires AUTH_TOKEN configuration"},
                    status_code=401,
                )
            # Dev mode — allow non-sensitive requests
            return await call_next(request)

        # Safe methods always allowed
        if request.method in self.SAFE_METHODS:
            return await call_next(request)

        # WebSocket upgrade requests are authenticated at the handler level
        if path.startswith("/ws"):
            return await call_next(request)

        # Check bearer token
        auth_header = request.headers.get("Authorization", "")
        provided = auth_header.removeprefix("Bearer ").strip()
        if provided != token:
            return JSONResponse(
                {"detail": "Unauthorized — provide Authorization: Bearer <token>"},
                status_code=401,
            )

        # Token is valid — resolve RBAC role and operator identity, store on
        # request.state so routes and dependencies can read them without
        # re-checking the token or accepting client-supplied identity claims.
        await self._attach_identity(request, provided)

        return await call_next(request)

    @staticmethod
    async def _attach_identity(request: Request, token: str) -> None:
        """Resolve RBAC role and stable operator ID from the validated token and
        store both on ``request.state`` so all downstream handlers can read them
        without re-examining the Authorization header or trusting client-supplied
        identity fields."""
        from alphaloop.webui.auth_rbac import Role, hash_token, resolve_role

        token_hash = hash_token(token)
        # Stable non-spoofable operator identifier (hash prefix, not the token)
        request.state.operator_id = f"op-{token_hash[:8]}"

        container = getattr(request.app.state, "container", None)
        settings_svc = getattr(container, "settings_service", None) if container else None

        try:
            role = await resolve_role(token, settings_svc)
            request.state.role = role if role is not None else Role.VIEWER
        except Exception:
            # Fail-safe: if role resolution errors, treat as least-privileged
            request.state.role = Role.VIEWER

    @staticmethod
    async def _resolve_token(request: Request) -> str:
        """Get the configured auth token.

        Resolution order:
          1. AUTH_TOKEN environment variable
          2. Settings service (DB-managed, cached in memory)
          3. AppConfig (pydantic BaseSettings / .env)
        """
        # Environment variable takes priority
        env_token = os.environ.get("AUTH_TOKEN", "")
        if env_token:
            return env_token

        # Fall back to container-managed sources
        container = getattr(request.app.state, "container", None)
        if container:
            # Check settings service (DB-managed, cached in memory)
            settings_svc = getattr(container, "settings_service", None)
            if settings_svc:
                db_token = await settings_svc.get("AUTH_TOKEN", "")
                if db_token:
                    return db_token
            # Fall back to AppConfig
            return getattr(container.config, "auth_token", "")

        return ""
