"""Unit tests for MemoryAccessPolicyService."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.models import AgentDefinition
from app.memory.memory_access_policy_service import MemoryAccessPolicyService, MemoryPolicyError


def _agent(agent_id: str = "test-agent", memory_access: list[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name="Test Agent",
        role="test_role",
        description="A test agent.",
        memory_access=memory_access if memory_access is not None else ["personal", "shared", "enterprise"],
    )


def test_load_valid_policy(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    assert service.document.personal_agent_memory.accessible_by == "owning_agent"
    assert service.document.shared_collaboration_memory.require_approval_for_overwrite is True
    assert service.document.enterprise_knowledge_memory.requires_approval_to_promote is True


def test_load_raises_when_file_missing(tmp_path: Path):
    with pytest.raises(MemoryPolicyError):
        MemoryAccessPolicyService.load(tmp_path)


def test_load_raises_when_section_missing(tmp_path: Path):
    (tmp_path / "memory_policy.yaml").write_text(
        "personal_agent_memory:\n  accessible_by: owning_agent\n",
        encoding="utf-8",
    )
    with pytest.raises(MemoryPolicyError):
        MemoryAccessPolicyService.load(tmp_path)


def test_load_raises_when_not_a_mapping(tmp_path: Path):
    (tmp_path / "memory_policy.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(MemoryPolicyError):
        MemoryAccessPolicyService.load(tmp_path)


def test_authorize_personal_read_allowed_for_owning_agent(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    decision = service.authorize_personal_read(requesting_agent_id="agent-a", owning_agent_id="agent-a")
    assert decision.allowed


def test_authorize_personal_read_denied_for_other_agent(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    decision = service.authorize_personal_read(requesting_agent_id="agent-a", owning_agent_id="agent-b")
    assert not decision.allowed


def test_authorize_personal_write_requires_personal_access(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    allowed = service.authorize_personal_write(agent=_agent(memory_access=["personal"]))
    denied = service.authorize_personal_write(agent=_agent(memory_access=["shared"]))
    assert allowed.allowed
    assert not denied.allowed


def test_authorize_shared_read_requires_shared_access(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    allowed = service.authorize_shared_read(agent=_agent(memory_access=["shared"]))
    denied = service.authorize_shared_read(agent=_agent(memory_access=["personal"]))
    assert allowed.allowed
    assert not denied.allowed


def test_authorize_shared_write_create_does_not_require_approval(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    decision = service.authorize_shared_write(
        agent=_agent(memory_access=["shared"]), is_overwrite=False, approval_status="not_required"
    )
    assert decision.allowed


def test_authorize_shared_write_overwrite_requires_approval(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    denied = service.authorize_shared_write(
        agent=_agent(memory_access=["shared"]), is_overwrite=True, approval_status="pending"
    )
    allowed = service.authorize_shared_write(
        agent=_agent(memory_access=["shared"]), is_overwrite=True, approval_status="approved"
    )
    assert not denied.allowed
    assert allowed.allowed


def test_authorize_shared_write_denied_without_shared_access(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    decision = service.authorize_shared_write(
        agent=_agent(memory_access=["personal"]), is_overwrite=False, approval_status="not_required"
    )
    assert not decision.allowed


def test_authorize_enterprise_read_requires_enterprise_access(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    allowed = service.authorize_enterprise_read(agent=_agent(memory_access=["enterprise"]))
    denied = service.authorize_enterprise_read(agent=_agent(memory_access=["shared"]))
    assert allowed.allowed
    assert not denied.allowed


def test_authorize_enterprise_write_requires_approval_to_promote(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    denied = service.authorize_enterprise_write(
        agent=_agent(memory_access=["enterprise"]), approval_status="pending"
    )
    allowed = service.authorize_enterprise_write(
        agent=_agent(memory_access=["enterprise"]), approval_status="approved"
    )
    assert not denied.allowed
    assert allowed.allowed


def test_authorize_enterprise_write_denied_without_enterprise_access(local_settings):
    service = MemoryAccessPolicyService.load(local_settings.policies_path)
    decision = service.authorize_enterprise_write(
        agent=_agent(memory_access=["shared"]), approval_status="approved"
    )
    assert not decision.allowed
