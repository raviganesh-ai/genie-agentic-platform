"""Unit tests for AzureResourceManagerProviderStatusSource's error normalization."""
from __future__ import annotations

import pytest

from app.deployment.provider_status_source import (
    AzureResourceManagerProviderStatusSource,
    ResourceProviderStatusError,
)


def test_rejects_blank_subscription_id():
    source = AzureResourceManagerProviderStatusSource()

    with pytest.raises(ResourceProviderStatusError, match="subscription_id must not be blank"):
        source.get_registration_state("   ", "Microsoft.Storage")


def test_rejects_blank_namespace():
    source = AzureResourceManagerProviderStatusSource()

    with pytest.raises(ResourceProviderStatusError, match="namespace must not be blank"):
        source.get_registration_state("sub-123", "   ")
