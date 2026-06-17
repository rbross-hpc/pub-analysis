# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Tests for the config-driven category classifier."""
import pytest
from puba.bib.classify import classify


def test_arxiv_preprint_no_doi():
    cat, rule = classify(doi=None, arxiv_id="2301.00234", venue=None)
    assert cat == "arxiv preprint"


def test_arxiv_preprint_arxiv_doi():
    cat, rule = classify(doi="10.48550/arxiv.2301.00234", arxiv_id="2301.00234", venue=None)
    assert cat == "arxiv preprint"


def test_arxiv_id_with_real_doi_and_journal_venue():
    cat, rule = classify(doi="10.1016/j.physd.2026.135207", arxiv_id="2301.00234", venue="Physica D")
    assert cat == "journal article"


def test_journal_article_by_venue():
    cat, rule = classify(doi="10.1016/j.physd.2026.135207", arxiv_id=None, venue="Physica D")
    assert cat == "journal article"


def test_conference_by_acronym_sc():
    cat, rule = classify(doi=None, arxiv_id=None, venue="Proceedings of SC24")
    assert cat == "conference paper"


def test_conference_by_acronym_neurips():
    cat, rule = classify(doi=None, arxiv_id=None, venue="NeurIPS 2024")
    assert cat == "conference paper"


def test_conference_by_pattern():
    cat, rule = classify(doi=None, arxiv_id=None, venue="Proceedings of the 2024 Workshop on X")
    assert cat in ("conference paper", "workshop paper")


def test_workshop_by_pattern():
    cat, rule = classify(doi=None, arxiv_id=None, venue="Workshop on Machine Learning Systems")
    assert cat == "workshop paper"


def test_preprint_by_doi_prefix():
    cat, rule = classify(doi="10.1101/2024.01.01.123456", arxiv_id=None, venue=None)
    assert cat == "preprint"


def test_book_chapter_by_crossref_type():
    cat, rule = classify(doi=None, arxiv_id=None, venue=None, crossref_type="book-chapter")
    assert cat == "book chapter"


def test_thesis_by_crossref_type():
    cat, rule = classify(doi=None, arxiv_id=None, venue=None, crossref_type="dissertation")
    assert cat == "thesis"


def test_other_fallback():
    cat, rule = classify(doi=None, arxiv_id=None, venue=None)
    assert cat == "other"
