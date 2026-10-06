"""Password hashing and the newline-delimited user-record format.

Uses only the standard library (PBKDF2-HMAC-SHA256 via ``hashlib``) so
first-party authentication adds no new dependency. Records are configured
as a single environment variable value (see
``Settings.auth_users_env_var``) rather than a database, since Genie's
platform authentication is intentionally small: one line per account,
``username:pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

__all__ = ["hash_password", "verify_password", "parse_user_records"]

_ALGORITHM = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """Return an encoded ``algorithm$iterations$salt$hash`` record for ``password``."""

    if not password:
        raise ValueError("password must not be empty.")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Return whether ``password`` matches a record produced by :func:`hash_password`.

    Never raises on a malformed ``encoded`` value - treats it as a
    non-match, since a corrupt/foreign record must never be able to grant
    access.
    """

    try:
        algorithm, iterations_str, salt_hex, digest_hex = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def parse_user_records(raw: str) -> dict[str, str]:
    """Parse ``Settings.auth_users_env_var``'s referenced value into ``{username: encoded_hash}``.

    One record per non-blank line, ``username:encoded_hash``. Later
    duplicate usernames overwrite earlier ones (last line wins) rather than
    raising, so a deployment can append a corrected line without needing to
    remove the old one.
    """

    users: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        username, separator, encoded_hash = stripped.partition(":")
        if not separator or not username or not encoded_hash:
            raise ValueError(
                f"Malformed user record (expected 'username:encoded_hash'): {stripped!r}"
            )
        users[username] = encoded_hash
    return users
