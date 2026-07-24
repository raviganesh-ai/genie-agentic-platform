"""Fail-closed tests for PromptTemplateValidator."""
from __future__ import annotations

import shutil

from app.validation.prompt_template_validator import PromptTemplateValidator


def test_passes_when_registry_populated(local_settings):
    assert PromptTemplateValidator().validate(local_settings).passed


def test_fails_closed_when_directory_missing(local_settings):
    shutil.rmtree(local_settings.prompts_path)
    result = PromptTemplateValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_directory_empty(local_settings):
    for item in local_settings.prompts_path.iterdir():
        item.unlink()
    result = PromptTemplateValidator().validate(local_settings)
    assert not result.passed
