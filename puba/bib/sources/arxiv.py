# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from annual-report/annual_report/api/arxiv.py
"""arXiv bibliographic source client."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import requests

from ._common import polite_wait, similarity

_BASE = "https://export.arxiv.org/api/query"
_NS = {"atom": "http://www.w3.org/2005/Atom"}
_USER_AGENT = "puba/0.1 (mailto:rbross-misc@pobox.com)"


def _bare(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def get_by_id(arxiv_id: str) -> dict[str, Any] | None:
    bare = _bare(arxiv_id)
    polite_wait("arxiv")
    try:
        resp = requests.get(
            _BASE,
            params={"id_list": bare, "max_results": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception:
        return None

    entry = root.find("atom:entry", _NS)
    if entry is None:
        return None

    title = (entry.findtext("atom:title", "", _NS) or "").strip().replace("\n", " ")
    published = entry.findtext("atom:published", "", _NS) or ""
    entry_id = entry.findtext("atom:id", "", _NS) or ""
    abstract = (entry.findtext("atom:summary", "", _NS) or "").strip().replace("\n", " ")
    authors = [
        a.findtext("atom:name", "", _NS) or ""
        for a in entry.findall("atom:author", _NS)
    ]
    year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
    pub_date = published[:10] if len(published) >= 10 else None

    return {
        "arxiv_id": bare,
        "title": title or None,
        "authors": [a for a in authors if a],
        "year": year,
        "publication_date": pub_date,
        "abstract": abstract or None,
        "url": entry_id or f"https://arxiv.org/abs/{bare}",
        "category": "arxiv preprint",
    }


def search_by_title(title: str, year: int | None = None) -> tuple[dict | None, float | None]:
    query = f'ti:"{title}"'
    if year:
        query += f" AND submittedDate:[{year}01010000 TO {year}12312359]"
    polite_wait("arxiv")
    try:
        resp = requests.get(
            _BASE,
            params={"search_query": query, "max_results": 5},
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception:
        return None, None

    entries = root.findall("atom:entry", _NS)
    if not entries:
        return None, None

    best = None
    best_sim = 0.0
    for entry in entries:
        t = (entry.findtext("atom:title", "", _NS) or "").strip().replace("\n", " ")
        s = similarity(title, t)
        if s > best_sim:
            best_sim = s
            best = entry

    if best is None:
        return None, None

    title_str = (best.findtext("atom:title", "", _NS) or "").strip().replace("\n", " ")
    published = best.findtext("atom:published", "", _NS) or ""
    entry_id = best.findtext("atom:id", "", _NS) or ""
    abstract = (best.findtext("atom:summary", "", _NS) or "").strip().replace("\n", " ")
    authors = [
        a.findtext("atom:name", "", _NS) or ""
        for a in best.findall("atom:author", _NS)
    ]
    bare_id = re.sub(r"v\d+$", "", entry_id.split("/")[-1]) if entry_id else None
    y = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
    pub_date = published[:10] if len(published) >= 10 else None

    return {
        "arxiv_id": bare_id,
        "title": title_str or None,
        "authors": [a for a in authors if a],
        "year": y,
        "publication_date": pub_date,
        "abstract": abstract or None,
        "url": entry_id or (f"https://arxiv.org/abs/{bare_id}" if bare_id else None),
        "category": "arxiv preprint",
    }, best_sim
