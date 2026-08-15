"""Shared strongly typed domain models.

Governance domain models (``governance_event``, ``recommendation_lineage``,
``decision_graph``, ``approval_models``) were added in Phase 5. Orchestration
runtime domain models (``workflow_state``, ``workflow_checkpoint``,
``workflow_models``, ``handoff_models``, ``collaboration_models``,
``reanalysis_models``) were added in Phase 6. Agent, workflow, prompt, and
memory domain models live alongside their owning package
(``app.agents.models``, ``app.workflows.models``, ``app.prompts.models``,
``app.memory.memory_models``) rather than here.
"""
