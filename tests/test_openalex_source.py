# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline unit tests for puba.bib.sources.openalex — _summarize references_count."""
from __future__ import annotations

from puba.bib.sources.openalex import _summarize

_BASE_WORK = {
    "id": "https://openalex.org/W2741809807",
    "display_name": "Attention Is All You Need",
    "publication_year": 2017,
    "publication_date": "2017-06-12",
    "type": "preprint",
    "doi": "https://doi.org/10.48550/arxiv.1706.03762",
    "primary_location": {},
    "authorships": [],
    "keywords": [],
    "open_access": {"oa_status": "gold"},
    "abstract_inverted_index": None,
}


def test_references_count_from_referenced_works_count():
    work = dict(_BASE_WORK, referenced_works_count=42,
                referenced_works=["https://openalex.org/W1", "https://openalex.org/W2"])
    result = _summarize(work)
    assert result["references_count"] == 42


def test_references_count_zero():
    work = dict(_BASE_WORK, referenced_works_count=0, referenced_works=[])
    result = _summarize(work)
    assert result["references_count"] == 0


def test_references_count_missing():
    work = dict(_BASE_WORK)
    work.pop("referenced_works_count", None)
    result = _summarize(work)
    assert result["references_count"] is None


def test_references_count_not_affected_by_referenced_works_list():
    work = dict(_BASE_WORK, referenced_works_count=5,
                referenced_works=["W1", "W2", "W3"])
    result = _summarize(work)
    assert result["references_count"] == 5


def test_summarize_does_not_expose_referenced_works_list():
    work = dict(_BASE_WORK, referenced_works_count=3,
                referenced_works=["W1", "W2", "W3"])
    result = _summarize(work)
    assert "referenced_works" not in result


def test_references_count_large():
    work = dict(_BASE_WORK, referenced_works_count=312)
    result = _summarize(work)
    assert result["references_count"] == 312
