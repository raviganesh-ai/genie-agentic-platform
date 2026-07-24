"""CLI: export the Foundry agent inventory (seeded from configuration) as JSON.

Usage (from the ``backend`` virtual environment)::

    python scripts/export_foundry_inventory.py [output-file.json]

Loads the externally configured agent registry and seeds a
``FoundryAgentInventoryService`` from every enabled agent (configuration
only - no network access, no live synchronization/drift status), then
writes the resulting list of ``FoundryAgentInventoryRecord`` entries as
JSON to the given file path, or to stdout if no path is given.

For a live, network-verified inventory snapshot (including current
synchronization/drift status), run ``scripts/sync_foundry_agents.py``
first against a long-lived process that keeps the resulting
``FoundryAgentInventoryService`` in memory (e.g. the running backend's
``/api/admin/foundry/inventory`` endpoint) - this script only reflects
configuration, per the Configuration Rules in
``.github/copilot-instructions.md`` (config is the source of truth for
*what* agents should exist).
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.agents.registry import AgentRegistry
from app.config.settings import get_settings
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService


async def _run(output_path: str | None) -> None:
    settings = get_settings()
    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)

    inventory_service = FoundryAgentInventoryService()
    for agent in agent_registry.list():
        if agent.enabled:
            await inventory_service.seed_from_agent(agent)

    records = await inventory_service.list()
    payload = json.dumps([record.model_dump(mode="json") for record in records], indent=2)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        print(payload)


def main() -> None:
    output_path = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(_run(output_path))


if __name__ == "__main__":
    main()
