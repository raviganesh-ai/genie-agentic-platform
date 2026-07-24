"""Unit tests for FoundryAgentProvider using a fake AgentApiClient.

No real Azure AI Foundry connectivity is required - FoundryAgentProvider
depends only on a project-service-like object exposing ``get_api_client()``,
so it is fully testable with a fake.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.agents.foundry.agent_provider import FoundryAgentProvider
from app.agents.foundry.errors import FoundryUnavailableError


@dataclass
class _FakeRun:
    id: str
    status: str


@dataclass
class _FakeTextValue:
    value: str


@dataclass
class _FakeTextBlock:
    text: _FakeTextValue


@dataclass
class _FakeMessage:
    role: str
    content: Any


class _FakeAgentApiClient:
    def __init__(self, *, run_statuses: list[str], messages: list[_FakeMessage]) -> None:
        self._run_statuses = list(run_statuses)
        self._messages = messages
        self.created_messages: list[dict[str, str]] = []
        self.created_runs: list[dict[str, str]] = []

    def create_thread(self) -> str:
        return "thread-1"

    def create_message(self, *, thread_id: str, role: str, content: str) -> None:
        self.created_messages.append({"thread_id": thread_id, "role": role, "content": content})

    def create_run(self, *, thread_id: str, agent_id: str) -> _FakeRun:
        self.created_runs.append({"thread_id": thread_id, "agent_id": agent_id})
        return _FakeRun(id="run-1", status=self._run_statuses[0])

    def get_run(self, *, thread_id: str, run_id: str) -> _FakeRun:
        status = self._run_statuses.pop(0) if len(self._run_statuses) > 1 else self._run_statuses[0]
        return _FakeRun(id=run_id, status=status)

    def list_messages(self, *, thread_id: str) -> list[_FakeMessage]:
        return self._messages


@dataclass
class _FakeProjectService:
    api_client: _FakeAgentApiClient

    def get_api_client(self) -> _FakeAgentApiClient:
        return self.api_client


def _assistant_message(text: str) -> _FakeMessage:
    return _FakeMessage(role="assistant", content=[_FakeTextBlock(text=_FakeTextValue(value=text))])


async def test_run_returns_output_text_on_completed_run():
    api_client = _FakeAgentApiClient(
        run_statuses=["completed"],
        messages=[_assistant_message("Here is the answer.")],
    )
    provider = FoundryAgentProvider(_FakeProjectService(api_client), poll_interval_seconds=0)

    result = await provider.run(foundry_agent_id="agent-123", input_text="hello")

    assert result.output_text == "Here is the answer."
    assert result.raw_status == "completed"
    assert api_client.created_runs == [{"thread_id": "thread-1", "agent_id": "agent-123"}]
    assert api_client.created_messages == [
        {"thread_id": "thread-1", "role": "user", "content": "hello"}
    ]


async def test_run_polls_until_terminal_status():
    api_client = _FakeAgentApiClient(
        run_statuses=["queued", "in_progress", "completed"],
        messages=[_assistant_message("Done.")],
    )
    provider = FoundryAgentProvider(_FakeProjectService(api_client), poll_interval_seconds=0)

    result = await provider.run(foundry_agent_id="agent-123", input_text="hello")

    assert result.output_text == "Done."


async def test_run_raises_foundry_unavailable_for_non_completed_terminal_status():
    api_client = _FakeAgentApiClient(run_statuses=["failed"], messages=[])
    provider = FoundryAgentProvider(_FakeProjectService(api_client), poll_interval_seconds=0)

    with pytest.raises(FoundryUnavailableError, match="failed"):
        await provider.run(foundry_agent_id="agent-123", input_text="hello")


async def test_run_raises_foundry_unavailable_when_never_terminal():
    api_client = _FakeAgentApiClient(run_statuses=["in_progress"], messages=[])
    provider = FoundryAgentProvider(
        _FakeProjectService(api_client), poll_interval_seconds=0, max_poll_attempts=2
    )

    with pytest.raises(FoundryUnavailableError, match="did not reach a terminal status"):
        await provider.run(foundry_agent_id="agent-123", input_text="hello")


async def test_run_raises_foundry_unavailable_when_no_assistant_message():
    api_client = _FakeAgentApiClient(run_statuses=["completed"], messages=[])
    provider = FoundryAgentProvider(_FakeProjectService(api_client), poll_interval_seconds=0)

    with pytest.raises(FoundryUnavailableError, match="no assistant message"):
        await provider.run(foundry_agent_id="agent-123", input_text="hello")


async def test_run_wraps_unexpected_exceptions_as_foundry_unavailable():
    class _BoomProjectService:
        def get_api_client(self):
            raise RuntimeError("credential expired")

    provider = FoundryAgentProvider(_BoomProjectService(), poll_interval_seconds=0)

    with pytest.raises(FoundryUnavailableError, match="credential expired"):
        await provider.run(foundry_agent_id="agent-123", input_text="hello")
