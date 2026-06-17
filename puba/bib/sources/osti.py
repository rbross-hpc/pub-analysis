# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from annual-report/annual_report/api/osti.py
"""OSTI bibliographic source client."""
from __future__ import annotations

from typing import Any

from ._common import base_session, normalize_doi, polite_wait, safe_get, similarity

_BASE = "https://www.osti.gov/api/v1/records"


def _session() -> Any:
    return base_session()


def _summarize(record: dict) -> dict[str, Any]:
    links = record.get("links") or []
    citation_url = next(
        (lnk.get("href") for lnk in links if lnk.get("rel") == "citation"),
        None,
    )
    authors_raw = record.get("authors") or []
    if isinstance(authors_raw, list):
        authors = [
            a.get("name") or f"{a.get('first_name', '')} {a.get('last_name', '')}".strip()
            for a in authors_raw
            if isinstance(a, dict)
        ]
    else:
        authors = []
    return {
        "osti_id": str(record.get("osti_id") or ""),
        "doi": normalize_doi(record.get("doi")),
        "title": record.get("title"),
        "authors": [a for a in authors if a],
        "year": int(record.get("publication_date", "")[:4]) if record.get("publication_date", "") else None,
        "publication_date": record.get("publication_date"),
        "venue": record.get("journal_name") or record.get("publisher"),
        "url": citation_url,
        "category": "technical report" if not record.get("journal_name") else "journal article",
    }


def search_by_doi(doi: str) -> tuple[dict | None, float | None]:
    doi = normalize_doi(doi)
    if not doi:
        return None, None
    polite_wait("osti")
    resp = safe_get(_session(), _BASE, params={"doi": doi})
    if resp is not None and resp.status_code == 200:
        records = resp.json()
        if isinstance(records, list) and records:
            return _summarize(records[0]), 1.0
    return None, None


def search_by_title(title: str) -> tuple[dict | None, float | None]:
    polite_wait("osti")
    resp = safe_get(_session(), _BASE, params={"title": title})
    if resp is not None and resp.status_code == 200:
        records = resp.json()
        if isinstance(records, list) and records:
            best = max(records, key=lambda r: similarity(title, r.get("title")))
            sim = similarity(title, best.get("title"))
            return _summarize(best), sim
    return None, None
