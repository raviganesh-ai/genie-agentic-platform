"""Azure AI Foundry access layer.

Architecture boundary (see "Production Agent Rules" in
``.github/copilot-instructions.md`` and the Phase 3 course-correction):
this package is the ONLY place in the entire backend permitted to import
the ``azure-ai-projects`` / ``azure-identity`` SDKs. Every other module -
including ``AzureAgentGateway`` itself, the future ``AgentOrchestrator``
(Phase 6), and every FastAPI route/service (Phase 7) - must depend only on
the ``AgentApiClient`` / ``FoundryAgentClient`` protocols defined here, never
on the SDK directly.

Layering:

    AzureAgentGateway (app.agents.azure_agent_gateway)
        -> FoundryAgentProvider          (agent_provider.py)   [implements FoundryAgentClient]
            -> FoundryProjectService     (project_service.py)  [owns AIProjectClient/credential lifecycle]
                -> AgentApiClient        (api_client.py)       [thin wrapper over the raw SDK]

    FoundryAgentSynchronizationService (agent_synchronization_service.py)
        -> FoundryProjectService -> AgentApiClient.agent_exists(...)
        [runs once at application startup, before app.state.ready is set,
        verifying every enabled agent's foundry_agent_id resolves to a real
        Foundry agent resource; never used for run execution itself]

Every Genie business agent (discovery, requirements, industry-expert,
solution-architect, risk-compliance, governance, debugging agents, etc.) is
an independently deployed Azure AI Foundry agent resource, referenced only
by its ``foundry_agent_id`` (see ``AgentDefinition``). No agent reasoning,
prompts, or instructions live in Python source; this package only knows how
to *invoke* an existing Foundry agent resource by id.
"""
