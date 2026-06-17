# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from annual-report/annual_report/api/openalex.py
"""OpenAlex bibliographic source client."""
from __future__ import annotations

import os
from typing import Any

from ._common import (
    base_session, normalize_doi, polite_wait, safe_get, similarity,
    is_arxiv_doi,
)

_BASE = "https://api.openalex.org/works"

_OA_TYPE_MAP = {
    "journal-article":   "journal article",
    "proceedings-article": "conference paper",
    "book-chapter":      "book chapter",
    "book":              "book",
    "dissertation":      "thesis",
    "preprint":          "preprint",
    "dataset":           "other",
    "other":             "other",
}


def _session() -> Any:
    s = base_session()
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        s.headers["Authorization"] = f"Bearer {api_key}"
    return s


def _reconstruct_abstract(inv_index: dict | None) -> str | None:
    if not inv_index:
        return None
    words: dict[int, str] = {}
    for word, positions in inv_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words)) or None


def _summarize(work: dict) -> dict[str, Any]:
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    venue = source.get("display_name")
    raw_type = work.get("type", "")
    category = _OA_TYPE_MAP.get(raw_type, "other")
    doi = normalize_doi(work.get("doi"))
    authors = [
        a.get("author", {}).get("display_name") or ""
        for a in work.get("authorships", [])
    ]
    keywords = [
        kw.get("display_name", "") for kw in work.get("keywords", [])
        if kw.get("display_name")
    ]
    oa_status = (work.get("open_access") or {}).get("oa_status")
    return {
        "title": work.get("display_name"),
        "authors": [a for a in authors if a],
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "venue": venue,
        "doi": doi if not is_arxiv_doi(doi) else None,
        "arxiv_doi": doi if is_arxiv_doi(doi) else None,
        "url": loc.get("landing_page_url") or work.get("id"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "category": category,
        "oa_status": oa_status,
        "keywords": keywords,
        "raw_type": raw_type,
        "openalex_id": work.get("id"),
    }


def get_by_doi(doi: str) -> tuple[dict | None, float | None]:
    doi = normalize_doi(doi)
    if not doi:
        return None, None
    polite_wait("openalex")
    resp = safe_get(_session(), f"{_BASE}/doi:{doi}")
    if resp is not None and resp.status_code == 200:
        work = resp.json()
        return _summarize(work), 1.0
    return None, None


def search_by_title(title: str, year: int | None = None) -> tuple[dict | None, float | None]:
    params: dict[str, Any] = {"search": title, "per-page": 5}
    if year:
        params["filter"] = f"publication_year:{year}"
    polite_wait("openalex")
    resp = safe_get(_session(), _BASE, params=params)
    if resp is not None and resp.status_code == 200:
        results = resp.json().get("results", [])
        if results:
            best = max(results, key=lambda w: similarity(title, w.get("display_name")))
            sim = similarity(title, best.get("display_name"))
            return _summarize(best), sim
    return None, None


def get_by_arxiv_id(arxiv_id: str) -> tuple[dict | None, float | None]:
    polite_wait("openalex")
    resp = safe_get(_session(), f"{_BASE}/arxiv:{arxiv_id}")
    if resp is not None and resp.status_code == 200:
        work = resp.json()
        return _summarize(work), 1.0
    return None, None
