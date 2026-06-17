# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""PDF text extraction: pypdf with pdfplumber fallback per page."""
from __future__ import annotations

from pathlib import Path


_MIN_CHARS_PER_PAGE = 80


def extract_pages(pdf_path: Path) -> list[str]:
    """Return a list of strings, one per page. Uses pypdf; falls back to pdfplumber per page."""
    pages_pypdf = _extract_pypdf(pdf_path)
    pages_plumber = None

    results = []
    for i, text in enumerate(pages_pypdf):
        if len(text.strip()) >= _MIN_CHARS_PER_PAGE:
            results.append(text)
        else:
            if pages_plumber is None:
                pages_plumber = _extract_pdfplumber(pdf_path)
            plumber_text = pages_plumber[i] if i < len(pages_plumber) else ""
            results.append(plumber_text if len(plumber_text.strip()) > len(text.strip()) else text)

    if not results and pages_plumber is not None:
        results = pages_plumber

    return results


def _extract_pypdf(pdf_path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return []


def _extract_pdfplumber(pdf_path: Path) -> list[str]:
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            return [page.extract_text() or "" for page in pdf.pages]
    except Exception:
        return []


def page_count(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0
