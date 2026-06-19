# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Render paper.md via MinerU pipeline extraction (formula recognition disabled)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..io import atomic_write_text, sha256_file
from ..pdf.mineru import run_mineru
from ..pdf.sections import Section, sections_to_json, short_names
from ..sidecar import load as load_bib_full
from ..state import ensure_analysis_dir


_HEADING_RE = re.compile(r'^(#{1,6}) (.+)$', re.MULTILINE)
_H1_RE = re.compile(r'^# .+$', re.MULTILINE)
_PAGE_MARKER_RE = re.compile(r'<!--\s*page\s+\d+\s*-->')
_NONALNUM_RUN_RE = re.compile(r'[^\w]+')


def _normalize_title(text: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to single space."""
    return _NONALNUM_RUN_RE.sub(" ", text.lower()).strip()


def _strip_cover_headings(md_with_markers: str, bib_title: str | None) -> str:
    """Drop cover-page material before the real paper title heading.

    Locates the first level-1 (#) heading whose normalized text starts with
    the first min(8, N) normalized words of bib_title. If found within the
    first 20 level-1 headings AND within the first 2 pages of content,
    everything up to and including that heading line is stripped.

    The caller (render()) prepends its own `# {bib_title}` line, so stripping
    the matched heading avoids duplication.

    No-op when:
    - bib_title is None, empty, or normalizes to fewer than 2 words
    - no matching heading found within the search window
    """
    if not bib_title:
        return md_with_markers

    norm_title = _normalize_title(bib_title)
    words = [w for w in norm_title.split() if w]
    if len(words) < 2:
        return md_with_markers

    prefix_words = words[:8]
    prefix = " ".join(prefix_words)

    page_markers = list(_PAGE_MARKER_RE.finditer(md_with_markers))
    page_limit_pos = (
        page_markers[2].start() if len(page_markers) >= 3 else len(md_with_markers)
    )

    h1_count = 0
    for m in _H1_RE.finditer(md_with_markers):
        if m.start() >= page_limit_pos:
            break
        h1_count += 1
        if h1_count > 20:
            break
        heading_text = m.group(0)[2:].strip()
        norm_heading = _normalize_title(heading_text)
        if norm_heading.startswith(prefix):
            end_of_line = md_with_markers.find("\n", m.start())
            strip_to = end_of_line + 1 if end_of_line != -1 else len(md_with_markers)
            return md_with_markers[strip_to:]

    return md_with_markers


def _bib_frontmatter(bib: dict[str, Any], bib_yaml_sha: str) -> str:
    fm: dict[str, Any] = {}
    for field in ("title", "authors", "year", "publication_date", "venue", "category",
                  "doi", "arxiv_id", "osti_id", "url"):
        val = bib.get(field)
        if val is not None:
            fm[field] = val
    fm["bib_yaml_sha"] = bib_yaml_sha[:12]
    return "---\n" + yaml.dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False) + "---\n\n"


def _author_line(bib: dict[str, Any]) -> str:
    authors = bib.get("authors") or []
    if not authors:
        return ""
    if len(authors) <= 3:
        return ", ".join(authors)
    return f"{authors[0]} et al."


def _venue_year_line(bib: dict[str, Any]) -> str:
    parts = []
    if bib.get("venue"):
        parts.append(bib["venue"])
    if bib.get("year"):
        parts.append(str(bib["year"]))
    return " · ".join(parts)


def _inject_page_markers(md_text: str, content_list: list[dict]) -> str:
    """Insert <!-- page N --> before the first non-empty block of each page_idx.

    Groups content_list by page_idx (preserving first-seen order). For each
    page, uses the first block with non-empty text as the anchor: searches
    forward in md_text for that block's text and inserts the marker at the
    start of the line containing it.

    Falls back to emitting the marker at the current cursor position when:
    - the page has no non-empty blocks at all (e.g. a pure-figure page), or
    - the anchor text is not found in the remaining markdown (MinerU omitted
      the block from the .md but retained it in content_list).

    N = page_idx + 1 (physical PDF page, 1-indexed from the first page in the
    file, including any cover/front-matter pages). See README §"Page numbering"
    for user-facing semantics and known limitations.
    """
    if not content_list:
        return f"\n<!-- page 1 -->\n\n{md_text}"

    pages: dict[int, list[dict]] = {}
    seen_pages: list[int] = []
    for block in content_list:
        pid = block.get("page_idx", 0)
        if pid not in pages:
            pages[pid] = []
            seen_pages.append(pid)
        pages[pid].append(block)

    result_parts: list[str] = []
    cursor = 0
    current_page: int | None = None

    for page_idx in seen_pages:
        anchor = next(
            (b for b in pages[page_idx] if b.get("text", "").strip()),
            None,
        )
        marker = (
            f"\n<!-- page {page_idx + 1} -->\n\n"
            if current_page is None
            else f"\n\n<!-- page {page_idx + 1} -->\n\n"
        )

        if anchor is None:
            result_parts.append(marker)
            current_page = page_idx
            continue

        frag = anchor["text"].strip()[:60]
        found = md_text.find(frag, cursor)

        if found == -1:
            result_parts.append(marker)
            current_page = page_idx
        else:
            line_start = md_text.rfind("\n", cursor, found)
            insert_at = line_start + 1 if line_start != -1 else found
            result_parts.append(md_text[cursor:insert_at])
            result_parts.append(marker)
            cursor = insert_at
            current_page = page_idx

    result_parts.append(md_text[cursor:])
    return "".join(result_parts)


def _parse_sections(assembled_md: str) -> list[Section]:
    """Parse # headings from the final assembled paper.md into Section objects.

    Offsets are into assembled_md so distill's md_text[start:end] slices work.
    """
    raw: list[tuple[int, str, int]] = []
    for m in _HEADING_RE.finditer(assembled_md):
        level = len(m.group(1))
        title = m.group(2).strip()
        raw.append((m.start(), title, level))

    sections: list[Section] = []
    for idx, (start, title, level) in enumerate(raw):
        end = raw[idx + 1][0] if idx + 1 < len(raw) else len(assembled_md)
        sections.append(Section(title=title, level=level, start=start, end=end))

    names = short_names(sections)
    for sec, name in zip(sections, names):
        sec.short_name = name

    return sections


def render(
    pdf_path: Path,
    force: bool = False,
) -> tuple[Path, bool]:
    """Render paper.md for pdf_path via MinerU.

    Returns (path_to_paper_md, was_cached). was_cached is True when the stage
    was already current and no work was performed.
    """
    from .. import config as cfg
    from ..state import is_stage_current, mark_stage_complete

    mineru_version = cfg.md().get("mineru_version", "mineru-1")
    ad = ensure_analysis_dir(pdf_path)
    paper_md = ad / "paper.md"

    if not force and is_stage_current(ad, pdf_path, "md", mineru_version):
        return paper_md, True

    bib_yaml_path = ad / "bib.yaml"
    bib: dict[str, Any] = {}
    bib_sha = ""
    if bib_yaml_path.exists():
        bib = load_bib_full(pdf_path)
        bib_sha = sha256_file(bib_yaml_path)

    if bib.get("needs_review"):
        from rich.console import Console
        _con = Console(stderr=True)
        _con.print(
            f"[yellow]Warning:[/yellow] {pdf_path.name}: bib.yaml has needs_review=true — "
            "bibliographic information may be unreliable."
        )
        for reason in (bib.get("_review_reasons") or []):
            _con.print(f"  [yellow]-[/yellow] {reason}")

    md_text, content_list = run_mineru(pdf_path, ad)

    md_with_markers = _inject_page_markers(md_text, content_list)
    md_with_markers = _strip_cover_headings(md_with_markers, bib.get("title"))

    parts: list[str] = []
    parts.append(_bib_frontmatter(bib, bib_sha))

    title = bib.get("title") or pdf_path.stem
    parts.append(f"# {title}\n")

    author_line = _author_line(bib)
    venue_line = _venue_year_line(bib)
    if author_line:
        parts.append(f"**{author_line}**  ")
    if venue_line:
        parts.append(f"*{venue_line}*\n")
    parts.append("")

    parts.append(md_with_markers)

    assembled = "\n".join(parts)

    atomic_write_text(paper_md, assembled)

    sections = _parse_sections(assembled)
    sections_data = sections_to_json(sections)
    atomic_write_text(ad / "paper.sections.json", json.dumps(sections_data, indent=2))

    mark_stage_complete(ad, pdf_path, "md", mineru_version)
    return paper_md, False
