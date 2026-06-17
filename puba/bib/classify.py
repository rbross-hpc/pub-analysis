# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Config-driven category classifier. Cascade order is in code; lists are in config.yaml."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from .. import config as cfg

_ARXIV_DOI_PREFIX = "10.48550/arxiv"


@lru_cache(maxsize=1)
def _cls() -> dict[str, Any]:
    return cfg.bib().get("classification", {})


def _patterns(key: str) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in _cls().get(key, [])]


def classify(
    doi: str | None,
    arxiv_id: str | None,
    venue: str | None,
    crossref_type: str | None = None,
) -> tuple[str, str]:
    """Return (category, rule_name). rule_name is recorded in provenance lookup_key."""
    cls = _cls()
    doi_norm = (doi or "").strip().lower()
    venue_s = (venue or "").strip()
    arxiv_id_s = (arxiv_id or "").strip()

    # Rule 1: arXiv preprint — arXiv ID set AND no real DOI
    if arxiv_id_s and (not doi_norm or doi_norm.startswith(_ARXIV_DOI_PREFIX)):
        return "arxiv preprint", "arxiv_id:no-real-doi"

    # Rule 2: arXiv ID + real DOI → classify by venue (fall through)

    # Rule 3: preprint server by DOI prefix
    for prefix in cls.get("preprint_doi_prefixes", []):
        if doi_norm.startswith(prefix.lower()):
            return "preprint", f"preprint_doi_prefix:{prefix}"

    # Rule 3b: preprint server by URL/venue
    for host in cls.get("preprint_hosts", []):
        if host.lower() in venue_s.lower() or host.lower() in doi_norm:
            return "preprint", f"preprint_host:{host}"

    # Rule 4: CrossRef/OpenAlex type — book, thesis, technical report
    if crossref_type:
        for ct in cls.get("book_crossref_types", []):
            if crossref_type.lower() == ct.lower():
                cat = "book chapter" if "chapter" in ct else "book"
                return cat, f"crossref_type:{crossref_type}"
        for ct in cls.get("technical_report_crossref_types", []):
            if crossref_type.lower() == ct.lower():
                return "technical report", f"crossref_type:{crossref_type}"
        for ct in cls.get("thesis_crossref_types", []):
            if crossref_type.lower() == ct.lower():
                return "thesis", f"crossref_type:{crossref_type}"

    # Rule 5: thesis by venue pattern
    for pat in _patterns("thesis_venue_patterns"):
        if pat.search(venue_s):
            return "thesis", f"thesis_venue_pattern:{pat.pattern}"

    # Rule 6: technical report by venue pattern
    for pat in _patterns("technical_report_venue_patterns"):
        if pat.search(venue_s):
            return "technical report", f"technical_report_venue_pattern:{pat.pattern}"

    # Rule 7: workshop
    for pat in _patterns("workshop_patterns"):
        if pat.search(venue_s):
            return "workshop paper", f"workshop_pattern:{pat.pattern}"

    # Rule 8: conference acronym (whole-word match against venue)
    for acronym in cls.get("conference_acronyms", []):
        pattern = re.compile(rf"(?<![A-Za-z]){re.escape(acronym)}(?![A-Za-z])")
        if pattern.search(venue_s):
            return "conference paper", f"conference_acronym:{acronym}"

    # Rule 9: conference venue pattern
    for pat in _patterns("conference_venue_patterns"):
        if pat.search(venue_s):
            return "conference paper", f"conference_venue_pattern:{pat.pattern}"

    # Rule 10: journal — fallback when venue is set and nothing else matched
    if venue_s:
        return "journal article", "derived:venue-present-no-conference-signal"

    return "other", "derived:no-rule-matched"
