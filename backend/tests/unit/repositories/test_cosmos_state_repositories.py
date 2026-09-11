from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.deploy_launch.models import DeploymentPipelineRun
from app.models.session_models import Session
from app.models.upload_models import UploadRecord
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult
from app.repositories.deployment_run_repository import CosmosDeploymentRunRepository
from app.repositories.session_repository import CosmosSessionRepository
from app.repositories.upload_repository import CosmosUploadRepository
from app.repositories.workflow_run_repository import CosmosWorkflowRunRepository


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
            and (
                "@sessionId" not in values
                or document["session_id"] == values["@sessionId"]
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


async def test_cosmos_workflow_repository_survives_repository_recreation() -> None:
    store = _FakeDocumentStore()
    now = datetime.now(UTC)
    run = WorkflowRunResult(
        workflow_run_id="workflow-run-1",
        workflow_id="solution-discovery-workflow",
        session_id="session-1",
        status="waiting_for_proceed",
        waves=[["analyze-requirements"]],
        step_results=[
            WorkflowStepResult(
                step_id="analyze-requirements",
                agent_id="genie-orchestrator",
                status="completed",
                output_text="Approved requirements",
                started_at=now,
                completed_at=now,
            )
        ],
    )

    await CosmosWorkflowRunRepository(store=store).put(run)
    restarted_repository = CosmosWorkflowRunRepository(store=store)

    assert await restarted_repository.get(workflow_run_id=run.workflow_run_id) == run
    assert await restarted_repository.list_for_session(session_id=run.session_id) == [run]
    assert await restarted_repository.list_for_session(session_id="session-2") == []


async def test_cosmos_upload_repository_preserves_extracted_text_after_restart() -> None:
    store = _FakeDocumentStore()
    now = datetime.now(UTC)
    upload = UploadRecord(
        id="upload-1",
        session_id="session-1",
        upload_type="transcript",
        file_name="discovery.txt",
        content_type="text/plain",
        size_bytes=18,
        uploaded_by="genie-internal-user",
        status="completed",
        transcript_text="Original transcript",
        uploaded_at=now,
        updated_at=now,
    )

    await CosmosUploadRepository(store=store).put(upload)
    restarted_repository = CosmosUploadRepository(store=store)

    assert await restarted_repository.get(upload_id=upload.id) == upload
    assert await restarted_repository.list_for_session(session_id=upload.session_id) == [upload]
    assert await restarted_repository.list_for_session(session_id="session-2") == []
