"""Multi-agent orchestration.

Phase 6: the Agentic Workflow Runtime and Collaboration Engine. Coordinates
independently deployed Azure AI Foundry agents (workflow state machine,
parallel execution waves, handoffs, collaboration events, reanalysis
routing, deliverable pipelines, and the debugging workflow). Contains no
business/agent reasoning of its own - all agent execution flows through
``app.agents.gateway.AgentGateway`` (``AzureAgentGateway`` in production).
See ``app.orchestration.agent_orchestrator.AgentOrchestrator`` for the
top-level facade.
"""
