"""
Role-based access control for the WebUI.

Roles:
  admin    — full access (config, risk params, bot management, strategy promotion)
  operator — can view all data, start/stop bots, run backtests
  viewer   — read-only access to dashboard, trades, research

Uses bearer tokens mapped to roles via app_settings table:
  rbac_token_{hash} = role

Usage in routes:
  from alphaloop.webui.auth_rbac import require_role, Role

  @router.post("/start")
  async def start(..., _rbac: None = require_role(Role.ADMIN)):
      ...
"""

import hashlib
import logging
from enum import StrEnum
from typing import Callable

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


# Endpoint -> minimum required role
ROUTE_PERMISSIONS: dict[str, Role] = {
    # Admin-only
    "PUT /api/settings": Role.ADMIN,
    "POST /api/bots/start": Role.ADMIN,
    "POST /api/strategies/*/promote": Role.ADMIN,
    "POST /api/strategies/*/activate": Role.ADMIN,
    "DELETE /api/strategies/*": Role.ADMIN,
    "PUT /api/strategies/*/models": Role.ADMIN,
    # Operator
    "POST /api/backtests": Role.OPERATOR,
    "PATCH /api/backtests/*/stop": Role.OPERATOR,
    "PATCH /api/backtests/*/resume": Role.OPERATOR,
    "POST /api/bots/*/stop": Role.OPERATOR,
    # Viewer — all GET endpoints
}

# Role hierarchy: admin > operator > viewer
ROLE_HIERARCHY = {
    Role.ADMIN: 3,
    Role.OPERATOR: 2,
    Role.VIEWER: 1,
}


def has_permission(user_role: Role, required_role: Role) -> bool:
    """Check if user_role meets or exceeds required_role."""
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required_role, 0)


def hash_token(token: str) -> str:
    """Hash a token for storage lookup."""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


async def resolve_role(
    token: str,
    settings_service=None,
) -> Role | None:
    """
    Resolve a bearer token to a role.

    Checks app_settings for rbac_token_{hash} = role.
    Falls back to admin if RBAC is not configured (single-token mode).
    """
    if not settings_service:
        return Role.ADMIN  # No settings service = dev mode

    # Check if RBAC is enabled
    rbac_enabled = await settings_service.get_bool("RBAC_ENABLED", default=False)
    if not rbac_enabled:
        return Role.ADMIN  # Single-token mode = admin

    # Look up token in settings
    token_hash = hash_token(token)
    role_str = await settings_service.get(f"rbac_token_{token_hash}")
    if not role_str:
        return None  # Unknown token

    try:
        return Role(role_str)
    except ValueError:
        logger.warning("[rbac] Unknown role '%s' for token hash %s", role_str, token_hash[:8])
        return None


def require_role(min_role: Role) -> Callable:
    """FastAPI dependency factory that enforces a minimum role for a route.

    The role is read from ``request.state.role`` which is set by
    ``BearerAuthMiddleware._attach_identity()`` after the bearer token is
    validated. When RBAC is disabled (single-token mode), the middleware
    assigns Role.ADMIN to every authenticated request, so all routes pass.

    Usage::

        @router.post("/start")
        async def start_agent(..., _rbac: None = require_role(Role.ADMIN)):
            ...
    """
    async def _check(request: Request) -> None:
        role: Role | None = getattr(request.state, "role", None)
        if role is None:
            # Middleware did not attach a role — RBAC not active or dev mode.
            # Grant access rather than blocking, to preserve existing behaviour
            # when RBAC_ENABLED=False (single-token mode = full admin access).
            return
        if not has_permission(role, min_role):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions — requires '{min_role}' role, "
                       f"token has '{role}' role.",
            )

    return Depends(_check)
