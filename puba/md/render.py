# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Render paper.md via MinerU hybrid-engine extraction."""
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
    """Insert <!-- page N --> before the first block of each new page_idx.

    Walks content_list in order; when page_idx increments, searches forward in
    md_text for the block's text and inserts the marker at the start of the
    line containing that text (so heading markers like '## ' are preserved).
    Falls back to a single <!-- page 1 --> prefix if anchoring fails.
    """
    if not content_list:
        return f"\n<!-- page 1 -->\n\n{md_text}"

    result_parts: list[str] = []
    cursor = 0
    current_page: int | None = None

    for block in content_list:
        page_idx = block.get("page_idx", 0)
        block_text = block.get("text", "").strip()

        if page_idx == current_page:
            continue

        if not block_text:
            if current_page is None:
                current_page = page_idx
                result_parts.append(f"\n<!-- page {page_idx + 1} -->\n\n")
            else:
                current_page = page_idx
                result_parts.append(f"\n\n<!-- page {page_idx + 1} -->\n\n")
            continue

        search_fragment = block_text[:60]
        found = md_text.find(search_fragment, cursor)

        if found == -1:
            if current_page is None:
                current_page = page_idx
                result_parts.append(f"\n<!-- page {page_idx + 1} -->\n\n")
            else:
                current_page = page_idx
        else:
            line_start = md_text.rfind("\n", cursor, found)
            insert_at = line_start + 1 if line_start != -1 else found

            result_parts.append(md_text[cursor:insert_at])
            marker = (
                f"\n<!-- page {page_idx + 1} -->\n\n"
                if current_page is None
                else f"\n\n<!-- page {page_idx + 1} -->\n\n"
            )
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

    md_text, content_list = run_mineru(pdf_path)

    md_with_markers = _inject_page_markers(md_text, content_list)

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
