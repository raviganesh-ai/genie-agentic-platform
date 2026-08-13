"""CLI: run the Phase 10A Foundry agent configuration validators and report the result.

Usage (from the ``backend`` virtual environment)::

    python scripts/validate_foundry_agents.py

Runs ``FoundryAgentRegistryValidator`` and ``FoundryAgentDriftValidator``
(configuration-only, no network access) against the currently configured
``Settings`` and prints every issue found. Genie is a personal dev/demo
deployment with no separate production tier, so both validators are
currently permanent no-ops (see their module docstrings) - kept as a
distinct, separately invoked pair for a future need to enforce Foundry
agent metadata (e.g. before a mission's agents are provisioned into a real
Foundry project).

Exits non-zero if either validator fails.
"""
from __future__ import annotations

import sys

from app.config.settings import get_settings
from app.validation.foundry_agent_drift_validator import FoundryAgentDriftValidator
from app.validation.foundry_agent_registry_validator import FoundryAgentRegistryValidator


def main() -> None:
    settings = get_settings()

    results = [
        FoundryAgentRegistryValidator().validate(settings),
        FoundryAgentDriftValidator().validate(settings),
    ]

    exit_code = 0
    for result in results:
        if result.passed:
            print(f"[PASS] {result.validator}")
            continue
        exit_code = 1
        print(f"[FAIL] {result.validator}")
        for issue in result.issues:
            print(f"  - {issue.message}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
