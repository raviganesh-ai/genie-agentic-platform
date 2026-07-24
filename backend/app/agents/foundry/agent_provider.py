"""Executes a run of an existing, independently deployed Foundry agent.

``FoundryAgentProvider`` is the sole implementation of the
``FoundryAgentClient`` protocol that ``AzureAgentGateway`` depends on. It
never creates or defines an agent - every Genie business agent (discovery,
requirements, industry-expert, solution-architect, risk-compliance,
governance, debugging agents, etc.) is provisioned independently in Azure
AI Foundry and referenced here only by its ``foundry_agent_id``. This
module contains no agent reasoning, prompts, or business logic of its own.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "expired"})
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_MAX_POLL_ATTEMPTS = 60


@dataclass(frozen=True)
class FoundryRunResult:
    """The outcome of one successful run against a Foundry-hosted agent."""

    output_text: str
    raw_status: str
    latency_ms: float


class FoundryAgentClient(Protocol):
    """The surface ``AzureAgentGateway`` needs from a Foundry-backed executor.

    ``AzureAgentGateway`` depends only on this protocol, never on the
    azure-ai-projects SDK or on ``FoundryProjectService``/``AgentApiClient``
    directly, so the Foundry access layer can evolve independently.
    """

    async def run(self, *, foundry_agent_id: str, input_text: str) -> FoundryRunResult:
        ...


class FoundryAgentProvider:
    """Runs an existing Foundry agent resource on a fresh thread and returns its reply."""

    def __init__(
        self,
        project_service: FoundryProjectService,
        *,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        max_poll_attempts: int = _DEFAULT_MAX_POLL_ATTEMPTS,
    ) -> None:
        self._project_service = project_service
        self._poll_interval_seconds = poll_interval_seconds
        self._max_poll_attempts = max_poll_attempts

    async def run(self, *, foundry_agent_id: str, input_text: str) -> FoundryRunResult:
        return await asyncio.to_thread(
            self._run_sync, foundry_agent_id=foundry_agent_id, input_text=input_text
        )

    def _run_sync(self, *, foundry_agent_id: str, input_text: str) -> FoundryRunResult:
        started = time.monotonic()
        try:
            client = self._project_service.get_api_client()
            thread_id = client.create_thread()
            client.create_message(thread_id=thread_id, role="user", content=input_text)
            run = client.create_run(thread_id=thread_id, agent_id=foundry_agent_id)
            run = self._poll_until_terminal(client, thread_id=thread_id, run_id=run.id)
            if run.status != "completed":
                raise FoundryUnavailableError(
                    f"Azure AI Foundry run for agent '{foundry_agent_id}' ended "
                    f"with status '{run.status}' instead of 'completed'."
                )
            messages = client.list_messages(thread_id=thread_id)
            output_text = _extract_latest_assistant_text(messages)
        except FoundryUnavailableError:
            raise
        except Exception as exc:
            raise FoundryUnavailableError(
                f"Azure AI Foundry execution failed for agent "
                f"'{foundry_agent_id}': {exc}"
            ) from exc

        latency_ms = (time.monotonic() - started) * 1000
        return FoundryRunResult(output_text=output_text, raw_status=run.status, latency_ms=latency_ms)

    def _poll_until_terminal(self, client: Any, *, thread_id: str, run_id: str) -> Any:
        run = client.get_run(thread_id=thread_id, run_id=run_id)
        attempts = 0
        while run.status not in _TERMINAL_RUN_STATUSES and attempts < self._max_poll_attempts:
            time.sleep(self._poll_interval_seconds)
            run = client.get_run(thread_id=thread_id, run_id=run_id)
            attempts += 1
        if run.status not in _TERMINAL_RUN_STATUSES:
            raise FoundryUnavailableError(
                f"Azure AI Foundry run '{run_id}' did not reach a terminal status "
                f"within {self._max_poll_attempts} polling attempts."
            )
        return run


def _extract_latest_assistant_text(messages: Any) -> str:
    """Extract the most recent assistant reply from a thread's messages.

    ASSUMPTION (SDK surface): each message exposes ``.role`` and ``.content``,
    where ``content`` is either a plain string or a list of content blocks
    each exposing ``.text.value`` (matching the OpenAI-Assistants-style
    content shape the azure-ai-projects Agent Service is documented to
    return). Isolated here so a shape change only needs to be reconciled in
    one function.
    """

    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                value = getattr(text, "value", None) if text is not None else None
                if value:
                    return value

    raise FoundryUnavailableError("Azure AI Foundry run produced no assistant message.")
