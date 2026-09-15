"""Persistence for the complete inventory of Genie-managed prototypes."""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from pydantic import ValidationError

from app.deploy_launch.models import DeploymentPipelineRun
from app.repositories.document_store import DocumentStore

__all__ = [
    "CosmosDeploymentRunRepository",
    "DeploymentRunRepository",
    "InMemoryDeploymentRunRepository",
]

_logger = logging.getLogger(__name__)
_PARTITION_KEY = "deployment-runs"
_METADATA_FIELDS = {"partitionKey", "recordType", "_rid", "_self", "_etag", "_attachments", "_ts"}


class DeploymentRunRepository(Protocol):
    async def put(self, run: DeploymentPipelineRun) -> None: ...

    async def list_all(self) -> list[DeploymentPipelineRun]: ...

    async def delete(self, *, pipeline_run_id: str) -> None: ...


class InMemoryDeploymentRunRepository:
    def __init__(self) -> None:
        self._runs: dict[str, DeploymentPipelineRun] = {}
        self._lock = asyncio.Lock()

    async def put(self, run: DeploymentPipelineRun) -> None:
        async with self._lock:
            self._runs[run.id] = run.model_copy(deep=True)

    async def list_all(self) -> list[DeploymentPipelineRun]:
        async with self._lock:
            return [run.model_copy(deep=True) for run in self._runs.values()]

    async def delete(self, *, pipeline_run_id: str) -> None:
        async with self._lock:
            self._runs.pop(pipeline_run_id, None)


class CosmosDeploymentRunRepository:
    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, run: DeploymentPipelineRun) -> None:
        document = run.model_dump(mode="json")
        document.update(
            {
                "id": run.id,
                "partitionKey": _PARTITION_KEY,
                "recordType": "deployment-run",
            }
        )
        await self._store.upsert(document)

    async def list_all(self) -> list[DeploymentPipelineRun]:
        documents = await self._store.query(
            query="SELECT * FROM c WHERE c.recordType = @recordType",
            parameters=[{"name": "@recordType", "value": "deployment-run"}],
            partition_key=_PARTITION_KEY,
        )
        runs: list[DeploymentPipelineRun] = []
        for document in documents:
            try:
                runs.append(
                    DeploymentPipelineRun.model_validate(
                        {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
                    )
                )
            except ValidationError:
                # A run persisted under a since-removed schema (e.g. an old
                # pipeline step id) must never crash startup for every other
                # run - skip it and keep going.
                _logger.warning(
                    "Skipping deployment run %s that no longer matches the current schema.",
                    document.get("id", "<unknown>"),
                )
        return runs

    async def delete(self, *, pipeline_run_id: str) -> None:
        await self._store.delete(document_id=pipeline_run_id, partition_key=_PARTITION_KEY)