"""Fail-closed tests for ConfigurationValidator."""
from __future__ import annotations

from app.validation.configuration_validator import ConfigurationValidator


def test_passes_for_valid_settings(local_settings):
    result = ConfigurationValidator().validate(local_settings)
    assert result.passed


def test_fails_closed_when_config_root_missing(local_settings, tmp_path):
    missing = tmp_path / "does-not-exist"
    broken = local_settings.model_copy(update={"config_root": missing})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed
    assert any("does not exist" in issue.message for issue in result.issues)


def test_fails_closed_for_invalid_log_level(local_settings):
    broken = local_settings.model_copy(update={"log_level": "VERBOSE"})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_for_empty_service_name(local_settings):
    broken = local_settings.model_copy(update={"service_name": "   "})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_for_blank_default_llm(local_settings):
    broken = local_settings.model_copy(update={"default_llm": "   "})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_passes_for_production_settings_with_deployment_config(foundry_configured_settings):
    result = ConfigurationValidator().validate(foundry_configured_settings)
    assert result.passed


def test_fails_closed_in_production_when_azure_subscription_id_missing(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(update={"azure_subscription_id": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_resource_group_missing(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(update={"deployment_resource_group": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_acr_name_missing(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"deployment_acr_name": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_acr_agent_pool_missing(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(
        update={"deployment_acr_agent_pool_name": None}
    )
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_container_apps_environment_id_missing(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(
        update={"deployment_container_apps_environment_id": None}
    )
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_storage_account_name_is_not_required_for_container_app_frontend_deployment(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(update={"deployment_storage_account_name": None})
    result = ConfigurationValidator().validate(broken)
    assert result.passed


def test_fails_closed_in_production_when_deployment_location_missing(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"deployment_location": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_missing_deployment_config_does_not_fail_in_development(local_settings):
    assert local_settings.deployment_resource_group is None
    result = ConfigurationValidator().validate(local_settings)
    assert result.passed


def test_production_fails_closed_when_prototype_api_gateway_is_disabled(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(
        update={"prototype_api_gateway_enabled": False}
    )

    result = ConfigurationValidator().validate(broken)

    assert not result.passed
    assert any(
        "prototype_api_gateway_enabled must be true" in issue.message
        for issue in result.issues
    )


def test_prototype_api_gateway_fails_closed_when_required_settings_are_missing(local_settings):
    broken = local_settings.model_copy(update={"prototype_api_gateway_enabled": True})

    result = ConfigurationValidator().validate(broken)

    assert not result.passed
    messages = {issue.message for issue in result.issues}
    assert any("prototype_api_gateway_publisher_email" in message for message in messages)
    assert any("prototype_api_gateway_publisher_name" in message for message in messages)


def test_prototype_api_gateway_does_not_require_entra(local_settings):
    configured = local_settings.model_copy(
        update={
            "prototype_api_gateway_enabled": True,
            "prototype_api_gateway_publisher_email": "genie@example.com",
            "prototype_api_gateway_publisher_name": "Genie",
        }
    )

    result = ConfigurationValidator().validate(configured)

    assert result.passed


def test_work_iq_enabled_without_endpoint_or_retrieve_tool_fails_closed(local_settings):
    broken = local_settings.model_copy(update={"work_iq_enabled": True})

    result = ConfigurationValidator().validate(broken)

    assert not result.passed
    messages = {issue.message for issue in result.issues}
    assert any("work_iq_mcp_endpoint" in message for message in messages)
    assert any("work_iq_retrieve_tool" in message for message in messages)


def test_work_iq_enabled_without_oauth_client_configuration_fails_closed(local_settings):
    broken = local_settings.model_copy(
        update={
            "work_iq_enabled": True,
            "work_iq_mcp_endpoint": "https://workiq.svc.cloud.microsoft/mcp",
            "work_iq_retrieve_tool": "ask",
        }
    )

    result = ConfigurationValidator().validate(broken)

    assert not result.passed
    messages = {issue.message for issue in result.issues}
    assert any("iq_oauth_tenant_id" in message for message in messages)
    assert any("iq_oauth_client_id" in message for message in messages)
    assert any("iq_oauth_client_secret_env_var" in message for message in messages)
    assert any("iq_oauth_redirect_uri" in message for message in messages)


def test_work_iq_oauth_redirect_uri_must_use_https(local_settings):
    broken = local_settings.model_copy(
        update={
            "work_iq_enabled": True,
            "work_iq_mcp_endpoint": "https://workiq.svc.cloud.microsoft/mcp",
            "work_iq_retrieve_tool": "ask",
            "iq_oauth_tenant_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_secret_env_var": "TEST_SECRET",
            "iq_oauth_redirect_uri": "http://localhost:8000/iq/connections/callback",
        }
    )

    result = ConfigurationValidator().validate(broken)

    assert not result.passed
    assert any("iq_oauth_redirect_uri must use HTTPS" in issue.message for issue in result.issues)


def test_fabric_iq_enabled_without_scopes_fails_closed(local_settings):
    broken = local_settings.model_copy(
        update={
            "fabric_iq_enabled": True,
            "fabric_iq_mcp_endpoint": "https://fabriciq.svc.cloud.microsoft/v1/mcp/fabriciq",
            "fabric_iq_retrieve_tool": "run_dax_query",
            "iq_oauth_tenant_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_secret_env_var": "TEST_SECRET",
            "iq_oauth_redirect_uri": "https://localhost:8000/iq/connections/callback",
        }
    )

    result = ConfigurationValidator().validate(broken)

    assert not result.passed
    assert any("fabric_iq_scopes" in issue.message for issue in result.issues)


def test_work_iq_fully_configured_in_development_passes(local_settings):
    configured = local_settings.model_copy(
        update={
            "work_iq_enabled": True,
            "work_iq_mcp_endpoint": "https://workiq.svc.cloud.microsoft/mcp",
            "work_iq_retrieve_tool": "ask",
            "iq_oauth_tenant_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_secret_env_var": "TEST_SECRET",
            "iq_oauth_redirect_uri": "https://localhost:8000/iq/connections/callback",
        }
    )

    result = ConfigurationValidator().validate(configured)

    assert result.passed


def test_work_iq_enabled_in_production_without_explicit_allow_flag_fails_closed(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(
        update={
            "work_iq_enabled": True,
            "work_iq_mcp_endpoint": "https://workiq.svc.cloud.microsoft/mcp",
            "work_iq_retrieve_tool": "ask",
            "iq_oauth_tenant_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_id": "00000000-0000-0000-0000-000000000000",
            "iq_oauth_client_secret_env_var": "TEST_SECRET",
            "iq_oauth_redirect_uri": "https://example.test/iq/connections/callback",
            "iq_delegated_oauth_allowed_in_production": False,
        }
    )

    result = ConfigurationValidator().validate(broken)

    assert not result.passed
    assert any(
        "iq_delegated_oauth_allowed_in_production" in issue.message for issue in result.issues
    )
