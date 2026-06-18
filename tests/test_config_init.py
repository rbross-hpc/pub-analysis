# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for `puba config init`."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from puba.cli import app
from puba import config as cfg

runner = CliRunner()


def test_config_init_creates_file(tmp_path):
    dest = tmp_path / "puba.config.yaml"
    result = runner.invoke(app, ["config", "init", "--path", str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.exists()


def test_config_init_default_destination(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "puba.config.yaml").exists()


def test_config_init_into_directory(tmp_path):
    result = runner.invoke(app, ["config", "init", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "puba.config.yaml").exists()


def test_config_init_content_is_valid_yaml(tmp_path):
    dest = tmp_path / "puba.config.yaml"
    runner.invoke(app, ["config", "init", "--path", str(dest)])
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "bib" in data
    assert "models" in data
    assert "md" in data


def test_config_init_content_matches_packaged(tmp_path):
    dest = tmp_path / "puba.config.yaml"
    runner.invoke(app, ["config", "init", "--path", str(dest)])
    packaged = cfg.packaged_config_path().read_bytes()
    assert dest.read_bytes() == packaged


def test_config_init_refuses_overwrite_without_force(tmp_path):
    dest = tmp_path / "puba.config.yaml"
    dest.write_text("existing: true\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "init", "--path", str(dest)])
    assert result.exit_code == 1
    assert dest.read_text(encoding="utf-8") == "existing: true\n"


def test_config_init_force_overwrites(tmp_path):
    dest = tmp_path / "puba.config.yaml"
    dest.write_text("existing: true\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "init", "--path", str(dest), "--force"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert "bib" in data


def test_config_init_quiet_produces_no_output(tmp_path):
    dest = tmp_path / "puba.config.yaml"
    result = runner.invoke(app, ["config", "init", "--path", str(dest), "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_config_init_warns_on_nonstandard_filename(tmp_path):
    dest = tmp_path / "custom.yaml"
    result = runner.invoke(app, ["config", "init", "--path", str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.exists()
    assert "Warning" in result.output or "puba.config.yaml" in result.output


def test_config_init_creates_parent_dirs(tmp_path):
    dest = tmp_path / "nested" / "deep" / "puba.config.yaml"
    result = runner.invoke(app, ["config", "init", "--path", str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.exists()


def test_packaged_config_path_returns_existing_file():
    p = cfg.packaged_config_path()
    assert p.exists()
    assert p.name == "config.yaml"


def test_local_config_path_returns_puba_config_yaml():
    p = cfg.local_config_path()
    assert p.name == "puba.config.yaml"
