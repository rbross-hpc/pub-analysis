# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline validation and verification for evidence-backed distillations.

This module intentionally has no LLM or artifact I/O dependency.  Callers supply
canonical source text in the coordinate system described by the selected scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n")

QUOTE_STATUSES = ("present", "not_found", "ambiguous")
SUPPORT_LABELS = (
    "supported",
    "partially_supported",
    "unsupported",
    "contradicted",
    "insufficient_evidence",
    "not_assessed",
)
VERIFIER_LABELS = SUPPORT_LABELS[:-1]


@dataclass(frozen=True)
class AtomizationLimits:
    max_claims: int = 20
    max_evidence_per_claim: int = 4
    max_quote_chars: int = 4000

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


def is_valid_response(response: Any) -> bool:
    """Return whether *response* has the required structured evidence shape.

    The answer and every quote must be non-blank strings. Each evidence item
    must be a mapping with a ``quote`` field. Extra response and
    evidence-item fields are deliberately permitted for forward compatibility.
    """
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("answer"), str)
        or not response["answer"].strip()
    ):
        return False
    evidence = response.get("evidence")
    if not isinstance(evidence, list):
        return False
    return all(
        isinstance(item, dict)
        and isinstance(item.get("quote"), str)
        and bool(item["quote"].strip())
        for item in evidence
    )


# An explicit alias reads naturally at LLM call sites while preserving the
# compact predicate name for callers that only need the structural check.
validate_response = is_valid_response
validate_response_schema = is_valid_response


def is_valid_atomizer_response(
    response: Any,
    limits: AtomizationLimits | None = None,
) -> bool:
    """Strictly validate the model atomizer response before normalization."""
    limits = limits or AtomizationLimits()
    if not isinstance(response, dict) or set(response) != {"claims"}:
        return False
    claims = response["claims"]
    if not isinstance(claims, list) or len(claims) > limits.max_claims:
        return False
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"claim", "evidence"}:
            return False
        if not isinstance(claim["claim"], str) or not claim["claim"].strip():
            return False
        evidence = claim["evidence"]
        if not isinstance(evidence, list) or len(evidence) > limits.max_evidence_per_claim:
            return False
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"quote"}:
                return False
            quote = item["quote"]
            if not isinstance(quote, str) or not quote.strip() or len(quote) > limits.max_quote_chars:
                return False
    return True


def normalize_atomizer_response(
    response: Any,
    limits: AtomizationLimits | None = None,
) -> list[dict[str, Any]]:
    """Assign stable local claim/evidence IDs to a valid atomizer response."""
    limits = limits or AtomizationLimits()
    if not is_valid_atomizer_response(response, limits):
        raise ValueError("Invalid atomizer response")
    normalized: list[dict[str, Any]] = []
    for claim_index, item in enumerate(response["claims"], start=1):
        claim_id = f"c{claim_index}"
        evidence = [
            {"id": f"{claim_id}e{evidence_index}", "quote": quote["quote"]}
            for evidence_index, quote in enumerate(item["evidence"], start=1)
        ]
        normalized.append({"id": claim_id, "claim": item["claim"].strip(), "evidence": evidence})
    return normalized


validate_atomizer_response = is_valid_atomizer_response


def is_valid_verifier_response(
    response: Any,
    known_evidence_ids: set[str] | list[str] | tuple[str, ...],
    *,
    max_rationale_chars: int = 1000,
) -> bool:
    """Validate a verifier response, including its evidence-ID trust boundary."""
    if not isinstance(max_rationale_chars, int) or isinstance(max_rationale_chars, bool) or max_rationale_chars < 1:
        raise ValueError("max_rationale_chars must be a positive integer")
    if not isinstance(response, dict) or set(response) != {"status", "rationale", "evidence_ids"}:
        return False
    rationale = response["rationale"]
    evidence_ids = response["evidence_ids"]
    known = set(known_evidence_ids)
    return (
        response["status"] in VERIFIER_LABELS
        and isinstance(rationale, str)
        and bool(rationale.strip())
        and len(rationale) <= max_rationale_chars
        and isinstance(evidence_ids, list)
        and all(isinstance(item, str) for item in evidence_ids)
        and len(evidence_ids) == len(set(evidence_ids))
        and set(evidence_ids) <= known
    )


def normalize_verifier_response(
    response: Any,
    known_evidence_ids: set[str] | list[str] | tuple[str, ...],
    *,
    max_rationale_chars: int = 1000,
) -> dict[str, Any]:
    """Return the persisted support assessment after strict validation."""
    if not is_valid_verifier_response(
        response, known_evidence_ids, max_rationale_chars=max_rationale_chars,
    ):
        raise ValueError("Invalid verifier response")
    return {
        "status": response["status"],
        "rationale": response["rationale"].strip(),
        "evidence_ids": list(response["evidence_ids"]),
    }


validate_verifier_response = is_valid_verifier_response


def _matching_offsets(text: str, quote: str, start: int, end: int) -> list[int]:
    """Return every exact, possibly overlapping, occurrence in ``[start, end)``."""
    offsets: list[int] = []
    offset = text.find(quote, start, end)
    while offset != -1:
        if offset + len(quote) <= end:
            offsets.append(offset)
        offset = text.find(quote, offset + 1, end)
    return offsets


def _containing_section(offset: int, quote_length: int, sections: list[dict[str, Any]]) -> str | None:
    quote_end = offset + quote_length
    for section in sections:
        start = section.get("start_offset")
        end = section.get("end_offset")
        if isinstance(start, int) and isinstance(end, int) and start <= offset and quote_end <= end:
            title = section.get("title")
            return title if isinstance(title, str) else None
    return None


def _page_at_offset(source: str, offset: int) -> int | None:
    page: int | None = None
    for marker in _PAGE_MARKER_RE.finditer(source):
        if marker.start() > offset:
            break
        page = int(marker.group(1))
    return page


def _validate_span(source: str, allowed_start: int, allowed_end: int | None) -> tuple[int, int]:
    end = len(source) if allowed_end is None else allowed_end
    if (
        not isinstance(allowed_start, int) or isinstance(allowed_start, bool)
        or not isinstance(end, int) or isinstance(end, bool)
        or not 0 <= allowed_start <= end <= len(source)
    ):
        raise ValueError("allowed span must be within the canonical source")
    return allowed_start, end


def aggregate_quote_presence(items: list[dict[str, Any]] | list[str]) -> dict[str, Any]:
    """Return deterministic quote-presence counts and aggregate status."""
    statuses = [item.get("quote_status") if isinstance(item, dict) else item for item in items]
    if any(status not in QUOTE_STATUSES for status in statuses):
        raise ValueError("Unknown quote status")
    counts = {"total": len(statuses), **{status: statuses.count(status) for status in QUOTE_STATUSES}}
    status = "complete" if statuses and counts["present"] == counts["total"] else "partial"
    return {"quote_presence_status": status, "quote_presence_counts": counts}


def process_quote_presence(
    evidence: list[dict[str, Any]],
    source: str,
    *,
    allowed_start: int = 0,
    allowed_end: int | None = None,
    sections: list[dict[str, Any]] | None = None,
    abstract: bool = False,
) -> dict[str, Any]:
    """Locate locally-IDed quotes exactly within the permitted canonical span."""
    if not isinstance(source, str):
        raise TypeError("canonical source must be a string")
    start, end = _validate_span(source, allowed_start, allowed_end)
    results: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("quote"), str) or not item["quote"].strip():
            raise ValueError("evidence items require a non-blank quote")
        result = {key: item[key] for key in ("id", "quote") if key in item}
        quote = item["quote"]
        offsets = _matching_offsets(source, quote, start, end)
        if not offsets:
            result["quote_status"] = "not_found"
        elif len(offsets) > 1:
            result["quote_status"] = "ambiguous"
        else:
            offset = offsets[0]
            result.update({
                "quote_status": "present",
                "offset": offset,
                "section": None if abstract else _containing_section(offset, len(quote), sections or []),
                "page": None if abstract else _page_at_offset(source, offset),
            })
        results.append(result)
    return {"evidence": results, **aggregate_quote_presence(results)}


def apply_quote_presence(
    claims: list[dict[str, Any]],
    source: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Apply quote-presence processing to normalized claims without dropping quotes."""
    processed_claims: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []
    for claim in claims:
        presence = process_quote_presence(claim.get("evidence", []), source, **kwargs)
        processed = {**claim, "evidence": presence["evidence"]}
        processed_claims.append(processed)
        all_evidence.extend(presence["evidence"])
    return {"claims": processed_claims, **aggregate_quote_presence(all_evidence)}


def aggregate_claim_support(claims_or_statuses: list[dict[str, Any]] | list[str]) -> dict[str, Any]:
    """Count claim labels and derive the schema-v2 aggregate support status."""
    statuses: list[Any] = []
    for item in claims_or_statuses:
        if isinstance(item, str):
            statuses.append(item)
        elif isinstance(item, dict):
            assessment = item.get("support_assessment")
            statuses.append(assessment.get("status") if isinstance(assessment, dict) else None)
        else:
            statuses.append(None)
    if any(status not in SUPPORT_LABELS for status in statuses):
        raise ValueError("Unknown claim support status")
    counts = {"total": len(statuses), **{label: statuses.count(label) for label in SUPPORT_LABELS}}
    if not statuses:
        aggregate = "not_applicable"
    elif counts["contradicted"]:
        aggregate = "contradicted"
    elif counts["supported"] == counts["total"]:
        aggregate = "supported"
    elif counts["unsupported"] == counts["total"]:
        aggregate = "unsupported"
    else:
        aggregate = "partial"
    return {"claim_support_status": aggregate, "claim_support_counts": counts}


def _paragraph_span(source: str, quote_start: int, quote_end: int, start: int, end: int) -> tuple[int, int]:
    left = start
    for boundary in _PARAGRAPH_BREAK_RE.finditer(source, start, quote_start):
        left = boundary.end()
    boundary = _PARAGRAPH_BREAK_RE.search(source, quote_end, end)
    right = boundary.start() if boundary else end
    return left, right


def build_verifier_context(
    source: str,
    evidence: list[dict[str, Any]],
    *,
    allowed_start: int = 0,
    allowed_end: int | None = None,
    max_context_chars: int | None = None,
) -> list[dict[str, Any]]:
    """Expand present quotes to clipped paragraph spans and merge overlaps."""
    start, end = _validate_span(source, allowed_start, allowed_end)
    spans: list[tuple[int, int, int, str]] = []
    for item in evidence:
        if item.get("quote_status") != "present":
            continue
        quote, offset, evidence_id = item.get("quote"), item.get("offset"), item.get("id")
        if not isinstance(quote, str) or not isinstance(offset, int) or not isinstance(evidence_id, str):
            raise ValueError("present evidence requires id, quote, and offset")
        quote_end = offset + len(quote)
        if offset < start or quote_end > end or source[offset:quote_end] != quote:
            raise ValueError("present evidence does not match the permitted canonical source")
        context_start, context_end = _paragraph_span(source, offset, quote_end, start, end)
        spans.append((context_start, context_end, offset, evidence_id))

    merged: list[dict[str, Any]] = []
    for context_start, context_end, _, evidence_id in sorted(spans):
        if merged and context_start < merged[-1]["end_offset"]:
            merged[-1]["end_offset"] = max(merged[-1]["end_offset"], context_end)
            merged[-1]["evidence_ids"].append(evidence_id)
        else:
            merged.append({
                "start_offset": context_start,
                "end_offset": context_end,
                "evidence_ids": [evidence_id],
            })
    for context in merged:
        context["text"] = source[context["start_offset"]:context["end_offset"]]
    total_chars = sum(len(context["text"]) for context in merged)
    if max_context_chars is not None:
        if not isinstance(max_context_chars, int) or isinstance(max_context_chars, bool) or max_context_chars < 1:
            raise ValueError("max_context_chars must be a positive integer")
        if total_chars > max_context_chars:
            raise ValueError("verifier context exceeds max_context_chars; quotes were not truncated")
    return merged


def verify_evidence(
    evidence: list[dict[str, Any]],
    scope: str,
    source: str,
    *,
    sections: list[dict[str, Any]] | None = None,
    section_name: str | None = None,
) -> dict[str, Any]:
    """Verify exact evidence quotes against a scope's canonical source.

    ``source`` is the literal bib abstract for ``abstract`` scope and raw,
    unmodified ``paper.md`` for narrative, full, and section scopes.  Offsets in
    returned items always address that exact supplied string.  For section scope,
    ``section_name`` identifies the allowed span in ``sections``.
    """
    if scope not in {"abstract", "narrative", "full", "section"}:
        raise ValueError(f"Unknown evidence scope: {scope!r}")
    if not isinstance(source, str):
        raise TypeError("evidence source must be a string")

    section_entries = sections or []
    allowed_start, allowed_end = 0, len(source)
    if scope == "section":
        matching_section = next(
            (entry for entry in section_entries if entry.get("short_name") == section_name), None
        )
        if matching_section is None:
            raise ValueError(f"Section {section_name!r} is not available for evidence verification")
        allowed_start = matching_section.get("start_offset")
        allowed_end = matching_section.get("end_offset")
        if not isinstance(allowed_start, int) or not isinstance(allowed_end, int):
            raise ValueError("Section evidence span must have integer start_offset and end_offset")

    verified_items: list[dict[str, Any]] = []
    for item in evidence:
        quote = item["quote"]
        offsets = _matching_offsets(source, quote, allowed_start, allowed_end)
        result: dict[str, Any] = {"quote": quote}
        if not offsets:
            result.update({"status": "unverified", "reason": "no_match"})
        elif len(offsets) > 1:
            result.update({"status": "unverified", "reason": "ambiguous_match"})
        else:
            offset = offsets[0]
            result.update({
                "status": "verified",
                "offset": offset,
                "section": None if scope == "abstract" else _containing_section(offset, len(quote), section_entries),
                "page": None if scope == "abstract" else _page_at_offset(source, offset),
            })
        verified_items.append(result)

    status = "verified" if verified_items and all(item["status"] == "verified" for item in verified_items) else "partial"
    return {"evidence": verified_items, "evidence_status": status}


verify = verify_evidence
