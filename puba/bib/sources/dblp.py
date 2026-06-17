# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from ref-checker DBLP client.
"""DBLP bibliographic source client."""
from __future__ import annotations

from typing import Any

from ._common import base_session, normalize_doi, polite_wait, safe_get, similarity

_BASE = "https://dblp.org/search/publ/api"

_DBLP_TYPE_MAP = {
    "Journal Articles": "journal article",
    "Conference and Workshop Papers": "conference paper",
    "Parts in Books or Collections": "book chapter",
    "Books and Theses": "book",
    "Editorship": "book",
    "Reference Works": "other",
    "Informal and Other Publications": "other",
}


def _session() -> Any:
    return base_session()


def _summarize(hit: dict) -> dict[str, Any]:
    info = hit.get("info", {})
    raw_type = info.get("type", "")
    category = _DBLP_TYPE_MAP.get(raw_type, "other")
    authors_raw = info.get("authors", {}).get("author", [])
    if isinstance(authors_raw, str):
        authors_raw = [authors_raw]
    authors = [
        (a.get("text") if isinstance(a, dict) else str(a))
        for a in authors_raw
        if a
    ]
    venue = info.get("venue")
    doi = normalize_doi(info.get("doi"))
    year = info.get("year")
    return {
        "title": info.get("title"),
        "authors": [a for a in authors if a],
        "year": int(year) if year and str(year).isdigit() else None,
        "publication_date": str(year) if year else None,
        "venue": venue,
        "doi": doi,
        "url": info.get("ee") or info.get("url"),
        "category": category,
        "raw_type": raw_type,
    }


def search_by_title(title: str) -> tuple[dict | None, float | None]:
    polite_wait("dblp")
    resp = safe_get(
        _session(),
        _BASE,
        params={"q": title, "format": "json", "h": 5},
    )
    if resp is None or resp.status_code != 200:
        return None, None
    try:
        hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
    except Exception:
        return None, None
    if not hits:
        return None, None
    best = max(hits, key=lambda h: similarity(title, h.get("info", {}).get("title")))
    best_title = best.get("info", {}).get("title", "")
    sim = similarity(title, best_title)
    return _summarize(best), sim
