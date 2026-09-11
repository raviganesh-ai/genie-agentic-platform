"""FastAPI identity dependency for the internal, anonymous Genie workspace."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.security.auth_models import AuthenticatedUser

__all__ = ["GENIE_ADMIN_ROLE", "get_current_user", "require_genie_admin"]

GENIE_ADMIN_ROLE = "Genie.Admin"
_INTERNAL_USER = AuthenticatedUser(
    user_id="genie-internal-user",
    object_id="genie-internal-user",
    display_name="Genie Internal User",
    roles=[GENIE_ADMIN_ROLE],
)


async def get_current_user() -> AuthenticatedUser:
    """Return the single non-user-controlled identity for this internal tool."""

    return _INTERNAL_USER.model_copy(deep=True)


def require_genie_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    if GENIE_ADMIN_ROLE not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User is not authorized for Genie administration (requires '{GENIE_ADMIN_ROLE}').",
        )
    return user
