# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""End-to-end distillation tests using the dorier-mofka fixture.

Requires live Argo API access (OPENAI_API_KEY).
Run with:   pytest tests/test_e2e_distill.py -v
Skip with:  pytest -m 'not network'
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.network


def _copy_pdf(src_name: str, tmp_path: Path) -> Path:
    src = FIXTURES / src_name
    dst = tmp_path / src_name
    shutil.copy2(src, dst)
    return dst


def _load_bib(pdf_path: Path) -> dict:
    bib_yaml = pdf_path.parent / f"{pdf_path.stem}.puba" / "bib.yaml"
    assert bib_yaml.exists(), f"bib.yaml not found at {bib_yaml}"
    return yaml.safe_load(bib_yaml.read_text(encoding="utf-8")) or {}


def _load_distillation(pdf_path: Path, name: str) -> dict:
    f = pdf_path.parent / f"{pdf_path.stem}.puba" / "analyses" / f"{name}.yaml"
    assert f.exists(), f"distillation file not found: {f}"
    return yaml.safe_load(f.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Mofka paper: dorier-mofka.pdf
# ANL Frontiers journal article, CC-BY 4.0
# DOI: 10.3389/fhpcp.2025.1638203  OSTI: 3002321
# Tests: bib resolution + distillation (summary, scope=abstract)
# ---------------------------------------------------------------------------

class TestDorierMofka:
    @pytest.fixture
    def pdf(self, tmp_path):
        return _copy_pdf("dorier-mofka.pdf", tmp_path)

    # --- Bib resolution ---

    @pytest.fixture(autouse=True)
    def _resolved_bib(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True)

    def test_bib_resolves(self, pdf):
        pass

    def test_doi(self, pdf):
        bib = _load_bib(pdf)
        assert bib["doi"] == "10.3389/fhpcp.2025.1638203"

    def test_osti_id(self, pdf):
        bib = _load_bib(pdf)
        assert bib["osti_id"] == "3002321"

    def test_year(self, pdf):
        bib = _load_bib(pdf)
        assert bib["year"] == 2025

    def test_category_journal(self, pdf):
        bib = _load_bib(pdf)
        assert bib["category"] == "journal article"

    def test_venue_frontiers(self, pdf):
        bib = _load_bib(pdf)
        assert "frontiers" in (bib["venue"] or "").lower()

    def test_author_dorier(self, pdf):
        bib = _load_bib(pdf)
        assert any("dorier" in a.lower() for a in (bib["authors"] or []))

    def test_abstract_present(self, pdf):
        bib = _load_bib(pdf)
        assert bib.get("abstract") and len(bib["abstract"]) > 50

    def test_analysis_dir_layout(self, pdf):
        ad = pdf.parent / "dorier-mofka.puba"
        assert ad.is_dir()
        assert (ad / "bib.yaml").exists()
        assert (ad / ".state.json").exists()
        assert (ad / "analyses").is_dir()

    # --- Distillation ---

    def test_distill_summary_runs(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        assert "summary" in queries
        result = run_query(pdf, queries["summary"], force=True)
        assert result["status"] == "distilled", f"Expected distilled, got: {result}"

    def test_distill_summary_output_file_exists(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        d = _load_distillation(pdf, "summary")
        assert d["name"] == "summary"
        assert d["scope"] == "abstract"
        assert d["output"].strip()
        assert d["generated_at"]

    def test_distill_summary_output_within_max_chars(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        q = queries["summary"]
        run_query(pdf, q, force=True)
        d = _load_distillation(pdf, "summary")
        if q.max_chars:
            assert len(d["output"]) <= q.max_chars + 1

    def test_distill_summary_provenance(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        d = _load_distillation(pdf, "summary")
        prov = d["_provenance"]
        assert prov["prompt_sha256"]
        assert prov["input_sha256"]
        assert prov["bib_yaml_sha"]
        assert prov["at"]
        assert prov["tool_version"]

    def test_distill_summary_cached_on_rerun(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        result2 = run_query(pdf, queries["summary"], force=False)
        assert result2["status"] == "cached"

    def test_distill_summary_utf8_output(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        raw = (
            pdf.parent / "dorier-mofka.puba" / "analyses" / "summary.yaml"
        ).read_bytes()
        raw.decode("utf-8")
