# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for --json output on bib, md, and run commands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from puba.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(result) -> dict:
    """Assert output is valid JSON and return it."""
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"output is not valid JSON (exit={result.exit_code}):\n{result.output!r}"
        ) from exc


def _make_stub_bib(tmp_path: Path, needs_review: bool = False) -> Path:
    """Write a minimal bib.yaml into tmp_path/<name>.puba/bib.yaml."""
    puba_dir = tmp_path / "paper.puba"
    puba_dir.mkdir(parents=True, exist_ok=True)
    bib = {"title": "Test Paper", "needs_review": needs_review}
    (puba_dir / "bib.yaml").write_text(yaml.dump(bib), encoding="utf-8")
    return puba_dir / "bib.yaml"


# ---------------------------------------------------------------------------
# Preflight error paths (no mocking needed — PDF doesn't exist / wrong ext)
# ---------------------------------------------------------------------------

def test_bib_json_missing_pdf_emits_error_json(tmp_path):
    fake = tmp_path / "ghost.pdf"
    result = runner.invoke(app, ["bib", str(fake), "--json"])
    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert data["command"] == "bib"
    assert data["stage"] == "preflight"
    assert "error_type" in data


def test_md_json_wrong_extension_emits_error_json(tmp_path):
    txt = tmp_path / "paper.txt"
    txt.write_text("hello")
    result = runner.invoke(app, ["md", str(txt), "--json"])
    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert data["command"] == "md"
    assert data["stage"] == "preflight"


def test_run_json_missing_pdf_emits_error_json(tmp_path):
    fake = tmp_path / "ghost.pdf"
    result = runner.invoke(app, ["run", str(fake), "--json"])
    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert data["command"] == "run"
    assert data["stage"] == "preflight"


# ---------------------------------------------------------------------------
# --json + --dry-run collision
# ---------------------------------------------------------------------------

def test_bib_json_dry_run_collision_is_usage_error(tmp_path):
    fake = tmp_path / "paper.pdf"
    fake.write_bytes(b"%PDF-1.4")
    result = runner.invoke(app, ["bib", str(fake), "--json", "--dry-run"])
    data = _parse(result)
    assert result.exit_code == 2
    assert data["ok"] is False
    assert "mutually exclusive" in data["error"]


def test_md_json_dry_run_collision_is_usage_error(tmp_path):
    fake = tmp_path / "paper.pdf"
    fake.write_bytes(b"%PDF-1.4")
    result = runner.invoke(app, ["md", str(fake), "--json", "--dry-run"])
    data = _parse(result)
    assert result.exit_code == 2
    assert data["ok"] is False
    assert "mutually exclusive" in data["error"]


# ---------------------------------------------------------------------------
# Success shapes (mocked resolve / render)
# ---------------------------------------------------------------------------

def test_bib_json_success_shape_fresh(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path)

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["bib", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["command"] == "bib"
    assert data["cached"] is False
    assert data["needs_review"] is False
    assert "pdf" in data
    assert "analysis_dir" in data
    assert "bib_yaml" in data


def test_bib_json_success_shape_cached(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path, needs_review=True)

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, True)):
        result = runner.invoke(app, ["bib", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["cached"] is True
    assert data["needs_review"] is True


def test_md_json_success_shape(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    puba_dir = tmp_path / "paper.puba"
    puba_dir.mkdir()
    paper_md = puba_dir / "paper.md"

    with patch("puba.md.render.render", return_value=(paper_md, False)):
        result = runner.invoke(app, ["md", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["command"] == "md"
    assert data["cached"] is False
    assert "paper_md" in data
    assert "paper_raw_txt" in data
    assert "paper_sections_json" in data


def test_run_json_success_shape(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path)
    puba_dir = tmp_path / "paper.puba"
    paper_md = puba_dir / "paper.md"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)), \
         patch("puba.md.render.render", return_value=(paper_md, True)):
        result = runner.invoke(app, ["run", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["command"] == "run"
    assert "stages" in data
    assert data["stages"]["bib"]["ok"] is True
    assert data["stages"]["bib"]["cached"] is False
    assert data["stages"]["md"]["ok"] is True
    assert data["stages"]["md"]["cached"] is True


# ---------------------------------------------------------------------------
# Failure shapes (mocked raises)
# ---------------------------------------------------------------------------

def test_run_json_bib_failure_skips_md(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    with patch("puba.bib.stub.resolve", side_effect=RuntimeError("openalex down")), \
         patch("puba.md.render.render") as mock_render:
        result = runner.invoke(app, ["run", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 2
    assert data["ok"] is False
    assert data["stage"] == "bib"
    assert data["stages"]["bib"]["ok"] is False
    assert "openalex down" in data["stages"]["bib"]["error"]
    assert "md" not in data["stages"]
    mock_render.assert_not_called()


def test_run_json_md_failure_after_bib_success(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path)

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)), \
         patch("puba.md.render.render", side_effect=ValueError("extraction failed")):
        result = runner.invoke(app, ["run", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert data["stage"] == "md"
    assert data["stages"]["bib"]["ok"] is True
    assert data["stages"]["md"]["ok"] is False
    assert "extraction failed" in data["stages"]["md"]["error"]


def test_bib_json_runtime_error_exits_2(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    with patch("puba.bib.stub.resolve", side_effect=RuntimeError("network error")):
        result = runner.invoke(app, ["bib", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 2
    assert data["ok"] is False
    assert data["error_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# stdout is only JSON; no Rich markup leaks
# ---------------------------------------------------------------------------

def test_json_stdout_is_only_json_bib(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path)

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["bib", str(pdf), "--json"])

    parsed = json.loads(result.output)
    assert isinstance(parsed, dict)
    assert result.output.strip().startswith("{")
    assert result.output.strip().endswith("}")


def test_json_suppresses_needs_review_stderr_warning(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path, needs_review=True)

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, True)):
        result = runner.invoke(app, ["bib", str(pdf), "--json"])

    assert "Warning" not in result.output
    data = _parse(result)
    assert data["needs_review"] is True


# ---------------------------------------------------------------------------
# (cached) indicator in non-JSON output
# ---------------------------------------------------------------------------

def test_bib_non_json_shows_cached_when_stage_skipped(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path)

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, True)):
        result = runner.invoke(app, ["bib", str(pdf)])

    assert "(cached)" in result.output


def test_bib_non_json_no_cached_tag_when_fresh(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path)

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["bib", str(pdf)])

    assert "(cached)" not in result.output


def test_run_non_json_marks_each_stage_independently(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    bib_yaml = _make_stub_bib(tmp_path)
    puba_dir = tmp_path / "paper.puba"
    paper_md = puba_dir / "paper.md"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, True)), \
         patch("puba.md.render.render", return_value=(paper_md, False)):
        result = runner.invoke(app, ["run", str(pdf)])

    output = result.output
    assert "(cached)" in output
    assert output.count("(cached)") == 1
    md_line = next(l for l in output.splitlines() if "md" in l and "✓" in l)
    assert "(cached)" not in md_line
