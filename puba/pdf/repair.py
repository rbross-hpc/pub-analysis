# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from ref-checker text repair module.
"""PDF text repair: de-hyphenation, glyph fixes, ligature normalization."""
from __future__ import annotations

import re
import unicodedata

# Protect URLs from de-hyphenation
_URL_RE = re.compile(r'https?://\S+')

# Ligature map
_LIGATURES = {
    '\ufb00': 'ff',
    '\ufb01': 'fi',
    '\ufb02': 'fl',
    '\ufb03': 'ffi',
    '\ufb04': 'ffl',
    '\ufb05': 'st',
    '\ufb06': 'st',
}

# Split-glyph patterns: "V ector" -> "Vector", "e\xef\xac\x83cient" -> "efficient"
# These catch the most common PDF space-insertion artifacts in common words.
_SPLIT_GLYPH_RE = re.compile(
    r'\b([A-Z])\s+([a-z]{2,})\b'  # "V ector"
)

def _protect_urls(text: str) -> tuple[str, list[str]]:
    """Replace URLs with placeholders; return modified text and URL list."""
    urls: list[str] = []
    def _replace(m: re.Match) -> str:
        placeholder = f"\x00URL{len(urls)}\x00"
        urls.append(m.group(0))
        return placeholder
    return _URL_RE.sub(_replace, text), urls


def _restore_urls(text: str, urls: list[str]) -> str:
    for i, url in enumerate(urls):
        text = text.replace(f"\x00URL{i}\x00", url)
    return text


def _normalize_ligatures(text: str) -> str:
    for lig, repl in _LIGATURES.items():
        text = text.replace(lig, repl)
    return text


def _normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _strip_soft_hyphens(text: str) -> str:
    return text.replace('\xad', '')


def _dehyphenate(text: str) -> str:
    """Join hyphenated line-breaks: 'algo-\nrithm' -> 'algorithm'."""
    # Pattern: word-\n word (with optional leading spaces on next line)
    return re.sub(r'(\w)-\n\s*(\w)', r'\1\2', text)


def _fix_split_glyphs(text: str) -> str:
    """Fix 'V ector' -> 'Vector' style artifacts (capital letter + space + lowercase word)."""
    return _SPLIT_GLYPH_RE.sub(lambda m: m.group(1) + m.group(2), text)


def repair(text: str) -> str:
    """Apply all repair passes to PDF-extracted text."""
    text, urls = _protect_urls(text)
    text = _strip_soft_hyphens(text)
    text = _normalize_ligatures(text)
    text = _normalize_unicode(text)
    text = _dehyphenate(text)
    text = _fix_split_glyphs(text)
    text = _restore_urls(text, urls)
    return text


def repair_pages(pages: list[str]) -> list[str]:
    return [repair(p) for p in pages]
