"""Integration test: Deploy & Launch API routes wiring.

Exercises auth/session-ownership through the real ASGI app (confirms
``app.main.create_app`` wires ``DeploymentPipelineService`` onto
``app.state`` correctly) - same minimal pattern as
``test_workflow_events_stream_api.py``. Full pipeline execution behavior
is covered by ``tests/unit/deploy_launch/test_pipeline_service.py``.
"""
from __future__ import annotations

import jwt
from httpx import ASGITransport, AsyncClient

from app.main import create_app


def _bearer_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, "unit-test-secret", algorithm="HS256")


async def test_start_requires_authentication(local_settings) -> None:
    app = create_app(settings=local_settings)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/sessions/does-not-exist/deploy-launch/start", json={"workflow_run_id": "run-1"}
            )
            assert resp.status_code == 401


async def test_start_requires_a_session_owned_by_the_caller(local_settings) -> None:
    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/sessions/does-not-exist/deploy-launch/start",
                json={"workflow_run_id": "run-1"},
                headers=headers,
            )
            assert resp.status_code == 404


async def test_list_deployments_requires_a_session_owned_by_the_caller(local_settings) -> None:
    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/sessions/does-not-exist/deploy-launch/", headers=headers
            )
            assert resp.status_code == 404
