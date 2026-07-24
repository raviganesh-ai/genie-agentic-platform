"""CLI: run the Phase 10A Foundry agent configuration validators and report the result.

Usage (from the ``backend`` virtual environment)::

    python scripts/validate_foundry_agents.py

Runs ``FoundryAgentRegistryValidator`` and ``FoundryAgentDriftValidator``
(configuration-only, no network access) against the currently configured
``Settings`` and prints every issue found. Both validators are no-ops
outside production mode (``GENIE_PROVIDER_MODE=production``), mirroring
``ProductionSafetyValidator``'s shape, so this script is most useful when
run with production-shaped environment variables set, e.g. in a CI/CD
deployment gate before promoting a new agent catalog.

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
