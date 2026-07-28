"""Deployment Agent function tools: ``provision_customer_agents``, ``generate_launch_link``.

Both bind directly onto ``CustomerAgentProvisioningService`` (see
``app.services.customer_agent_provisioning_service``) - the already
existing per-customer Foundry agent fleet provisioner - never a new,
parallel provisioning path.

``generate_launch_link`` intentionally does not fabricate a public,
shareable URL: Genie's earlier customer-preview access-link feature was
retired, and inventing a new undocumented endpoint here would violate
"Never invent external APIs" (see ``.github/copilot-instructions.md``).
It instead returns a structured internal reference (the session id and
provisioned agent count) that a real launch-link feature can be built on
top of later.
"""
from __future__ import annotations

from typing import Any

from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.services.customer_agent_provisioning_service import (
    CustomerAgentProvisioningError,
    CustomerAgentProvisioningService,
    NullCustomerAgentProvisioningService,
)

__all__ = ["register_deployment_tools"]

_AGENT_ID = "deployment-agent"


def register_deployment_tools(
    registry: AgentToolRegistry,
    *,
    customer_agent_provisioning_service: (
        CustomerAgentProvisioningService | NullCustomerAgentProvisioningService
    ),
) -> None:
    """Register ``provision_customer_agents``/``generate_launch_link``."""

    async def _provision_customer_agents(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError("'provision_customer_agents' requires an active session_id.")
        scope_id = arguments.get("scope_id")

        try:
            records = await customer_agent_provisioning_service.provision_for_session(
                session_id=context.session_id, scope_id=scope_id, trace_id=context.trace_id
            )
        except CustomerAgentProvisioningError as exc:
            raise ToolExecutionError(str(exc)) from exc

        return {
            "provisioned": True,
            "agent_count": len(records),
            "agent_ids": [record.agent_id for record in records],
        }

    async def _generate_launch_link(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError("'generate_launch_link' requires an active session_id.")
        scope_id = arguments.get("scope_id")

        provisioned = customer_agent_provisioning_service.provisioned_agents(
            context.session_id, scope_id=scope_id
        )
        if not provisioned:
            raise ToolExecutionError(
                "'generate_launch_link' requires customer agents to already be "
                "provisioned for this session; call 'provision_customer_agents' first."
            )

        return {
            "session_reference": context.session_id,
            "provisioned_agent_count": len(provisioned),
        }

    registry.register(
        agent_id=_AGENT_ID, tool_name="provision_customer_agents", fn=_provision_customer_agents
    )
    registry.register(agent_id=_AGENT_ID, tool_name="generate_launch_link", fn=_generate_launch_link)
