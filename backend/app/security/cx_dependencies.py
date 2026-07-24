"""FastAPI dependency for the customer-experience (cx) session identity.

Distinct from ``app.security.dependencies.get_current_user`` (internal
Mission Control staff, authenticated via Microsoft Entra ID): this
dependency authenticates the generated customer prototype surface using a
short-lived, session-scoped ``CxTokenService`` token (see
``app.security.cx_tokens``), read from either an ``HttpOnly`` cookie (set
by ``app/api/cx.py`` on first load) or a one-time ``t`` query-string
parameter (present only in the link a Genie operator shares with the
customer). Fails closed with 401/403 before any route handler runs, and
always re-validates that the token's ``session_id`` claim matches the
requested path's ``session_id`` - a token minted for one customer session
can never be used to access another.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.security.cx_tokens import CustomerSessionClaims, CxTokenInvalidError, CxTokenService

__all__ = ["CX_SESSION_COOKIE_NAME", "get_current_customer_session"]

CX_SESSION_COOKIE_NAME = "genie_cx_session"


async def get_current_customer_session(
    session_id: str, request: Request
) -> CustomerSessionClaims:
    token = request.cookies.get(CX_SESSION_COOKIE_NAME) or request.query_params.get("t")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing customer-experience session token.",
        )

    cx_token_service: CxTokenService = request.app.state.cx_token_service
    try:
        claims = cx_token_service.validate(token)
    except CxTokenInvalidError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if claims.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer-experience token is not valid for this session.",
        )

    return claims
