"""Microsoft Defender for Cloud assessment gateway for Deploy & Launch.

Defender for Cloud exposes a real, directly callable management-plane REST
API - ``Microsoft.Security/assessments`` - that returns each resource's
current security posture without any Logic App/promptbook indirection (see
https://learn.microsoft.com/rest/api/defenderforcloud/assessments/list).
This gateway queries that API scoped to the mission's own resource group
and reports every currently "Unhealthy" assessment as a finding; it is the
deterministic, primary source composed into
``app.deploy_launch.models.SecurityCopilotScanReport`` (the optional
Security Copilot Automated Action overlay is the secondary, narrative
source - see ``app.deploy_launch.security_copilot_gateway``).

This step is explicitly informational-only and must never block Launch:
when the feature is not enabled/configured, or the query itself fails,
``available=False`` with an honest explanation is reported instead of
raising - mirroring the Null-service pattern used elsewhere in this
package.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config.settings import Settings
from app.deploy_launch.models import SecurityCopilotFinding, SecurityCopilotScanReport

__all__ = [
    "DefenderForCloudGateway",
    "NullDefenderForCloudGateway",
    "create_defender_for_cloud_gateway",
]

_logger = logging.getLogger(__name__)
_ASSESSMENTS_API_VERSION = "2021-06-01"
_MANAGEMENT_SCOPE = "https://management.azure.com/.default"
_UNHEALTHY_STATUS_CODE = "Unhealthy"
_VALID_SEVERITIES = {"informational", "low", "medium", "high", "critical"}


class DefenderForCloudGateway:
    """Queries real, per-mission Microsoft Defender for Cloud security assessments."""

    def __init__(
        self,
        *,
        subscription_id: str,
        timeout_seconds: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: Any | None = None,
    ) -> None:
        self._subscription_id = subscription_id
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._credential = credential

    def _get_credential(self) -> Any:
        if self._credential is not None:
            return self._credential
        from azure.identity import DefaultAzureCredential

        self._credential = DefaultAzureCredential()
        return self._credential

    async def scan(self, *, resource_group_name: str | None) -> SecurityCopilotScanReport:
        if not resource_group_name:
            return SecurityCopilotScanReport(
                available=False,
                summary="Defender for Cloud scan skipped: no resource group is known yet.",
            )

        try:
            credential = self._get_credential()
            token = await asyncio.to_thread(lambda: credential.get_token(_MANAGEMENT_SCOPE).token)
        except Exception as exc:  # noqa: BLE001 - informational-only boundary; see module docstring.
            _logger.warning("Defender for Cloud authentication failed: %s", exc)
            return SecurityCopilotScanReport(
                available=False, summary=f"Azure authentication failed: {exc}"
            )

        url = (
            f"https://management.azure.com/subscriptions/{self._subscription_id}"
            f"/resourceGroups/{resource_group_name}/providers/Microsoft.Security/assessments"
            f"?api-version={_ASSESSMENTS_API_VERSION}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                payload: Any = response.json()
        except httpx.HTTPError as exc:
            _logger.warning("Defender for Cloud assessments query failed: %s", exc)
            return SecurityCopilotScanReport(
                available=False,
                summary=f"Microsoft Defender for Cloud query failed: {exc}",
            )

        findings = _parse_unhealthy_assessments(payload)
        summary = (
            f"Microsoft Defender for Cloud reports {len(findings)} unhealthy "
            f"assessment(s) for resource group '{resource_group_name}'."
            if findings
            else (
                "Microsoft Defender for Cloud reports no unhealthy assessments for "
                f"resource group '{resource_group_name}'."
            )
        )
        return SecurityCopilotScanReport(available=True, summary=summary, findings=findings)


def _parse_unhealthy_assessments(payload: Any) -> list[SecurityCopilotFinding]:
    raw_assessments = payload.get("value", []) if isinstance(payload, dict) else []
    findings: list[SecurityCopilotFinding] = []
    for assessment in raw_assessments:
        if not isinstance(assessment, dict):
            continue
        properties = assessment.get("properties") or {}
        status = (properties.get("status") or {}).get("code")
        if status != _UNHEALTHY_STATUS_CODE:
            continue
        metadata = properties.get("metadata") or {}
        severity = str(metadata.get("severity") or "medium").lower()
        findings.append(
            SecurityCopilotFinding(
                source="defender-for-cloud",
                severity=severity if severity in _VALID_SEVERITIES else "medium",
                title=metadata.get("displayName") or assessment.get("name") or "Untitled assessment",
                description=(
                    metadata.get("description")
                    or (properties.get("status") or {}).get("description")
                    or ""
                ),
                resource=(properties.get("resourceDetails") or {}).get("Id"),
            )
        )
    return findings


class NullDefenderForCloudGateway:
    """Local/test double: honestly reports the scan as unavailable, never fabricated."""

    async def scan(self, *, resource_group_name: str | None) -> SecurityCopilotScanReport:
        del resource_group_name
        return SecurityCopilotScanReport(
            available=False,
            summary=(
                "Microsoft Defender for Cloud scanning is not configured for this "
                "deployment; set GENIE_DEFENDER_FOR_CLOUD_ENABLED=true and "
                "GENIE_AZURE_SUBSCRIPTION_ID to enable it."
            ),
        )


def create_defender_for_cloud_gateway(
    *, settings: Settings
) -> DefenderForCloudGateway | NullDefenderForCloudGateway:
    """Builds the real gateway, or an honest "unavailable" double.

    Never raises when unconfigured - this scan is informational-only and
    must never block Launch.
    """

    if not settings.defender_for_cloud_enabled or not settings.azure_subscription_id:
        return NullDefenderForCloudGateway()
    return DefenderForCloudGateway(
        subscription_id=settings.azure_subscription_id,
        timeout_seconds=settings.defender_for_cloud_timeout_seconds,
    )
