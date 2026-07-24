"""Unit tests for the customer-experience (cx) session token seam."""
from __future__ import annotations

import time

import pytest

from app.security.cx_tokens import (
    CxTokenInvalidError,
    CxTokenService,
    CxTokenServiceError,
    create_cx_token_service,
)


def test_mint_then_validate_round_trips_claims() -> None:
    service = CxTokenService(signing_key="unit-test-signing-key")

    token = service.mint(session_id="session-1", workflow_run_id="run-1", ttl_seconds=60)
    claims = service.validate(token)

    assert claims.session_id == "session-1"
    assert claims.workflow_run_id == "run-1"


def test_validate_rejects_expired_token() -> None:
    service = CxTokenService(signing_key="unit-test-signing-key")

    token = service.mint(session_id="session-1", workflow_run_id="run-1", ttl_seconds=0)
    time.sleep(1.1)

    with pytest.raises(CxTokenInvalidError):
        service.validate(token)


def test_validate_rejects_token_signed_with_a_different_key() -> None:
    minted_by = CxTokenService(signing_key="key-a")
    validated_by = CxTokenService(signing_key="key-b")

    token = minted_by.mint(session_id="session-1", workflow_run_id="run-1", ttl_seconds=60)

    with pytest.raises(CxTokenInvalidError):
        validated_by.validate(token)


def test_validate_rejects_garbage_token() -> None:
    service = CxTokenService(signing_key="unit-test-signing-key")

    with pytest.raises(CxTokenInvalidError):
        service.validate("not-a-real-token")


def test_service_construction_rejects_blank_signing_key() -> None:
    with pytest.raises(CxTokenServiceError):
        CxTokenService(signing_key="   ")


def test_create_cx_token_service_fails_closed_in_production_without_configured_key(
    production_settings,
) -> None:
    unsafe = production_settings.model_copy(update={"cx_token_signing_key": None})

    with pytest.raises(CxTokenServiceError):
        create_cx_token_service(unsafe)


def test_create_cx_token_service_uses_configured_key_in_production(production_settings) -> None:
    configured = production_settings.model_copy(
        update={"cx_token_signing_key": "prod-signing-key"}
    )

    service = create_cx_token_service(configured)

    token = service.mint(session_id="session-1", workflow_run_id="run-1", ttl_seconds=60)
    assert service.validate(token).session_id == "session-1"


def test_create_cx_token_service_generates_ephemeral_key_in_local_dev(local_settings) -> None:
    assert local_settings.cx_token_signing_key is None
    assert local_settings.allow_local_agents is True

    service = create_cx_token_service(local_settings)

    token = service.mint(session_id="session-1", workflow_run_id="run-1", ttl_seconds=60)
    assert service.validate(token).session_id == "session-1"


def test_create_cx_token_service_fails_closed_when_local_agents_disallowed_and_no_key(
    local_settings,
) -> None:
    unsafe = local_settings.model_copy(update={"allow_local_agents": False})

    with pytest.raises(CxTokenServiceError):
        create_cx_token_service(unsafe)
