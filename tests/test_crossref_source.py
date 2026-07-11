# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline unit tests for puba.bib.sources.crossref — _summarize references_count."""
from __future__ import annotations

from puba.bib.sources.crossref import _summarize

_BASE_ITEM = {
    "type": "journal-article",
    "DOI": "10.1145/3682060",
    "title": ["Hybrid PDES Simulation of HPC Networks Using Zombie Packets"],
    "author": [{"given": "Rob", "family": "Ross"}],
    "published": {"date-parts": [[2024, 11, 1]]},
    "container-title": ["ACM Transactions on Modeling and Computer Simulation"],
    "URL": "https://doi.org/10.1145/3682060",
}


def test_references_count_from_references_count_field():
    item = dict(_BASE_ITEM, **{"references-count": 45})
    result = _summarize(item)
    assert result["references_count"] == 45


def test_references_count_zero_from_field():
    item = dict(_BASE_ITEM, **{"references-count": 0})
    result = _summarize(item)
    assert result["references_count"] == 0


def test_references_count_fallback_to_reference_array_length():
    item = dict(_BASE_ITEM, reference=[
        {"DOI": "10.1000/abc", "unstructured": "Smith et al., 2020"},
        {"unstructured": "Jones, 2019, Some Conference"},
        {"DOI": "10.1000/xyz"},
    ])
    result = _summarize(item)
    assert result["references_count"] == 3


def test_references_count_prefers_explicit_field_over_array():
    item = dict(_BASE_ITEM, **{"references-count": 50},
                reference=[{"DOI": "10.1000/a"}, {"DOI": "10.1000/b"}])
    result = _summarize(item)
    assert result["references_count"] == 50


def test_references_count_missing_both():
    item = dict(_BASE_ITEM)
    result = _summarize(item)
    assert result["references_count"] is None


def test_references_count_empty_reference_array():
    item = dict(_BASE_ITEM, reference=[])
    result = _summarize(item)
    assert result["references_count"] is None


def test_references_count_none_reference_field():
    item = dict(_BASE_ITEM, reference=None)
    result = _summarize(item)
    assert result["references_count"] is None
