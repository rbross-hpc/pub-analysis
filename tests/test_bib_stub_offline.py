# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for bib stub helpers using mocked source responses."""
from unittest.mock import patch, MagicMock
from puba.bib.sources._common import normalize_doi, extract_doi, extract_arxiv_id, similarity


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
