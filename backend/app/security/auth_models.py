"""Authenticated user identity model.

Populated exclusively from a validated Microsoft Entra ID bearer token
(see ``app.security.token_validator``) - never trusted from any other
request field, per the Security Requirements in
``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AuthenticatedUser"]


class AuthenticatedUser(BaseModel):
    """The identity of the user making the current request."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(
        min_length=1,
        description="Canonical '<tid>:<oid>' identity, or the subject in local development.",
    )
    tenant_id: str = ""
    object_id: str = Field(
        default="",
        description="The token's immutable 'oid' claim, falling back to 'sub'.",
    )
    display_name: str = ""
    roles: list[str] = Field(default_factory=list)
