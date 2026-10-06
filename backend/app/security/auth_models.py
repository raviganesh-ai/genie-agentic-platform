"""Internal request identity model."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AuthenticatedUser", "GENIE_ADMIN_ROLE"]

GENIE_ADMIN_ROLE = "Genie.Admin"


class AuthenticatedUser(BaseModel):
    """The deterministic internal identity attached to every request."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(
        min_length=1,
        description="Stable identity used for ownership and governance attribution.",
    )
    tenant_id: str = ""
    object_id: str = Field(
        default="",
        description="Stable object identifier for the internal principal.",
    )
    display_name: str = ""
    roles: list[str] = Field(default_factory=list)
