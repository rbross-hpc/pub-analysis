# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline unit tests for puba.bib.sources.osti — _summarize author parsing."""
from __future__ import annotations

from puba.bib.sources.osti import _summarize


_BASE_RECORD = {
    "osti_id": "2587778",
    "doi": "10.5194/gmd-18-5655-2025",
    "title": "Features of mid- and high-latitude low-level clouds",
    "publication_date": "2025-09-05T00:00:00Z",
    "journal_name": "Geoscientific Model Development",
    "links": [{"rel": "citation", "href": "https://www.osti.gov/biblio/2587778"}],
}


def test_summarize_authors_as_strings():
    record = dict(_BASE_RECORD, authors=[
        "Wan, Hui [Pacific Northwest National Laboratory (PNNL), Richland, WA (United States)] (ORCID:0000000152944116)",
        "Yenpure, Abhishek [Kitware, Inc., Clifton Park, NY (United States)]",
        "Zeng, Xubin [University of Arizona, Tucson, AZ (United States)]",
    ])
    result = _summarize(record)
    assert result["authors"] == ["Wan, Hui", "Yenpure, Abhishek", "Zeng, Xubin"]


def test_summarize_authors_as_dicts():
    record = dict(_BASE_RECORD, authors=[
        {"name": "Smith, Alice", "first_name": "Alice", "last_name": "Smith"},
        {"name": "Jones, Bob", "first_name": "Bob", "last_name": "Jones"},
    ])
    result = _summarize(record)
    assert result["authors"] == ["Smith, Alice", "Jones, Bob"]


def test_summarize_authors_as_dicts_name_fallback():
    record = dict(_BASE_RECORD, authors=[
        {"first_name": "Alice", "last_name": "Smith"},
    ])
    result = _summarize(record)
    assert result["authors"] == ["Alice Smith"]


def test_summarize_authors_mixed_types():
    record = dict(_BASE_RECORD, authors=[
        "Wan, Hui [PNNL]",
        {"name": "Jones, Bob"},
    ])
    result = _summarize(record)
    assert result["authors"] == ["Wan, Hui", "Jones, Bob"]


def test_summarize_authors_empty_list():
    record = dict(_BASE_RECORD, authors=[])
    result = _summarize(record)
    assert result["authors"] == []


def test_summarize_authors_none():
    record = dict(_BASE_RECORD, authors=None)
    result = _summarize(record)
    assert result["authors"] == []


def test_summarize_string_author_no_affiliation():
    record = dict(_BASE_RECORD, authors=["Wan, Hui"])
    result = _summarize(record)
    assert result["authors"] == ["Wan, Hui"]
