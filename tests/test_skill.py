# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for `puba skill`."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from puba.cli import app

runner = CliRunner()


def test_skill_show_exits_zero():
    result = runner.invoke(app, ["skill", "show"])
    assert result.exit_code == 0, result.output


def test_skill_show_frontmatter():
    result = runner.invoke(app, ["skill", "show"])
    assert result.output.startswith("---\nname: publication-analysis")


def test_skill_export_creates_skill_md(tmp_path):
    dest = tmp_path / "publication-analysis"
    result = runner.invoke(app, ["skill", "export", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "SKILL.md").exists()


def test_skill_export_refuses_nonempty_without_force(tmp_path):
    dest = tmp_path / "publication-analysis"
    dest.mkdir()
    (dest / "existing.txt").write_text("block")
    result = runner.invoke(app, ["skill", "export", str(dest)])
    assert result.exit_code != 0
    assert (dest / "existing.txt").exists()


def test_skill_export_force_overwrites(tmp_path):
    dest = tmp_path / "publication-analysis"
    dest.mkdir()
    (dest / "existing.txt").write_text("old")
    result = runner.invoke(app, ["skill", "export", str(dest), "--force"])
    assert result.exit_code == 0, result.output
    assert (dest / "SKILL.md").exists()
    assert not (dest / "existing.txt").exists()
