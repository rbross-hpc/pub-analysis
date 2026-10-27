# BSD 3-Clause License
"""Offline tests for human distillation review overlays."""
from __future__ import annotations

from copy import deepcopy

import pytest

from puba.distill.review import (
    apply_packet,
    artifact_digest,
    claim_fingerprint,
    clear_review,
    export_packet,
    load_review,
    resolve_review,
    validate_review,
    write_review,
)


def _record() -> dict:
    claims = [
        {
            "id": "c1",
            "claim": "First claim.",
            "evidence": [{
                "id": "c1e1", "quote": "First quote.", "quote_status": "present",
                "offset": 10, "section": "Results", "page": 2,
            }],
            "support_assessment": {
                "status": "partially_supported", "rationale": "Only partly stated.",
                "evidence_ids": ["c1e1"],
            },
        },
        {
            "id": "c2",
            "claim": "Second claim.",
            "evidence": [{
                "id": "c2e1", "quote": "Second quote.", "quote_status": "present",
                "offset": 20, "section": "Results", "page": 2,
            }],
            "support_assessment": {
                "status": "supported", "rationale": "Directly stated.",
                "evidence_ids": ["c2e1"],
            },
        },
    ]
    return {
        "schema_version": 2,
        "name": "impact",
        "scope": "narrative",
        "evidence_mode": "claims",
        "model": "generator",
        "verifier_model": "verifier",
        "generated_at": "2026-10-27T00:00:00+00:00",
        "output": "First claim. Second claim.",
        "prompt": "Assess impact.",
        "claims": claims,
        "quote_presence_status": "complete",
        "quote_presence_counts": {"total": 2, "present": 2, "not_found": 0, "ambiguous": 0},
        "claim_support_status": "partial",
        "claim_support_counts": {
            "total": 2, "supported": 1, "partially_supported": 1,
            "unsupported": 0, "contradicted": 0, "insufficient_evidence": 0,
            "not_assessed": 0,
        },
    }


def _base_packet(record: dict) -> dict:
    return {"base_artifact_sha256": artifact_digest(record)}


def test_export_packet_preserves_generated_content():
    record = _record()
    packet = export_packet(record)

    assert packet["generated_output"] == record["output"]
    assert packet["base_artifact_sha256"] == artifact_digest(record)
    assert [item["claim_id"] for item in packet["claims"]] == ["c1", "c2"]
    assert packet["claims"][0]["model_assessment"]["status"] == "partially_supported"


def test_claim_fingerprints_distinguish_identical_claims_by_generated_id():
    record = _record()
    duplicate = deepcopy(record["claims"][0])
    duplicate["id"] = "c2"

    assert claim_fingerprint(record["claims"][0]) != claim_fingerprint(duplicate)


def test_apply_accept_and_correct_produces_complete_human_aggregate(monkeypatch):
    from puba.distill import review as module

    monkeypatch.setattr(module, "now_iso", lambda: "2026-10-27T01:00:00+00:00")
    record = _record()
    first, second = [claim_fingerprint(claim) for claim in record["claims"]]
    packet = {
        **_base_packet(record),
        "reviewer": "Author",
        "claim_reviews": [
            {"claim_fingerprint": first, "decision": "correct", "status": "supported",
             "rationale": "The narrower claim is directly supported.",
             "evidence_ids": ["c1e1"]},
            {"claim_fingerprint": second, "decision": "accept"},
        ],
    }

    review = apply_packet(record, packet)
    resolved = resolve_review(record, review)

    assert resolved["review_status"] == "complete"
    assert resolved["accepted"] == 1
    assert resolved["corrected"] == 1
    assert resolved["human_claim_support_status"] == "supported"
    assert resolved["human_claim_support_counts"]["supported"] == 2
    assert all(item["reviewer"] == "Author" for item in review["claim_reviews"])


def test_remove_duplicate_excludes_claim_without_answer_override():
    record = _record()
    first, second = [claim_fingerprint(claim) for claim in record["claims"]]
    review = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [
            {"claim_fingerprint": first, "decision": "remove",
             "removal_reason": "duplicate", "rationale": "Duplicates c2.",
             "duplicate_of": second},
            {"claim_fingerprint": second, "decision": "accept"},
        ],
    })

    resolved = resolve_review(record, review)

    assert resolved["review_status"] == "complete"
    assert resolved["removed"] == 1
    assert resolved["effective_claims"] == 1
    assert resolved["human_claim_support_status"] == "supported"
    assert resolved["effective_output_source"] == "generated"


def test_remove_from_answer_requires_matching_override():
    record = _record()
    first = claim_fingerprint(record["claims"][0])
    decision = {"claim_fingerprint": first, "decision": "remove",
                "removal_reason": "remove_from_answer", "rationale": "Author removed it."}

    with pytest.raises(ValueError, match="requires an answer_override"):
        apply_packet(record, {**_base_packet(record), "claim_reviews": [decision]})

    with pytest.raises(ValueError, match="identify all"):
        apply_packet(record, {
            **_base_packet(record), "claim_reviews": [decision],
            "answer_override": {"value": "Second claim.", "no_new_claims": True,
                                "removed_claim_fingerprints": [], "reason": "Remove first."},
        })

    review = apply_packet(record, {
        **_base_packet(record), "claim_reviews": [decision],
        "answer_override": {"value": "Second claim.", "no_new_claims": True,
                            "removed_claim_fingerprints": [first], "reason": "Remove first."},
    })
    resolved = resolve_review(record, review)
    assert resolved["effective_output"] == "Second claim."
    assert resolved["effective_output_source"] == "human_override"


def test_answer_override_rejects_new_claims_attestation_or_blank_reason():
    record = _record()
    for override in (
        {"value": "Edited.", "no_new_claims": False,
         "removed_claim_fingerprints": [], "reason": "Edit."},
        {"value": "Edited.", "no_new_claims": True,
         "removed_claim_fingerprints": [], "reason": ""},
    ):
        with pytest.raises(ValueError):
            apply_packet(record, {**_base_packet(record), "answer_override": override})


def test_apply_rejects_fields_irrelevant_to_decision_and_rebase_only_fields():
    record = _record()
    first = claim_fingerprint(record["claims"][0])

    with pytest.raises(ValueError, match="invalid for its decision"):
        apply_packet(record, {
            **_base_packet(record),
            "claim_reviews": [{
                "claim_fingerprint": first, "decision": "accept", "rationale": "Ignored.",
            }],
        })
    with pytest.raises(ValueError, match="only valid when rebasing"):
        apply_packet(record, {
            **_base_packet(record), "carry_forward_claims": [first],
        })
    with pytest.raises(ValueError, match="must be a boolean"):
        apply_packet(record, {
            **_base_packet(record), "clear_answer_override": "yes",
        })


def test_partial_packets_merge_on_current_base():
    record = _record()
    first, second = [claim_fingerprint(claim) for claim in record["claims"]]
    existing = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{"claim_fingerprint": first, "decision": "accept"}],
    })
    merged = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{"claim_fingerprint": second, "decision": "accept"}],
    }, existing)

    assert resolve_review(record, merged)["review_status"] == "complete"
    assert len(merged["history"]) == 2


def test_changed_generated_artifact_makes_review_stale_and_disables_override():
    record = _record()
    review = apply_packet(record, {
        **_base_packet(record),
        "answer_override": {"value": "Edited answer.", "no_new_claims": True,
                            "removed_claim_fingerprints": [], "reason": "Clarity."},
    })
    changed = deepcopy(record)
    changed["output"] = "New generated answer."

    resolved = resolve_review(changed, review)

    assert resolved["review_status"] == "stale"
    assert resolved["effective_output"] == "New generated answer."
    assert resolved["effective_output_source"] == "generated"
    with pytest.raises(ValueError, match="use review rebase"):
        apply_packet(changed, _base_packet(changed), review)


def test_rebase_carries_only_exact_claim_and_requires_answer_resubmission():
    record = _record()
    first = claim_fingerprint(record["claims"][0])
    review = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{"claim_fingerprint": first, "decision": "accept"}],
        "answer_override": {"value": "Edited answer.", "no_new_claims": True,
                            "removed_claim_fingerprints": [], "reason": "Clarity."},
    })
    changed = deepcopy(record)
    changed["generated_at"] = "2026-10-28T00:00:00+00:00"

    rebased = apply_packet(changed, {
        **_base_packet(changed), "carry_forward_claims": [first],
    }, review, rebase=True)

    assert len(rebased["claim_reviews"]) == 1
    assert rebased["answer_override"] is None
    assert resolve_review(changed, rebased)["review_status"] == "in_progress"
    assert rebased["history"][-1]["before"]["base"] == review["base"]
    assert rebased["history"][-1]["after"]["base"] == rebased["base"]
    assert rebased["claim_reviews"][0]["reviewed_at"] == review["claim_reviews"][0]["reviewed_at"]


def test_rebase_rejects_changed_assessment_for_carried_claim():
    record = _record()
    first = claim_fingerprint(record["claims"][0])
    review = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{"claim_fingerprint": first, "decision": "accept"}],
    })
    changed = deepcopy(record)
    changed["claims"][0]["support_assessment"]["status"] = "supported"
    changed["generated_at"] = "2026-10-28T00:00:00+00:00"

    with pytest.raises(ValueError, match="changed model assessment"):
        apply_packet(changed, {
            **_base_packet(changed), "carry_forward_claims": [first],
        }, review, rebase=True)


def test_rebase_requires_stale_existing_review_and_exact_evidence():
    record = _record()
    first = claim_fingerprint(record["claims"][0])
    review = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{"claim_fingerprint": first, "decision": "accept"}],
    })

    with pytest.raises(ValueError, match="stale existing review"):
        apply_packet(record, _base_packet(record), review, rebase=True)
    with pytest.raises(ValueError, match="stale existing review"):
        apply_packet(record, _base_packet(record), rebase=True)

    changed = deepcopy(record)
    changed["generated_at"] = "2026-10-28T00:00:00+00:00"
    changed["claims"][0]["evidence"][0]["offset"] = 11
    assert claim_fingerprint(changed["claims"][0]) != first
    with pytest.raises(ValueError, match="no exact current match"):
        apply_packet(changed, {
            **_base_packet(changed), "carry_forward_claims": [first],
        }, review, rebase=True)


def test_write_and_load_review_round_trip(tmp_path):
    record = _record()
    review = apply_packet(record, _base_packet(record))

    path = write_review(tmp_path, "impact", review)

    assert path == tmp_path / "reviews" / "impact.yaml"
    assert load_review(tmp_path, "impact") == review

    with pytest.raises(ValueError, match="does not match"):
        write_review(tmp_path, "other", review)


def test_clear_preserves_history_and_selected_review_parts():
    record = _record()
    first = claim_fingerprint(record["claims"][0])
    review = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{"claim_fingerprint": first, "decision": "accept"}],
        "answer_override": {"value": "Edited.", "no_new_claims": True,
                            "removed_claim_fingerprints": [], "reason": "Clarity."},
    })

    cleared = clear_review(
        record, review, clear_claims=True, clear_answer=False, reviewer="Author",
    )

    assert cleared["claim_reviews"] == []
    assert cleared["answer_override"]["value"] == "Edited."
    assert cleared["history"][-1]["action"] == "cleared"
    assert cleared["history"][-1]["before"]["claim_reviews"][0]["decision"] == "accept"
    assert cleared["history"][-1]["after"]["claim_reviews"] == []


def test_clear_keeps_remove_from_answer_decisions_and_override_coupled():
    record = _record()
    first = claim_fingerprint(record["claims"][0])
    review = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{
            "claim_fingerprint": first, "decision": "remove",
            "removal_reason": "remove_from_answer", "rationale": "Not retained.",
        }],
        "answer_override": {
            "value": "Second claim.", "no_new_claims": True,
            "removed_claim_fingerprints": [first], "reason": "Removed first claim.",
        },
    })

    with pytest.raises(ValueError, match="also requires clearing the answer"):
        clear_review(record, review, clear_claims=True, clear_answer=False)
    with pytest.raises(ValueError, match="also requires clearing remove_from_answer"):
        clear_review(record, review, clear_claims=False, clear_answer=True)

    cleared = clear_review(record, review, clear_claims=True, clear_answer=True)
    assert cleared["claim_reviews"] == []
    assert cleared["answer_override"] is None


def test_apply_history_retains_superseded_decision_values():
    record = _record()
    first = claim_fingerprint(record["claims"][0])
    accepted = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{"claim_fingerprint": first, "decision": "accept"}],
    })
    corrected = apply_packet(record, {
        **_base_packet(record),
        "claim_reviews": [{
            "claim_fingerprint": first, "decision": "correct", "status": "supported",
            "rationale": "Direct support.", "evidence_ids": ["c1e1"],
        }],
    }, accepted)

    last = corrected["history"][-1]
    assert last["before"]["claim_reviews"][0]["decision"] == "accept"
    assert last["after"]["claim_reviews"][0]["decision"] == "correct"

    broken_chain = deepcopy(corrected)
    broken_chain["history"][-1]["before"]["claim_reviews"] = []
    with pytest.raises(ValueError, match="not continuous"):
        validate_review(broken_chain)

    truncated = deepcopy(corrected)
    truncated["history"] = truncated["history"][1:]
    with pytest.raises(ValueError, match="empty state"):
        validate_review(truncated)


def test_resolve_cross_checks_complete_base_provenance():
    record = _record()
    review = apply_packet(record, _base_packet(record))
    review["base"]["output_sha256"] = "0" * 64
    review["history"][-1]["after"]["base"]["output_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="base provenance"):
        resolve_review(record, review)


def test_review_supports_legacy_artifact_without_generation_timestamp():
    record = _record()
    del record["generated_at"]

    review = apply_packet(record, _base_packet(record))

    assert review["base"]["generated_at"] == "not-recorded"
    assert resolve_review(record, review)["applicability"] == "current"


def test_apply_does_not_mutate_generated_record():
    record = _record()
    original = deepcopy(record)
    apply_packet(record, {
        **_base_packet(record),
        "answer_override": {"value": "Edited.", "no_new_claims": True,
                            "removed_claim_fingerprints": [], "reason": "Clarity."},
    })
    assert record == original
