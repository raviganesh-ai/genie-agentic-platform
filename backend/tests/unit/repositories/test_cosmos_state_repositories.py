from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.deploy_launch.models import DeploymentPipelineRun
from app.models.session_models import Session
from app.repositories.deployment_run_repository import CosmosDeploymentRunRepository
from app.repositories.session_repository import CosmosSessionRepository


class _FakeDocumentStore:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.documents[(document["partitionKey"], document["id"])] = dict(document)

    async def read(self, *, document_id: str, partition_key: str) -> dict[str, Any] | None:
        return self.documents.get((partition_key, document_id))

    async def query(
        self,
        *,
        query: str,
        parameters: list[dict[str, Any]],
        partition_key: str,
    ) -> list[dict[str, Any]]:
        del query
        values = {item["name"]: item["value"] for item in parameters}
        return [
            document
            for (key, _), document in self.documents.items()
            if key == partition_key
            and document["recordType"] == values["@recordType"]
            and (
                "@ownerUserId" not in values
                or document["owner_user_id"] == values["@ownerUserId"]
            )
        ]

    async def delete(self, *, document_id: str, partition_key: str) -> None:
        self.documents.pop((partition_key, document_id), None)

    async def close(self) -> None:
        return None


async def test_cosmos_session_repository_round_trips_canonical_owner() -> None:
    store = _FakeDocumentStore()
    repository = CosmosSessionRepository(store=store)
    now = datetime.now(UTC)
    session = Session(
        id="session-1",
        owner_user_id="tenant-1:object-1",
        title="Corporate prototype",
        created_at=now,
        updated_at=now,
    )

    await repository.put(session)

    assert await repository.get(session_id=session.id) == session
    assert await repository.list_for_owner(owner_user_id=session.owner_user_id) == [session]
    assert await repository.list_for_owner(owner_user_id="tenant-2:object-1") == []


async def test_cosmos_deployment_repository_round_trips_owner_and_ttl() -> None:
    store = _FakeDocumentStore()
    repository = CosmosDeploymentRunRepository(store=store)
    now = datetime.now(UTC)
    run = DeploymentPipelineRun(
        id="prototype-1",
        session_id="session-1",
        workflow_run_id="workflow-1",
        owner_user_id="tenant-1:object-1",
        owner_tenant_id="tenant-1",
        owner_object_id="object-1",
        status="completed",
        expires_at=now + timedelta(days=7),
        created_at=now,
        updated_at=now,
    )

    await repository.put(run)

    assert await repository.list_all() == [run]
    await repository.delete(pipeline_run_id=run.id)
    assert await repository.list_all() == []