"""Application/domain services shared across API routers.

Implemented in Phase 7: ``SessionService``, ``WorkshopService``,
``ArchitectureService``, and ``OutputService`` contain every piece of
business logic Phase 7's API routers need, so routes themselves stay thin.
Every service delegates orchestration/agent execution to the unmodified
Phase 6 ``AgentOrchestrator``.
"""
