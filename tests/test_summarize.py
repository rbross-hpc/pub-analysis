# BSD 3-Clause License
"""Offline coverage for the deterministic ``puba summarize`` artifact."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from puba.cli import app

runner = CliRunner()


def _paper(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 summary test")
    ad = tmp_path / "paper.puba"
    (ad / "analyses").mkdir(parents=True)
    return pdf, ad


def _summary(pdf):
    return pdf.parent / f"{pdf.stem}.summary.md"


def test_summarize_fully_populated_artifacts(tmp_path):
    pdf, ad = _paper(tmp_path)
    (ad / "bib.json").write_text(json.dumps({
        "title": "A Test Paper", "authors": ["Ada Lovelace"], "needs_review": True,
    }), encoding="utf-8")
    (ad / "paper.md").write_text("# Paper", encoding="utf-8")
    (ad / "paper.sections.json").write_text("[]", encoding="utf-8")
    (ad / "paper.figures.json").write_text(json.dumps({"figures": [
        {"id": "fig-1", "caption": "An important chart"},
    ]}), encoding="utf-8")
    (ad / "analyses" / "summary.json").write_text(json.dumps({
        "name": "summary", "model": "test-model", "prompt": "Explain the contribution.",
        "output": "It contributes a test.", "evidence_status": "partial",
        "evidence": [{"quote": "contributes", "status": "unverified", "reason": "no_match"}],
    }), encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    text = _summary(pdf).read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert "artifact_type: puba-summary" in text
    assert "source_pdf:" in text
    assert "A Test Paper" in text
    assert "1 figure(s) extracted." in text
    assert "An important chart" in text
    assert "Explain the contribution." in text
    assert "test-model" in text
    assert "It contributes a test." in text
    assert "Evidence status: **partial**." in text
    assert '"status": "unverified"' in text
    assert "needs_review: true" in text
    assert "evidence_status: partial" in text


def test_summarize_nothing_run_yet_succeeds(tmp_path):
    pdf, _ = _paper(tmp_path)

    result = runner.invoke(app, ["summarize", str(pdf)])

    text = _summary(pdf).read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert "| bib | never-run |" in text
    assert "| md | never-run |" in text
    assert "| figures | never-run |" in text
    assert "| distill | never-run |" in text
    assert "No bibliographic record exists yet." in text
    assert "No figures have been extracted." in text
    assert "No distillations exist on disk." in text


def test_summarize_flags_stale_and_invalid_stages(tmp_path):
    pdf, ad = _paper(tmp_path)
    (ad / "bib.json").write_text(json.dumps({"title": "Stale"}), encoding="utf-8")
    (ad / "paper.figures.json").write_text("{bad json", encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    text = _summary(pdf).read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert "| bib | stale |" in text
    assert "| figures | invalid |" in text
    assert "- bib stage is stale." in text
    assert "- figures stage is invalid." in text


def test_summarize_legacy_distillation_without_prompt_is_flagged(tmp_path):
    pdf, ad = _paper(tmp_path)
    (ad / "analyses" / "legacy.yaml").write_text(
        "name: legacy\nmodel: older-model\noutput: legacy answer\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["summarize", str(pdf)])

    text = _summary(pdf).read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert "not recorded (generated before prompt persistence was added)" in text
    assert "Distillation 'legacy' is missing persisted prompt text." in text


def test_summarize_flags_json_and_yaml_forms_present(tmp_path):
    pdf, ad = _paper(tmp_path)
    (ad / "bib.json").write_text(json.dumps({"title": "JSON wins"}), encoding="utf-8")
    (ad / "bib.yaml").write_text("title: YAML loses\n", encoding="utf-8")
    (ad / "analyses" / "summary.json").write_text(json.dumps({"output": "json"}), encoding="utf-8")
    (ad / "analyses" / "summary.yaml").write_text("output: yaml\n", encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    text = _summary(pdf).read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert "Both bib.json and legacy bib.yaml are present" in text
    assert "Both analyses/summary.json and legacy analyses/summary.yaml are present" in text
    assert "JSON wins" in text
    assert "yaml" not in text.split("## Missing / Needs Attention")[0]


def test_summarize_tolerates_malformed_local_config(tmp_path, monkeypatch):
    """Inspection remains available when a project-local override cannot parse."""
    from puba import config

    pdf, ad = _paper(tmp_path)
    (ad / "analyses" / "existing.json").write_text(
        json.dumps({"output": "already generated"}), encoding="utf-8"
    )
    # A state entry makes the on-disk distillation a known prior run.  Without
    # readable query configuration it must be reported conservatively as stale,
    # rather than being treated as current or aborting summary generation.
    (ad / ".state.json").write_text(json.dumps({
        "pdf_sha256": "deliberately-not-the-current-pdf",
        "stages": {"distill": {"existing": {"completed_at": "2026-01-01T00:00:00+00:00"}}},
    }), encoding="utf-8")
    (tmp_path / "puba.config.yaml").write_text("models: [unterminated", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config.load.cache_clear()

    result = runner.invoke(app, ["summarize", str(pdf)])

    text = _summary(pdf).read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert "| bib | never-run |" in text
    assert "| md | never-run |" in text
    assert "| figures | never-run |" in text
    assert "| distill | stale |" in text
    assert "- distill stage is stale." in text
    assert "already generated" in text
    config.load.cache_clear()


def test_summarize_uses_atomic_write_and_is_deterministic_except_timestamp(tmp_path, monkeypatch):
    pdf, _ = _paper(tmp_path)
    calls = []
    from puba import summarize

    original_write = summarize.atomic_write_text
    monkeypatch.setattr(summarize, "atomic_write_text", lambda path, text: calls.append((path, text)))
    monkeypatch.setattr(summarize, "now_iso", lambda: "2026-09-03T00:00:00+00:00")

    result = runner.invoke(app, ["summarize", str(pdf)])
    first = calls[-1]
    result_again = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    assert result_again.exit_code == 0, result_again.output
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][0] == _summary(pdf)
    monkeypatch.setattr(summarize, "atomic_write_text", original_write)
