"""Errors raised by the Azure AI Foundry access layer."""
from __future__ import annotations


class FoundryUnavailableError(RuntimeError):
    """Raised when Azure AI Foundry cannot be reached or a run does not succeed.

    Every failure mode in this package - missing SDK, credential failure,
    network failure, a run that ends in a non-completed terminal status, an
    agent resource that no longer exists - is normalized to this single
    error type. Callers (``AzureAgentGateway``) must never catch this and
    fall back to local or mock execution; they must record a governance
    trace event and re-raise (fail closed).
    """


class FoundryAgentSynchronizationError(RuntimeError):
    """Raised when one or more ``foundry_agent_id`` references cannot be verified.

    Raised by ``FoundryAgentSynchronizationService`` when Azure AI Foundry is
    unreachable, or when an enabled agent's configured ``foundry_agent_id``
    does not resolve to an existing Foundry agent resource. Callers (the
    application startup sequence) must not fall back to local/mock
    execution or start accepting traffic in response to this - it must
    propagate and keep the application from becoming ready (fail closed).
    """
