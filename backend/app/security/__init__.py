"""Authentication, authorization, and Microsoft Entra ID integration.

Implemented in Phase 7 (APIs): ``token_validator`` selects between a real
Microsoft Entra ID validator and a local-dev validator (mirroring the
``AgentGateway`` seam from Phase 3); ``dependencies.get_current_user`` is
the FastAPI dependency every Phase 7 API route uses to authenticate
requests.
"""
