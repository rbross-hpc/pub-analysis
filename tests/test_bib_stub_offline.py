# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for bib stub helpers using mocked source responses."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml
import pytest

from puba.bib.sources._common import normalize_doi, extract_doi, extract_arxiv_id, similarity
from puba.bib.conflicts import detect_conflicts


def test_normalize_doi_strips_prefix():
    assert normalize_doi("https://doi.org/10.1016/j.physd.2026.135207") == "10.1016/j.physd.2026.135207"
    assert normalize_doi("doi:10.1016/j.physd.2026.135207") == "10.1016/j.physd.2026.135207"
    assert normalize_doi("DOI:10.1016/j.physd.2026.135207") == "10.1016/j.physd.2026.135207"


def test_normalize_doi_none():
    assert normalize_doi(None) is None
    assert normalize_doi("") is None


def test_extract_doi_from_text():
    text = "Available at https://doi.org/10.1145/3731599.1234567 in the proceedings."
    assert extract_doi(text) == "10.1145/3731599.1234567"


def test_extract_doi_bare():
    text = "doi: 10.1016/j.physd.2026.135207 was assigned."
    doi = extract_doi(text)
    assert doi == "10.1016/j.physd.2026.135207"


def test_extract_arxiv_id_new_style():
    assert extract_arxiv_id("", "2301.00234v2.pdf") == "2301.00234"
    assert extract_arxiv_id("arXiv:2301.00234", "") == "2301.00234"


def test_extract_arxiv_strips_version():
    assert extract_arxiv_id("", "2301.00234v3.pdf") == "2301.00234"


def test_similarity_exact():
    assert similarity("Attention is All You Need", "Attention is All You Need") == 1.0


def test_similarity_case_insensitive():
    s = similarity("attention is all you need", "Attention Is All You Need")
    assert s > 0.95


def test_similarity_zero_for_unrelated():
    s = similarity("Quantum Computing", "Renaissance Painting Techniques")
    assert s < 0.5


def test_similarity_none_safe():
    assert similarity(None, "some title") == 0.0
    assert similarity("some title", None) == 0.0


# ---------------------------------------------------------------------------
# detect_conflicts — unit tests
# ---------------------------------------------------------------------------

_PAPER_A = {
    "title": "Attention Is All You Need",
    "authors": ["Vaswani, A.", "Shazeer, N."],
    "year": 2017,
    "doi": "10.5555/3295222.3295349",
    "venue": "Advances in Neural Information Processing Systems",
}

_PAPER_B_AGREE = {
    "title": "Attention Is All You Need",
    "authors": ["Vaswani, A.", "Shazeer, N."],
    "year": 2017,
    "doi": "10.5555/3295222.3295349",
    "venue": "Neural Information Processing Systems",
}

_PAPER_C_DIFFERENT = {
    "title": "BERT: Pre-training of Deep Bidirectional Transformers",
    "authors": ["Devlin, J.", "Chang, M."],
    "year": 2019,
    "doi": "10.18653/v1/N19-1423",
    "venue": "ACL",
}


def test_detect_conflicts_no_results():
    assert detect_conflicts({}) == {}


def test_detect_conflicts_single_source():
    assert detect_conflicts({"openalex": _PAPER_A}) == {}


def test_detect_conflicts_two_sources_agree():
    results = {"openalex": _PAPER_A, "crossref": _PAPER_B_AGREE}
    assert detect_conflicts(results) == {}


def test_detect_conflicts_two_sources_title_conflict():
    results = {"openalex": _PAPER_A, "crossref": _PAPER_C_DIFFERENT}
    conflicts = detect_conflicts(results)
    assert "title" in conflicts


def test_detect_conflicts_two_sources_doi_conflict():
    a = dict(_PAPER_A)
    b = dict(_PAPER_A, doi="10.9999/other")
    results = {"openalex": a, "crossref": b}
    conflicts = detect_conflicts(results)
    assert "doi" in conflicts


def test_detect_conflicts_ignores_none_sources():
    results = {"openalex": _PAPER_A, "crossref": None, "osti": None}
    assert detect_conflicts(results) == {}


# ---------------------------------------------------------------------------
# needs_review — integration via resolve() with mocked sources
# ---------------------------------------------------------------------------

def _make_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    return pdf


def _oa_hit(sim=1.0):
    return (dict(_PAPER_A), sim, "10.5555/3295222.3295349")


def _cr_hit(sim=1.0):
    return (dict(_PAPER_B_AGREE), sim, "10.5555/3295222.3295349")


def _no_hit():
    return (None, None, "")


def _low_sim_hit():
    return (dict(_PAPER_C_DIFFERENT), 0.55, "some title")


def _resolve_with_mocked_sources(tmp_path, oa, cr, osti_result, no_llm=True):
    from puba.bib.stub import resolve

    pdf = _make_pdf(tmp_path)

    with patch("puba.bib.stub._first_pages_text", return_value=""), \
         patch("puba.bib.stub.extract_doi", return_value="10.5555/3295222.3295349"), \
         patch("puba.bib.stub.extract_arxiv_id", return_value=None), \
         patch("puba.bib.sources.openalex.get_by_doi", return_value=(oa[0], oa[1])), \
         patch("puba.bib.sources.openalex.search_by_title", return_value=(None, None)), \
         patch("puba.bib.sources.crossref.get_by_doi", return_value=(cr[0], cr[1])), \
         patch("puba.bib.sources.crossref.search_by_title", return_value=(None, None)), \
         patch("puba.bib.sources.osti.search_by_doi", return_value=(osti_result[0], osti_result[1])), \
         patch("puba.bib.sources.osti.search_by_title", return_value=(None, None)), \
         patch("puba.bib.sources.dblp.search_by_title", return_value=(None, None)), \
         patch("puba.bib.sources.arxiv.get_by_id", return_value=None), \
         patch("puba.bib.sources.arxiv.search_by_title", return_value=(None, None)), \
         patch("puba.bib.sources.semanticscholar.get_by_doi", return_value=(None, None)), \
         patch("puba.bib.sources.semanticscholar.search_by_title", return_value=(None, None)):
        bib_path, _ = resolve(pdf, force=True, no_llm=no_llm)

    return yaml.safe_load(bib_path.read_text(encoding="utf-8")) or {}


def test_needs_review_false_single_good_hit(tmp_path):
    bib = _resolve_with_mocked_sources(tmp_path, _oa_hit(), _no_hit(), _no_hit())
    assert bib["needs_review"] is False


def test_needs_review_false_two_sources_agree(tmp_path):
    bib = _resolve_with_mocked_sources(tmp_path, _oa_hit(), _cr_hit(), _no_hit())
    assert bib["needs_review"] is False


def test_needs_review_false_one_good_one_low_sim(tmp_path):
    bib = _resolve_with_mocked_sources(tmp_path, _oa_hit(sim=1.0), _low_sim_hit(), _no_hit())
    assert bib["needs_review"] is False


def test_needs_review_true_two_good_sources_disagree(tmp_path):
    cr_different = (dict(_PAPER_C_DIFFERENT), 0.95, "10.5555/3295222.3295349")
    bib = _resolve_with_mocked_sources(tmp_path, _oa_hit(sim=1.0), cr_different, _no_hit())
    assert bib["needs_review"] is True
