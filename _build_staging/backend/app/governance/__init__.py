"""Governance, lineage, approvals, traceability, and session replay (Phase 5).

``GovernanceService`` (governance_service.py) records agent execution,
memory access, tool use, policy evaluations, and denied access events -
both to a session-queryable repository and to the configured
``GovernanceProvider`` (governance_models.py): ``Agent365GovernanceProvider``
is an interface only (no Agent365 SDK exists to call), and
``LocalGovernanceTraceProvider`` is a concrete in-memory implementation for
local development and tests only, never production.

``RecommendationLineageService``, ``DecisionGraphService``, and
``ApprovalService`` record recommendation provenance, agent/recommendation/
approval dependency graphs, and the approval checkpoint framework,
respectively. ``TraceabilityService`` and ``ReplayService`` compose all of
the above into customer-facing traceability views and full session replay
reconstruction.
"""
