# BSD 3-Clause License
"""CLI coverage for agent-driven human distillation review."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from puba.cli import app

runner = CliRunner()


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 review")
    ad = tmp_path / "paper.puba"
    analyses = ad / "analyses"
    analyses.mkdir(parents=True)
    record = {
        "schema_version": 2, "name": "impact", "scope": "narrative",
        "evidence_mode": "claims", "model": "generator", "verifier_model": "verifier",
        "generated_at": "2026-10-27T00:00:00+00:00", "prompt": "Assess impact.",
        "output": "Generated answer.",
        "claims": [{
            "id": "c1", "claim": "A claim.",
            "evidence": [{"id": "c1e1", "quote": "A quote.",
                          "quote_status": "present", "offset": 5}],
            "support_assessment": {"status": "partially_supported",
                                   "rationale": "Partial.", "evidence_ids": ["c1e1"]},
        }],
        "quote_presence_status": "complete",
        "quote_presence_counts": {"total": 1, "present": 1, "not_found": 0, "ambiguous": 0},
        "claim_support_status": "partial",
        "claim_support_counts": {"total": 1, "supported": 0, "partially_supported": 1,
                                 "unsupported": 0, "contradicted": 0,
                                 "insufficient_evidence": 0, "not_assessed": 0},
    }
    path = analyses / "impact.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return pdf, ad, path, record


def test_review_export_apply_show_and_original(tmp_path: Path):
    pdf, ad, artifact, _ = _setup(tmp_path)
    original_bytes = artifact.read_bytes()

    exported = runner.invoke(app, ["review", "export", str(pdf), "impact"])
    assert exported.exit_code == 0, exported.output
    envelope = json.loads(exported.output)
    packet = envelope["review_packet"]
    fingerprint = packet["claims"][0]["claim_fingerprint"]
    apply_input = {
        "base_artifact_sha256": packet["base_artifact_sha256"],
        "reviewer": "Author",
        "claim_reviews": [{
            "claim_fingerprint": fingerprint, "decision": "correct",
            "status": "supported", "rationale": "Direct support.",
            "evidence_ids": ["c1e1"],
        }],
        "answer_override": {
            "value": "Human-edited answer.", "no_new_claims": True,
            "removed_claim_fingerprints": [], "reason": "Clarity.",
        },
    }
    packet_path = tmp_path / "review.json"
    packet_path.write_text(json.dumps(apply_input), encoding="utf-8")

    applied = runner.invoke(app, [
        "review", "apply", str(pdf), "impact", "--json-file", str(packet_path),
    ])
    assert applied.exit_code == 0, applied.output
    applied_data = json.loads(applied.output)
    assert applied_data["resolved"]["review_status"] == "complete"
    assert applied_data["resolved"]["human_claim_support_status"] == "supported"
    assert artifact.read_bytes() == original_bytes
    assert (ad / "reviews" / "impact.yaml").exists()

    effective = runner.invoke(app, ["show", "distill", str(pdf), "impact"])
    assert effective.exit_code == 0
    assert effective.output.strip() == "Human-edited answer."
    original = runner.invoke(app, ["show", "distill", str(pdf), "impact", "--original"])
    assert original.output.strip() == "Generated answer."
    shown = runner.invoke(app, ["show", "distill", str(pdf), "impact", "--json"])
    shown_data = json.loads(shown.output)
    assert shown_data["output"] == "Generated answer."
    assert shown_data["effective_output"] == "Human-edited answer."
    assert shown_data["effective_output_source"] == "human_override"
    assert shown_data["review_resolution"]["corrected"] == 1

    status = runner.invoke(app, ["review", "show", str(pdf), "impact", "--json"])
    assert json.loads(status.output)["resolved"]["review_status"] == "complete"
    listed = runner.invoke(app, ["review", "list", str(pdf), "--json"])
    listed_data = json.loads(listed.output)
    assert listed_data["reviews"][0]["review_status"] == "complete"
    assert listed_data["reviews"][0]["effective_output_source"] == "human_override"


def test_review_apply_invalid_packet_is_atomic(tmp_path: Path):
    pdf, ad, _, _ = _setup(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"base_artifact_sha256": "wrong"}), encoding="utf-8")

    result = runner.invoke(app, [
        "review", "apply", str(pdf), "impact", "--json-file", str(bad),
    ])

    assert result.exit_code == 2
    assert json.loads(result.output)["ok"] is False
    assert not (ad / "reviews").exists()


def test_review_apply_resolution_failure_does_not_replace_sidecar(tmp_path: Path):
    from puba.distill.review import (
        apply_packet, artifact_digest, claim_fingerprint, write_review,
    )

    pdf, ad, _, record = _setup(tmp_path)
    fingerprint = claim_fingerprint(record["claims"][0])
    review = apply_packet(record, {
        "base_artifact_sha256": artifact_digest(record),
        "claim_reviews": [{"claim_fingerprint": fingerprint, "decision": "accept"}],
    })
    review["claim_reviews"][0]["observed_claim"] = "Tampered claim."
    review["history"][-1]["after"]["claim_reviews"][0]["observed_claim"] = "Tampered claim."
    path = write_review(ad, "impact", review)
    original_bytes = path.read_bytes()
    packet = tmp_path / "review.json"
    packet.write_text(json.dumps({
        "base_artifact_sha256": artifact_digest(record),
    }), encoding="utf-8")

    result = runner.invoke(app, [
        "review", "apply", str(pdf), "impact", "--json-file", str(packet),
    ])

    assert result.exit_code == 2
    assert "provenance does not match" in result.output
    assert path.read_bytes() == original_bytes


def test_review_clear_retains_sidecar_history(tmp_path: Path):
    pdf, ad, _, _ = _setup(tmp_path)
    exported = json.loads(runner.invoke(
        app, ["review", "export", str(pdf), "impact"],
    ).output)["review_packet"]
    fingerprint = exported["claims"][0]["claim_fingerprint"]
    packet = tmp_path / "review.json"
    packet.write_text(json.dumps({
        "base_artifact_sha256": exported["base_artifact_sha256"],
        "claim_reviews": [{"claim_fingerprint": fingerprint, "decision": "accept"}],
    }), encoding="utf-8")
    runner.invoke(app, ["review", "apply", str(pdf), "impact", "--json-file", str(packet)])

    cleared = runner.invoke(app, ["review", "clear", str(pdf), "impact", "--claims"])

    assert cleared.exit_code == 0, cleared.output
    data = json.loads(cleared.output)
    assert data["review"]["claim_reviews"] == []
    assert data["review"]["history"][-1]["action"] == "cleared"
    assert (ad / "reviews" / "impact.yaml").exists()


def test_clean_all_preserves_reviews_and_explicit_delete_requires_yes(tmp_path: Path):
    pdf, ad, _, _ = _setup(tmp_path)
    reviews = ad / "reviews"
    reviews.mkdir()
    (reviews / "impact.yaml").write_text("human data", encoding="utf-8")

    cleaned = runner.invoke(app, ["clean", str(pdf), "--what", "all"])
    assert cleaned.exit_code == 0, cleaned.output
    assert (reviews / "impact.yaml").read_text(encoding="utf-8") == "human data"

    refused = runner.invoke(app, ["clean", str(pdf), "--what", "reviews"])
    assert refused.exit_code == 2
    assert reviews.exists()

    deleted = runner.invoke(app, ["clean", str(pdf), "--what", "reviews", "--yes"])
    assert deleted.exit_code == 0, deleted.output
    assert not reviews.exists()
