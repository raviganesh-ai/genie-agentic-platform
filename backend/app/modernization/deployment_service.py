"""Real Azure Container Apps deployment for an already-approved modernization
plan - a separate, honestly-scoped capability from ``ModernizationService``'s
own PR-authoring flow (see the module-level docstring below for the exact
scope boundary).

Why Container Apps only: ``azure-mgmt-appcontainers`` is the only
container-hosting Azure management SDK installed in this environment (see
``backend_deployment_service.py``'s own precedent); automating App
Service/AKS/Functions would require new dependencies and is out of scope for
this pass. Callers (the API layer) are expected to surface this limitation
honestly rather than implying universal one-click deployment.

Why public-repository only: the real build step (``_build_image``) points
Azure Container Registry's Quick Task build directly at the plan's own
pushed branch via a plain git/GitHub HTTPS URL
(``DockerBuildRequest.source_location``) rather than uploading a local
tarball (``backend_deployment_service.py``'s own approach for Genie's own
generated code) - this is a real, documented ACR Tasks capability (the same
mechanism ``az acr build <git-url>`` and ``az acr task create --context
<git-url>`` use), chosen here because it needs zero local materialization of
a third-party repository. It only works without extra credential plumbing
for public repositories (``SourceRegistryCredentials`` would be required for
private ones) - acceptable because the plan's own repository must already be
public for Genie to have opened a pull request against it via GitHub MCP in
the first place for most of this codebase's demo/evaluation repositories,
but this is a real limitation for private production repositories, called
out here rather than silently assumed to work universally.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.settings import Settings
from app.deploy_launch.resource_naming import modernization_deployment_container_app_name
from app.governance.approval_service import ApprovalService
from app.governance.governance_service import GovernanceService
from app.modernization.deployment_repository import ModernizationDeploymentRepository
from app.modernization.models import (
    ModernizationDeployment,
    ModernizationDeploymentEnvironmentVariable,
    ModernizationDeploymentStrategy,
    ModernizationPlan,
)
from app.modernization.parsing import ModernizationAgentResponseError, parse_agent_response
from app.modernization.repository import ModernizationPlanRepository
from app.orchestration.agent_orchestrator import AgentOrchestrator

DeploymentProgressCallback = Callable[[str], Awaitable[None]]

_ACR_RUN_FAILURE_STATUSES = frozenset({"failed", "canceled", "cancelled", "error", "timeout"})
_ACR_BUILD_MAX_WAIT_SECONDS = 600
_HEALTH_CHECK_MAX_WAIT_SECONDS = 300

__all__ = [
    "ModernizationDeploymentError",
    "ModernizationDeploymentService",
    "create_modernization_deployment_service",
]


class ModernizationDeploymentError(RuntimeError):
    """Raised when proposing or executing a real modernization deployment fails."""


class _GeneratedStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_app_name: str = Field(min_length=1)
    container_port: int = Field(gt=0, le=65535)
    health_check_path: str = Field(min_length=1)
    environment_variables: list[ModernizationDeploymentEnvironmentVariable] = Field(
        default_factory=list
    )
    cpu: float = Field(gt=0)
    memory: str = Field(min_length=1)
    min_replicas: int = Field(ge=0)
    max_replicas: int = Field(gt=0)
    steps: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _replica_bounds_are_sane(self) -> _GeneratedStrategy:
        if self.max_replicas < self.min_replicas:
            raise ValueError("max_replicas must be greater than or equal to min_replicas.")
        return self


class ModernizationDeploymentService:
    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        acr_name: str,
        acr_agent_pool_name: str | None = None,
        container_apps_environment_id: str,
        location: str,
        plan_repository: ModernizationPlanRepository,
        deployment_repository: ModernizationDeploymentRepository,
        orchestrator: AgentOrchestrator,
        approval_service: ApprovalService,
        governance_service: GovernanceService,
    ) -> None:
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._acr_name = acr_name
        self._acr_agent_pool_name = acr_agent_pool_name
        self._container_apps_environment_id = container_apps_environment_id
        self._location = location
        self._plan_repository = plan_repository
        self._deployment_repository = deployment_repository
        self._orchestrator = orchestrator
        self._approval_service = approval_service
        self._governance_service = governance_service

    async def propose_strategy(
        self,
        *,
        session_id: str,
        plan_id: str,
        requesting_user_id: str,
        trace_id: str,
    ) -> ModernizationDeployment:
        """Asks the Build Agent for a concrete, evidence-grounded deployment
        strategy for an already-approved plan whose pull request has been
        opened - see this module's own docstring for why it must already
        have a pull request (the branch the real build will clone from)."""

        plan = await self._owned_plan(session_id=session_id, plan_id=plan_id)
        if plan.status != "pull_request_opened":
            raise ModernizationDeploymentError(
                "A real deployment can only be proposed after this plan's pull request "
                "has been opened."
            )
        if not any(change.path == "Dockerfile" for change in plan.changes):
            raise ModernizationDeploymentError(
                "This plan does not include a Dockerfile at the repository root, so Genie "
                "cannot build and deploy it automatically."
            )

        variables = {
            "repository_full_name": plan.repository_full_name,
            "target": plan.target or "unspecified",
            "capability_name": plan.capability_name or "unspecified",
            "rewrite_strategy": plan.rewrite_strategy,
            "deployment_plan_json": _dump_json(plan.deployment_plan),
            "changed_files_json": _dump_json(
                [
                    {"path": change.path, "reason": change.reason, "content": change.content}
                    for change in plan.changes
                ]
            ),
            "retry_instruction": "",
        }
        generated = await self._generate_strategy_contents(
            variables=variables, session_id=session_id, trace_id=trace_id
        )
        strategy = ModernizationDeploymentStrategy(
            resource_app_name=generated.resource_app_name,
            container_port=generated.container_port,
            health_check_path=generated.health_check_path,
            environment_variables=generated.environment_variables,
            cpu=generated.cpu,
            memory=generated.memory,
            min_replicas=generated.min_replicas,
            max_replicas=generated.max_replicas,
            steps=generated.steps,
            rationale=generated.rationale,
        )
        now = datetime.now(UTC)
        deployment = ModernizationDeployment(
            id=str(uuid4()),
            session_id=session_id,
            plan_id=plan.id,
            strategy=strategy,
            status="strategy_proposed",
            created_at=now,
            updated_at=now,
        )
        await self._deployment_repository.put(deployment)
        return deployment

    async def request_deployment(
        self,
        *,
        session_id: str,
        deployment_id: str,
        requesting_user_id: str,
        trace_id: str,
    ) -> ModernizationDeployment:
        """Opens a governance approval request for actually provisioning
        real, billable Azure resources - a separate checkpoint from the
        plan's own PR approval, since the two are independent decisions
        (see ``config/policies/approval_policy.yaml``'s
        "modernization-deployment-approval" checkpoint)."""

        del requesting_user_id
        deployment = await self._owned_deployment(
            session_id=session_id, deployment_id=deployment_id
        )
        if deployment.status not in {"strategy_proposed", "failed"}:
            raise ModernizationDeploymentError(
                f"Deployment '{deployment_id}' cannot be (re-)requested from status "
                f"'{deployment.status}'."
            )
        approval = await self._approval_service.request_approval(
            checkpoint_id="modernization-deployment-approval",
            session_id=session_id,
            trace_id=trace_id,
            requested_by_agent_id="build-agent",
            subject_type="modernization_deployment",
            subject_id=deployment.id,
        )
        deployment = deployment.model_copy(
            update={
                "status": "pending_approval",
                "approval_request_id": approval.id,
                "error": None,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._deployment_repository.put(deployment)
        return deployment

    async def execute_deployment(
        self,
        *,
        session_id: str,
        deployment_id: str,
        requesting_user_id: str,
        trace_id: str,
        on_progress: DeploymentProgressCallback | None = None,
    ) -> ModernizationDeployment:
        """Builds the plan's own pushed branch in ACR and deploys it to a
        real Azure Container App - requires an approved
        "modernization-deployment-approval" decision (see
        ``request_deployment``)."""

        del requesting_user_id

        async def _report(message: str) -> None:
            if on_progress is not None:
                await on_progress(message)

        deployment = await self._owned_deployment(
            session_id=session_id, deployment_id=deployment_id
        )
        if not deployment.approval_request_id:
            raise ModernizationDeploymentError("Deployment has no approval request.")
        approval = await self._approval_service.get_request(deployment.approval_request_id)
        if (
            approval is None
            or approval.status != "approved"
            or approval.subject_id != deployment.id
            or approval.subject_type != "modernization_deployment"
        ):
            raise ModernizationDeploymentError(
                "Deployment requires an approved governance decision."
            )
        plan = await self._owned_plan(session_id=session_id, plan_id=deployment.plan_id)

        deploying = deployment.model_copy(
            update={"status": "deploying", "error": None, "updated_at": datetime.now(UTC)}
        )
        await self._deployment_repository.put(deploying)
        try:
            image_tag = await self._build_image(
                plan=plan, deployment=deploying, on_progress=_report
            )
            fqdn = await self._create_or_update_container_app(
                plan=plan, deployment=deploying, image_tag=image_tag, on_progress=_report
            )
            health_check_url = (
                f"https://{fqdn}{deploying.strategy.health_check_path}"
            )
            await _report(f"Verifying the deployed app responds at {health_check_url}...")
            await self._wait_for_healthy(health_check_url)
        except ModernizationDeploymentError as exc:
            failed = deploying.model_copy(
                update={"status": "failed", "error": str(exc), "updated_at": datetime.now(UTC)}
            )
            await self._deployment_repository.put(failed)
            raise
        healthy = deploying.model_copy(
            update={
                "status": "healthy",
                "image_tag": image_tag,
                "container_app_fqdn": fqdn,
                "health_check_url": health_check_url,
                "error": None,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._deployment_repository.put(healthy)
        await self._governance_service.record_tool_request(
            session_id=session_id,
            trace_id=trace_id,
            agent_id="modernization-deployment-service",
            tool_name="azure.container_apps.deploy",
            detail={
                "plan_id": plan.id,
                "deployment_id": healthy.id,
                "repository_full_name": plan.repository_full_name,
                "branch_name": plan.branch_name,
                "image_tag": image_tag,
                "container_app_fqdn": fqdn,
                "health_check_url": health_check_url,
            },
        )
        return healthy

    async def get_deployment(
        self,
        *,
        session_id: str,
        plan_id: str,
        requesting_user_id: str,
    ) -> ModernizationDeployment | None:
        del requesting_user_id
        await self._owned_plan(session_id=session_id, plan_id=plan_id)
        deployments = await self._deployment_repository.list_for_plan(plan_id=plan_id)
        return deployments[0] if deployments else None

    async def _generate_strategy_contents(
        self,
        *,
        variables: dict[str, str],
        session_id: str,
        trace_id: str,
    ) -> _GeneratedStrategy:
        for attempt in range(2):
            result = await self._orchestrator.execute_agent(
                agent_id="build-agent",
                prompt_id="modernization-deployment-strategy-v1",
                variables=variables,
                session_id=session_id,
                trace_id=trace_id,
            )
            try:
                return parse_agent_response(result.output_text, _GeneratedStrategy)
            except ModernizationAgentResponseError as exc:
                if attempt == 1:
                    raise ModernizationDeploymentError(
                        f"The Foundry Build Agent returned an invalid deployment strategy "
                        f"contract: {exc}"
                    ) from exc
                variables["retry_instruction"] = (
                    "A prior response was malformed, truncated, or schema-invalid. "
                    f"Correct these exact validation issues: {exc} Regenerate the complete "
                    "response as fresh JSON with no Markdown fences."
                )
        raise ModernizationDeploymentError(
            "Deployment strategy generation retry loop exited unexpectedly."
        )

    def _acr_client(self) -> Any:
        """Client pinned to the ``2019-06-01-preview`` API version - the
        version that still exposes the ACR Quick Task source-upload/build-run
        APIs (see ``backend_deployment_service.py``'s own documented
        assumption, which this mirrors). Factored out as its own method (like
        that module's identical ``_acr_client``) so tests can monkeypatch it
        instead of needing a real Azure credential."""

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import ContainerRegistryManagementClient
        except ImportError as exc:
            raise ModernizationDeploymentError(
                "azure-mgmt-containerregistry / azure-identity are not installed."
            ) from exc
        try:
            return ContainerRegistryManagementClient(
                DefaultAzureCredential(),
                self._subscription_id,
                api_version="2019-06-01-preview",
            )
        except Exception as exc:
            raise ModernizationDeploymentError(
                f"Failed to construct ACR management client: {exc}"
            ) from exc

    def _acr_credentials_client(self) -> Any:
        """Client on the default (stable) API version - exposes
        ``registries.list_credentials`` (moved out of the preview profile
        used by ``_acr_client``)."""

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import ContainerRegistryManagementClient
        except ImportError as exc:
            raise ModernizationDeploymentError(
                "azure-mgmt-containerregistry / azure-identity are not installed."
            ) from exc
        try:
            return ContainerRegistryManagementClient(DefaultAzureCredential(), self._subscription_id)
        except Exception as exc:
            raise ModernizationDeploymentError(
                f"Failed to construct ACR credentials client: {exc}"
            ) from exc

    def _container_apps_client(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.appcontainers import ContainerAppsAPIClient
        except ImportError as exc:
            raise ModernizationDeploymentError(
                "azure-mgmt-appcontainers / azure-identity are not installed."
            ) from exc
        try:
            return ContainerAppsAPIClient(DefaultAzureCredential(), self._subscription_id)
        except Exception as exc:
            raise ModernizationDeploymentError(
                f"Failed to construct Container Apps client: {exc}"
            ) from exc

    async def _build_image(
        self,
        *,
        plan: ModernizationPlan,
        deployment: ModernizationDeployment,
        on_progress: Callable[[str], Awaitable[None]],
    ) -> str:
        """Builds the plan's own pushed branch directly from GitHub via an
        ACR Quick Task - see this module's own docstring for why
        ``source_location`` is a plain git URL rather than an uploaded
        tarball."""

        try:
            from azure.mgmt.containerregistry.v2019_06_01_preview.models import (
                DockerBuildRequest,
                PlatformProperties,
            )
        except ImportError as exc:
            raise ModernizationDeploymentError(
                "azure-mgmt-containerregistry is not installed."
            ) from exc

        acr_client = self._acr_client()

        image_tag = (
            f"{self._acr_name}.azurecr.io/"
            f"{deployment.strategy.resource_app_name}:{plan.id[:8]}"
        )
        source_location = f"https://github.com/{plan.repository_full_name}.git#{plan.branch_name}"
        try:
            await on_progress(
                f"Building container image in Azure Container Registry from "
                f"{plan.repository_full_name}@{plan.branch_name} (this can take a minute or two)..."
            )
            build_request = DockerBuildRequest(
                agent_pool_name=self._acr_agent_pool_name,
                source_location=source_location,
                platform=PlatformProperties(os="Linux"),
                docker_file_path="Dockerfile",
                image_names=[image_tag],
                is_push_enabled=True,
                no_cache=False,
            )
            poller = await asyncio.to_thread(
                acr_client.registries.begin_schedule_run,
                self._resource_group,
                self._acr_name,
                build_request,
            )
            run_result = await asyncio.to_thread(poller.result)
            run_id = getattr(run_result, "run_id", None)
            if not run_id:
                raise ModernizationDeploymentError(
                    f"ACR build for image '{image_tag}' returned no run ID."
                )
            await self._poll_acr_run(acr_client=acr_client, run_id=run_id, image_tag=image_tag)
        except ModernizationDeploymentError:
            raise
        except Exception as exc:
            raise ModernizationDeploymentError(
                f"Failed to build deployment image in ACR: {exc}"
            ) from exc
        return image_tag

    async def _poll_acr_run(self, *, acr_client: Any, run_id: str, image_tag: str) -> None:
        start_time = time.monotonic()
        while True:
            if time.monotonic() - start_time > _ACR_BUILD_MAX_WAIT_SECONDS:
                raise ModernizationDeploymentError(
                    f"ACR build for image '{image_tag}' did not complete within "
                    f"{_ACR_BUILD_MAX_WAIT_SECONDS} seconds (run ID: {run_id})."
                )
            run_detail = await asyncio.to_thread(
                acr_client.runs.get, self._resource_group, self._acr_name, run_id
            )
            run_status = getattr(run_detail, "status", None)
            if not run_status:
                raise ModernizationDeploymentError(
                    f"ACR build for image '{image_tag}' returned no status (run ID: {run_id})."
                )
            status_lower = run_status.lower()
            if status_lower == "succeeded":
                return
            if status_lower in _ACR_RUN_FAILURE_STATUSES:
                raise ModernizationDeploymentError(
                    f"ACR build for image '{image_tag}' ended with status '{run_status}' "
                    f"(run ID: {run_id})."
                )
            await asyncio.sleep(5)

    async def _create_or_update_container_app(
        self,
        *,
        plan: ModernizationPlan,
        deployment: ModernizationDeployment,
        image_tag: str,
        on_progress: Callable[[str], Awaitable[None]],
    ) -> str:
        try:
            from azure.mgmt.appcontainers.models import (
                Configuration,
                Container,
                ContainerApp,
                EnvironmentVar,
                Ingress,
                RegistryCredentials,
                Secret,
                Template,
            )
        except ImportError as exc:
            raise ModernizationDeploymentError(
                "azure-mgmt-appcontainers is not installed."
            ) from exc

        strategy = deployment.strategy
        # The agent-proposed resource_app_name is shown to the user and used
        # in tags, but the actual Azure resource name is derived
        # deterministically from the plan id (modernization_deployment_
        # container_app_name) so two plans can never collide on a shared
        # Container App name even if the agent proposes the same name twice.
        app_name = modernization_deployment_container_app_name(plan.id)
        try:
            await on_progress("Reading Azure Container Registry credentials...")
            credentials_client = self._acr_credentials_client()
            credentials = await asyncio.to_thread(
                credentials_client.registries.list_credentials,
                self._resource_group,
                self._acr_name,
            )
            registry_username = credentials.username
            registry_password = credentials.passwords[0].value
        except Exception as exc:
            raise ModernizationDeploymentError(
                f"Failed to read ACR admin credentials: {exc}"
            ) from exc

        try:
            container_apps_client = self._container_apps_client()
            env_vars = [
                EnvironmentVar(name=variable.name, value=variable.value)
                for variable in strategy.environment_variables
                if not variable.secret
            ]
            containers = [
                Container(
                    name=app_name,
                    image=image_tag,
                    env=env_vars,
                    resources={"cpu": strategy.cpu, "memory": strategy.memory},
                )
            ]
            envelope = ContainerApp(
                location=self._location,
                tags={
                    "genie-managed-by": "genie-modernization",
                    "genie-modernization-plan-id": plan.id,
                },
                managed_environment_id=self._container_apps_environment_id,
                configuration=Configuration(
                    ingress=Ingress(external=True, target_port=strategy.container_port),
                    registries=[
                        RegistryCredentials(
                            server=f"{self._acr_name}.azurecr.io",
                            username=registry_username,
                            password_secret_ref="acr-password",
                        )
                    ],
                    secrets=[Secret(name="acr-password", value=registry_password)],
                ),
                template=Template(
                    containers=containers,
                    scale={
                        "minReplicas": strategy.min_replicas,
                        "maxReplicas": strategy.max_replicas,
                    },
                ),
            )
            await on_progress("Creating/updating the Azure Container App revision...")
            poller = await asyncio.to_thread(
                container_apps_client.container_apps.begin_create_or_update,
                self._resource_group,
                app_name,
                envelope,
            )
            result = await asyncio.to_thread(poller.result)
        except Exception as exc:
            raise ModernizationDeploymentError(f"Failed to deploy Container App: {exc}") from exc

        fqdn = getattr(getattr(result.configuration, "ingress", None), "fqdn", None)
        if not fqdn:
            raise ModernizationDeploymentError(
                "Container App deployment did not return a public FQDN."
            )
        return fqdn

    async def _wait_for_healthy(self, health_check_url: str) -> None:
        import httpx

        deadline = time.monotonic() + _HEALTH_CHECK_MAX_WAIT_SECONDS
        last_error = "no response"
        async with httpx.AsyncClient(timeout=15) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(health_check_url)
                    if response.status_code < 400:
                        return
                    last_error = f"HTTP {response.status_code}"
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                await asyncio.sleep(5)
        raise ModernizationDeploymentError(
            f"Deployed app at {health_check_url} did not become healthy within "
            f"{_HEALTH_CHECK_MAX_WAIT_SECONDS} seconds ({last_error})."
        )

    async def _owned_plan(self, *, session_id: str, plan_id: str) -> ModernizationPlan:
        plan = await self._plan_repository.get(plan_id=plan_id)
        if plan is None or plan.session_id != session_id:
            raise ModernizationDeploymentError("Modernization plan was not found for this session.")
        return plan

    async def _owned_deployment(
        self, *, session_id: str, deployment_id: str
    ) -> ModernizationDeployment:
        deployment = await self._deployment_repository.get(deployment_id=deployment_id)
        if deployment is None or deployment.session_id != session_id:
            raise ModernizationDeploymentError(
                "Modernization deployment was not found for this session."
            )
        return deployment


def _dump_json(value: Any) -> str:
    return json.dumps(value)


def create_modernization_deployment_service(
    *,
    settings: Settings,
    plan_repository: ModernizationPlanRepository,
    deployment_repository: ModernizationDeploymentRepository,
    orchestrator: AgentOrchestrator,
    approval_service: ApprovalService,
    governance_service: GovernanceService,
) -> ModernizationDeploymentService:
    """Builds the real service or fails closed when Azure is incomplete -
    these are the same five settings ``create_backend_deployment_service``
    already requires, so this never introduces a new startup failure mode
    in an environment where that service already constructs successfully."""

    required = (
        settings.azure_subscription_id,
        settings.deployment_resource_group,
        settings.deployment_acr_name,
        settings.deployment_acr_agent_pool_name,
        settings.deployment_container_apps_environment_id,
        settings.deployment_location,
    )
    if not all(required):
        raise ModernizationDeploymentError(
            "azure_subscription_id, deployment_resource_group, deployment_acr_name, "
            "deployment_acr_agent_pool_name, "
            "deployment_container_apps_environment_id, and deployment_location must all be "
            "configured; real modernization deployment is not permitted without them."
        )
    return ModernizationDeploymentService(
        subscription_id=settings.azure_subscription_id,  # type: ignore[arg-type]
        resource_group=settings.deployment_resource_group,  # type: ignore[arg-type]
        acr_name=settings.deployment_acr_name,  # type: ignore[arg-type]
        acr_agent_pool_name=settings.deployment_acr_agent_pool_name,
        container_apps_environment_id=settings.deployment_container_apps_environment_id,  # type: ignore[arg-type]
        location=settings.deployment_location,  # type: ignore[arg-type]
        plan_repository=plan_repository,
        deployment_repository=deployment_repository,
        orchestrator=orchestrator,
        approval_service=approval_service,
        governance_service=governance_service,
    )
