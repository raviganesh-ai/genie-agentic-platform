"""Integration test: ``GET /sessions/{id}/workflow-events/stream`` (SSE).

Exercises the route's auth and session-ownership wiring through the real
ASGI app (same pattern as every other per-session route test). The
streaming generator's own event-delivery/keepalive/disconnect behavior is
covered directly (and more reliably) by
``tests/unit/api/test_workflow_events_stream.py``: ``httpx.ASGITransport``
buffers an entire ASGI response body before returning it to the client, so
it cannot exercise genuinely concurrent "publish while a live SSE
connection is open" behavior without a real socket-based server - not
worth the added weight/fragility here.
"""
from __future__ import annotations

import jwt
from httpx import ASGITransport, AsyncClient

from app.main import create_app


def _bearer_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, "unit-test-secret", algorithm="HS256")


async def test_stream_requires_authentication(local_settings) -> None:
    app = create_app(settings=local_settings)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/sessions/does-not-exist/workflow-events/stream")
            assert resp.status_code == 401


async def test_stream_requires_a_session_owned_by_the_caller(local_settings) -> None:
    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/sessions/does-not-exist/workflow-events/stream", headers=headers
            )
            assert resp.status_code == 404

