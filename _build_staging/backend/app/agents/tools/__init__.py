"""Concrete function-tool implementations for Genie's Foundry agents.

Each module here registers one agent's real tool(s) into an
``AgentToolRegistry`` (``app.agents.tool_execution``), bound to already
existing domain services (memory, governance, customer agent
provisioning) - never inventing new persistence or business logic. See
``build_default_tool_registry`` in ``app.agents.tools.registration`` for
the single wiring entry point.
"""
from __future__ import annotations
