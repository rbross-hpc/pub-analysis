# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from ref-checker Semantic Scholar client.
"""Semantic Scholar bibliographic source client — last-resort fallback."""
from __future__ import annotations

import os
from typing import Any

from ._common import base_session, normalize_doi, polite_wait, safe_get, similarity

_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_SEARCH_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,authors,year,publicationDate,venue,externalIds,abstract,openAccessPdf,publicationTypes"


def _session() -> Any:
    s = base_session()
    api_key = os.environ.get("SEMANTICSCHOLAR_API_KEY")
    if api_key:
        s.headers["x-api-key"] = api_key
    return s


def _summarize(paper: dict) -> dict[str, Any]:
    ext = paper.get("externalIds") or {}
    doi = normalize_doi(ext.get("DOI"))
    arxiv_id = ext.get("ArXiv")
    authors = [a.get("name", "") for a in (paper.get("authors") or [])]
    pub_types = paper.get("publicationTypes") or []
    if "JournalArticle" in pub_types:
        category = "journal article"
    elif "Conference" in pub_types:
        category = "conference paper"
    elif "Review" in pub_types:
        category = "journal article"
    else:
        category = "other"
    year = paper.get("year")
    pub_date = paper.get("publicationDate")
    return {
        "title": paper.get("title"),
        "authors": [a for a in authors if a],
        "year": year,
        "publication_date": pub_date,
        "venue": paper.get("venue"),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": (paper.get("openAccessPdf") or {}).get("url"),
        "abstract": paper.get("abstract"),
        "category": category,
    }


def get_by_doi(doi: str) -> tuple[dict | None, float | None]:
    doi_norm = normalize_doi(doi)
    if not doi_norm:
        return None, None
    polite_wait("semanticscholar")
    resp = safe_get(_session(), f"{_BASE}/DOI:{doi_norm}", params={"fields": _FIELDS})
    if resp is not None and resp.status_code == 200:
        paper = resp.json()
        if paper.get("title"):
            return _summarize(paper), 1.0
    return None, None


def search_by_title(title: str, year: int | None = None) -> tuple[dict | None, float | None]:
    params: dict[str, Any] = {"query": title, "fields": _FIELDS, "limit": 5}
    if year:
        params["year"] = str(year)
    polite_wait("semanticscholar")
    resp = safe_get(_session(), _SEARCH_BASE, params=params)
    if resp is None or resp.status_code != 200:
        return None, None
    try:
        data = resp.json().get("data", [])
    except Exception:
        return None, None
    if not data:
        return None, None
    best = max(data, key=lambda p: similarity(title, p.get("title")))
    sim = similarity(title, best.get("title"))
    return _summarize(best), sim
