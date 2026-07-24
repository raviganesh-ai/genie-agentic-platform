"""Fail-closed tests for NoHardcodingValidator."""
from __future__ import annotations

from app.validation.no_hardcoding_validator import NoHardcodingValidator


def test_passes_for_clean_config(local_settings):
    assert NoHardcodingValidator().validate(local_settings).passed


def test_fails_closed_when_connection_string_hardcoded(local_settings):
    bad_file = local_settings.agents_path / "leaked.yaml"
    bad_file.write_text(
        "connection_string: DefaultEndpointsProtocol=https;AccountName=x;AccountKey=abc123==\n",
        encoding="utf-8",
    )
    result = NoHardcodingValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_api_key_hardcoded(local_settings):
    bad_file = local_settings.prompts_path / "leaked.yaml"
    bad_file.write_text("api_key: 'sk-abcdefghijklmnopqrstuvwx'\n", encoding="utf-8")
    result = NoHardcodingValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_password_hardcoded(local_settings):
    bad_file = local_settings.workflows_path / "leaked.yaml"
    bad_file.write_text("password: SuperSecretValue123\n", encoding="utf-8")
    result = NoHardcodingValidator().validate(local_settings)
    assert not result.passed
