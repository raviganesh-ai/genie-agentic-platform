"""HTTP API routers.

Implemented in Phase 7: every router is a thin FastAPI ``APIRouter`` that
authenticates the caller (``app.security.dependencies.get_current_user``),
resolves its service via ``app.api.dependencies``, and delegates all
business logic to that service. Wired into the application in
``app.main.create_app``.
"""
