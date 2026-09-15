"""Wire-contract tests for the Microsoft Defender for Cloud scan gateway."""
from __future__ import annotations

import httpx

from app.config.settings import Settings
from app.deploy_launch.defender_for_cloud_gateway import (
    DefenderForCloudGateway,
    NullDefenderForCloudGateway,
    create_defender_for_cloud_gateway,
)


class _FakeCredential:
    def __init__(self, *, token: str = "fake-arm-token") -> None:
        self.scopes: list[str] = []
        self._token = token

    def get_token(self, *scopes: str):
        self.scopes.extend(scopes)
        return type("AccessToken", (), {"token": self._token})()


def test_create_returns_null_gateway_when_not_enabled():
    settings = Settings(defender_for_cloud_enabled=False)  # type: ignore[call-arg]

    gateway = create_defender_for_cloud_gateway(settings=settings)

    assert isinstance(gateway, NullDefenderForCloudGateway)


def test_create_returns_null_gateway_when_no_subscription_configured():
    settings = Settings(defender_for_cloud_enabled=True, azure_subscription_id=None)  # type: ignore[call-arg]

    gateway = create_defender_for_cloud_gateway(settings=settings)

    assert isinstance(gateway, NullDefenderForCloudGateway)


def test_create_returns_real_gateway_when_fully_configured():
    settings = Settings(  # type: ignore[call-arg]
        defender_for_cloud_enabled=True, azure_subscription_id="sub-1"
    )

    gateway = create_defender_for_cloud_gateway(settings=settings)

    assert isinstance(gateway, DefenderForCloudGateway)


async def test_null_gateway_reports_unavailable_without_fabricating():
    gateway = NullDefenderForCloudGateway()

    report = await gateway.scan(resource_group_name="rg-1")

    assert report.available is False
    assert "GENIE_DEFENDER_FOR_CLOUD_ENABLED" in report.summary


async def test_scan_skips_when_no_resource_group_is_known_yet():
    gateway = DefenderForCloudGateway(subscription_id="sub-1", credential=_FakeCredential())

    report = await gateway.scan(resource_group_name=None)

    assert report.available is False
    assert "no resource group is known yet" in report.summary


async def test_scan_reports_only_unhealthy_assessments_as_findings():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "Microsoft.Security/assessments" in str(request.url)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "name": "healthy-assessment",
                        "properties": {
                            "status": {"code": "Healthy"},
                            "metadata": {"displayName": "Healthy check", "severity": "low"},
                        },
                    },
                    {
                        "name": "unhealthy-assessment",
                        "properties": {
                            "status": {"code": "Unhealthy", "description": "Not remediated"},
                            "metadata": {
                                "displayName": "Storage account allows public access",
                                "severity": "High",
                                "description": "Restrict network access.",
                            },
                            "resourceDetails": {"Id": "/subscriptions/sub-1/.../storage1"},
                        },
                    },
                ]
            },
        )

    gateway = DefenderForCloudGateway(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    report = await gateway.scan(resource_group_name="rg-1")

    assert report.available is True
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.source == "defender-for-cloud"
    assert finding.severity == "high"
    assert finding.title == "Storage account allows public access"
    assert finding.resource == "/subscriptions/sub-1/.../storage1"


async def test_scan_reports_a_clean_bill_of_health_with_zero_findings():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": []})

    gateway = DefenderForCloudGateway(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    report = await gateway.scan(resource_group_name="rg-1")

    assert report.available is True
    assert report.findings == []
    assert "no unhealthy assessments" in report.summary


async def test_scan_reports_unavailable_instead_of_raising_on_http_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "Forbidden"}})

    gateway = DefenderForCloudGateway(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    report = await gateway.scan(resource_group_name="rg-1")

    assert report.available is False
    assert "Microsoft Defender for Cloud query failed" in report.summary
