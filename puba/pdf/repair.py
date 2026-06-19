# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from ref-checker text repair module.
"""PDF text repair: de-hyphenation, glyph fixes, ligature normalization,
and running header/footer stripping."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Protect URLs from de-hyphenation
_URL_RE = re.compile(r'https?://\S+')

# Named tabular numeral glyphs emitted by some fonts (e.g. Frontiers PDFs)
# e.g. "/zero.tnum" -> "0", "/one.tnum" -> "1", etc.
_TNUM_MAP = {
    '/zero.tnum':  '0',
    '/one.tnum':   '1',
    '/two.tnum':   '2',
    '/three.tnum': '3',
    '/four.tnum':  '4',
    '/five.tnum':  '5',
    '/six.tnum':   '6',
    '/seven.tnum': '7',
    '/eight.tnum': '8',
    '/nine.tnum':  '9',
}
_TNUM_RE = re.compile(r'/(zero|one|two|three|four|five|six|seven|eight|nine)\.tnum')

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


def _fix_tnum_glyphs(text: str) -> str:
    """Replace /zero.tnum .. /nine.tnum glyph names with plain digits."""
    return _TNUM_RE.sub(lambda m: _TNUM_MAP[m.group(0)], text)


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
    text = _fix_tnum_glyphs(text)
    text = _normalize_ligatures(text)
    text = _normalize_unicode(text)
    text = _dehyphenate(text)
    text = _fix_split_glyphs(text)
    text = _restore_urls(text, urls)
    return text


def _line_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _strip_headers_footers(
    pages: list[str],
    position_lines: int = 3,
    min_page_fraction: float = 0.5,
    sim_threshold: float = 0.9,
) -> list[str]:
    """Remove running page headers and footers from each page.

    A line is a candidate header/footer if:
    - It appears within the first or last `position_lines` lines of a page, AND
    - A similar line (similarity >= sim_threshold) appears in the same position
      band on at least `min_page_fraction` of all pages.

    Only lines in the position band are ever stripped — body lines are never
    touched even if they repeat across pages.
    """
    if len(pages) < 2:
        return pages

    min_pages = max(2, int(len(pages) * min_page_fraction))

    def _candidate_lines(page: str) -> dict[str, list[str]]:
        lines = page.splitlines()
        top = [l for l in lines[:position_lines] if l.strip()]
        bot = [l for l in lines[-position_lines:] if l.strip()]
        return {"top": top, "bot": bot}

    candidates = [_candidate_lines(p) for p in pages]

    def _is_repeating(band: str, line: str) -> bool:
        count = 0
        for cands in candidates:
            for cline in cands[band]:
                if _line_sim(line, cline) >= sim_threshold:
                    count += 1
                    break
        return count >= min_pages

    result = []
    for page, cand in zip(pages, candidates):
        lines = page.splitlines()
        to_strip: set[int] = set()

        n = len(lines)
        top_end = min(position_lines, n // 2)
        bot_start = max(n - position_lines, n - n // 2, top_end)
        top_indices = list(range(top_end))
        bot_indices = list(range(bot_start, n))

        for idx in top_indices:
            line = lines[idx]
            if line.strip() and _is_repeating("top", line):
                to_strip.add(idx)

        for idx in bot_indices:
            line = lines[idx]
            if line.strip() and _is_repeating("bot", line):
                to_strip.add(idx)

        result.append("\n".join(l for i, l in enumerate(lines) if i not in to_strip))

    return result


def repair_pages(pages: list[str]) -> list[str]:
    repaired = [repair(p) for p in pages]
    return _strip_headers_footers(repaired)
