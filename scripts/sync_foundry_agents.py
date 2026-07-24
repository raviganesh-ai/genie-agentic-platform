"""CLI: run a Foundry agent inventory synchronization pass and print the report.

Usage (from the ``backend`` virtual environment)::

    python scripts/sync_foundry_agents.py

Loads the externally configured agent/prompt registries and Azure AI
Foundry connection settings from the environment (``GENIE_*`` variables,
see ``app.config.settings.Settings``), runs the same per-agent
synchronization procedure ``app.main`` runs at startup
(``app.services.foundry_agent_synchronization_service.FoundryAgentSynchronizationService``),
and prints the resulting ``SynchronizationReport`` as JSON.

Exits non-zero if the report has blocking failures (an agent could not be
found in Foundry, or failed metadata validation) or if any critical drift
was detected, so this script can be wired into a CI/CD deployment gate
without inventing a new pipeline.
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.agents.foundry.project_service import FoundryProjectService
from app.agents.registry import AgentRegistry
from app.config.settings import get_settings
from app.prompts.registry import PromptRegistry
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_synchronization_service import FoundryAgentSynchronizationService


async def _run() -> int:
    settings = get_settings()

    if not settings.azure_foundry_endpoint or not settings.azure_foundry_project_name:
        print(
            "azure_foundry_endpoint and azure_foundry_project_name must both be "
            "configured (GENIE_AZURE_FOUNDRY_ENDPOINT / "
            "GENIE_AZURE_FOUNDRY_PROJECT_NAME) to synchronize against Azure AI Foundry.",
            file=sys.stderr,
        )
        return 1

    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
    prompt_registry = PromptRegistry.load(settings.prompts_path)
    project_service = FoundryProjectService(
        endpoint=settings.azure_foundry_endpoint,
        project_name=settings.azure_foundry_project_name,
    )
    inventory_service = FoundryAgentInventoryService()
    synchronization_service = FoundryAgentSynchronizationService(
        project_service=project_service,
        prompt_registry=prompt_registry,
        inventory_service=inventory_service,
    )

    report = await synchronization_service.synchronize(agent_registry)
    print(report.model_dump_json(indent=2))

    critical_drift = any(
        drift_report.has_critical_issues
        for drift_report in synchronization_service.drift_reports().values()
    )
    if report.has_blocking_failures or critical_drift:
        return 1
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
