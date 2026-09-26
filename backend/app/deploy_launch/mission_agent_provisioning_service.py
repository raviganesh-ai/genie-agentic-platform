"""Provisions this mission's own specialist + orchestrator Foundry agents.

Distinct from ``app.services.customer_agent_provisioning_service`` (which
clones dedicated, per-customer copies of Genie's OWN catalog agents -
requirements-analyst, architecture-designer, etc. - to run Genie's internal
discovery/build workflow): this service creates brand-new Azure AI Foundry
Prompt Agents for the mission being *built for the customer* - the exact
specialist agents (and the orchestrator) the Build Agent generated code
for in ``app.deploy_launch.code_materializer``. Each generated agent module
references its own already-provisioned Foundry agent name "read from
configuration, never hardcoded" (per the ``build-generation-v1`` prompt) -
this service is what performs that provisioning, before the generated code
can run.

Uses the same ``AgentApiClient.create_agent``/``delete_agent`` admin-plane
surface as ``CustomerAgentProvisioningService`` (see
``app.agents.foundry.api_client``) - no new SDK surface is invented here.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.config.settings import Settings

# Invoked immediately after each individual agent finishes provisioning (agents
# are provisioned strictly one at a time - see the loop in ``provision()``
# below) - lets callers (``DeploymentPipelineService``) surface real, live
# per-agent progress instead of only learning about every agent at once when
# the whole batch finishes.
AgentProvisionedCallback = Callable[["ProvisionedMissionAgent"], Awaitable[None]]

__all__ = [
    "MissionAgentProvisioningError",
    "MissionAgentProvisioningService",
    "NullMissionAgentProvisioningService",
    "ProvisionedMissionAgent",
    "create_mission_agent_provisioning_service",
]


class MissionAgentProvisioningError(RuntimeError):
    """Raised when a mission's specialist/orchestrator agents cannot be provisioned."""


@dataclass(frozen=True)
class ProvisionedMissionAgent:
    """One mission agent's real, resolved Foundry agent name."""

    agent_name: str
    foundry_agent_name: str
    provisioned_at: datetime


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "agent"


# Azure AI Foundry agent names must start and end with an alphanumeric
# character, may contain hyphens in the middle, and must not exceed 63
# characters total (a real, observed provisioning failure - see
# MissionAgentProvisioningError). ``agent_name`` here is extracted directly
# from the Build Agent's own generated "# agent: <name>" comment (see
# app.deploy_launch.code_materializer.materialize_build) - an LLM-authored,
# unbounded-length string - so the combined
# "{mission_slug}-{slugified agent_name}" name must be defensively
# truncated to this limit rather than trusting it fits.
_FOUNDRY_AGENT_NAME_MAX_LENGTH = 63


def _build_foundry_agent_name(mission_slug: str, agent_name: str) -> str:
    """Builds a Foundry-valid agent name, truncating as needed to fit 63 chars."""

    agent_slug = _slugify(agent_name)
    candidate = f"{mission_slug}-{agent_slug}"
    if len(candidate) <= _FOUNDRY_AGENT_NAME_MAX_LENGTH:
        return candidate

    digest = sha256(agent_name.encode("utf-8")).hexdigest()[:8]
    suffix = f"-{digest}"
    stem_length = _FOUNDRY_AGENT_NAME_MAX_LENGTH - len(suffix)
    stem = candidate[:stem_length].strip("-") or "agent"
    return f"{stem}{suffix}"


def _extract_agent_instructions(architecture_document: str, agent_name: str) -> str:
    """Deterministically extracts this agent's own description from the
    architecture document's "## Multi-Agent Workflow" section (never
    LLM-invented). Falls back to a generic, still-real instruction
    referencing the agent's own name if no matching paragraph is found."""

    section_match = re.search(
        r"##\s*Multi-Agent Workflow(.*?)(?=\n##\s|\Z)", architecture_document, re.DOTALL | re.IGNORECASE
    )
    section_text = section_match.group(1) if section_match else architecture_document

    for paragraph in re.split(r"\n\s*\n", section_text):
        if agent_name.lower() in paragraph.lower():
            return paragraph.strip()

    return (
        f"You are the '{agent_name}' agent for this mission. Perform exactly the "
        f"responsibility assigned to '{agent_name}' in the mission's approved "
        f"architecture, and hand off to the Orchestrator Agent when done."
    )


class MissionAgentProvisioningService:
    """Creates one real Foundry Prompt Agent per mission specialist + orchestrator."""

    def __init__(self, *, project_service: FoundryProjectService, model_deployment_ref: str) -> None:
        self._project_service = project_service
        self._model_deployment_ref = model_deployment_ref

    async def provision(
        self,
        *,
        mission_slug: str,
        agent_names: list[str],
        architecture_document: str,
        on_agent_provisioned: AgentProvisionedCallback | None = None,
        model_deployment_ref: str | None = None,
    ) -> list[ProvisionedMissionAgent]:
        """Provisions every named mission agent, rolling back all of them on any failure.

        When given, ``on_agent_provisioned`` is awaited right after each
        individual agent is created (in order) - callers can use this to
        reflect real, live per-agent progress rather than waiting for the
        whole batch to finish.

        ``model_deployment_ref``, when given, overrides this service's
        platform-default model for every agent in this mission - e.g. the
        model the user actually selected on Genie's own Landing page for
        this session (see ``DeploymentPipelineService``'s resolution of the
        discovery workflow run's ``agent_scope_id``), so the generated
        prototype's orchestrator and specialist agents run on the model the
        user approved, not always the platform default.
        """

        if not agent_names:
            raise MissionAgentProvisioningError(
                "Cannot provision mission agents: the expected agent fleet is empty."
            )
        if len(set(agent_names)) != len(agent_names):
            raise MissionAgentProvisioningError(
                "Cannot provision mission agents: logical agent names must be unique."
            )

        try:
            client = self._project_service.get_api_client()
        except FoundryUnavailableError as exc:
            raise MissionAgentProvisioningError(
                f"Cannot provision mission agents: {exc}"
            ) from exc

        effective_model = (model_deployment_ref or self._model_deployment_ref).strip()
        if not effective_model:
            raise MissionAgentProvisioningError(
                "Cannot provision mission agents: the model deployment reference is empty."
            )
        provisioned: list[ProvisionedMissionAgent] = []
        created_foundry_names: list[str] = []
        try:
            for agent_name in agent_names:
                requested_foundry_name = _build_foundry_agent_name(mission_slug, agent_name)
                foundry_agent_name = client.create_agent(
                    name=requested_foundry_name,
                    model=effective_model,
                    instructions=_extract_agent_instructions(architecture_document, agent_name),
                    description=agent_name,
                )
                created_foundry_names.append(requested_foundry_name)
                if not foundry_agent_name or not foundry_agent_name.strip():
                    raise MissionAgentProvisioningError(
                        f"Foundry returned an empty resource name for '{agent_name}'."
                    )
                foundry_agent_name = foundry_agent_name.strip()
                if foundry_agent_name != requested_foundry_name:
                    raise MissionAgentProvisioningError(
                        "Foundry returned unexpected resource name "
                        f"'{foundry_agent_name}' for requested agent '{requested_foundry_name}'."
                    )
                if not client.agent_exists(foundry_agent_name):
                    raise MissionAgentProvisioningError(
                        f"Foundry did not confirm the created agent '{foundry_agent_name}'."
                    )
                record = ProvisionedMissionAgent(
                    agent_name=agent_name,
                    foundry_agent_name=foundry_agent_name,
                    provisioned_at=datetime.now(UTC),
                )
                provisioned.append(record)
                if on_agent_provisioned is not None:
                    await on_agent_provisioned(record)
        except Exception as exc:
            for foundry_agent_name in reversed(created_foundry_names):
                self._safe_delete(foundry_agent_name)
            raise MissionAgentProvisioningError(
                f"Failed to provision mission agents for '{mission_slug}': {exc}"
            ) from exc

        foundry_names = [record.foundry_agent_name for record in provisioned]
        if len(provisioned) != len(agent_names) or len(set(foundry_names)) != len(foundry_names):
            for foundry_agent_name in reversed(created_foundry_names):
                self._safe_delete(foundry_agent_name)
            raise MissionAgentProvisioningError(
                "Foundry provisioning did not produce one unique resource per expected agent."
            )

        return provisioned

    def _safe_delete(self, foundry_agent_name: str) -> None:
        try:
            self._project_service.get_api_client().delete_agent(foundry_agent_name)
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup during rollback
            pass

    async def delete(self, *, foundry_agent_names: list[str]) -> None:
        """Deletes all Foundry agents owned by an abandoned prototype run."""

        try:
            client = self._project_service.get_api_client()
        except Exception as exc:
            raise MissionAgentProvisioningError(
                f"Failed to initialize mission-agent cleanup: {exc}"
            ) from exc
        failures: list[str] = []
        for foundry_agent_name in foundry_agent_names:
            try:
                client.delete_agent(foundry_agent_name)
            except Exception as exc:  # noqa: BLE001 - SDK errors have no stable base.
                if getattr(exc, "status_code", None) == 404:
                    continue
                failures.append(f"{foundry_agent_name}: {exc}")
        if failures:
            raise MissionAgentProvisioningError(
                "Failed to delete mission agents: " + "; ".join(failures)
            )


class NullMissionAgentProvisioningService:
    """Local-mode stand-in: returns fake agent names, makes no Azure calls."""

    async def provision(
        self,
        *,
        mission_slug: str,
        agent_names: list[str],
        architecture_document: str,
        on_agent_provisioned: AgentProvisionedCallback | None = None,
        model_deployment_ref: str | None = None,
    ) -> list[ProvisionedMissionAgent]:
        del model_deployment_ref
        provisioned: list[ProvisionedMissionAgent] = []
        for agent_name in agent_names:
            record = ProvisionedMissionAgent(
                agent_name=agent_name,
                foundry_agent_name=f"local-{_build_foundry_agent_name(mission_slug, agent_name)}",
                provisioned_at=datetime.now(UTC),
            )
            provisioned.append(record)
            if on_agent_provisioned is not None:
                await on_agent_provisioned(record)
        return provisioned

    async def delete(self, *, foundry_agent_names: list[str]) -> None:
        del foundry_agent_names


def create_mission_agent_provisioning_service(
    *, settings: Settings
) -> MissionAgentProvisioningService | NullMissionAgentProvisioningService:
    """Builds real Foundry provisioning or fails closed."""

    has_config = bool(settings.azure_foundry_endpoint and settings.azure_foundry_project_name)

    if not has_config:
        raise MissionAgentProvisioningError(
            "azure_foundry_endpoint and azure_foundry_project_name must be configured; "
            "local/fake mission agent provisioning is not permitted."
        )

    project_service = FoundryProjectService(
        endpoint=settings.azure_foundry_endpoint or "",
        project_name=settings.azure_foundry_project_name or "",
    )
    return MissionAgentProvisioningService(
        project_service=project_service, model_deployment_ref=settings.default_llm
    )
