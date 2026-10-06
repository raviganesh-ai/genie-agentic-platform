"""First-party login: the only unauthenticated, credential-accepting route.

A deliberately small surface - one route, one job: turn a username/password
into a short-lived bearer token. See ``app.security.auth_service`` for the
actual validation/issuance logic; this router only translates HTTP <->
service calls and maps domain errors to response codes, per "API routes
must remain thin" in ``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_auth_service
from app.security.auth_service import AuthenticationError, AuthNotConfiguredError, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: float


class AuthStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_enabled: bool


@router.get("/status")
async def auth_status(
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthStatusResponse:
    """Unauthenticated: lets the frontend decide whether to show a login
    gate at all, so a dev/test deployment with auth disabled keeps working
    exactly as before with no login screen."""

    return AuthStatusResponse(auth_enabled=auth_service.enabled)


@router.post("/login")
async def login(
    body: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    try:
        token = auth_service.authenticate(username=body.username, password=body.password)
    except AuthNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return LoginResponse(
        access_token=token,
        expires_in_seconds=auth_service.token_ttl_seconds,
    )
