# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for `puba show bib/md/sections/info/distill`."""
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


def _make_analysis_dir(
    tmp_path: Path,
    needs_review: bool = False,
    review_reasons: list[str] | None = None,
) -> tuple[Path, Path]:
    """Create paper.pdf and paper.puba/ with stub bib.yaml and paper.md."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    puba_dir = tmp_path / "paper.puba"
    puba_dir.mkdir()
    (puba_dir / "analyses").mkdir()

    bib: dict = {
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
    if review_reasons:
        bib["_review_reasons"] = review_reasons
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
    pdf, puba_dir = _make_analysis_dir(tmp_path, needs_review=True,
                                        review_reasons=["title missing"])
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["show", "bib", str(pdf), "--json"])

    data = _parse(result)
    assert result.exit_code == 0
    assert data["needs_review"] is True
    assert data["review_reasons"] == ["title missing"]


def test_show_bib_displays_review_reasons(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path, needs_review=True,
                                        review_reasons=["authors missing", "year missing"])
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["show", "bib", str(pdf)])

    assert result.exit_code == 0
    assert "authors missing" in result.output
    assert "year missing" in result.output


def test_show_bib_exits_0_regardless_of_review(tmp_path):
    pdf, puba_dir = _make_analysis_dir(tmp_path, needs_review=True,
                                        review_reasons=["title missing"])
    bib_yaml = puba_dir / "bib.yaml"

    with patch("puba.bib.stub.resolve", return_value=(bib_yaml, False)):
        result = runner.invoke(app, ["show", "bib", str(pdf), "--json"])

    assert result.exit_code == 0


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
    assert "paper_raw_txt" not in data
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


# ---------------------------------------------------------------------------
# show distill — helpers
# ---------------------------------------------------------------------------

_DISTILL_PROVENANCE = {
    "source": "argo/Claude Sonnet 4.6",
    "at": "2026-06-18T12:00:00Z",
    "prompt_sha256": "abc123",
    "input_sha256": "def456",
    "tool_version": "0.1.0",
    "prompt_source": "config.yaml",
    "token_count_estimate": 500,
    "truncated": False,
}


def _write_distillation(analyses_dir: Path, name: str, output: str, scope: str = "abstract",
                         section: str | None = None, corrupt: bool = False) -> Path:
    f = analyses_dir / f"{name}.yaml"
    if corrupt:
        f.write_text(": : :\n", encoding="utf-8")
        return f
    record = {
        "name": name,
        "scope": scope,
        "model": "Claude Sonnet 4.6",
        "generated_at": "2026-06-18T12:00:00Z",
        "output": output,
        "_provenance": _DISTILL_PROVENANCE,
    }
    if section:
        record["section"] = section
    f.write_text(yaml.dump(record, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return f


def _make_distill_setup(tmp_path: Path) -> tuple[Path, Path]:
    """Create paper.pdf and paper.puba/analyses/."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    puba_dir = tmp_path / "paper.puba"
    puba_dir.mkdir()
    analyses_dir = puba_dir / "analyses"
    analyses_dir.mkdir()
    return pdf, analyses_dir


# ---------------------------------------------------------------------------
# show distill — plain text
# ---------------------------------------------------------------------------

def test_show_distill_plain_prints_only_output(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "This paper proposes X. Method does Y. Result is Z.")
    result = runner.invoke(app, ["show", "distill", str(pdf), "summary"])
    assert result.exit_code == 0
    assert "This paper proposes X." in result.output
    assert "name:" not in result.output
    assert "scope:" not in result.output


def test_show_distill_no_name_plain_errors_with_listing(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "Summary text.")
    _write_distillation(analyses_dir, "methods", "Methods text.")
    result = runner.invoke(app, ["show", "distill", str(pdf)])
    assert result.exit_code == 2
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "summary" in combined
    assert "methods" in combined


def test_show_distill_unknown_name_errors_with_listing(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "Summary text.")
    result = runner.invoke(app, ["show", "distill", str(pdf), "nonexistent"])
    assert result.exit_code == 2
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "summary" in combined


def test_show_distill_missing_analyses_dir_errors(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (tmp_path / "paper.puba").mkdir()
    result = runner.invoke(app, ["show", "distill", str(pdf), "summary"])
    assert result.exit_code == 1
    assert "distill" in result.output.lower() or "distill" in (result.stderr if hasattr(result, "stderr") else "").lower()


def test_show_distill_empty_analyses_dir_errors(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    result = runner.invoke(app, ["show", "distill", str(pdf), "summary"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# show distill — JSON single
# ---------------------------------------------------------------------------

def test_show_distill_json_single_shape_includes_provenance(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "This paper proposes X.")
    result = runner.invoke(app, ["show", "distill", str(pdf), "summary", "--json"])
    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["command"] == "show.distill"
    assert data["name"] == "summary"
    assert data["scope"] == "abstract"
    assert data["output"] == "This paper proposes X."
    assert data["chars"] == len("This paper proposes X.")
    assert "_provenance" in data
    assert data["_provenance"]["source"] == "argo/Claude Sonnet 4.6"


def test_show_distill_no_name_json_errors_with_available(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "Summary.")
    _write_distillation(analyses_dir, "methods", "Methods.")
    result = runner.invoke(app, ["show", "distill", str(pdf), "--json"])
    data = _parse(result)
    assert result.exit_code == 2
    assert data["ok"] is False
    assert "available" in data
    assert sorted(data["available"]) == ["methods", "summary"]


# ---------------------------------------------------------------------------
# show distill — --all
# ---------------------------------------------------------------------------

def test_show_distill_all_requires_json(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "Summary.")
    result = runner.invoke(app, ["show", "distill", str(pdf), "--all"])
    assert result.exit_code == 2


def test_show_distill_all_and_name_mutually_exclusive(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "Summary.")
    result = runner.invoke(app, ["show", "distill", str(pdf), "summary", "--all", "--json"])
    assert result.exit_code == 2
    data = _parse(result)
    assert data["ok"] is False


def test_show_distill_all_json_emits_all_records(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "Summary text.")
    _write_distillation(analyses_dir, "methods", "Methods text.", scope="section", section="methods")
    _write_distillation(analyses_dir, "contributions", "Contributions text.")
    result = runner.invoke(app, ["show", "distill", str(pdf), "--all", "--json"])
    data = _parse(result)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["command"] == "show.distill"
    assert data["count"] == 3
    assert len(data["distillations"]) == 3
    names = [d["name"] for d in data["distillations"]]
    assert names == sorted(names)
    for d in data["distillations"]:
        assert "output" in d
        assert "_provenance" in d
        assert "chars" in d


def test_show_distill_all_json_fails_on_corrupt_yaml(tmp_path):
    pdf, analyses_dir = _make_distill_setup(tmp_path)
    _write_distillation(analyses_dir, "summary", "Summary text.")
    _write_distillation(analyses_dir, "bad", "", corrupt=True)
    result = runner.invoke(app, ["show", "distill", str(pdf), "--all", "--json"])
    data = _parse(result)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert "bad_file" in data
