"""One-off operator CLI: provision real Azure AI Foundry agent resources.

Usage (from the repo root, backend virtual environment activated)::

    python scripts/provision_foundry_agents.py --endpoint <account-endpoint> \\
        --project <project-name>

This is the IaC/CLI-equivalent "provisioning" step referenced by
``config/agents/registry.yaml``'s own comments and by
``backend/app/agents/foundry/api_client.py``'s architecture assumption:
every Genie agent is an independently deployed Azure AI Foundry Prompt
Agent resource, created here (by an operator, out-of-band) - never ad hoc
by the running backend.

Unlike the classic Assistants API, a Foundry Prompt Agent's ``agent_name``
is chosen by the caller up front rather than server-generated - so each
enabled agent in ``config/agents/registry.yaml`` MUST already have its
intended ``foundry_agent_id`` (used here as the Foundry ``agent_name``) set
before running this script; it is never invented here. For every such
agent whose ``foundry_agent_id`` does not yet resolve to an existing
Foundry resource, this script creates a new version (model =
the agent's ``model_deployment_ref`` or the platform default LLM) under
that same name. It never writes back to ``config/agents/registry.yaml``
itself.
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
        from azure.ai.projects.models import PromptAgentDefinition
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        print(f"ERROR: azure-ai-projects / azure-identity not installed: {exc}", file=sys.stderr)
        sys.exit(1)

    client = AIProjectClient(endpoint=args.endpoint, credential=DefaultAzureCredential())

    created: dict[str, str] = {}
    for agent in agent_registry.list():
        if not agent.enabled:
            continue

        if not agent.foundry_agent_id:
            print(
                f"[skip] {agent.id}: no foundry_agent_id configured in "
                f"config/agents/registry.yaml; set the intended Foundry agent_name "
                f"there first (Prompt Agent names are chosen up front, not "
                f"server-generated)."
            )
            continue

        already_provisioned = False
        try:
            client.agents.get(agent.foundry_agent_id)
            already_provisioned = True
        except Exception:  # noqa: BLE001 - any lookup failure means "not yet provisioned"
            already_provisioned = False

        if already_provisioned:
            print(f"[skip] {agent.id}: foundry_agent_id '{agent.foundry_agent_id}' already exists.")
            continue

        model = agent.model_deployment_ref or settings.default_llm
        instructions = _build_instructions(agent.name, agent.description, agent.capabilities)

        if args.dry_run:
            print(
                f"[dry-run] would create agent_name '{agent.foundry_agent_id}' for "
                f"'{agent.id}' with model '{model}'."
            )
            continue

        definition = PromptAgentDefinition(kind="prompt", model=model, instructions=instructions)
        version_details = client.agents.create_version(
            agent.foundry_agent_id,
            definition=definition,
            metadata={"genie_agent_id": agent.id, "genie_owner": agent.owner or ""},
            description=agent.description.strip(),
        )
        created[agent.id] = version_details.name
        print(
            f"[created] {agent.id} -> foundry_agent_id: {version_details.name} "
            f"(version: {version_details.version}, model: {model})"
        )

    if created:
        print("\nProvisioned the following agent_name(s) (already reflected in registry.yaml):")
        for agent_id, foundry_agent_id in created.items():
            print(f"  {agent_id}: {foundry_agent_id}")


if __name__ == "__main__":
    main()

