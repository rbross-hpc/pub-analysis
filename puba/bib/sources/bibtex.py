# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from annual-report/annual_report/api/bibtex.py
"""BibTeX file source client."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._common import normalize_doi, similarity

_MONTH_MAP = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_TYPE_MAP = {
    "article":       "journal article",
    "inproceedings": "conference paper",
    "proceedings":   "conference paper",
    "conference":    "conference paper",
    "book":          "book",
    "inbook":        "book chapter",
    "incollection":  "book chapter",
    "phdthesis":     "thesis",
    "mastersthesis": "thesis",
    "techreport":    "technical report",
    "misc":          "other",
    "unpublished":   "other",
}

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_DOI_RE = re.compile(
    r"(?:https?://doi\.org/|doi:\s*)(10\.\d{4,9}/[^\s,;\"\'<>]+)"
    r"|(?<!\w)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)(?!\w)",
    re.IGNORECASE,
)


def _parse_authors(raw: str) -> list[str]:
    parts = re.split(r"\s+and\s+", raw, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _parse_year_date(raw: str) -> tuple[int | None, str | None]:
    m = _YEAR_RE.search(raw)
    if not m:
        return None, None
    year = int(m.group(1))
    lower = raw.lower()
    for name, num in _MONTH_MAP.items():
        if name in lower:
            return year, f"{year}-{num:02d}"
    return year, str(year)


def _extract_doi(s: str) -> str | None:
    m = _DOI_RE.search(s)
    if m:
        raw = m.group(1) or m.group(2)
        return normalize_doi(raw)
    return None


def load_bib_file(bib_path: Path) -> list[dict[str, Any]]:
    try:
        import bibtexparser
        text = bib_path.read_text(encoding="utf-8", errors="replace")
        db = bibtexparser.loads(text)
    except Exception:
        return []

    entries = []
    for entry in db.entries:
        raw_type = entry.get("ENTRYTYPE", "misc").lower()
        category = _TYPE_MAP.get(raw_type, "other")
        raw_year = entry.get("year", "") or ""
        year, pub_date = _parse_year_date(raw_year)
        raw_author = entry.get("author", "") or ""
        authors = _parse_authors(raw_author) if raw_author else []
        venue = (
            entry.get("booktitle")
            or entry.get("journal")
            or entry.get("publisher")
            or entry.get("series")
            or None
        )
        raw_doi = entry.get("doi") or entry.get("url") or ""
        doi = _extract_doi(raw_doi) if raw_doi else None
        entries.append({
            "bibtex_key": entry.get("ID", ""),
            "title": (entry.get("title", "") or "").strip("{}") or None,
            "authors": authors,
            "year": year,
            "publication_date": pub_date,
            "venue": venue,
            "doi": doi,
            "category": category,
            "raw_type": raw_type,
        })
    return entries


def lookup_by_title(
    title: str,
    bib_path: Path,
    min_sim: float = 0.85,
) -> tuple[dict | None, float | None]:
    if not title or not bib_path.exists():
        return None, None
    entries = load_bib_file(bib_path)
    if not entries:
        return None, None
    best = max(entries, key=lambda e: similarity(title, e.get("title")))
    sim = similarity(title, best.get("title"))
    if sim >= min_sim:
        return best, sim
    return None, None


def lookup_by_doi(doi: str, bib_path: Path) -> dict | None:
    norm = normalize_doi(doi)
    if not norm or not bib_path.exists():
        return None
    for entry in load_bib_file(bib_path):
        if normalize_doi(entry.get("doi")) == norm:
            return entry
    return None
