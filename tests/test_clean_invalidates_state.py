# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Tests that `puba clean --what <stage>` removes both output files and the
corresponding .state.json cache entry, and that is_distill_current() returns
False when the output YAML is absent regardless of what .state.json says."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from puba.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return pdf


@pytest.fixture
def analysis_dir(fake_pdf: Path) -> Path:
    ad = fake_pdf.parent / "paper.puba"
    ad.mkdir()
    (ad / "analyses").mkdir()
    return ad


def _seed_state(analysis_dir: Path, stages: dict) -> None:
    state = {"pdf_sha256": "deadbeef", "tool_version": "0.1.0", "stages": stages}
    (analysis_dir / ".state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def _load_state(analysis_dir: Path) -> dict:
    p = analysis_dir / ".state.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# clean --what distill
# ---------------------------------------------------------------------------

def test_clean_distill_removes_yaml_files(fake_pdf, analysis_dir):
    (analysis_dir / "analyses" / "foo.yaml").write_text("name: foo\noutput: x\n", encoding="utf-8")
    (analysis_dir / "analyses" / "bar.yaml").write_text("name: bar\noutput: y\n", encoding="utf-8")
    _seed_state(analysis_dir, {"distill": {"foo": {"completed_at": "2026-01-01"}, "bar": {"completed_at": "2026-01-01"}}, "md": {"completed_at": "2026-01-01"}})

    result = runner.invoke(app, ["clean", str(fake_pdf), "--what", "distill"])
    assert result.exit_code == 0
    assert not (analysis_dir / "analyses" / "foo.yaml").exists()
    assert not (analysis_dir / "analyses" / "bar.yaml").exists()


def test_clean_distill_removes_stages_distill_from_state(fake_pdf, analysis_dir):
    (analysis_dir / "analyses" / "foo.yaml").write_text("name: foo\noutput: x\n", encoding="utf-8")
    _seed_state(analysis_dir, {
        "distill": {"foo": {"completed_at": "2026-01-01"}},
        "md": {"completed_at": "2026-01-01"},
    })

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "distill"])

    state = _load_state(analysis_dir)
    assert "distill" not in state.get("stages", {})


def test_clean_distill_preserves_other_stages_in_state(fake_pdf, analysis_dir):
    _seed_state(analysis_dir, {
        "distill": {"foo": {"completed_at": "2026-01-01"}},
        "md": {"completed_at": "2026-06-01"},
        "bib": {"completed_at": "2026-06-01"},
    })

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "distill"])

    state = _load_state(analysis_dir)
    stages = state.get("stages", {})
    assert "distill" not in stages
    assert "md" in stages
    assert "bib" in stages


def test_clean_distill_noop_when_no_state_file(fake_pdf, analysis_dir):
    result = runner.invoke(app, ["clean", str(fake_pdf), "--what", "distill"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# clean --what bib
# ---------------------------------------------------------------------------

def test_clean_bib_removes_bib_yaml(fake_pdf, analysis_dir):
    bib_file = analysis_dir / "bib.yaml"
    bib_file.write_text("title: Test\n", encoding="utf-8")
    _seed_state(analysis_dir, {"bib": {"completed_at": "2026-01-01"}, "md": {"completed_at": "2026-01-01"}})

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "bib"])

    assert not bib_file.exists()


def test_clean_bib_removes_stages_bib_from_state(fake_pdf, analysis_dir):
    (analysis_dir / "bib.yaml").write_text("title: Test\n", encoding="utf-8")
    _seed_state(analysis_dir, {
        "bib": {"completed_at": "2026-01-01"},
        "md": {"completed_at": "2026-01-01"},
    })

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "bib"])

    state = _load_state(analysis_dir)
    assert "bib" not in state.get("stages", {})
    assert "md" in state.get("stages", {})


# ---------------------------------------------------------------------------
# clean --what md
# ---------------------------------------------------------------------------

def test_clean_md_removes_stages_md_from_state(fake_pdf, analysis_dir):
    (analysis_dir / "paper.md").write_text("# Title\n", encoding="utf-8")
    (analysis_dir / "paper.sections.json").write_text("[]", encoding="utf-8")
    _seed_state(analysis_dir, {
        "md": {"completed_at": "2026-01-01"},
        "bib": {"completed_at": "2026-01-01"},
    })

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "md"])

    state = _load_state(analysis_dir)
    assert "md" not in state.get("stages", {})
    assert "bib" in state.get("stages", {})


# ---------------------------------------------------------------------------
# clean --what figures
# ---------------------------------------------------------------------------

def test_clean_figures_removes_stages_figures_from_state(fake_pdf, analysis_dir):
    (analysis_dir / "paper.figures.json").write_text("{}", encoding="utf-8")
    _seed_state(analysis_dir, {
        "figures": {"completed_at": "2026-01-01"},
        "md": {"completed_at": "2026-01-01"},
    })

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "figures"])

    state = _load_state(analysis_dir)
    assert "figures" not in state.get("stages", {})
    assert "md" in state.get("stages", {})


# ---------------------------------------------------------------------------
# clean --what state and --what all: .state.json itself is deleted
# ---------------------------------------------------------------------------

def test_clean_state_removes_state_json(fake_pdf, analysis_dir):
    _seed_state(analysis_dir, {"bib": {"completed_at": "2026-01-01"}})

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "state"])

    assert not (analysis_dir / ".state.json").exists()


def test_clean_all_removes_state_json(fake_pdf, analysis_dir):
    _seed_state(analysis_dir, {"bib": {"completed_at": "2026-01-01"}})

    runner.invoke(app, ["clean", str(fake_pdf), "--what", "all"])

    assert not (analysis_dir / ".state.json").exists()


# ---------------------------------------------------------------------------
# is_distill_current: returns False when YAML is absent even if state matches
# ---------------------------------------------------------------------------

def test_is_distill_current_false_when_yaml_missing(fake_pdf, analysis_dir):
    from puba.state import mark_distill_complete, is_distill_current

    input_sha = "abc123"
    prompt_sha = "def456"
    model = "GPT-5.4"

    mark_distill_complete(analysis_dir, fake_pdf, "foo", input_sha, prompt_sha, model)

    assert not (analysis_dir / "analyses" / "foo.yaml").exists()

    assert not is_distill_current(analysis_dir, fake_pdf, "foo", input_sha, prompt_sha, model)


def test_is_distill_current_true_when_yaml_present(fake_pdf, analysis_dir):
    from puba.state import mark_distill_complete, is_distill_current

    input_sha = "abc123"
    prompt_sha = "def456"
    model = "GPT-5.4"

    mark_distill_complete(analysis_dir, fake_pdf, "foo", input_sha, prompt_sha, model)
    (analysis_dir / "analyses" / "foo.yaml").write_text("name: foo\noutput: x\n", encoding="utf-8")

    assert is_distill_current(analysis_dir, fake_pdf, "foo", input_sha, prompt_sha, model)
