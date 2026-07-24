"""Scans externalized configuration for hardcoded secrets or forbidden values.

Configuration files must never embed secrets, connection strings, or API
keys directly, per the Configuration Rules in
``.github/copilot-instructions.md``. Anything sensitive must instead be
resolved at runtime from Azure Key Vault via managed identity.
"""
from __future__ import annotations

import re

from app.config.settings import Settings
from app.validation.base import ValidationResult

_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Azure Storage connection string",
        re.compile(r"DefaultEndpointsProtocol=", re.IGNORECASE),
    ),
    ("Embedded account key", re.compile(r"AccountKey=", re.IGNORECASE)),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    (
        "Generic API key / secret / password assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_-]{8,}"
        ),
    ),
)


class NoHardcodingValidator:
    """Fails closed if externalized configuration files embed secrets directly."""

    name = "NoHardcodingValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        root = settings.config_root
        if not root.is_dir():
            # ConfigurationValidator is responsible for reporting a missing
            # config_root; there is nothing to scan here.
            return ValidationResult.ok(self.name)

        errors: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for label, pattern in _FORBIDDEN_PATTERNS:
                if pattern.search(text):
                    errors.append(f"{label} found hardcoded in '{path}'.")

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
