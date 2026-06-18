# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for `puba show bib/md/sections/info`."""
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

def _parse(result) -> dict | list:
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"output is not valid JSON (exit={result.exit_code}):\n{result.output!r}"
        ) from exc


def _make_analysis_dir(tmp_path: Path, needs_review: bool = False) -> tuple[Path, Path]:
    """Create paper.pdf and paper.puba/ with stub bib.yaml and paper.md."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    puba_dir = tmp_path / "paper.puba"
    puba_dir.mkdir()
    (puba_dir / "analyses").mkdir()

    bib = {
        "title": "Test Paper on Things",
        "authors": ["Alice Smith", "Bob Jones"],
        "year": 2026,
        "venue": "Journal of Testing",
        "category": "journal article",
        "doi": "10.1234/test.2026",
        "arxiv_id": None,
        "osti_id": None,
        "url": None,
        "abstract": "We tested things.",
        "needs_review": needs_review,
        "_provenance": {
            "title": {"source": "openalex", "lookup_key": "doi", "at": "2026-01-01"},
            "doi": {"source": "pdf", "lookup_key": "regex", "at": "2026-01-01"},
        },
        "_meta": {"schema_version": 1, "tool_version": "0.1.0"},
    }
    (puba_dir / "bib.yaml").write_text(yaml.dump(bib), encoding="utf-8")

    sections = [
        {"title": "Abstract", "short_name": "abstract", "level": 1, "start": 0, "end": 100},
        {"title": "Introduction", "short_name": "introduction", "level": 1, "start": 100, "end": 400},
    ]
    (puba_dir / "paper.sections.json").write_text(json.dumps(sections), encoding="utf-8")

    md_text = "# Test Paper on Things\n\n## Abstract\n\nWe tested things.\n"
    (puba_dir / "paper.md").write_text(md_text, encoding="utf-8")
    (puba_dir / "paper.raw.txt").write_text("raw text", encoding="utf-8")

    return pdf, puba_dir


# ---------------------------------------------------------------------------
# show bib — preflight errors
# ---------------------------------------------------------------------------

def test_show_bib_missing_pdf_emits_error_json(tmp_path):
    fake = tmp_path / "ghost.pdf"
    result = runner.invoke(app, ["show", "bib", str(fake), "--json"])
    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert data["stage"] == "preflight"


def test_show_bib_no_run_without_cache_errors(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = runner.invoke(app, ["show", "bib", str(pdf), "--json", "--no-run"])
    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert "CacheError" in data["error_type"]


# ---------------------------------------------------------------------------
# show bib — success (mocked resolve)
# ---------------------------------------------------------------------------

def test_show_bib_json_success_shape(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["show", "bib", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["command"] == "show.bib"
    assert data["cached"] is False
    assert "bib" in data
    assert "provenance" in data
    assert data["bib"]["title"] == "Test Paper on Things"
    assert data["bib"]["doi"] == "10.1234/test.2026"
    assert "conflicts" not in data
    assert "lookup_log" not in data


def test_show_bib_json_verbose_includes_meta(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, True)):
        result = runner.invoke(app, ["show", "bib", str(pdf), "--json", "--verbose"])

    data = _parse(result)
    assert result.exit_code == 0
    assert "conflicts" in data
    assert "lookup_log" in data
    assert "meta" in data
    assert data["cached"] is True


def test_show_bib_json_needs_review_flag(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path, needs_review=True)
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["show", "bib", str(pdf), "--json"])

    data = _parse(result)
    assert data["needs_review"] is True


def test_show_bib_rich_output_contains_title(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["show", "bib", str(pdf)])

    assert result.exit_code == 0
    assert "Test Paper on Things" in result.output


def test_show_bib_rich_cached_tag(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, True)):
        result = runner.invoke(app, ["show", "bib", str(pdf)])

    assert "(cached)" in result.output


def test_show_bib_no_run_succeeds_when_cached(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, True)), \
         patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "bib", str(pdf), "--json", "--no-run"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True


# ---------------------------------------------------------------------------
# show md — success (mocked render)
# ---------------------------------------------------------------------------

def test_show_md_json_paths_shape(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    paper_md = puba_dir / "paper.md"

    with patch("puba.md.render.render", return_value=(paper_md, False)):
        result = runner.invoke(app, ["show", "md", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["command"] == "show.md"
    assert "paper_md" in data
    assert "paper_raw_txt" in data
    assert "paper_sections_json" in data
    assert "content" not in data
    assert "sections" not in data


def test_show_md_json_include_content(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    paper_md = puba_dir / "paper.md"

    with patch("puba.md.render.render", return_value=(paper_md, False)):
        result = runner.invoke(app, ["show", "md", str(pdf), "--json", "--include-content"])

    data = _parse(result)
    assert result.exit_code == 0
    assert "content" in data
    assert "Test Paper on Things" in data["content"]
    assert "sections" in data
    assert isinstance(data["sections"], list)
    assert len(data["sections"]) == 2
    assert data["sections"][0]["short_name"] == "abstract"


def test_show_md_include_content_without_json_is_error(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    paper_md = puba_dir / "paper.md"

    with patch("puba.md.render.render", return_value=(paper_md, False)):
        result = runner.invoke(app, ["show", "md", str(pdf), "--include-content"])

    assert result.exit_code == 2
    data = _parse(result)
    assert data["ok"] is False
    assert "UsageError" in data["error_type"]


def test_show_md_rich_output_is_raw_markdown(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    paper_md = puba_dir / "paper.md"

    with patch("puba.md.render.render", return_value=(paper_md, False)):
        result = runner.invoke(app, ["show", "md", str(pdf)])

    assert result.exit_code == 0
    assert "# Test Paper on Things" in result.output


def test_show_md_no_run_without_cache_errors(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = runner.invoke(app, ["show", "md", str(pdf), "--json", "--no-run"])
    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert "CacheError" in data["error_type"]


# ---------------------------------------------------------------------------
# show sections
# ---------------------------------------------------------------------------

def test_show_sections_json(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    paper_md = puba_dir / "paper.md"

    with patch("puba.md.render.render", return_value=(paper_md, True)):
        result = runner.invoke(app, ["show", "sections", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert isinstance(data, list)
    assert data[0]["short_name"] == "abstract"


def test_show_sections_rich_table(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)
    paper_md = puba_dir / "paper.md"

    with patch("puba.md.render.render", return_value=(paper_md, True)):
        result = runner.invoke(app, ["show", "sections", str(pdf)])

    assert result.exit_code == 0
    assert "abstract" in result.output
    assert "introduction" in result.output.lower()


# ---------------------------------------------------------------------------
# show info
# ---------------------------------------------------------------------------

def test_show_info_json_shape(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)

    result = runner.invoke(app, ["show", "info", str(pdf), "--json"])
    data = _parse(result)
    assert result.exit_code == 0
    assert "pdf" in data
    assert "state" in data
    assert "bib" in data
    assert "distillations" in data
    assert data["bib"]["title"] == "Test Paper on Things"


def test_show_info_rich_output(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path)

    result = runner.invoke(app, ["show", "info", str(pdf)])
    assert result.exit_code == 0
    assert "Test Paper on Things" in result.output


# ---------------------------------------------------------------------------
# Former top-level commands are gone
# ---------------------------------------------------------------------------

def test_top_level_info_command_removed():
    result = runner.invoke(app, ["info", "--help"])
    assert result.exit_code != 0


def test_top_level_sections_command_removed():
    result = runner.invoke(app, ["sections", "--help"])
    assert result.exit_code != 0
