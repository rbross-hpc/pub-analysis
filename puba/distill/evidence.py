# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline validation and verification for evidence-backed distillations.

This module intentionally has no LLM or artifact I/O dependency.  Callers supply
canonical source text in the coordinate system described by the selected scope.
"""
from __future__ import annotations

import re
from typing import Any

_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")


def is_valid_response(response: Any) -> bool:
    """Return whether *response* has the required structured evidence shape.

    The answer may be any string (including an empty one).  Each evidence item
    must be a mapping with a non-blank string ``quote``.  Extra response and
    evidence-item fields are deliberately permitted for forward compatibility.
    """
    if not isinstance(response, dict) or not isinstance(response.get("answer"), str):
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
