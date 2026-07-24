"""Unit tests for the shared YAML configuration loader helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.yaml_loader import YamlLoadError, iter_yaml_files, load_yaml_file


def test_iter_yaml_files_returns_empty_list_for_missing_directory(tmp_path: Path):
    assert iter_yaml_files(tmp_path / "missing") == []


def test_iter_yaml_files_filters_by_suffix(tmp_path: Path):
    (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "b.yml").write_text("b: 2\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("not yaml\n", encoding="utf-8")

    files = iter_yaml_files(tmp_path)

    assert [f.name for f in files] == ["a.yaml", "b.yml"]


def test_load_yaml_file_parses_valid_yaml(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("key: value\n", encoding="utf-8")

    assert load_yaml_file(path) == {"key": "value"}


def test_load_yaml_file_raises_on_invalid_yaml(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("key: [unbalanced\n", encoding="utf-8")

    with pytest.raises(YamlLoadError):
        load_yaml_file(path)


def test_load_yaml_file_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(YamlLoadError):
        load_yaml_file(tmp_path / "does-not-exist.yaml")
