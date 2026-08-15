"""Deployment-readiness domain models (Phase 10: Azure deployment).

These models describe the state of an Azure *subscription* before Genie's
infrastructure exists - never the running application's own configuration.
See ``app/deployment/deployment_readiness_validator.py`` for how they are
produced.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RegistrationState = Literal[
    "Registered",
    "Registering",
    "NotRegistered",
    "Unregistering",
    "Unknown",
]

__all__ = [
    "DeploymentReadinessReport",
    "RegistrationState",
    "ResourceProviderRequirement",
    "ResourceProviderStatus",
]


class ResourceProviderRequirement(BaseModel):
    """A single Azure resource provider namespace Genie's infrastructure needs.

    Loaded from ``config/deployment/resource_providers.yaml`` - never
    hardcoded, per the Configuration Rules in
    ``.github/copilot-instructions.md``.
    """

    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required: bool = True


class ResourceProviderStatus(BaseModel):
    """The observed registration state of one provider namespace on a subscription."""

    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    required: bool
    registration_state: RegistrationState

    @property
    def is_blocking(self) -> bool:
        return self.required and self.registration_state != "Registered"


class DeploymentReadinessReport(BaseModel):
    """Aggregate result of checking every required provider on a subscription."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: str = Field(min_length=1)
    checked_at: datetime
    statuses: list[ResourceProviderStatus] = Field(default_factory=list)

    @property
    def blocking_statuses(self) -> list[ResourceProviderStatus]:
        return [status for status in self.statuses if status.is_blocking]

    @property
    def is_ready(self) -> bool:
        return len(self.blocking_statuses) == 0
