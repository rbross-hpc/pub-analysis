# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Focused tests for deterministic schema-v2 evidence primitives."""
from __future__ import annotations

import pytest

from puba.distill.evidence import (
    AtomizationLimits,
    aggregate_claim_support,
    aggregate_quote_presence,
    apply_quote_presence,
    build_verifier_context,
    is_valid_atomizer_response,
    is_valid_verifier_response,
    normalize_atomizer_response,
    normalize_verifier_response,
    process_quote_presence,
)


def test_atomizer_validation_and_local_id_normalization():
    response = {"claims": [
        {"claim": "  First fact.  ", "evidence": [{"quote": "Exact quote."}]},
        {"claim": "Second fact.", "evidence": []},
    ]}

    assert is_valid_atomizer_response(response)
    assert normalize_atomizer_response(response) == [
        {"id": "c1", "claim": "First fact.", "evidence": [
            {"id": "c1e1", "quote": "Exact quote."},
        ]},
        {"id": "c2", "claim": "Second fact.", "evidence": []},
    ]


@pytest.mark.parametrize("response", [
    {"claims": "not-a-list"},
    {"claims": [{"claim": "fact", "evidence": [], "id": "model-id"}]},
    {"claims": [{"claim": " ", "evidence": []}]},
    {"claims": [{"claim": "fact", "evidence": [{"quote": ""}]}]},
])
def test_atomizer_validator_is_strict(response):
    assert not is_valid_atomizer_response(response)


def test_atomizer_limits_are_enforced_without_partial_normalization():
    response = {"claims": [{"claim": "fact", "evidence": [
        {"quote": "one"}, {"quote": "two"},
    ]}]}
    limits = AtomizationLimits(max_claims=1, max_evidence_per_claim=1, max_quote_chars=3)
    assert not is_valid_atomizer_response(response, limits)
    with pytest.raises(ValueError, match="Invalid atomizer"):
        normalize_atomizer_response(response, limits)


def test_quote_presence_retains_all_states_and_locations():
    source = "<!-- page 2 -->\n# Results\nUnique quote. Repeated. Repeated."
    results_start = source.index("# Results")
    sections = [{
        "title": "Results", "short_name": "results",
        "start_offset": results_start, "end_offset": len(source),
    }]
    result = process_quote_presence([
        {"id": "c1e1", "quote": "Unique quote."},
        {"id": "c1e2", "quote": "missing"},
        {"id": "c1e3", "quote": "Repeated."},
    ], source, allowed_start=results_start, allowed_end=len(source), sections=sections)

    assert [item["quote_status"] for item in result["evidence"]] == [
        "present", "not_found", "ambiguous",
    ]
    assert result["evidence"][0]["section"] == "Results"
    assert result["evidence"][0]["page"] == 2
    assert result["quote_presence_status"] == "partial"
    assert result["quote_presence_counts"] == {
        "total": 3, "present": 1, "not_found": 1, "ambiguous": 1,
    }


def test_quote_presence_obeys_allowed_span_and_empty_aggregate():
    source = "outside quote | inside quote"
    start = source.index("inside")
    result = process_quote_presence(
        [{"id": "c1e1", "quote": "outside quote"}], source,
        allowed_start=start, allowed_end=len(source),
    )
    assert result["evidence"] == [
        {"id": "c1e1", "quote": "outside quote", "quote_status": "not_found"},
    ]
    assert aggregate_quote_presence([]) == {
        "quote_presence_status": "partial",
        "quote_presence_counts": {"total": 0, "present": 0, "not_found": 0, "ambiguous": 0},
    }


def test_apply_quote_presence_aggregates_across_claims():
    claims = normalize_atomizer_response({"claims": [
        {"claim": "one", "evidence": [{"quote": "alpha"}]},
        {"claim": "two", "evidence": [{"quote": "beta"}]},
    ]})
    result = apply_quote_presence(claims, "alpha beta")
    assert result["quote_presence_status"] == "complete"
    assert result["quote_presence_counts"]["present"] == 2


@pytest.mark.parametrize("label", [
    "supported", "partially_supported", "unsupported", "contradicted",
    "insufficient_evidence",
])
def test_verifier_accepts_every_model_label(label):
    response = {"status": label, "rationale": "A bounded reason.", "evidence_ids": ["c1e1"]}
    assert is_valid_verifier_response(response, {"c1e1"})
    assert normalize_verifier_response(response, {"c1e1"}) == response


@pytest.mark.parametrize("response", [
    {"status": "not_assessed", "rationale": "local only", "evidence_ids": []},
    {"status": "supported", "rationale": " ", "evidence_ids": ["c1e1"]},
    {"status": "supported", "rationale": "ok", "evidence_ids": ["unknown"]},
    {"status": "supported", "rationale": "ok", "evidence_ids": [], "extra": True},
])
def test_verifier_rejects_local_label_blank_unknown_ids_and_extra_fields(response):
    assert not is_valid_verifier_response(response, {"c1e1"})


def test_support_aggregates_cover_empty_terminal_and_mixed_states():
    assert aggregate_claim_support([])["claim_support_status"] == "not_applicable"
    assert aggregate_claim_support(["supported", "supported"])["claim_support_status"] == "supported"
    assert aggregate_claim_support(["unsupported"])["claim_support_status"] == "unsupported"
    assert aggregate_claim_support(["supported", "contradicted"])["claim_support_status"] == "contradicted"
    assert aggregate_claim_support(["insufficient_evidence"])["claim_support_status"] == "partial"
    mixed = aggregate_claim_support(["partially_supported", "not_assessed"])
    assert mixed["claim_support_status"] == "partial"
    assert mixed["claim_support_counts"]["not_assessed"] == 1


def test_context_is_clipped_merged_source_ordered_and_preserves_quotes():
    source = "OUTSIDE\n\nFirst paragraph has quote one and quote two.\n\nLast paragraph.\n\nOUTSIDE"
    allowed_start = source.index("First")
    allowed_end = source.index("\n\nOUTSIDE", allowed_start)
    quote_one = "quote one"
    quote_two = "quote two"
    evidence = [
        {"id": "c1e2", "quote": quote_two, "quote_status": "present", "offset": source.index(quote_two)},
        {"id": "c1e1", "quote": quote_one, "quote_status": "present", "offset": source.index(quote_one)},
        {"id": "c1e3", "quote": "absent", "quote_status": "not_found"},
    ]
    contexts = build_verifier_context(
        source, evidence, allowed_start=allowed_start, allowed_end=allowed_end,
    )

    assert len(contexts) == 1
    assert contexts[0]["start_offset"] == allowed_start
    assert contexts[0]["end_offset"] == source.index("\n\nLast")
    assert quote_one in contexts[0]["text"] and quote_two in contexts[0]["text"]
    assert contexts[0]["evidence_ids"] == ["c1e1", "c1e2"]


def test_context_limit_rejects_instead_of_truncating_quote():
    source = "A very long exact quote in one paragraph."
    quote = "very long exact quote"
    evidence = [{
        "id": "c1e1", "quote": quote, "quote_status": "present", "offset": source.index(quote),
    }]
    with pytest.raises(ValueError, match="not truncated"):
        build_verifier_context(source, evidence, max_context_chars=5)
