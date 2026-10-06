"""FastAPI identity dependency.

When first-party authentication is disabled (``Settings.auth_enabled`` -
the default, matching every existing dev/test environment), every request
resolves to the single deterministic internal identity, exactly as before.
When enabled, every request must carry a valid ``Authorization: Bearer
<token>`` header issued by ``POST /auth/login`` (see
``app.security.auth_service``) - missing, malformed, expired, or
mis-signed tokens are rejected with 401 before any route body runs, so
``app.services.session_service``'s per-session ownership checks compare
real, distinct identities instead of the same shared value for every
caller.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.security.auth_models import GENIE_ADMIN_ROLE, AuthenticatedUser
from app.security.auth_service import AuthNotConfiguredError, AuthService
from app.security.token_service import TokenValidationError

__all__ = ["GENIE_ADMIN_ROLE", "get_current_user", "require_genie_admin"]

_INTERNAL_USER = AuthenticatedUser(
    user_id="genie-internal-user",
    object_id="genie-internal-user",
    display_name="Genie Internal User",
    roles=[GENIE_ADMIN_ROLE],
)
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="A valid 'Authorization: Bearer <token>' header is required.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(request: Request) -> AuthenticatedUser:
    auth_service: AuthService = request.app.state.auth_service
    if not auth_service.enabled:
        return _INTERNAL_USER.model_copy(deep=True)

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _UNAUTHORIZED
    try:
        claims = auth_service.validate_bearer_token(token)
    except (TokenValidationError, AuthNotConfiguredError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return AuthenticatedUser(
        user_id=claims.user_id,
        object_id=claims.user_id,
        display_name=claims.display_name or claims.user_id,
        roles=list(claims.roles),
    )


def require_genie_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    if GENIE_ADMIN_ROLE not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User is not authorized for Genie administration (requires '{GENIE_ADMIN_ROLE}').",
        )
    return user
