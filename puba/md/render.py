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
_NONALNUM_RUN_RE = re.compile(r'[^\w]+')


def _normalize_title(text: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to single space."""
    return _NONALNUM_RUN_RE.sub(" ", text.lower()).strip()


def _strip_cover_headings(md_text: str, bib_title: str | None) -> str:
    """Drop cover-page material before the real paper title heading.

    Locates the first level-1 (#) heading whose normalized text starts with
    the first min(8, N) normalized words of bib_title. If found within the
    first 20 level-1 headings AND within the first 6000 characters of the
    markdown, everything up to and including that heading line is stripped.

    The caller (render()) prepends its own `# {bib_title}` line, so stripping
    the matched heading avoids duplication.

    Expects raw MinerU markdown with no page markers (marker injection runs
    after cover-strip in the render() pipeline).

    No-op when:
    - bib_title is None, empty, or normalizes to fewer than 2 words
    - no matching heading found within the search window
    """
    if not bib_title:
        return md_text

    norm_title = _normalize_title(bib_title)
    words = [w for w in norm_title.split() if w]
    if len(words) < 2:
        return md_text

    prefix_words = words[:8]
    prefix = " ".join(prefix_words)

    search_limit = min(6000, len(md_text))

    h1_count = 0
    for m in _H1_RE.finditer(md_text):
        if m.start() >= search_limit:
            break
        h1_count += 1
        if h1_count > 20:
            break
        heading_text = m.group(0)[2:].strip()
        norm_heading = _normalize_title(heading_text)
        if norm_heading.startswith(prefix):
            end_of_line = md_text.find("\n", m.start())
            strip_to = end_of_line + 1 if end_of_line != -1 else len(md_text)
            return md_text[strip_to:]

    return md_text


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
    """Insert <!-- page N --> before surviving content of each page_idx.

    Groups content_list by page_idx (preserving first-seen order). For each
    page, tries each non-empty block with text >= 8 chars as an anchor:
    searches forward in md_text from the current cursor. The first hit is used
    as an anchored marker (cursor advances to that position).

    When no block anchors from cursor but at least one block's text exists
    somewhere in md_text (search from 0), the page's content is present but
    not reachable from cursor (e.g. a repeated running header already consumed
    by an earlier page). A fallback marker is emitted at the current cursor
    position without advancing the cursor. The affected page_idx is appended
    to the returned fallback list.

    Pages with no qualifying blocks (none with text >= 8 chars) AND whose
    text is absent from md_text entirely are silently skipped — no marker.
    This is the correct behavior for cover-stripped pages and pure-figure pages.

    Returns (result_text, fallback_pages) where fallback_pages is a list of
    page_idx values for which a fallback marker was emitted. render() emits a
    warning to stderr when len(fallback_pages) >= 2.

    N = page_idx + 1 (physical PDF page, 1-indexed from the first page in the
    file, including any cover/front-matter pages). See docs/markdown-rendering.md
    §"Page numbering" for user-facing semantics.
    """
    if not content_list:
        return f"\n<!-- page 1 -->\n\n{md_text}", []

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
    fallback_pages: list[int] = []

    for page_idx in seen_pages:
        long_blocks = [
            b for b in pages[page_idx]
            if len(b.get("text", "").strip()) >= 8
        ]
        marker = (
            f"\n<!-- page {page_idx + 1} -->\n\n"
            if current_page is None
            else f"\n\n<!-- page {page_idx + 1} -->\n\n"
        )

        if not long_blocks:
            current_page = page_idx
            continue

        anchored = False
        for block in long_blocks:
            frag = block["text"].strip()[:60]
            found = md_text.find(frag, cursor)
            if found != -1:
                line_start = md_text.rfind("\n", cursor, found)
                insert_at = line_start + 1 if line_start != -1 else found
                result_parts.append(md_text[cursor:insert_at])
                result_parts.append(marker)
                cursor = insert_at
                current_page = page_idx
                anchored = True
                break

        if not anchored:
            present_anywhere = any(
                md_text.find(b["text"].strip()[:60]) != -1
                for b in long_blocks
            )
            if present_anywhere:
                result_parts.append(marker)
                fallback_pages.append(page_idx)
                current_page = page_idx

    result_parts.append(md_text[cursor:])
    return "".join(result_parts), fallback_pages


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

    md_stripped = _strip_cover_headings(md_text, bib.get("title"))
    md_with_markers, fallback_pages = _inject_page_markers(md_stripped, content_list)

    if len(fallback_pages) >= 2:
        from rich.console import Console
        _con = Console(stderr=True)
        affected = ", ".join(str(p + 1) for p in fallback_pages)
        _con.print(
            f"[yellow]Warning:[/yellow] {pdf_path.name}: page-marker placement "
            f"degraded on {len(fallback_pages)} pages ({affected}) — cursor "
            "overshoot from repeated anchor text. Markers present but approximate. "
            "See docs/markdown-rendering.md §\"Page numbering\"."
        )

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
