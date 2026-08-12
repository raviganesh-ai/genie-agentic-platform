"""FastAPI authentication dependency.

Validates every request's bearer token via the ``TokenValidator`` built at
startup (``app.state.token_validator``, set in ``app.main.create_app``) and
exposes the resulting ``AuthenticatedUser``. Unauthorized requests fail
closed with ``401`` before any route handler runs, per "API routes must
validate: user identity, session ownership, authorization policies.
Unauthorized requests must fail." in ``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security.auth_models import AuthenticatedUser
from app.security.token_validator import (
    AuthenticationError,
    AuthenticationServiceUnavailableError,
    TokenValidator,
)

__all__ = ["get_current_user"]

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_validator: TokenValidator = request.app.state.token_validator
    try:
        claims = await token_validator.validate(
            credentials.credentials,
            method=request.method,
            path=request.url.path,
        )
    except AuthenticationServiceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
            headers={"WWW-Authenticate": exc.www_authenticate or "Bearer"},
        ) from exc

    user_id = str(claims.get("oid") or claims.get("sub"))
    roles = claims.get("roles")
    return AuthenticatedUser(
        user_id=user_id,
        display_name=str(claims.get("name", "")),
        roles=list(roles) if isinstance(roles, list) else [],
    )
