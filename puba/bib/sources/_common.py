# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from ref-checker and annual-report API clients.
"""Shared utilities for bibliographic source clients: similarity, DOI normalization,
rate limiting, retry, and polite-pool env handling."""
from __future__ import annotations

import os
import re
import time
from difflib import SequenceMatcher
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

_USER_AGENT = "puba/0.1 (mailto:rbross-misc@pobox.com)"

_DOI_RE = re.compile(
    r"(?:https?://doi\.org/|doi:\s*)(10\.\d{4,9}/[^\s,;\"\'<>]+)"
    r"|(?<!\w)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)(?!\w)",
    re.IGNORECASE,
)

_ARXIV_NEW_RE = re.compile(r"\b(\d{4}\.\d{4,5}(?:v\d+)?)\b")
_ARXIV_OLD_RE = re.compile(r"\b([a-z\-]+/\d{7}(?:v\d+)?)\b")
_ARXIV_DOI_PREFIX = "10.48550/arxiv"

_last_query_time: dict[str, float] = {}

_ORCID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]$", re.IGNORECASE)

_PLACEHOLDER_RES = [
    re.compile(r"/[nx]{4,}(\.[nx]{4,})?$", re.IGNORECASE),
    re.compile(r"/0{4,}(\.0{4,})?$"),
    re.compile(
        r"/(example|sample|placeholder|template|todo|your[-_]?doi)(/.*)?$",
        re.IGNORECASE,
    ),
    re.compile(r"^10\.\d+/\s*$"),
]


def _normalize_orcid(orcid: str | None) -> str | None:
    """Strip URL prefix from an ORCID and validate format.

    Returns the bare 19-char ORCID (e.g. '0000-0002-1234-567X') or None
    if the input is absent or does not match the expected format.
    """
    if not orcid:
        return None
    bare = re.sub(r"^https?://orcid\.org/", "", orcid.strip(), flags=re.IGNORECASE)
    return bare if _ORCID_RE.match(bare) else None


def make_author(
    name: str,
    *,
    orcid: str | None = None,
    orcid_authenticated: bool | None = None,
    openalex_author_id: str | None = None,
    author_position: str | None = None,
    affiliations: list[dict] | None = None,
) -> dict:
    """Return a uniform author dict.

    All sources produce this shape so that consumers (sidecar, _upsert_postgres)
    never have to special-case individual sources.
    """
    return {
        "name":                 name,
        "orcid":                orcid,
        "orcid_authenticated":  orcid_authenticated,
        "openalex_author_id":   openalex_author_id,
        "author_position":      author_position,
        "affiliations":         affiliations or [],
    }


def polite_wait(source: str) -> None:
    from ... import config as cfg
    limits = cfg.bib().get("rate_limits_s", {})
    delay = limits.get(source, 1.0)
    last = _last_query_time.get(source, 0.0)
    elapsed = time.monotonic() - last
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_query_time[source] = time.monotonic()


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^https?://doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = doi.lower().rstrip(".,);")
    return doi or None


def is_placeholder_doi(doi: str | None) -> bool:
    """Return True if doi matches a known ACM/publisher template placeholder pattern.

    Accepts either raw (prefixed) or already-normalized DOI strings.
    Always returns False for None / empty input.
    """
    doi = normalize_doi(doi)
    if not doi:
        return False
    return any(pat.search(doi) for pat in _PLACEHOLDER_RES)


def is_arxiv_doi(doi: str | None) -> bool:
    if not doi:
        return False
    return normalize_doi(doi, ) and normalize_doi(doi).startswith(_ARXIV_DOI_PREFIX)


def extract_doi(text: str) -> str | None:
    m = _DOI_RE.search(text)
    if m:
        raw = m.group(1) or m.group(2)
        normalized = normalize_doi(raw)
        if is_placeholder_doi(normalized):
            return None
        return normalized
    return None


def extract_arxiv_id(text: str, filename: str = "") -> str | None:
    for src in [filename, text]:
        m = _ARXIV_NEW_RE.search(src)
        if m:
            return re.sub(r"v\d+$", "", m.group(1))
        m = _ARXIV_OLD_RE.search(src)
        if m:
            return re.sub(r"v\d+$", "", m.group(1))
    return None


def similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0

    def norm(s: str) -> str:
        s = s.casefold()
        s = re.sub(r"[^\w\s]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def first_author_surname(authors: list) -> str | None:
    if not authors:
        return None
    first = authors[0]
    if isinstance(first, dict):
        first = first.get("name") or ""
    if "," in first:
        parts = first.split(",")
        return parts[0].strip().casefold() or None
    parts = first.split()
    return parts[-1].casefold() if parts else None


def base_session(extra_headers: dict[str, str] | None = None) -> requests.Session:
    s = requests.Session()
    headers = {"User-Agent": _USER_AGENT}
    mailto = os.environ.get("OPENALEX_MAILTO")
    if mailto:
        headers["User-Agent"] = f"{_USER_AGENT}; mailto:{mailto}"
    if extra_headers:
        headers.update(extra_headers)
    s.headers.update(headers)
    return s


def safe_get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response | None:
    try:
        resp = session.get(url, timeout=30, **kwargs)
        return resp
    except Exception:
        return None
