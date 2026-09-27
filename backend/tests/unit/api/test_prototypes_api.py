"""Unit tests for the self-service /prototypes/mine API routes.

Distinct from the genie-admin-only /api/admin/prototypes routes
(app.api.prototype_admin): these let an ordinary authenticated user see and
delete their OWN prototype runs so hitting the per-owner active-prototype
quota (DeploymentPipelineService.start's "Active prototype limit reached")
is self-serviceable instead of a dead end with no in-product recourse.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import prototypes
from app.api.dependencies import get_deployment_pipeline_service
from app.deploy_launch.models import DeploymentPipelineRun
from app.deploy_launch.pipeline_service import DeploymentPipelineStepFailedError
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user


def _run(run_id: str, owner_user_id: str) -> DeploymentPipelineRun:
    return DeploymentPipelineRun(
        id=run_id,
        session_id="session-1",
        workflow_run_id="workflow-1",
        owner_user_id=owner_user_id,
        status="failed",
    )


class _FakePipelineService:
    def __init__(self) -> None:
        self.runs: dict[str, DeploymentPipelineRun] = {}
        self.abandoned: list[str] = []
        self.abandon_error: Exception | None = None

    def list_runs_for_owner(self, owner_user_id: str) -> list[DeploymentPipelineRun]:
        return [run for run in self.runs.values() if run.owner_user_id == owner_user_id]

    def get_run(self, pipeline_run_id: str) -> DeploymentPipelineRun | None:
        return self.runs.get(pipeline_run_id)

    async def abandon(self, *, pipeline_run_id: str, mission_title: str | None = None) -> None:
        if self.abandon_error is not None:
            raise self.abandon_error
        self.abandoned.append(pipeline_run_id)


@pytest.fixture
def app_and_service() -> tuple[FastAPI, _FakePipelineService]:
    app = FastAPI()
    app.include_router(prototypes.router)
    service = _FakePipelineService()
    app.dependency_overrides[get_deployment_pipeline_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-a")
    return app, service


def test_list_my_prototypes_returns_only_the_authenticated_users_own_runs(
    app_and_service: tuple[FastAPI, _FakePipelineService],
) -> None:
    app, service = app_and_service
    service.runs["mine"] = _run("mine", "user-a")
    service.runs["other"] = _run("other", "user-b")

    with TestClient(app) as client:
        response = client.get("/prototypes/mine")

    assert response.status_code == 200
    assert [run["id"] for run in response.json()] == ["mine"]


def test_delete_my_prototype_rejects_a_run_owned_by_someone_else(
    app_and_service: tuple[FastAPI, _FakePipelineService],
) -> None:
    app, service = app_and_service
    service.runs["other"] = _run("other", "user-b")

    with TestClient(app) as client:
        response = client.delete("/prototypes/mine/other")

    assert response.status_code == 404
    assert service.abandoned == []


def test_delete_my_prototype_abandons_an_owned_run(
    app_and_service: tuple[FastAPI, _FakePipelineService],
) -> None:
    app, service = app_and_service
    service.runs["mine"] = _run("mine", "user-a")

    with TestClient(app) as client:
        response = client.delete("/prototypes/mine/mine")

    assert response.status_code == 204
    assert service.abandoned == ["mine"]


def test_delete_my_prototype_surfaces_a_running_run_as_a_conflict(
    app_and_service: tuple[FastAPI, _FakePipelineService],
) -> None:
    app, service = app_and_service
    service.runs["mine"] = _run("mine", "user-a")
    service.abandon_error = DeploymentPipelineStepFailedError(
        "A running Deploy & Launch run cannot be abandoned."
    )

    with TestClient(app) as client:
        response = client.delete("/prototypes/mine/mine")

    assert response.status_code == 409
