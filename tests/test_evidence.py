# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline unit tests for exact-quote evidence verification."""
from __future__ import annotations

import pytest

from puba.distill.evidence import is_valid_response, verify_evidence


def test_response_schema_accepts_required_shape():
    assert is_valid_response({"answer": "", "evidence": [{"quote": "source text"}]})


@pytest.mark.parametrize("response", [
    {"evidence": []},
    {"answer": "answer", "evidence": "not a list"},
    {"answer": "answer", "evidence": [{}]},
    {"answer": "answer", "evidence": [{"quote": ""}]},
    {"answer": "answer", "evidence": [{"quote": "   "}]},
])
def test_response_schema_rejects_invalid_structures(response):
    assert not is_valid_response(response)


def test_abstract_scope_unique_match_has_abstract_coordinates_only():
    source = "A concise canonical abstract."
    result = verify_evidence([{"quote": "canonical"}], "abstract", source)

    assert result == {"evidence": [{
        "quote": "canonical", "status": "verified", "offset": 10,
        "section": None, "page": None,
    }], "evidence_status": "verified"}


def test_full_scope_derives_section_and_nearest_preceding_page():
    source = "<!-- page 2 -->\n# Introduction\nFirst text.\n<!-- page 3 -->\n## Methods\nExact finding."
    offset = source.index("Exact finding")
    sections = [
        {"short_name": "introduction", "title": "Introduction", "start_offset": 0, "end_offset": source.index("## Methods")},
        {"short_name": "methods", "title": "Methods", "start_offset": source.index("## Methods"), "end_offset": len(source)},
    ]
    result = verify_evidence([{"quote": "Exact finding."}], "full", source, sections=sections)

    item = result["evidence"][0]
    assert item == {"quote": "Exact finding.", "status": "verified", "offset": offset, "section": "Methods", "page": 3}
    assert result["evidence_status"] == "verified"


def test_narrative_scope_uses_unmodified_paper_markers_for_page_derivation():
    source = "<!-- page 8 -->\nNarrative quote."
    result = verify_evidence([{"quote": "Narrative quote."}], "narrative", source)

    assert result["evidence"][0]["offset"] == source.index("Narrative quote.")
    assert result["evidence"][0]["page"] == 8
    assert result["evidence"][0]["section"] is None


def test_no_match_and_duplicate_matches_are_unverified_partial():
    source = "Duplicated quote. Duplicated quote."
    result = verify_evidence(
        [{"quote": "absent"}, {"quote": "Duplicated quote."}], "full", source,
    )

    assert result == {"evidence": [
        {"quote": "absent", "status": "unverified", "reason": "no_match"},
        {"quote": "Duplicated quote.", "status": "unverified", "reason": "ambiguous_match"},
    ], "evidence_status": "partial"}


def test_section_scope_accepts_in_span_match_and_rejects_out_of_span_only_match():
    source = "Introduction shared quote.\nMethods shared quote."
    methods_start = source.index("Methods")
    sections = [
        {"short_name": "introduction", "title": "Introduction", "start_offset": 0, "end_offset": methods_start},
        {"short_name": "methods", "title": "Methods", "start_offset": methods_start, "end_offset": len(source)},
    ]

    in_span = verify_evidence([{"quote": "Methods shared quote."}], "section", source, sections=sections, section_name="methods")
    out_of_span = verify_evidence([{"quote": "Introduction shared quote."}], "section", source, sections=sections, section_name="methods")

    assert in_span["evidence"][0]["status"] == "verified"
    assert in_span["evidence"][0]["offset"] == methods_start
    assert in_span["evidence"][0]["section"] == "Methods"
    assert out_of_span == {"evidence": [{"quote": "Introduction shared quote.", "status": "unverified", "reason": "no_match"}], "evidence_status": "partial"}


def test_empty_evidence_is_partial():
    assert verify_evidence([], "full", "source") == {"evidence": [], "evidence_status": "partial"}
