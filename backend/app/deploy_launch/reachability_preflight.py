"""Real, single-shot reachability probe reused by pre-deploy safety checks.

Phase E of the Genie platform-change roadmap: before a Container Apps
revision mutation that could cut off a mission's live traffic (a backend
or frontend redeploy - ``begin_create_or_update`` replaces the active
revision in the single-revision mode this platform uses, with no
automatic rollback if the new revision never becomes healthy), record
whether the resource *already being there* was itself reachable right
before the mutation. That lets a caller distinguish, after the fact, "this
mission had no prior healthy deployment at all (a first deploy that never
came up)" from the much more serious "this mission was serving healthy
traffic and this action may have just taken it down" - two very different
situations that look identical today (both surface as the same generic
readiness-timeout error).

Deliberately a single bounded probe, not a polling loop like
``BackendDeploymentService._wait_for_readiness`` (which exists to wait for
a *new* deployment to come up) - this exists only to snapshot whether an
*existing* resource was healthy a moment before a mutation, so it must
stay fast and never block the deploy on a slow/flaky prior resource.
"""
from __future__ import annotations

__all__ = ["probe_reachability"]

_PROBE_TIMEOUT_SECONDS = 10


async def probe_reachability(base_url: str) -> bool:
    """Single real HTTP GET to ``{base_url}/health/ready``.

    Returns ``True`` only for an HTTP 200 response whose JSON body is
    exactly ``{"status": "ready"}`` (the same contract
    ``_wait_for_readiness`` already requires of every generated mission
    backend's ``main.py`` scaffold - see
    ``app.deploy_launch.code_materializer._MAIN_PY_TEMPLATE``). Any other
    outcome - a non-200 response, an unreachable host, a timeout, or a
    response that is not valid JSON - returns ``False``. Never raises:
    this is a safety probe, not a hard precondition, so a probe failure
    must never itself block or crash the caller's own deploy flow.
    """

    import httpx

    readiness_url = f"{base_url.rstrip('/')}/health/ready"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(readiness_url)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return payload.get("status") == "ready"
