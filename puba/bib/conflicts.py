# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Conflict detection between tier-1 source results."""
from __future__ import annotations

from typing import Any

from ..bib.sources._common import similarity, first_author_surname
from .. import config as cfg


def detect_conflicts(results: dict[str, dict | None]) -> dict[str, list[dict]]:
    """Given a dict of {source_name: summary_dict|None}, return conflicts per field.

    Only considers sources that returned a non-None result.
    Returns {field: [{source, value}, ...]} for fields where sources disagree.
    """
    thresholds = cfg.bib().get("conflict_thresholds", {})
    title_min = thresholds.get("title_sim_min", 0.85)
    year_max = thresholds.get("year_diff_max", 1)
    venue_min = thresholds.get("venue_sim_min", 0.70)
    author_must = thresholds.get("author_surname_must_match", True)
    doi_must = thresholds.get("doi_must_match", True)

    active = {src: res for src, res in results.items() if res}
    if len(active) < 2:
        return {}

    conflicts: dict[str, list[dict]] = {}

    def _add_conflict(field: str, values: list[tuple[str, Any]]) -> None:
        conflicts[field] = [{"source": src, "value": val} for src, val in values]

    # Title conflicts
    titles = [(src, r.get("title")) for src, r in active.items() if r.get("title")]
    if len(titles) >= 2:
        for i in range(len(titles)):
            for j in range(i + 1, len(titles)):
                sim = similarity(titles[i][1], titles[j][1])
                if sim < title_min:
                    _add_conflict("title", titles)
                    break
            else:
                continue
            break

    # Year conflicts
    years = [(src, r.get("year")) for src, r in active.items() if r.get("year")]
    if len(years) >= 2:
        year_vals = [y for _, y in years]
        if max(year_vals) - min(year_vals) > year_max:
            _add_conflict("year", years)

    # DOI conflicts
    if doi_must:
        dois = [(src, r.get("doi")) for src, r in active.items() if r.get("doi")]
        if len(dois) >= 2:
            doi_vals = set(d for _, d in dois)
            if len(doi_vals) > 1:
                _add_conflict("doi", dois)

    # First-author surname conflicts
    if author_must:
        authors = [(src, r.get("authors")) for src, r in active.items() if r.get("authors")]
        if len(authors) >= 2:
            surnames = [(src, first_author_surname(auths)) for src, auths in authors]
            surnames = [(src, s) for src, s in surnames if s]
            if len(surnames) >= 2:
                unique_surnames = set(s for _, s in surnames)
                if len(unique_surnames) > 1:
                    _add_conflict("authors", [(src, r.get("authors")) for src, r in active.items() if r.get("authors")])

    # Venue conflicts (looser threshold, only if both have venues)
    venues = [(src, r.get("venue")) for src, r in active.items() if r.get("venue")]
    if len(venues) >= 2:
        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                sim = similarity(venues[i][1], venues[j][1])
                if sim < venue_min:
                    _add_conflict("venue", venues)
                    break
            else:
                continue
            break

    return conflicts
