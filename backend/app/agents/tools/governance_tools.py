"""Governance Reviewer function tool: ``record_governance_decision``.

Records the agent's policy-compliance decision as a genuine governance
event via ``GovernanceService.record_policy_evaluation`` (the same audit
trail every other governed action already flows through), instead of
only stating the decision as free-form prose.
"""
from __future__ import annotations

from typing import Any

from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.governance.governance_service import GovernanceService

__all__ = ["register_governance_tools"]

_AGENT_ID = "governance-reviewer"
_TOOL_NAME = "record_governance_decision"
_VALID_DECISIONS = frozenset({"approved", "rejected"})


def register_governance_tools(
    registry: AgentToolRegistry, *, governance_service: GovernanceService
) -> None:
    """Register ``record_governance_decision`` for the ``governance-reviewer`` agent."""

    async def _record_governance_decision(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError(f"'{_TOOL_NAME}' requires an active session_id.")

        policy_name = arguments.get("policy_name")
        decision = arguments.get("decision")
        rationale = arguments.get("rationale", "")
        if not isinstance(policy_name, str) or not policy_name.strip():
            raise ToolExecutionError(f"'{_TOOL_NAME}' requires a non-empty 'policy_name'.")
        if decision not in _VALID_DECISIONS:
            raise ToolExecutionError(
                f"'{_TOOL_NAME}' requires 'decision' to be one of {sorted(_VALID_DECISIONS)}."
            )

        event = await governance_service.record_policy_evaluation(
            session_id=context.session_id,
            trace_id=context.trace_id,
            agent_id=context.agent.id,
            policy_name=policy_name,
            allowed=(decision == "approved"),
            detail={"rationale": rationale},
        )

        return {"recorded": True, "event_id": event.id, "decision": decision}

    registry.register(agent_id=_AGENT_ID, tool_name=_TOOL_NAME, fn=_record_governance_decision)
