"""One-off operator CLI: provision real Azure AI Foundry agent resources.

Usage (from the repo root, backend virtual environment activated)::

    python scripts/provision_foundry_agents.py --endpoint <account-endpoint> \\
        --project <project-name>

This is the IaC/CLI-equivalent "provisioning" step referenced by
``config/agents/registry.yaml``'s own comments and by
``backend/app/agents/foundry/api_client.py``'s architecture assumption:
every Genie agent is an independently deployed Azure AI Foundry agent
resource, created here (by an operator, out-of-band) - never ad hoc by the
running backend.

For every *enabled* agent in the local ``AgentRegistry`` whose
``foundry_agent_id`` still looks like an unprovisioned placeholder (i.e.
resolving it via ``agent_exists`` on the real Foundry project fails), this
script creates a matching Foundry Agent resource (model = the agent's
``model_deployment_ref`` or the platform default LLM) and prints the real
Foundry-assigned agent id. It never writes back to
``config/agents/registry.yaml`` itself - the operator is expected to copy
the printed ids in after reviewing them, per the "never hardcode real ids"
guidance in that file's header comment.
"""
from __future__ import annotations

import argparse
import sys

from app.agents.registry import AgentRegistry
from app.config.settings import get_settings


def _build_instructions(name: str, description: str, capabilities: list[str]) -> str:
    capability_list = ", ".join(capabilities) if capabilities else "general reasoning"
    return (
        f"You are the {name} agent for the Genie Agentic Experience Center. "
        f"{description.strip()} Your capabilities include: {capability_list}."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        required=True,
        help=(
            "Azure AI Foundry project endpoint, e.g. "
            "https://<account>.services.ai.azure.com/api/projects/<project>"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="List planned creations only.")
    args = parser.parse_args()

    settings = get_settings()
    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        print(f"ERROR: azure-ai-projects / azure-identity not installed: {exc}", file=sys.stderr)
        sys.exit(1)

    client = AIProjectClient(endpoint=args.endpoint, credential=DefaultAzureCredential())

    created: dict[str, str] = {}
    for agent in agent_registry.list():
        if not agent.enabled:
            continue

        already_provisioned = False
        if agent.foundry_agent_id:
            try:
                client.agents.get_agent(agent.foundry_agent_id)
                already_provisioned = True
            except Exception:  # noqa: BLE001 - any lookup failure means "not yet provisioned"
                already_provisioned = False

        if already_provisioned:
            print(f"[skip] {agent.id}: foundry_agent_id '{agent.foundry_agent_id}' already exists.")
            continue

        model = agent.model_deployment_ref or settings.default_llm
        instructions = _build_instructions(agent.name, agent.description, agent.capabilities)

        if args.dry_run:
            print(f"[dry-run] would create agent for '{agent.id}' with model '{model}'.")
            continue

        created_agent = client.agents.create_agent(
            model=model,
            name=agent.name,
            description=agent.description.strip(),
            instructions=instructions,
            metadata={"genie_agent_id": agent.id, "genie_owner": agent.owner},
        )
        created[agent.id] = created_agent.id
        print(f"[created] {agent.id} -> foundry_agent_id: {created_agent.id} (model: {model})")

    if created and not args.dry_run:
        print("\nUpdate config/agents/registry.yaml with these real foundry_agent_id values:")
        for agent_id, foundry_agent_id in created.items():
            print(f"  {agent_id}: {foundry_agent_id}")


if __name__ == "__main__":
    main()
