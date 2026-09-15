"""Microsoft Security Copilot scan gateway for Deploy & Launch.

Security Copilot does not (as of this writing) expose a directly callable,
per-mission "run this promptbook against this resource group" management
plane API. The documented, actually automatable integration surface is a
Logic Apps HTTP-trigger "Automated Action" that is wired, inside the
Security Copilot portal, to run a promptbook and post its findings back -
see Microsoft's Security Copilot automation/plugin documentation. This
gateway therefore posts the mission's own context to a pre-configured
Logic App HTTP trigger and parses whatever findings it returns; it never
fabricates a scan result of its own.

This step is explicitly informational-only and must never block Launch
(see ``app.deploy_launch.models.SecurityCopilotScanReport``): when no
Logic App endpoint is configured, or the request itself fails,
``available=False`` with an honest explanation is reported instead of
raising - mirroring the Null-service pattern used elsewhere in this
package (``NullBackendDeploymentService`` etc.) but never failing closed,
since an unconfigured/unreachable scan must not stop a mission launch.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config.settings import Settings
from app.deploy_launch.models import SecurityCopilotFinding, SecurityCopilotScanReport

__all__ = [
    "NullSecurityCopilotGateway",
    "SecurityCopilotGateway",
    "create_security_copilot_gateway",
]

_logger = logging.getLogger(__name__)


class SecurityCopilotGateway:
    """Invokes a configured Security Copilot "Automated Action" Logic App trigger."""

    def __init__(
        self,
        *,
        logic_app_trigger_url: str,
        timeout_seconds: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._logic_app_trigger_url = logic_app_trigger_url
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def scan(
        self,
        *,
        mission_slug: str,
        mission_title: str,
        resource_group_name: str | None,
    ) -> SecurityCopilotScanReport:
        payload = {
            "missionSlug": mission_slug,
            "missionTitle": mission_title,
            "resourceGroupName": resource_group_name,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(self._logic_app_trigger_url, json=payload)
                response.raise_for_status()
                body: Any = response.json()
        except httpx.HTTPError as exc:
            _logger.warning("Security Copilot scan request failed: %s", exc)
            return SecurityCopilotScanReport(
                available=False,
                summary=f"Security Copilot scan request failed: {exc}",
            )
        except ValueError as exc:  # response.json() on a non-JSON body
            _logger.warning("Security Copilot scan returned an unparsable response: %s", exc)
            return SecurityCopilotScanReport(
                available=False,
                summary=f"Security Copilot scan returned an unparsable response: {exc}",
            )

        raw_findings = body.get("findings", []) if isinstance(body, dict) else []
        findings = [
            SecurityCopilotFinding(
                severity=finding.get("severity") or "informational",
                title=finding.get("title") or "Untitled finding",
                description=finding.get("description") or "",
                resource=finding.get("resource"),
            )
            for finding in raw_findings
            if isinstance(finding, dict)
        ]
        summary = (
            body.get("summary")
            if isinstance(body, dict) and body.get("summary")
            else f"Security Copilot scan completed with {len(findings)} finding(s)."
        )
        reference_url = body.get("referenceUrl") if isinstance(body, dict) else None
        return SecurityCopilotScanReport(
            available=True,
            summary=summary,
            findings=findings,
            reference_url=reference_url,
        )


class NullSecurityCopilotGateway:
    """Local/test double: honestly reports the scan as unavailable, never fabricated."""

    async def scan(
        self,
        *,
        mission_slug: str,
        mission_title: str,
        resource_group_name: str | None,
    ) -> SecurityCopilotScanReport:
        del mission_slug, mission_title, resource_group_name
        return SecurityCopilotScanReport(
            available=False,
            summary=(
                "Security Copilot scan is not configured for this deployment; set "
                "GENIE_SECURITY_COPILOT_LOGIC_APP_URL to a Security Copilot Automated "
                "Action Logic App HTTP-trigger URL to enable it."
            ),
        )


def create_security_copilot_gateway(
    *, settings: Settings
) -> SecurityCopilotGateway | NullSecurityCopilotGateway:
    """Builds the real gateway, or an honest "unavailable" double.

    Unlike ``create_backend_deployment_service`` and similar Deploy & Launch
    factories, this never raises when unconfigured - the Security Copilot
    scan step is informational-only and must never block Launch.
    """

    if not settings.security_copilot_logic_app_url:
        return NullSecurityCopilotGateway()
    return SecurityCopilotGateway(
        logic_app_trigger_url=settings.security_copilot_logic_app_url,
        timeout_seconds=settings.security_copilot_timeout_seconds,
    )
