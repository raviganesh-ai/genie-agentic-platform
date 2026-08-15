"""Fail-closed startup validation package.

Every validator in this package implements the ``StartupValidator``
protocol and is executed by ``StartupValidationRunner`` before the
application is allowed to accept traffic, per the Validation Requirements
and Fail Closed Requirements in ``.github/copilot-instructions.md``.
"""
