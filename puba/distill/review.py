# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Human review overlays for generated distillation records."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..distill.evidence import VERIFIER_LABELS, aggregate_claim_support
from ..io import atomic_write_yaml, now_iso

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DECISIONS = {"accept", "correct", "remove"}
_REMOVAL_REASONS = {"duplicate", "not_a_claim", "out_of_scope", "remove_from_answer"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def review_path(analysis_dir: Path, name: str) -> Path:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid distillation review name: {name!r}")
    return analysis_dir / "reviews" / f"{name}.yaml"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def artifact_digest(record: dict[str, Any]) -> str:
    return _digest(record)


def output_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(record["output"].encode()).hexdigest()


def claim_fingerprint(claim: dict[str, Any]) -> str:
    return _digest({
        "id": claim.get("id"),
        "claim": claim.get("claim"),
        "evidence": claim.get("evidence", []),
    })


def assessment_digest(claim: dict[str, Any]) -> str:
    return _digest(claim.get("support_assessment"))


def _claim_index(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        claim_fingerprint(claim): claim
        for claim in record.get("claims", [])
        if isinstance(claim, dict)
    }


def load_review(analysis_dir: Path, name: str) -> dict[str, Any] | None:
    path = review_path(analysis_dir, name)
    if not path.exists():
        return None
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    review = validate_review(value)
    if review["analysis_name"] != name:
        raise ValueError("review analysis_name does not match its filename")
    return review


def validate_review(value: Any) -> dict[str, Any]:
    """Strictly validate a persisted review sidecar."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("review schema_version must be 1")
    allowed = {"schema_version", "analysis_name", "base", "claim_reviews",
               "answer_override", "history"}
    if set(value) - allowed:
        raise ValueError("review contains unknown fields")
    name = value.get("analysis_name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError("review analysis_name is invalid")
    base = value.get("base")
    _validate_base(base, "review base")
    claim_reviews = value.get("claim_reviews")
    if not isinstance(claim_reviews, list):
        raise ValueError("claim_reviews must be a list")
    fingerprints: set[str] = set()
    for item in claim_reviews:
        _validate_persisted_decision(item)
        fingerprint = item["claim_fingerprint"]
        if fingerprint in fingerprints:
            raise ValueError("claim_reviews contain duplicate fingerprints")
        fingerprints.add(fingerprint)
    override = value.get("answer_override")
    if override is not None:
        _validate_persisted_override(override)
    _validate_review_relations(claim_reviews, override)
    history = value.get("history")
    if not isinstance(history, list) or not history:
        raise ValueError("review history must be a non-empty list")
    for entry in history:
        _validate_history_entry(entry)
    if history[0]["before"] != _snapshot(None, [], None):
        raise ValueError("review history does not begin from an empty state")
    for previous, current in zip(history, history[1:]):
        if previous["after"] != current["before"]:
            raise ValueError("review history snapshots are not continuous")
        if _parse_timestamp(previous["at"]) > _parse_timestamp(current["at"]):
            raise ValueError("review history timestamps are not chronological")
    terminal = _snapshot(base, claim_reviews, override)
    if history[-1]["after"] != terminal:
        raise ValueError("review history does not match the current review state")
    return deepcopy(value)


def _require_optional_identity(value: Any, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{field} must be a non-blank string when provided")


def _require_sha256(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _require_timestamp(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = _parse_timestamp(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_base(value: Any, field: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "artifact_sha256", "output_sha256", "generated_at",
    }:
        raise ValueError(f"{field} is invalid")
    _require_sha256(value.get("artifact_sha256"), f"{field} artifact_sha256")
    _require_sha256(value.get("output_sha256"), f"{field} output_sha256")
    generated_at = value.get("generated_at")
    if generated_at != "not-recorded":
        _require_timestamp(generated_at, f"{field} generated_at")


def _validate_persisted_decision(item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError("claim review decisions must be objects")
    common = {
        "claim_fingerprint", "observed_claim_id", "observed_claim",
        "model_assessment_sha256", "decision", "reviewer", "reviewed_at",
    }
    decision = item.get("decision")
    allowed = set(common)
    if decision == "correct":
        allowed |= {"status", "rationale", "evidence_ids"}
    elif decision == "remove":
        allowed |= {"removal_reason", "rationale", "duplicate_of"}
    if set(item) != allowed - ({"duplicate_of"} if decision == "remove" and "duplicate_of" not in item else set()):
        raise ValueError("claim review decision has missing or unknown fields")
    for field in ("claim_fingerprint", "observed_claim_id", "observed_claim",
                  "reviewed_at"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            raise ValueError(f"claim review {field} must be a non-blank string")
    _require_sha256(item.get("claim_fingerprint"), "claim review claim_fingerprint")
    _require_sha256(item.get("model_assessment_sha256"), "claim review model_assessment_sha256")
    _require_timestamp(item.get("reviewed_at"), "claim review reviewed_at")
    _require_optional_identity(item.get("reviewer"), "reviewer")
    if decision not in _DECISIONS:
        raise ValueError("claim review decision is invalid")
    if decision == "correct":
        if item.get("status") not in VERIFIER_LABELS:
            raise ValueError("corrected claim status is invalid")
        if not isinstance(item.get("rationale"), str) or not item["rationale"].strip():
            raise ValueError("corrected claims require a rationale")
        if not isinstance(item.get("evidence_ids"), list) or any(
            not isinstance(entry, str) for entry in item["evidence_ids"]
        ) or len(item["evidence_ids"]) != len(set(item["evidence_ids"])):
            raise ValueError("corrected claim evidence_ids are invalid")
    if decision == "remove":
        if item.get("removal_reason") not in _REMOVAL_REASONS:
            raise ValueError("claim removal reason is invalid")
        if not isinstance(item.get("rationale"), str) or not item["rationale"].strip():
            raise ValueError("removed claims require a rationale")
        duplicate_of = item.get("duplicate_of")
        if item["removal_reason"] == "duplicate":
            if not isinstance(duplicate_of, str) or not duplicate_of:
                raise ValueError("duplicate claim removals require duplicate_of")
        elif duplicate_of is not None:
            raise ValueError("duplicate_of is only valid for duplicate removals")


def _validate_persisted_override(override: Any) -> None:
    if not isinstance(override, dict) or set(override) != {
        "value", "no_new_claims", "removed_claim_fingerprints", "reason",
        "reviewer", "updated_at", "base_output_sha256",
    }:
        raise ValueError("answer_override has missing or unknown fields")
    if not isinstance(override["value"], str) or not override["value"].strip():
        raise ValueError("answer override value must be non-blank")
    if override["no_new_claims"] is not True:
        raise ValueError("answer override must attest no_new_claims: true")
    if not isinstance(override["removed_claim_fingerprints"], list) or any(
        not isinstance(item, str) for item in override["removed_claim_fingerprints"]
    ) or len(override["removed_claim_fingerprints"]) != len(
        set(override["removed_claim_fingerprints"])
    ):
        raise ValueError("answer override removed_claim_fingerprints are invalid")
    for field in ("reason", "updated_at", "base_output_sha256"):
        if not isinstance(override[field], str) or not override[field].strip():
            raise ValueError(f"answer override {field} must be non-blank")
    _require_timestamp(override["updated_at"], "answer override updated_at")
    _require_sha256(override["base_output_sha256"], "answer override base_output_sha256")
    _require_optional_identity(override.get("reviewer"), "reviewer")


def _validate_review_relations(
    claim_reviews: list[dict[str, Any]], override: dict[str, Any] | None,
) -> None:
    removed = {
        item["claim_fingerprint"]: item
        for item in claim_reviews if item["decision"] == "remove"
    }
    for fingerprint, item in removed.items():
        if item["removal_reason"] == "duplicate" and (
            item["duplicate_of"] == fingerprint or item["duplicate_of"] in removed
        ):
            raise ValueError("duplicate_of must reference an effective claim")
    substantive = {
        fingerprint for fingerprint, item in removed.items()
        if item["removal_reason"] == "remove_from_answer"
    }
    declared = set(override["removed_claim_fingerprints"]) if override else set()
    if substantive != declared:
        raise ValueError("remove_from_answer decisions and answer override must match")


def _validate_snapshot(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "base", "claim_reviews", "answer_override",
    }:
        raise ValueError("review history snapshot is invalid")
    if value["base"] is not None:
        _validate_base(value["base"], "review history base")
    decisions = value["claim_reviews"]
    if not isinstance(decisions, list):
        raise ValueError("review history claim_reviews must be a list")
    fingerprints: set[str] = set()
    for item in decisions:
        _validate_persisted_decision(item)
        if item["claim_fingerprint"] in fingerprints:
            raise ValueError("review history contains duplicate claim fingerprints")
        fingerprints.add(item["claim_fingerprint"])
    override = value["answer_override"]
    if override is not None:
        _validate_persisted_override(override)
    _validate_review_relations(decisions, override)


def _validate_history_entry(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "action", "at", "reviewer", "before", "after",
    }:
        raise ValueError("review history entry is invalid")
    if value["action"] not in {"applied", "rebased", "cleared"}:
        raise ValueError("review history action is invalid")
    _require_timestamp(value["at"], "review history at")
    _require_optional_identity(value["reviewer"], "review history reviewer")
    _validate_snapshot(value["before"])
    _validate_snapshot(value["after"])


def _snapshot(
    base: dict[str, str] | None,
    claim_reviews: list[dict[str, Any]],
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "base": deepcopy(base),
        "claim_reviews": deepcopy(claim_reviews),
        "answer_override": deepcopy(override),
    }


def export_packet(record: dict[str, Any], review: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return an agent-friendly review packet for a generated record."""
    claims = []
    for claim in record.get("claims", []):
        claims.append({
            "claim_fingerprint": claim_fingerprint(claim),
            "claim_id": claim["id"],
            "claim": claim["claim"],
            "model_assessment": deepcopy(claim["support_assessment"]),
            "model_assessment_sha256": assessment_digest(claim),
            "evidence": deepcopy(claim["evidence"]),
        })
    return {
        "schema_version": 1,
        "analysis_name": record.get("name"),
        "base_artifact_sha256": artifact_digest(record),
        "base_output_sha256": output_digest(record),
        "generated_at": record.get("generated_at"),
        "generated_output": record["output"],
        "claims": claims,
        "existing_review": deepcopy(review),
        "instructions": {
            "decisions": ["accept", "correct", "remove"],
            "removal_reasons": sorted(_REMOVAL_REASONS),
            "reviewer_optional": True,
        },
    }


def _base(record: dict[str, Any]) -> dict[str, str]:
    generated_at = record.get("generated_at")
    if generated_at is None:
        generated_at = "not-recorded"
    elif generated_at != "not-recorded":
        _require_timestamp(generated_at, "generated artifact generated_at")
    return {
        "artifact_sha256": artifact_digest(record),
        "output_sha256": output_digest(record),
        "generated_at": generated_at,
    }


def _decision_from_input(
    raw: Any,
    claim: dict[str, Any],
    *,
    reviewer: str | None,
    reviewed_at: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("claim review decisions must be objects")
    decision = raw.get("decision")
    if decision not in _DECISIONS:
        raise ValueError("claim review decision must be accept, correct, or remove")
    allowed = {"claim_fingerprint", "decision", "reviewer"}
    if decision == "correct":
        allowed |= {"status", "rationale", "evidence_ids"}
    elif decision == "remove":
        allowed |= {"removal_reason", "rationale", "duplicate_of"}
    if set(raw) - allowed:
        raise ValueError("claim review input contains fields invalid for its decision")
    fingerprint = claim_fingerprint(claim)
    item: dict[str, Any] = {
        "claim_fingerprint": fingerprint,
        "observed_claim_id": claim["id"],
        "observed_claim": claim["claim"],
        "model_assessment_sha256": assessment_digest(claim),
        "decision": decision,
        "reviewer": raw.get("reviewer", reviewer),
        "reviewed_at": reviewed_at,
    }
    _require_optional_identity(item["reviewer"], "reviewer")
    if decision == "correct":
        item.update({
            "status": raw.get("status"),
            "rationale": raw.get("rationale"),
            "evidence_ids": raw.get("evidence_ids"),
        })
        present_ids = {
            evidence["id"] for evidence in claim.get("evidence", [])
            if evidence.get("quote_status") == "present"
        }
        if not isinstance(item["evidence_ids"], list) or not set(item["evidence_ids"]) <= present_ids:
            raise ValueError("corrected claim evidence_ids must reference present evidence")
    elif decision == "remove":
        item.update({
            "removal_reason": raw.get("removal_reason"),
            "rationale": raw.get("rationale"),
        })
        if "duplicate_of" in raw:
            item["duplicate_of"] = raw["duplicate_of"]
    _validate_persisted_decision(item)
    return item


def apply_packet(
    record: dict[str, Any],
    packet: Any,
    existing: dict[str, Any] | None = None,
    *,
    rebase: bool = False,
) -> dict[str, Any]:
    """Validate and apply a complete or partial agent-authored review packet."""
    if not isinstance(packet, dict):
        raise ValueError("review packet must be an object")
    allowed = {
        "base_artifact_sha256", "reviewer", "claim_reviews", "answer_override",
        "clear_answer_override", "carry_forward_claims",
    }
    if set(packet) - allowed:
        raise ValueError("review packet contains unknown fields")
    current_digest = artifact_digest(record)
    if packet.get("base_artifact_sha256") != current_digest:
        raise ValueError("review packet does not target the current generated artifact")
    reviewer = packet.get("reviewer")
    _require_optional_identity(reviewer, "reviewer")
    if existing is not None:
        existing = validate_review(existing)
        if existing["analysis_name"] != record.get("name"):
            raise ValueError("existing review targets a different analysis")
        existing_current = existing["base"]["artifact_sha256"] == current_digest
        if rebase and existing_current:
            raise ValueError("review rebase requires a stale existing review")
        if not rebase and not existing_current:
            raise ValueError("existing review is stale; use review rebase")
    elif rebase:
        raise ValueError("review rebase requires a stale existing review")
    now = now_iso()
    claims = _claim_index(record)
    if record.get("evidence_mode") != "claims" and (
        packet.get("claim_reviews") or packet.get("carry_forward_claims")
    ):
        raise ValueError("claim reviews require a claims-mode distillation")

    merged: dict[str, dict[str, Any]] = {}
    if existing and existing["base"]["artifact_sha256"] == current_digest:
        merged = {item["claim_fingerprint"]: deepcopy(item) for item in existing["claim_reviews"]}
    if rebase and existing:
        old = {item["claim_fingerprint"]: item for item in existing["claim_reviews"]}
        carry = packet.get("carry_forward_claims", [])
        if not isinstance(carry, list) or any(not isinstance(item, str) for item in carry):
            raise ValueError("carry_forward_claims must be a list of fingerprints")
        for fingerprint in carry:
            if fingerprint not in claims or fingerprint not in old:
                raise ValueError("carried claim review has no exact current match")
            if old[fingerprint]["model_assessment_sha256"] != assessment_digest(claims[fingerprint]):
                raise ValueError("changed model assessment requires a new decision")
            carried = deepcopy(old[fingerprint])
            if carried["decision"] == "correct":
                present_ids = {
                    item["id"] for item in claims[fingerprint].get("evidence", [])
                    if item.get("quote_status") == "present"
                }
                if not set(carried["evidence_ids"]) <= present_ids:
                    raise ValueError("changed evidence IDs require a new decision")
            carried["observed_claim_id"] = claims[fingerprint]["id"]
            merged[fingerprint] = carried
    elif packet.get("carry_forward_claims"):
        raise ValueError("carry_forward_claims is only valid when rebasing")

    if "clear_answer_override" in packet and not isinstance(packet["clear_answer_override"], bool):
        raise ValueError("clear_answer_override must be a boolean")

    raw_decisions = packet.get("claim_reviews", [])
    if not isinstance(raw_decisions, list):
        raise ValueError("claim_reviews must be a list")
    seen: set[str] = set()
    for raw in raw_decisions:
        if not isinstance(raw, dict) or not isinstance(raw.get("claim_fingerprint"), str):
            raise ValueError("claim review requires claim_fingerprint")
        fingerprint = raw["claim_fingerprint"]
        if fingerprint in seen or fingerprint not in claims:
            raise ValueError("claim review fingerprint is duplicate or unknown")
        seen.add(fingerprint)
        merged[fingerprint] = _decision_from_input(
            raw, claims[fingerprint], reviewer=reviewer, reviewed_at=now,
        )

    removed = {
        fingerprint: item for fingerprint, item in merged.items()
        if item["decision"] == "remove"
    }
    for fingerprint, item in removed.items():
        if item["removal_reason"] == "duplicate":
            duplicate_of = item["duplicate_of"]
            if duplicate_of == fingerprint or duplicate_of not in claims or duplicate_of in removed:
                raise ValueError("duplicate_of must reference an effective current claim")

    previous_override = None
    if existing and existing["base"]["artifact_sha256"] == current_digest:
        previous_override = existing.get("answer_override")
    if rebase:
        previous_override = None
    override = previous_override
    if packet.get("clear_answer_override") is True:
        override = None
    elif "answer_override" in packet:
        raw_override = packet["answer_override"]
        if not isinstance(raw_override, dict):
            raise ValueError("answer_override must be an object")
        if set(raw_override) - {
            "value", "no_new_claims", "removed_claim_fingerprints", "reason", "reviewer",
        }:
            raise ValueError("answer_override contains unknown fields")
        override = {
            "value": raw_override.get("value"),
            "no_new_claims": raw_override.get("no_new_claims"),
            "removed_claim_fingerprints": raw_override.get("removed_claim_fingerprints", []),
            "reason": raw_override.get("reason"),
            "reviewer": raw_override.get("reviewer", reviewer),
            "updated_at": now,
            "base_output_sha256": output_digest(record),
        }
        _validate_persisted_override(override)

    substantive_removals = {
        fingerprint for fingerprint, item in removed.items()
        if item["removal_reason"] == "remove_from_answer"
    }
    if substantive_removals:
        if override is None:
            raise ValueError("remove_from_answer requires an answer_override")
        declared = set(override["removed_claim_fingerprints"])
        if declared != substantive_removals:
            raise ValueError("answer_override must identify all remove_from_answer claims")
    elif override is not None and override["removed_claim_fingerprints"]:
        raise ValueError("answer_override names claims not removed from the answer")

    claim_reviews = [merged[key] for key in sorted(merged)]
    current_base = _base(record)
    before = _snapshot(
        existing["base"] if existing else None,
        existing["claim_reviews"] if existing else [],
        existing.get("answer_override") if existing else None,
    )
    after = _snapshot(current_base, claim_reviews, override)
    history = deepcopy(existing.get("history", [])) if existing else []
    history.append({
        "action": "rebased" if rebase else "applied",
        "at": now,
        "reviewer": reviewer,
        "before": before,
        "after": after,
    })
    result = {
        "schema_version": 1,
        "analysis_name": record.get("name"),
        "base": current_base,
        "claim_reviews": claim_reviews,
        "answer_override": override,
        "history": history,
    }
    return validate_review(result)


def resolve_review(record: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve generated values and an optional human overlay without mutation."""
    generated = record["output"]
    base_result: dict[str, Any] = {
        "applicability": "none",
        "review_status": "not_started",
        "effective_output": generated,
        "effective_output_source": "generated",
        "answer_override": None,
        "claim_reviews": {},
        "reviewed": 0,
        "total_claims": len(record.get("claims", [])),
        "accepted": 0,
        "corrected": 0,
        "removed": 0,
        "effective_claims": len(record.get("claims", [])),
    }
    if review is None:
        return base_result
    review = validate_review(review)
    if review["analysis_name"] != record.get("name") or (
        review["base"]["artifact_sha256"] != artifact_digest(record)
    ):
        return {**base_result, "applicability": "stale", "review_status": "stale"}
    if review["base"] != _base(record):
        raise ValueError("current review base provenance does not match the artifact")

    decisions = {item["claim_fingerprint"]: item for item in review["claim_reviews"]}
    claims = _claim_index(record)
    if len(claims) != len(record.get("claims", [])):
        raise ValueError("generated artifact contains duplicate claim identities")
    active_decisions = {key: value for key, value in decisions.items() if key in claims}
    if len(active_decisions) != len(decisions):
        raise ValueError("current review references an unknown claim")
    removed_fingerprints = {
        key for key, item in active_decisions.items() if item["decision"] == "remove"
    }
    for fingerprint, decision in active_decisions.items():
        claim = claims[fingerprint]
        if decision["observed_claim_id"] != claim["id"] or (
            decision["observed_claim"] != claim["claim"]
        ):
            raise ValueError("current review claim provenance does not match the artifact")
        if decision["model_assessment_sha256"] != assessment_digest(claim):
            raise ValueError("current review model assessment does not match the artifact")
        if decision["decision"] == "correct":
            present_ids = {
                item["id"] for item in claim.get("evidence", [])
                if item.get("quote_status") == "present"
            }
            if not set(decision["evidence_ids"]) <= present_ids:
                raise ValueError("current review references unavailable evidence")
        if decision["decision"] == "remove" and decision["removal_reason"] == "duplicate":
            duplicate_of = decision["duplicate_of"]
            if duplicate_of not in claims or duplicate_of in removed_fingerprints:
                raise ValueError("current duplicate removal does not reference an effective claim")
    override = review.get("answer_override")
    if override is not None and override["base_output_sha256"] != output_digest(record):
        raise ValueError("current answer override does not match the generated output")
    accepted = sum(item["decision"] == "accept" for item in active_decisions.values())
    corrected = sum(item["decision"] == "correct" for item in active_decisions.values())
    removed = sum(item["decision"] == "remove" for item in active_decisions.values())
    total = len(claims)
    reviewed = len(active_decisions)
    status = "complete" if reviewed == total else "in_progress" if reviewed else "not_started"
    result = {
        **base_result,
        "applicability": "current",
        "review_status": status,
        "claim_reviews": active_decisions,
        "reviewed": reviewed,
        "total_claims": total,
        "accepted": accepted,
        "corrected": corrected,
        "removed": removed,
        "effective_claims": total - removed,
    }
    if override is not None:
        result.update({
            "effective_output": override["value"],
            "effective_output_source": "human_override",
            "answer_override": deepcopy(override),
        })
    if status == "complete":
        effective_statuses = []
        for fingerprint, claim in claims.items():
            decision = active_decisions[fingerprint]
            if decision["decision"] == "remove":
                continue
            if decision["decision"] == "correct":
                effective_statuses.append(decision["status"])
            else:
                effective_statuses.append(claim["support_assessment"]["status"])
        aggregate = aggregate_claim_support(effective_statuses)
        result["human_claim_support_status"] = aggregate["claim_support_status"]
        result["human_claim_support_counts"] = aggregate["claim_support_counts"]
    return result


def write_review(analysis_dir: Path, name: str, review: dict[str, Any]) -> Path:
    validated = validate_review(review)
    if validated["analysis_name"] != name:
        raise ValueError("review analysis_name does not match its filename")
    path = review_path(analysis_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_yaml(path, validated)
    return path


def clear_review(
    record: dict[str, Any],
    existing: dict[str, Any],
    *,
    clear_claims: bool,
    clear_answer: bool,
    reviewer: str | None = None,
) -> dict[str, Any]:
    if not clear_claims and not clear_answer:
        raise ValueError("clear requires claims, answer, or both")
    existing = validate_review(existing)
    substantive_removals = any(
        item["decision"] == "remove" and item["removal_reason"] == "remove_from_answer"
        for item in existing["claim_reviews"]
    )
    override_has_removals = bool(
        existing.get("answer_override")
        and existing["answer_override"]["removed_claim_fingerprints"]
    )
    if clear_claims and not clear_answer and override_has_removals:
        raise ValueError("clearing remove_from_answer claims also requires clearing the answer")
    if clear_answer and not clear_claims and substantive_removals:
        raise ValueError("clearing the answer also requires clearing remove_from_answer claims")
    now = now_iso()
    claim_reviews = [] if clear_claims else deepcopy(existing["claim_reviews"])
    answer_override = None if clear_answer else deepcopy(existing.get("answer_override"))
    history = deepcopy(existing["history"])
    history.append({
        "action": "cleared",
        "at": now,
        "reviewer": reviewer,
        "before": _snapshot(
            existing["base"], existing["claim_reviews"], existing.get("answer_override"),
        ),
        "after": _snapshot(existing["base"], claim_reviews, answer_override),
    })
    result = {
        **deepcopy(existing),
        "base": deepcopy(existing["base"]),
        "claim_reviews": claim_reviews,
        "answer_override": answer_override,
        "history": history,
    }
    return validate_review(result)
