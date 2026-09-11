"""Workflow run repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for local
development and tests; Cosmos DB / Azure SQL for a later phase, per the
Architecture Principles in ``.github/copilot-instructions.md``) can change
without touching ``WorkflowExecutionService`` or any caller - mirroring
every other repository in ``app.repositories``.

Durably persisting every ``WorkflowRunResult`` (including its full
``step_results``) as soon as a wave completes, a run pauses for approval,
fails, or completes is what makes a stalled/never-started stage
recoverable: even if the client that was supposed to kick off the next
step never reaches the server (a dropped network request, a suspended
browser tab, ...), or the backend process itself restarts mid-mission, the
last durably persisted run/step state is never lost - a caller can always
look it up (``GET /workflows/runs/{id}``) and resume from exactly where it
left off (``POST /workflows/runs/{id}/resume``) instead of losing all
prior progress.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.models.workflow_models import WorkflowRunResult
from app.repositories.document_store import DocumentStore

__all__ = [
    "CosmosWorkflowRunRepository",
    "InMemoryWorkflowRunRepository",
    "WorkflowRunRepository",
]

_PARTITION_KEY = "workflow-runs"
_METADATA_FIELDS = {
    "id",
    "partitionKey",
    "recordType",
    "_rid",
    "_self",
    "_etag",
    "_attachments",
    "_ts",
}


class WorkflowRunRepository(Protocol):
    """Storage for ``WorkflowRunResult`` records (one entry per run, latest known state)."""

    async def put(self, result: WorkflowRunResult) -> None:
        """Insert or replace a workflow run's current (possibly still in-progress) result."""
        ...

    async def get(self, *, workflow_run_id: str) -> WorkflowRunResult | None:
        """Fetch a single workflow run's latest persisted result, or ``None`` if unknown."""
        ...

    async def list_for_session(self, *, session_id: str) -> list[WorkflowRunResult]:
        """List every workflow run recorded for ``session_id``, in the order first stored."""
        ...


class InMemoryWorkflowRunRepository:
    """Process-local ``WorkflowRunRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend (Cosmos DB /
    Azure SQL per the Architecture Principles in
    ``.github/copilot-instructions.md``).
    """

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRunResult] = {}
        self._run_ids_by_session: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def put(self, result: WorkflowRunResult) -> None:
        async with self._lock:
            self._runs[result.workflow_run_id] = result
            run_ids = self._run_ids_by_session.setdefault(result.session_id, [])
            if result.workflow_run_id not in run_ids:
                run_ids.append(result.workflow_run_id)

    async def get(self, *, workflow_run_id: str) -> WorkflowRunResult | None:
        async with self._lock:
            return self._runs.get(workflow_run_id)

    async def list_for_session(self, *, session_id: str) -> list[WorkflowRunResult]:
        async with self._lock:
            return [
                self._runs[run_id]
                for run_id in self._run_ids_by_session.get(session_id, [])
                if run_id in self._runs
            ]


class CosmosWorkflowRunRepository:
    """Durable workflow checkpoints in Genie's managed-identity Cosmos container."""

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, result: WorkflowRunResult) -> None:
        document = result.model_dump(mode="json")
        document.update(
            {
                "id": result.workflow_run_id,
                "partitionKey": _PARTITION_KEY,
                "recordType": "workflow-run",
            }
        )
        await self._store.upsert(document)

    async def get(self, *, workflow_run_id: str) -> WorkflowRunResult | None:
        document = await self._store.read(
            document_id=workflow_run_id,
            partition_key=_PARTITION_KEY,
        )
        if document is None:
            return None
        return self._to_model(document)

    async def list_for_session(self, *, session_id: str) -> list[WorkflowRunResult]:
        documents = await self._store.query(
            query=(
                "SELECT * FROM c WHERE c.recordType = @recordType "
                "AND c.session_id = @sessionId"
            ),
            parameters=[
                {"name": "@recordType", "value": "workflow-run"},
                {"name": "@sessionId", "value": session_id},
            ],
            partition_key=_PARTITION_KEY,
        )
        return [self._to_model(document) for document in documents]

    @staticmethod
    def _to_model(document: dict[str, object]) -> WorkflowRunResult:
        return WorkflowRunResult.model_validate(
            {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
        )
