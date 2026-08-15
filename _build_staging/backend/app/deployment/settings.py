"""Deployment-time settings for provisioning Genie's Azure infrastructure.

Deliberately separate from ``app.config.settings.Settings``: the running
Genie backend never needs to know its own Azure subscription id, but the
deployment-readiness tooling that runs *before* any infrastructure exists
does. Keeping the two apart means the application's Settings never grows
fields it has no runtime use for.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DeploymentSettings(BaseSettings):
    """Externally sourced settings for the deployment-readiness CLI tooling.

    All values come from environment variables prefixed ``GENIE_DEPLOY_``
    (optionally via a local ``.env`` file) - never hardcoded, per the
    Configuration Rules in ``.github/copilot-instructions.md``.
    """

    model_config = SettingsConfigDict(
        env_prefix="GENIE_DEPLOY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    subscription_id: str | None = None
    location: str = "eastus2"
    resource_providers_config_dir: Path = Path("config/deployment")


def get_deployment_settings() -> DeploymentSettings:
    """Build a fresh ``DeploymentSettings`` instance from the current environment."""

    return DeploymentSettings()
