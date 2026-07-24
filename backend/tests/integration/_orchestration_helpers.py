"""Shared config-tree builder for Phase 6 orchestration integration tests.

Not a test module itself (no ``test_`` prefix); imported by the Phase 6
integration test files to build a hermetic, temporary configuration tree
exercising parallel execution, approval gating, and fail-closed scenarios.
"""
from __future__ import annotations

from pathlib import Path

from app.config.settings import Settings

_AGENTS_YAML = """
agents:
  - id: agent-a
    name: Agent A
    role: role_a
    description: Executes step-a.
    capabilities:
      - cap-a
    memory_access:
      - personal
      - shared
    enabled: true
  - id: agent-b
    name: Agent B
    role: role_b
    description: Executes step-b.
    capabilities:
      - cap-b
    enabled: true
  - id: agent-c
    name: Agent C
    role: role_c
    description: Executes step-c, which depends on step-a and step-b.
    capabilities:
      - cap-c
    enabled: true
  - id: disabled-agent
    name: Disabled Agent
    role: role_disabled
    description: An agent that is registered but disabled.
    capabilities:
      - cap-disabled
    enabled: false
  - id: debugging-agent
    name: Debugging Agent
    role: debugging
    description: Diagnoses detected failures.
    capabilities:
      - failure_diagnosis
    enabled: true
"""

_PROMPTS_YAML = """
prompts:
  - id: prompt-a
    name: Prompt A
    description: Prompt for step-a.
    template: "Process input {x}"
    variables:
      - x
  - id: prompt-b
    name: Prompt B
    description: Prompt for step-b.
    template: "Process input {y}"
    variables:
      - y
  - id: prompt-c
    name: Prompt C
    description: Prompt for step-c.
    template: "Combine prior outputs."
    variables: []
  - id: failure-diagnosis-v1
    name: Failure Diagnosis
    description: Diagnoses a detected failure.
    template: "Diagnose: {failure_details}"
    variables:
      - failure_details
"""

_WORKFLOWS_YAML = """
workflows:
  - id: parallel-workflow
    name: Parallel Workflow
    description: Two parallel steps followed by a dependent, approval-gated step.
    steps:
      - id: step-a
        agent_id: agent-a
        description: First parallel step.
        depends_on: []
        prompt_id: prompt-a
      - id: step-b
        agent_id: agent-b
        description: Second parallel step.
        depends_on: []
        prompt_id: prompt-b
      - id: step-c
        agent_id: agent-c
        description: Depends on both parallel steps; requires approval.
        depends_on:
          - step-a
          - step-b
        prompt_id: prompt-c
        requires_approval_checkpoint: checkpoint-1
    enabled: true

  - id: disabled-agent-workflow
    name: Disabled Agent Workflow
    description: References a disabled agent, to exercise fail-closed behavior.
    steps:
      - id: step-only
        agent_id: disabled-agent
        description: Cannot execute; agent is disabled.
        depends_on: []
        prompt_id: prompt-a
    enabled: true

  - id: memory-gated-workflow
    name: Memory Gated Workflow
    description: Requires a shared memory reference that is never written.
    steps:
      - id: step-mem
        agent_id: agent-a
        description: Requires a missing shared memory reference.
        depends_on: []
        prompt_id: prompt-a
        required_memory_references:
          - missing-key
    enabled: true

  - id: debugging-workflow
    name: Debugging Workflow
    description: Diagnoses a detected failure.
    steps:
      - id: diagnose-failure
        agent_id: debugging-agent
        description: Diagnose the detected failure and recommend remediation.
        depends_on: []
        prompt_id: failure-diagnosis-v1
    enabled: true

  - id: transcript-workflow
    name: Transcript Workflow
    description: >-
      One step derives its variable directly from the session's uploaded
      call transcript; a second, dependent step derives its variable from
      the first step's output - exercises variable_sources end to end.
    steps:
      - id: step-transcript
        agent_id: agent-a
        description: Derives variable 'x' from the transcript.
        depends_on: []
        prompt_id: prompt-a
        variable_sources:
          x: transcript
      - id: step-chained
        agent_id: agent-b
        description: Derives variable 'y' from step-transcript's output.
        depends_on:
          - step-transcript
        prompt_id: prompt-b
        variable_sources:
          y: "step:step-transcript"
    enabled: true
"""

_MEMORY_POLICY_YAML = """
personal_agent_memory:
  accessible_by: owning_agent
shared_collaboration_memory:
  accessible_by: session_participants
  emits_governance_events: true
  require_approval_for_overwrite: true
enterprise_knowledge_memory:
  accessible_by: approved_reviewers
  requires_approval_to_promote: true
"""

_GOVERNANCE_POLICY_YAML = """
agent_execution_governance:
  track_registration: true
  track_versions: true
  track_lifecycle: true
  track_executions: true
  track_communication: true
  track_memory_reads: true
  track_memory_writes: true
  track_tool_requests: true
  track_policy_evaluations: true
  track_denied_access: true
decision_lineage:
  require_evidence_references: true
session_replay:
  enabled: true
"""

_APPROVAL_POLICY_YAML = """
default_expiry_minutes: 1440
checkpoints:
  - id: checkpoint-1
    name: Checkpoint One
    description: The single approval checkpoint used by these tests.
    required: true
"""


def write_orchestration_config(config_root: Path) -> None:
    agents_dir = config_root / "agents"
    prompts_dir = config_root / "prompts"
    workflows_dir = config_root / "workflows"
    policies_dir = config_root / "policies"
    for directory in (agents_dir, prompts_dir, workflows_dir, policies_dir):
        directory.mkdir(parents=True, exist_ok=True)

    (agents_dir / "registry.yaml").write_text(_AGENTS_YAML, encoding="utf-8")
    (prompts_dir / "registry.yaml").write_text(_PROMPTS_YAML, encoding="utf-8")
    (workflows_dir / "registry.yaml").write_text(_WORKFLOWS_YAML, encoding="utf-8")
    (policies_dir / "memory_policy.yaml").write_text(_MEMORY_POLICY_YAML, encoding="utf-8")
    (policies_dir / "governance_policy.yaml").write_text(_GOVERNANCE_POLICY_YAML, encoding="utf-8")
    (policies_dir / "approval_policy.yaml").write_text(_APPROVAL_POLICY_YAML, encoding="utf-8")


def build_orchestration_settings(config_root: Path) -> Settings:
    write_orchestration_config(config_root)
    return Settings(
        environment="development",
        provider_mode="local",
        governance_provider="local",
        allow_mock_agents=True,
        allow_local_agents=True,
        use_synthetic_data=True,
        config_root=config_root,
    )
