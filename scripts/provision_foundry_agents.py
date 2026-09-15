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

Any agent with ``tool_definitions`` configured (e.g. ``genie-orchestrator``'s
``call_<agent>`` delegation tools) has those schemas attached directly to
the persisted ``PromptAgentDefinition.tools`` here. This is required, not
optional: Azure AI Foundry's Agent Framework SDK only sends tool
declarations to the model when they are part of the agent's own persisted
definition - declarations passed per-request are silently dropped whenever
the run references an existing agent by name/version, so without this an
orchestrator-style agent could never actually call any of its tools.

Any agent with ``mcp_tools`` configured (e.g. ``finops-hub-agent``'s Azure
MCP Server Kusto query tool) has its remote MCP server's *own* tools
real-connected to and discovered here (via ``agent_framework.
MCPStreamableHTTPTool``), then persisted as ordinary ``FunctionTool``
schemas exactly like ``tool_definitions`` above - for the same reason:
the model only ever learns a tool exists from what is already declared on
the Foundry agent resource. At run time, ``FoundryAgentProvider`` builds a
fresh ``MCPStreamableHTTPTool`` and routes matching tool calls to the real
MCP server directly (never through Genie's own ``AgentToolRegistry``). See
``/memories/repo/mcp-tool-integration.md`` for the full verified rationale
(this dual persist-then-dispatch design was confirmed by introspecting the
installed ``agent_framework_foundry`` package, not assumed from docs).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.agents.models import AgentMcpToolDefinition
from app.agents.registry import AgentRegistry
from app.config.settings import Settings, get_settings


def _build_instructions(name: str, description: str, capabilities: list[str]) -> str:
    capability_list = ", ".join(capabilities) if capabilities else "general reasoning"
    return (
        f"You are the {name} agent for the Genie Agentic Experience Center. "
        f"{description.strip()} Your capabilities include: {capability_list}."
    )


# Foundry agent-version 'description' has a documented maxLength of 512
# characters - config/agents/registry.yaml's own `description` field has no
# such limit (it also documents delegation-tool design decisions), so it
# must be truncated here rather than in the shared config.
_FOUNDRY_DESCRIPTION_MAX_LENGTH = 512


def _foundry_description(description: str) -> str:
    text = description.strip()
    if len(text) <= _FOUNDRY_DESCRIPTION_MAX_LENGTH:
        return text
    return text[: _FOUNDRY_DESCRIPTION_MAX_LENGTH - 1].rstrip() + "\u2026"


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
    parser.add_argument(
        "--agent",
        dest="agents",
        action="append",
        default=None,
        metavar="AGENT_ID",
        help=(
            "Restrict provisioning to this agent id (config/agents/registry.yaml "
            "'id', not the Foundry agent_name). Repeatable. Default: every enabled "
            "agent."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Create a new Foundry agent version even if the agent_name already "
            "exists, so its instructions/description are refreshed. Only takes "
            "effect together with --agent (never applies to every agent at once), "
            "to keep the default run idempotent/safe to re-run."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)

    try:
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        print(f"ERROR: azure-ai-projects / azure-identity not installed: {exc}", file=sys.stderr)
        sys.exit(1)

    from app.agents.foundry.agent_provider import tool_definition_to_json_schema

    def _build_tools(tool_definitions: list) -> list | None:
        # A Foundry Prompt Agent only learns a tool exists if it is declared
        # on the agent's own persisted definition here - tool declarations
        # passed per-request (agent_framework.foundry.FoundryAgent(...).run
        # (tools=...)) are silently dropped whenever the run references an
        # existing agent by name/version (confirmed via the agent_framework
        # SDK's own warning: "tool declarations cannot be sent when an agent
        # is specified; they are omitted from the request and used only for
        # client-side function dispatch"). Without this, an orchestrator-
        # style agent with tool_definitions (e.g. genie-orchestrator) can
        # never actually call any of its call_<agent> delegation tools.
        if not tool_definitions:
            return None
        return [
            FunctionTool(
                name=tool_definition.name,
                description=tool_definition.description,
                parameters=tool_definition_to_json_schema(tool_definition),
                strict=False,
            )
            for tool_definition in tool_definitions
        ]

    async def _discover_mcp_function_tools(
        mcp_definitions: list[AgentMcpToolDefinition], settings: Settings
    ) -> list:
        """Real-connects to each configured MCP server and returns its tools.

        Each discovered ``agent_framework.FunctionTool``'s own JSON-schema
        spec (``to_json_schema_spec()``) is translated into the same
        ``azure.ai.projects.models.FunctionTool`` shape ``_build_tools``
        produces for ordinary ``tool_definitions`` - the persisted
        definition does not distinguish where a tool schema originated.
        Skips (with a printed warning) any MCP tool whose
        ``server_url_setting`` is not configured, rather than failing the
        whole provisioning run for one unavailable server.
        """

        from agent_framework import MCPStreamableHTTPTool

        discovered: list = []
        for mcp_definition in mcp_definitions:
            server_url = getattr(settings, mcp_definition.server_url_setting, None)
            if not server_url:
                print(
                    f"[skip-mcp] '{mcp_definition.name}': setting "
                    f"'{mcp_definition.server_url_setting}' is not configured; no tools "
                    f"discovered/persisted for it."
                )
                continue

            mcp_tool = MCPStreamableHTTPTool(
                name=mcp_definition.name,
                url=server_url,
                description=mcp_definition.description,
                allowed_tools=mcp_definition.allowed_tools,
                approval_mode=mcp_definition.approval_mode,
            )
            async with mcp_tool:
                for discovered_function in mcp_tool.functions:
                    spec = discovered_function.to_json_schema_spec()["function"]
                    discovered.append(
                        FunctionTool(
                            name=spec["name"],
                            description=spec["description"],
                            parameters=spec["parameters"],
                            strict=False,
                        )
                    )
        return discovered

    client = AIProjectClient(endpoint=args.endpoint, credential=DefaultAzureCredential())

    created: dict[str, str] = {}
    for agent in agent_registry.list():
        if not agent.enabled:
            continue

        if args.agents is not None and agent.id not in args.agents:
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

        force_update = already_provisioned and args.force and args.agents is not None
        if already_provisioned and not force_update:
            print(f"[skip] {agent.id}: foundry_agent_id '{agent.foundry_agent_id}' already exists.")
            continue

        model = agent.model_deployment_ref or settings.default_llm
        instructions = _build_instructions(agent.name, agent.description, agent.capabilities)

        if args.dry_run:
            action = "update (new version for)" if force_update else "create"
            print(
                f"[dry-run] would {action} agent_name '{agent.foundry_agent_id}' for "
                f"'{agent.id}' with model '{model}'."
            )
            continue

        function_tools = list(_build_tools(agent.tool_definitions) or [])
        if agent.mcp_tools:
            function_tools.extend(
                asyncio.run(_discover_mcp_function_tools(agent.mcp_tools, settings))
            )

        definition = PromptAgentDefinition(
            kind="prompt",
            model=model,
            instructions=instructions,
            tools=function_tools or None,
        )
        version_details = client.agents.create_version(
            agent.foundry_agent_id,
            definition=definition,
            metadata={"genie_agent_id": agent.id, "genie_owner": agent.owner or ""},
            description=_foundry_description(agent.description),
        )
        created[agent.id] = version_details.name
        verb = "updated" if force_update else "created"
        print(
            f"[{verb}] {agent.id} -> foundry_agent_id: {version_details.name} "
            f"(version: {version_details.version}, model: {model})"
        )

    if created:
        print("\nProvisioned/updated the following agent_name(s) (already reflected in registry.yaml):")
        for agent_id, foundry_agent_id in created.items():
            print(f"  {agent_id}: {foundry_agent_id}")


if __name__ == "__main__":
    main()

