"""Per-prototype Microsoft Entra application provisioning for MISE gateways."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.config.settings import Settings
from app.deploy_launch.mission_identity_service import (
    AsyncTokenCredential,
    create_user_assigned_token_credential,
)

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_GRAPH_URL = "https://graph.microsoft.com/v1.0"

__all__ = [
    "NullPrototypeAuthenticationService",
    "PrototypeAuthenticationConfiguration",
    "PrototypeAuthenticationError",
    "PrototypeAuthenticationService",
    "create_prototype_authentication_service",
]


class PrototypeAuthenticationError(RuntimeError):
    """Raised when a prototype authentication boundary cannot be provisioned."""


@dataclass
class PrototypeAuthenticationConfiguration:
    """Non-secret Entra identifiers for one independently protected prototype."""

    application_object_id: str
    service_principal_object_id: str
    client_id: str
    tenant_id: str
    delegated_scope: str
    application_role_id: str
    mission_slug: str = "legacy"
    shared: bool = False
    frontend_redirect_uri: str | None = None


class PrototypeAuthenticationService:
    """Creates one Entra API/SPA application and test app-role assignment per prototype."""

    def __init__(
        self,
        *,
        tenant_id: str,
        test_principal_client_id: str,
        credential: AsyncTokenCredential | None = None,
        http_client: httpx.AsyncClient | None = None,
        shared_configuration: PrototypeAuthenticationConfiguration | None = None,
        shared_frontend_domain: str | None = None,
        shared_slot_count: int = 0,
    ) -> None:
        if not tenant_id.strip() or not test_principal_client_id.strip():
            raise PrototypeAuthenticationError(
                "tenant_id and test_principal_client_id are required for prototype MISE."
            )
        if credential is None:
            credential = create_user_assigned_token_credential(
                client_id=test_principal_client_id
            )
        self._tenant_id = tenant_id
        self._test_principal_client_id = test_principal_client_id
        self._credential = credential
        self._http_client = http_client or httpx.AsyncClient(timeout=15.0)
        self._owns_http_client = http_client is None
        self._shared_configuration = shared_configuration
        self._shared_frontend_domain = (shared_frontend_domain or "").lower().strip(".")
        self._shared_slot_count = shared_slot_count

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        transient_statuses: tuple[int, ...] = (),
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        response: httpx.Response | None = None
        for attempt in range(max_attempts):
            try:
                access_token = await self._credential.get_token(_GRAPH_SCOPE)
                response = await self._http_client.request(
                    method,
                    f"{_GRAPH_URL}{path}",
                    headers={"Authorization": f"Bearer {access_token.token}"},
                    json=json,
                )
            except Exception as exc:
                if attempt == max_attempts - 1:
                    raise PrototypeAuthenticationError(
                        f"Microsoft Graph request for prototype authentication failed: {exc}"
                    ) from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code in expected_statuses:
                break
            if response.status_code not in transient_statuses or attempt == max_attempts - 1:
                break
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
            await asyncio.sleep(delay)
        if response is None:
            raise PrototypeAuthenticationError(
                "Microsoft Graph request produced no response."
            )
        if response.status_code not in expected_statuses:
            request_id = response.headers.get("request-id", "unavailable")
            raise PrototypeAuthenticationError(
                "Microsoft Graph rejected prototype authentication provisioning "
                f"({response.status_code}, request-id {request_id})."
            )
        if response.status_code == 204 or not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise PrototypeAuthenticationError("Microsoft Graph returned an invalid JSON object.")
        return payload

    async def provision(self, *, mission_slug: str) -> PrototypeAuthenticationConfiguration:
        """Creates a single-tenant API/SPA registration and assigns its app role to Genie."""

        if self._shared_configuration is not None:
            shared = self._shared_configuration
            return PrototypeAuthenticationConfiguration(
                application_object_id=shared.application_object_id,
                service_principal_object_id=shared.service_principal_object_id,
                client_id=shared.client_id,
                tenant_id=shared.tenant_id,
                delegated_scope=shared.delegated_scope,
                application_role_id=shared.application_role_id,
                mission_slug=mission_slug,
                shared=True,
            )

        delegated_scope_id = str(uuid4())
        application_role_id = str(uuid4())
        application_object_id: str | None = None
        try:
            application = await self._request(
                "POST",
                "/applications",
                expected_statuses=(201,),
                json={
                    "displayName": f"Genie Prototype - {mission_slug}"[:120],
                    "signInAudience": "AzureADMyOrg",
                    "tags": ["GeniePrototype", f"genie-mission:{mission_slug}"],
                    "api": {
                        "requestedAccessTokenVersion": 2,
                        "oauth2PermissionScopes": [
                            {
                                "adminConsentDescription": "Access this Genie prototype.",
                                "adminConsentDisplayName": "Access prototype",
                                "id": delegated_scope_id,
                                "isEnabled": True,
                                "type": "User",
                                "userConsentDescription": "Access this Genie prototype on your behalf.",
                                "userConsentDisplayName": "Access prototype",
                                "value": "access_as_user",
                            }
                        ],
                    },
                    "appRoles": [
                        {
                            "allowedMemberTypes": ["Application"],
                            "description": "Run authenticated prototype acceptance tests.",
                            "displayName": "Prototype.Invoke",
                            "id": application_role_id,
                            "isEnabled": True,
                            "value": "Prototype.Invoke",
                        }
                    ],
                    "optionalClaims": {
                        "accessToken": [
                            {
                                "name": "idtyp",
                                "essential": False,
                                "additionalProperties": ["include_user_token"],
                            }
                        ]
                    },
                },
            )
            application_object_id = self._required_id(application, "id", "application")
            client_id = self._required_id(application, "appId", "application")
            await self._request(
                "PATCH",
                f"/applications/{application_object_id}",
                expected_statuses=(204,),
                json={"identifierUris": [f"api://{client_id}"]},
            )
            service_principal = await self._request(
                "POST",
                "/servicePrincipals",
                expected_statuses=(201,),
                transient_statuses=(404, 429, 500, 502, 503, 504),
                max_attempts=5,
                json={"appId": client_id, "tags": ["GeniePrototype"]},
            )
            service_principal_object_id = self._required_id(
                service_principal, "id", "service principal"
            )
            principal_result = await self._request(
                "GET",
                "/servicePrincipals?%24filter="
                f"appId%20eq%20%27{self._test_principal_client_id}%27",
                expected_statuses=(200,),
                json=None,
            )
            principals = principal_result.get("value")
            if not isinstance(principals, list):
                raise PrototypeAuthenticationError(
                    "Microsoft Graph did not return service principals for the test identity."
                )
            test_principal = next(
                (
                    item
                    for item in principals
                    if isinstance(item, dict)
                    and item.get("appId") == self._test_principal_client_id
                ),
                None,
            )
            if not isinstance(test_principal, dict):
                raise PrototypeAuthenticationError(
                    "The configured Genie managed identity has no Entra service principal."
                )
            test_principal_object_id = self._required_id(
                test_principal, "id", "test principal"
            )
            await self._request(
                "POST",
                f"/servicePrincipals/{test_principal_object_id}/appRoleAssignments",
                expected_statuses=(201,),
                transient_statuses=(404, 429, 500, 502, 503, 504),
                max_attempts=5,
                json={
                    "principalId": test_principal_object_id,
                    "resourceId": service_principal_object_id,
                    "appRoleId": application_role_id,
                },
            )
            return PrototypeAuthenticationConfiguration(
                application_object_id=application_object_id,
                service_principal_object_id=service_principal_object_id,
                client_id=client_id,
                tenant_id=self._tenant_id,
                delegated_scope=f"api://{client_id}/access_as_user",
                application_role_id=application_role_id,
                mission_slug=mission_slug,
            )
        except Exception:
            if application_object_id is not None:
                try:
                    await self._request(
                        "DELETE",
                        f"/applications/{application_object_id}",
                        expected_statuses=(204, 404),
                        transient_statuses=(429, 500, 502, 503, 504),
                        max_attempts=5,
                    )
                except PrototypeAuthenticationError:
                    pass
            raise

    async def configure_frontend_redirect(
        self,
        configuration: PrototypeAuthenticationConfiguration,
        *,
        frontend_url: str,
    ) -> None:
        if not frontend_url.startswith("https://"):
            raise PrototypeAuthenticationError(
                "Prototype SPA redirect URI must use an HTTPS origin."
            )
        redirect_uri = frontend_url.rstrip("/") + "/"
        if configuration.shared:
            parsed = urlparse(redirect_uri)
            match = re.fullmatch(
                r"genie-prototype-(\d{3})-frontend\."
                + re.escape(self._shared_frontend_domain),
                (parsed.hostname or "").lower(),
            )
            slot = int(match.group(1)) if match else 0
            if parsed.scheme != "https" or parsed.path != "/" or not (
                1 <= slot <= self._shared_slot_count
            ):
                raise PrototypeAuthenticationError(
                    "Prototype frontend URL is not a pre-registered shared authentication slot."
                )
        else:
            await self._request(
                "PATCH",
                f"/applications/{configuration.application_object_id}",
                expected_statuses=(204,),
                json={"spa": {"redirectUris": [redirect_uri]}},
            )
        configuration.frontend_redirect_uri = redirect_uri

    async def delete(
        self, configuration: PrototypeAuthenticationConfiguration
    ) -> None:
        """Deletes the owned application; Entra cascades its service principal and roles."""

        if configuration.shared:
            return
        await self._request(
            "DELETE",
            f"/applications/{configuration.application_object_id}",
            expected_statuses=(204, 404),
            transient_statuses=(429, 500, 502, 503, 504),
            max_attempts=5,
        )

    async def get_test_access_token(
        self, configuration: PrototypeAuthenticationConfiguration
    ) -> str:
        """Gets a short-lived, audience-limited application token after role propagation."""

        last_error: Exception | None = None
        for attempt in range(5):
            try:
                token = await self._credential.get_token(
                    f"api://{configuration.client_id}/.default"
                )
                if token.token.strip():
                    return token.token
            except Exception as exc:  # noqa: BLE001 - Azure credential errors share no stable base.
                last_error = exc
            if attempt < 4:
                await asyncio.sleep(2**attempt)
        raise PrototypeAuthenticationError(
            f"Could not acquire the prototype acceptance-test token: {last_error}"
        )

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()
        close = getattr(self._credential, "close", None)
        if callable(close):
            await close()

    @staticmethod
    def _required_id(payload: dict[str, Any], name: str, resource: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise PrototypeAuthenticationError(
                f"Microsoft Graph {resource} response omitted '{name}'."
            )
        return value


class NullPrototypeAuthenticationService:
    """Explicit disabled-mode implementation; it never fabricates authentication."""

    async def provision(self, *, mission_slug: str) -> None:
        del mission_slug

    async def configure_frontend_redirect(self, configuration: None, *, frontend_url: str) -> None:
        del configuration, frontend_url

    async def get_test_access_token(self, configuration: None) -> None:
        del configuration

    async def delete(self, configuration: None) -> None:
        del configuration

    async def close(self) -> None:
        return None


def create_prototype_authentication_service(
    *, settings: Settings
) -> PrototypeAuthenticationService | NullPrototypeAuthenticationService:
    if not settings.prototype_api_gateway_enabled:
        return NullPrototypeAuthenticationService()
    if not settings.entra_tenant_id or not settings.prototype_test_principal_client_id:
        raise PrototypeAuthenticationError(
            "entra_tenant_id and prototype_test_principal_client_id are required "
            "when the prototype API gateway is enabled."
        )
    shared_configuration = None
    if settings.prototype_authentication_mode == "shared":
        shared_values = (
            settings.prototype_shared_application_object_id,
            settings.prototype_shared_service_principal_object_id,
            settings.prototype_shared_client_id,
            settings.prototype_shared_delegated_scope,
            settings.prototype_shared_application_role_id,
        )
        if not all(shared_values):
            raise PrototypeAuthenticationError(
                "All prototype_shared_* settings are required for shared prototype authentication."
            )
        if not settings.prototype_shared_frontend_domain:
            raise PrototypeAuthenticationError(
                "prototype_shared_frontend_domain is required for shared authentication."
            )
        shared_configuration = PrototypeAuthenticationConfiguration(
            application_object_id=settings.prototype_shared_application_object_id or "",
            service_principal_object_id=(
                settings.prototype_shared_service_principal_object_id or ""
            ),
            client_id=settings.prototype_shared_client_id or "",
            tenant_id=settings.entra_tenant_id,
            delegated_scope=settings.prototype_shared_delegated_scope or "",
            application_role_id=settings.prototype_shared_application_role_id or "",
            shared=True,
        )
    return PrototypeAuthenticationService(
        tenant_id=settings.entra_tenant_id,
        test_principal_client_id=settings.prototype_test_principal_client_id,
        shared_configuration=shared_configuration,
        shared_frontend_domain=settings.prototype_shared_frontend_domain,
        shared_slot_count=settings.prototype_shared_slot_count,
    )
