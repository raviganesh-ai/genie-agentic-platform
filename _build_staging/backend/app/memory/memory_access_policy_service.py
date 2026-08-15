"""Memory access policy engine.

Loads the externalized memory tier access policy (``config/policies/
memory_policy.yaml``) and enforces read/write/update/promotion
authorization for all three memory tiers, per the "Memory Access Policy
Engine" requirements in ``.github/copilot-instructions.md``. Denied
operations are the caller's responsibility to report as governance events
(see ``app.memory.memory_events``); this service only decides.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.models import AgentDefinition
from app.memory.memory_models import ApprovalStatus
from app.utils.yaml_loader import YamlLoadError, load_yaml_file

_DEFAULT_POLICY_FILENAME = "memory_policy.yaml"


class MemoryPolicyError(RuntimeError):
    """Raised when the memory access policy configuration is missing or invalid."""


class PersonalAgentMemoryPolicy(BaseModel):
    """Policy for Personal Agent Memory: readable only by its owning agent."""

    model_config = ConfigDict(extra="forbid")

    accessible_by: str = "owning_agent"


class SharedCollaborationMemoryPolicy(BaseModel):
    """Policy for Shared Collaboration Memory: governed writes, no silent overwrite."""

    model_config = ConfigDict(extra="forbid")

    accessible_by: str = "session_participants"
    emits_governance_events: bool = True
    require_approval_for_overwrite: bool = True


class EnterpriseKnowledgeMemoryPolicy(BaseModel):
    """Policy for Enterprise Knowledge Memory: reviewer-gated, approval-gated promotion."""

    model_config = ConfigDict(extra="forbid")

    accessible_by: str = "approved_reviewers"
    requires_approval_to_promote: bool = True


class MemoryPolicyDocument(BaseModel):
    """The full, required memory tier access policy document."""

    model_config = ConfigDict(extra="forbid")

    personal_agent_memory: PersonalAgentMemoryPolicy
    shared_collaboration_memory: SharedCollaborationMemoryPolicy
    enterprise_knowledge_memory: EnterpriseKnowledgeMemoryPolicy


@dataclass(frozen=True)
class AuthorizationDecision:
    """The outcome of a single authorization check."""

    allowed: bool
    reason: str


class MemoryAccessPolicyService:
    """Enforces memory tier access policy loaded from externalized configuration."""

    def __init__(self, document: MemoryPolicyDocument) -> None:
        self._document = document

    @property
    def document(self) -> MemoryPolicyDocument:
        return self._document

    @classmethod
    def load(
        cls, policies_path: Path, filename: str = _DEFAULT_POLICY_FILENAME
    ) -> MemoryAccessPolicyService:
        """Load and validate the memory policy file. Fails closed on any problem."""

        path = policies_path / filename
        if not path.is_file():
            raise MemoryPolicyError(f"Memory policy file '{path}' does not exist.")

        try:
            raw = load_yaml_file(path)
        except YamlLoadError as exc:
            raise MemoryPolicyError(str(exc)) from exc

        if not isinstance(raw, dict):
            raise MemoryPolicyError(f"Memory policy file '{path}' must define a top-level mapping.")

        try:
            document = MemoryPolicyDocument.model_validate(raw)
        except ValidationError as exc:
            raise MemoryPolicyError(f"Invalid memory policy in '{path}': {exc}") from exc

        return cls(document)

    # --- Personal Agent Memory -------------------------------------------------

    def authorize_personal_read(
        self, *, requesting_agent_id: str, owning_agent_id: str
    ) -> AuthorizationDecision:
        if requesting_agent_id == owning_agent_id:
            return AuthorizationDecision(True, "Requesting agent owns this personal memory.")
        return AuthorizationDecision(
            False,
            (
                f"Personal memory owned by agent '{owning_agent_id}' is not "
                f"accessible to agent '{requesting_agent_id}'; policy grants "
                f"access only to the owning agent."
            ),
        )

    def authorize_personal_write(self, *, agent: AgentDefinition) -> AuthorizationDecision:
        if "personal" not in agent.memory_access:
            return AuthorizationDecision(
                False, f"Agent '{agent.id}' is not authorized for personal memory access."
            )
        return AuthorizationDecision(True, "Agent is authorized for personal memory access.")

    # --- Shared Collaboration Memory --------------------------------------------

    def authorize_shared_read(self, *, agent: AgentDefinition) -> AuthorizationDecision:
        if "shared" not in agent.memory_access:
            return AuthorizationDecision(
                False, f"Agent '{agent.id}' is not authorized for shared memory access."
            )
        return AuthorizationDecision(True, "Agent is authorized for shared memory access.")

    def authorize_shared_write(
        self, *, agent: AgentDefinition, is_overwrite: bool, approval_status: ApprovalStatus
    ) -> AuthorizationDecision:
        if "shared" not in agent.memory_access:
            return AuthorizationDecision(
                False, f"Agent '{agent.id}' is not authorized for shared memory access."
            )
        if (
            is_overwrite
            and self._document.shared_collaboration_memory.require_approval_for_overwrite
            and approval_status != "approved"
        ):
            return AuthorizationDecision(
                False,
                "Shared memory overwrite requires an approved lineage.approval_status.",
            )
        return AuthorizationDecision(True, "Write authorized.")

    # --- Enterprise Knowledge Memory ---------------------------------------------

    def authorize_enterprise_read(self, *, agent: AgentDefinition) -> AuthorizationDecision:
        # ASSUMPTION: the policy's "approved_reviewers" concept maps to any
        # agent whose declared memory_access includes "enterprise" - Genie
        # has no separate reviewer/RBAC role model yet.
        if "enterprise" not in agent.memory_access:
            return AuthorizationDecision(
                False,
                f"Agent '{agent.id}' is not an approved reviewer for enterprise "
                f"knowledge memory.",
            )
        return AuthorizationDecision(True, "Read authorized.")

    def authorize_enterprise_write(
        self, *, agent: AgentDefinition, approval_status: ApprovalStatus
    ) -> AuthorizationDecision:
        if "enterprise" not in agent.memory_access:
            return AuthorizationDecision(
                False,
                f"Agent '{agent.id}' is not authorized for enterprise knowledge "
                f"memory access.",
            )
        if (
            self._document.enterprise_knowledge_memory.requires_approval_to_promote
            and approval_status != "approved"
        ):
            return AuthorizationDecision(
                False,
                "Promotion to enterprise knowledge memory requires an approved "
                "lineage.approval_status.",
            )
        return AuthorizationDecision(True, "Promotion authorized.")
