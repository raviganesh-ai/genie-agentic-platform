"""Agent registry (Phase 2) and Azure AI Foundry gateway (Phase 3).

Every business/debugging agent (discovery, requirements, industry-expert,
data-architect, solution-architect, risk-compliance, innovation,
ui-designer, roadmap, governance, executive-summary, cost-optimization,
responsible-ai, workshop-facilitator, and the debugging agents) is an
independently deployed Azure AI Foundry agent resource, configured in
``config/agents/*.yaml`` and invoked only through ``AzureAgentGateway``
(``azure_agent_gateway.py``) -> ``FoundryAgentProvider``
(``foundry/agent_provider.py``). No agent reasoning, prompts, or sample
outputs live in this package's Python source.
"""
