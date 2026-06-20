# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Marker-safe character slicing for rendered markdown documents."""
from __future__ import annotations

import re

_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+\d+\s*-->")


def slice_md(
    text: str,
    head: int | None = None,
    tail: int | None = None,
) -> tuple[str, int]:
    """Return a character slice of text, never splitting a page marker.

    Exactly one of head or tail must be provided.

    head=N: return at most the first N characters. If the cut at N falls
    inside a ``<!-- page ... -->`` marker, the cut is retracted to just
    before the marker start. The returned slice is therefore <= N chars.

    tail=N: return at most the last N characters. If the cut-start falls
    inside a marker, the cut is advanced to just after the marker end.
    The returned slice is therefore <= N chars.

    Returns (slice, requested_chars) where requested_chars is head or tail.
    """
    if (head is None) == (tail is None):
        raise ValueError("Exactly one of head or tail must be provided.")

    total = len(text)

    if head is not None:
        requested = head
        cut = min(head, total)
        m = _straddling_marker(text, cut)
        if m is not None:
            cut = m.start()
        return text[:cut], requested

    requested = tail
    start = max(0, total - tail)
    m = _straddling_marker(text, start)
    if m is not None:
        start = m.end()
    return text[start:], requested


def _straddling_marker(text: str, offset: int) -> re.Match | None:
    """Return the page marker that straddles offset, or None.

    A marker straddles offset if marker.start() < offset < marker.end().
    Markers that end exactly at offset or start exactly at offset are not
    considered straddling.
    """
    search_start = max(0, offset - 30)
    search_end = min(len(text), offset + 30)
    for m in _PAGE_MARKER_RE.finditer(text, search_start, search_end):
        if m.start() < offset < m.end():
            return m
    return None
