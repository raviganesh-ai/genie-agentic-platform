"""Tests for Genie's deterministic internal request principal."""

from app.security.dependencies import GENIE_ADMIN_ROLE, get_current_user


async def test_current_user_is_fixed_internal_admin_and_returns_an_isolated_copy() -> None:
    first = await get_current_user()
    second = await get_current_user()

    assert first.user_id == "genie-internal-user"
    assert first.object_id == "genie-internal-user"
    assert first.display_name == "Genie Internal User"
    assert first.roles == [GENIE_ADMIN_ROLE]
    assert first is not second

    first.roles.clear()
    assert second.roles == [GENIE_ADMIN_ROLE]