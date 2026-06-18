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


def is_arxiv_doi(doi: str | None) -> bool:
    if not doi:
        return False
    return normalize_doi(doi, ) and normalize_doi(doi).startswith(_ARXIV_DOI_PREFIX)


def extract_doi(text: str) -> str | None:
    m = _DOI_RE.search(text)
    if m:
        raw = m.group(1) or m.group(2)
        return normalize_doi(raw)
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


def first_author_surname(authors: list[str]) -> str | None:
    if not authors:
        return None
    first = authors[0]
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
