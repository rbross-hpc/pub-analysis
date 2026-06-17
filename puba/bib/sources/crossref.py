# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from ref-checker crossref client.
"""CrossRef bibliographic source client."""
from __future__ import annotations

import os
from typing import Any

from ._common import base_session, normalize_doi, polite_wait, safe_get, similarity

_BASE = "https://api.crossref.org/works"

_CR_TYPE_MAP = {
    "journal-article":   "journal article",
    "proceedings-article": "conference paper",
    "book-chapter":      "book chapter",
    "book":              "book",
    "monograph":         "book",
    "edited-book":       "book",
    "dissertation":      "thesis",
    "posted-content":    "preprint",
    "report":            "technical report",
    "report-component":  "technical report",
    "other":             "other",
}


def _session() -> Any:
    s = base_session()
    mailto = os.environ.get("OPENALEX_MAILTO")
    if mailto:
        s.headers["User-Agent"] = f"puba/0.1 (mailto:{mailto})"
    return s


def _extract_authors(item: dict) -> list[str]:
    authors = []
    for a in item.get("author", []):
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip() if given else family
        if name:
            authors.append(name)
    return authors


def _extract_date(item: dict) -> tuple[int | None, str | None]:
    for key in ("published", "published-print", "published-online", "created"):
        dp = item.get(key, {}).get("date-parts", [[]])
        if dp and dp[0]:
            parts = dp[0]
            year = parts[0] if len(parts) >= 1 else None
            if len(parts) >= 3:
                pub_date = f"{parts[0]}-{parts[1]:02d}-{parts[2]:02d}"
            elif len(parts) >= 2:
                pub_date = f"{parts[0]}-{parts[1]:02d}"
            else:
                pub_date = str(parts[0]) if parts[0] else None
            return year, pub_date
    return None, None


def _summarize(item: dict) -> dict[str, Any]:
    raw_type = item.get("type", "other")
    category = _CR_TYPE_MAP.get(raw_type, "other")
    doi = normalize_doi(item.get("DOI"))
    year, pub_date = _extract_date(item)
    titles = item.get("title", [])
    title = titles[0] if titles else None
    container = item.get("container-title", [])
    venue = container[0] if container else item.get("publisher")
    abstract = item.get("abstract")
    if abstract:
        import re
        abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
    return {
        "title": title,
        "authors": _extract_authors(item),
        "year": year,
        "publication_date": pub_date,
        "venue": venue,
        "doi": doi,
        "url": item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        "abstract": abstract or None,
        "category": category,
        "raw_type": raw_type,
        "issn": (item.get("ISSN") or [None])[0],
        "isbn": (item.get("ISBN") or [None])[0],
    }


def get_by_doi(doi: str) -> tuple[dict | None, float | None]:
    doi = normalize_doi(doi)
    if not doi:
        return None, None
    polite_wait("crossref")
    resp = safe_get(_session(), f"{_BASE}/{doi}")
    if resp is not None and resp.status_code == 200:
        item = resp.json().get("message", {})
        return _summarize(item), 1.0
    return None, None


def search_by_title(title: str, year: int | None = None) -> tuple[dict | None, float | None]:
    params: dict[str, Any] = {"query.title": title, "rows": 5}
    if year:
        params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"
    polite_wait("crossref")
    resp = safe_get(_session(), _BASE, params=params)
    if resp is not None and resp.status_code == 200:
        items = resp.json().get("message", {}).get("items", [])
        if items:
            best = max(items, key=lambda i: similarity(title, (i.get("title") or [""])[0]))
            best_title = (best.get("title") or [""])[0]
            sim = similarity(title, best_title)
            return _summarize(best), sim
    return None, None
