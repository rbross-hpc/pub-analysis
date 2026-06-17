# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""End-to-end bib resolution tests against live external APIs.

Run with:   pytest tests/test_e2e_bib.py -v
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


# ---------------------------------------------------------------------------
# klasky-5.pdf — Frontiers journal article, CC-BY 4.0
# DOI: 10.3389/fhpcp.2026.1778471  OSTI: 3028571
# ---------------------------------------------------------------------------

class TestKlasky5:
    @pytest.fixture
    def pdf(self, tmp_path):
        return _copy_pdf("klasky-5.pdf", tmp_path)

    def test_bib_resolves(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)

    def test_doi(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert bib["doi"] == "10.3389/fhpcp.2026.1778471"

    def test_osti_id(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert bib["osti_id"] == "3028571"

    def test_year(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert bib["year"] == 2026

    def test_category_journal(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert bib["category"] == "journal article"

    def test_venue_frontiers(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert "frontiers" in (bib["venue"] or "").lower()

    def test_authors_include_klasky(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert any("klasky" in a.lower() for a in (bib["authors"] or []))

    def test_no_conflict(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert not bib.get("needs_review"), \
            f"Unexpected needs_review=true; conflicts: {bib.get('_conflicts')}"

    def test_doi_provenance_from_authoritative_source(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        prov = bib.get("_provenance", {})
        assert prov.get("doi", {}).get("source") in (
            "openalex", "crossref", "osti", "pdf"
        ), f"Unexpected doi provenance: {prov.get('doi')}"

    def test_title_provenance_from_authoritative_source(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        prov = bib.get("_provenance", {})
        assert prov.get("title", {}).get("source") in (
            "openalex", "crossref", "osti"
        ), f"Unexpected title provenance: {prov.get('title')}"

    def test_analysis_dir_layout(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        ad = pdf.parent / "klasky-5.puba"
        assert ad.is_dir()
        assert (ad / "bib.yaml").exists()
        assert (ad / ".state.json").exists()
        assert (ad / "analyses").is_dir()

    def test_cached_run_is_noop(self, pdf):
        from puba.bib.stub import resolve
        from puba.state import is_stage_current, analysis_dir
        from puba import config as cfg
        resolve(pdf, force=True, no_llm=True)
        ad = analysis_dir(pdf)
        prompt_version = cfg.prompt_versions().get("bib_extract", "bib-1")
        assert is_stage_current(ad, pdf, "bib", prompt_version)
        bib_mtime_before = (ad / "bib.yaml").stat().st_mtime
        resolve(pdf, force=False, no_llm=True)
        bib_mtime_after = (ad / "bib.yaml").stat().st_mtime
        assert bib_mtime_before == bib_mtime_after, "bib.yaml was rewritten on cached run"


# ---------------------------------------------------------------------------
# zfp-spectral-report.pdf — DOE OSTI technical report, public domain
# DOI: 10.2172/2998448  OSTI: 2998448
# ---------------------------------------------------------------------------

class TestZfpSpectralReport:
    @pytest.fixture
    def pdf(self, tmp_path):
        return _copy_pdf("zfp-spectral-report.pdf", tmp_path)

    def test_bib_resolves(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)

    def test_doi(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert bib["doi"] == "10.2172/2998448"

    def test_osti_id(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert bib["osti_id"] == "2998448"

    def test_year(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert bib["year"] == 2025

    def test_category_technical_report_or_other(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        # TODO: tighten to "technical report" once classifier is confirmed
        assert bib["category"] in ("technical report", "other"), \
            f"Unexpected category: {bib['category']}"

    def test_author_lindstrom(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        assert any("lindstrom" in a.lower() for a in (bib["authors"] or []))

    def test_osti_is_canonical_source(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        bib = _load_bib(pdf)
        log = bib.get("_lookup_log", {})
        assert log.get("osti", {}).get("status") == "hit", \
            f"Expected OSTI hit; got: {log.get('osti')}"

    def test_analysis_dir_layout(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True, no_llm=True)
        ad = pdf.parent / "zfp-spectral-report.puba"
        assert ad.is_dir()
        assert (ad / "bib.yaml").exists()
        assert (ad / ".state.json").exists()
        assert (ad / "analyses").is_dir()
