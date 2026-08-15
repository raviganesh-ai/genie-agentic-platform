"""Memory service facade: wires policy, governance, repositories, and stores.

``create_memory_service`` is the single entry point future callers (the
Phase 6 ``AgentOrchestrator``, Phase 7 API routes) should use to obtain a
ready-to-use ``MemoryService`` - mirroring the ``create_agent_gateway``
factory pattern established in Phase 3.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.memory.enterprise_knowledge_store import EnterpriseKnowledgeStore
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.memory.memory_events import MemoryGovernanceRecorder
from app.memory.personal_memory_store import PersonalMemoryStore
from app.memory.shared_memory_store import SharedMemoryStore
from app.repositories.enterprise_memory_repository import (
    EnterpriseMemoryRepository,
    InMemoryEnterpriseKnowledgeRepository,
)
from app.repositories.personal_memory_repository import (
    InMemoryPersonalMemoryRepository,
    PersonalMemoryRepository,
)
from app.repositories.shared_memory_repository import (
    InMemorySharedMemoryRepository,
    SharedMemoryRepository,
)

__all__ = ["MemoryService", "create_memory_service"]


class MemoryService:
    """Groups the three memory tier stores behind one object for convenient DI."""

    def __init__(
        self,
        *,
        personal: PersonalMemoryStore,
        shared: SharedMemoryStore,
        enterprise: EnterpriseKnowledgeStore,
    ) -> None:
        self.personal = personal
        self.shared = shared
        self.enterprise = enterprise


def create_memory_service(
    *,
    settings: Settings,
    governance_recorder: MemoryGovernanceRecorder | None = None,
    personal_repository: PersonalMemoryRepository | None = None,
    shared_repository: SharedMemoryRepository | None = None,
    enterprise_repository: EnterpriseMemoryRepository | None = None,
) -> MemoryService:
    """Build a ``MemoryService`` wired to the externally configured memory policy.

    Raises ``MemoryPolicyError`` (fail closed) if the memory policy
    configuration is missing or invalid. Repository parameters default to
    the in-memory reference implementations (local/dev/test only); pass
    real ``Settings.memory_store_backend``-specific implementations for a
    durable production deployment.
    """

    policy_service = MemoryAccessPolicyService.load(settings.policies_path)

    personal_repo = personal_repository or InMemoryPersonalMemoryRepository()
    shared_repo = shared_repository or InMemorySharedMemoryRepository()
    enterprise_repo = enterprise_repository or InMemoryEnterpriseKnowledgeRepository()

    return MemoryService(
        personal=PersonalMemoryStore(personal_repo, policy_service, governance_recorder),
        shared=SharedMemoryStore(shared_repo, policy_service, governance_recorder),
        enterprise=EnterpriseKnowledgeStore(enterprise_repo, policy_service, governance_recorder),
    )
