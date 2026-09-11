"""Integration test: ``GET /sessions/{id}/workflow-events/stream`` (SSE).

Exercises the route's internal identity and session-validation wiring through the real
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

from httpx import ASGITransport, AsyncClient

from app.main import create_app


async def test_stream_allows_anonymous_internal_access(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/sessions/does-not-exist/workflow-events/stream")
            assert resp.status_code == 404


async def test_stream_requires_a_valid_session(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/sessions/does-not-exist/workflow-events/stream")
            assert resp.status_code == 404

