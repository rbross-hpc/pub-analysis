# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Assemble paper.md from extracted text, sections, and bib record."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..io import atomic_write_text, sha256_file
from ..pdf.extract import extract_pages
from ..pdf.repair import repair_pages
from ..pdf.sections import detect_sections, sections_to_json
from ..sidecar import load as load_bib_full
from ..state import analysis_dir, ensure_analysis_dir


_FIGURE_RE = re.compile(r'^(Fig(?:ure)?\.?\s*\d+[.:]\s*.+)$', re.MULTILINE | re.IGNORECASE)
_FOOTNOTE_RE = re.compile(r'^\s*(\d+)\s+([^\d].+)$', re.MULTILINE)
_PAGE_MARKER = "<!-- page {n} -->"


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


def _section_heading(title: str, level: int) -> str:
    prefix = "#" * min(level + 1, 4)
    return f"{prefix} {title}"


def _add_page_markers(pages: list[str]) -> list[str]:
    marked = []
    for i, text in enumerate(pages):
        marker = f"\n\n<!-- page {i + 1} -->\n\n"
        marked.append(marker + text)
    return marked


def render(
    pdf_path: Path,
    force: bool = False,
    llm_cleanup: bool = True,
) -> tuple[Path, bool]:
    """Render paper.md for pdf_path.

    Returns (path_to_paper_md, was_cached). was_cached is True when the stage
    was already current and no rendering work was performed.
    """
    from .. import config as cfg, __version__
    from ..state import is_stage_current, mark_stage_complete

    prompt_version = cfg.prompt_versions().get("md_cleanup", "md-cleanup-1")
    ad = ensure_analysis_dir(pdf_path)
    paper_md = ad / "paper.md"

    if not force and is_stage_current(ad, pdf_path, "md", prompt_version):
        return paper_md, True

    # Load bib
    bib_yaml_path = ad / "bib.yaml"
    bib = {}
    bib_sha = ""
    if bib_yaml_path.exists():
        bib = load_bib_full(pdf_path)
        bib_sha = sha256_file(bib_yaml_path)

    if bib.get("needs_review"):
        from rich.console import Console
        _md_err = Console(stderr=True)
        _md_err.print(
            f"[yellow]Warning:[/yellow] {pdf_path.name}: bib.yaml has needs_review=true — "
            "bibliographic information may be unreliable."
        )
        for reason in (bib.get("_review_reasons") or []):
            _md_err.print(f"  [yellow]-[/yellow] {reason}")

    # Extract + repair
    pages_raw = extract_pages(pdf_path)
    pages_repaired = repair_pages(pages_raw)

    full_text = "\n\n".join(pages_repaired)
    atomic_write_text(ad / "paper.raw.txt", full_text)

    # Section detection
    sections = detect_sections(full_text)
    sections_data = sections_to_json(sections)
    atomic_write_text(ad / "paper.sections.json", json.dumps(sections_data, indent=2))

    # Assemble markdown
    parts: list[str] = []

    # Frontmatter
    parts.append(_bib_frontmatter(bib, bib_sha))

    # Title
    title = bib.get("title") or pdf_path.stem
    parts.append(f"# {title}\n")

    # Authors + venue
    author_line = _author_line(bib)
    venue_line = _venue_year_line(bib)
    if author_line:
        parts.append(f"**{author_line}**  ")
    if venue_line:
        parts.append(f"*{venue_line}*\n")
    parts.append("")

    if not sections:
        # No sections detected — emit page-marked raw text
        for i, page in enumerate(pages_repaired):
            parts.append(f"\n<!-- page {i + 1} -->\n")
            parts.append(page)
    else:
        # Emit sections
        for sec in sections:
            heading = _section_heading(sec.title, sec.level)
            body = full_text[sec.start:sec.end]
            # Remove the heading line itself from body
            body_lines = body.split("\n")
            if body_lines and body_lines[0].strip().lower().startswith(sec.title.lower()[:10]):
                body_lines = body_lines[1:]
            body = "\n".join(body_lines).strip()

            # Find which pages this section spans for page markers
            char_pos = sec.start
            page_boundaries = _compute_page_boundaries(pages_repaired)

            parts.append(f"\n{heading}\n")

            # Inject page markers within section body
            body_with_markers = _inject_page_markers(body, sec.start, page_boundaries)

            if llm_cleanup and body.strip():
                body_with_markers = _llm_clean_section(body_with_markers, sec.title, prompt_version)

            # Convert figure captions
            body_with_markers = _format_figures(body_with_markers)

            parts.append(body_with_markers)
            parts.append("")

    markdown = "\n".join(parts)
    atomic_write_text(paper_md, markdown)

    mark_stage_complete(ad, pdf_path, "md", prompt_version)
    return paper_md, False


def _compute_page_boundaries(pages: list[str]) -> list[int]:
    """Return cumulative character offsets where each page starts in the joined text."""
    boundaries = []
    pos = 0
    for page in pages:
        boundaries.append(pos)
        pos += len(page) + 2  # +2 for "\n\n" join
    return boundaries


def _inject_page_markers(body: str, section_start: int, page_boundaries: list[int]) -> str:
    """Insert <!-- page N --> comments at the right places within body."""
    result = body
    offset = 0
    for i, boundary in enumerate(page_boundaries):
        rel = boundary - section_start
        if 0 < rel < len(body):
            marker = f"\n\n<!-- page {i + 1} -->\n\n"
            insert_pos = rel + offset
            result = result[:insert_pos] + marker + result[insert_pos:]
            offset += len(marker)
    return result


def _format_figures(text: str) -> str:
    def _repl(m: re.Match) -> str:
        return f"*{m.group(1).strip()}*"
    return _FIGURE_RE.sub(_repl, text)


def _llm_clean_section(body: str, section_title: str, prompt_version: str) -> str:
    """LLM cleanup of one section. Fails the whole md run on error (by re-raising)."""
    import tiktoken
    from .cleanup import clean_section
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        enc = None

    cap = 8000
    if enc:
        tokens = enc.encode(body)
        if len(tokens) > cap:
            # Split on paragraph boundaries
            paragraphs = body.split("\n\n")
            chunks: list[str] = []
            current: list[str] = []
            current_tokens = 0
            for para in paragraphs:
                para_tokens = len(enc.encode(para))
                if current_tokens + para_tokens > cap and current:
                    chunks.append("\n\n".join(current))
                    current = [para]
                    current_tokens = para_tokens
                else:
                    current.append(para)
                    current_tokens += para_tokens
            if current:
                chunks.append("\n\n".join(current))
            return "\n\n".join(clean_section(chunk, section_title) for chunk in chunks)

    return clean_section(body, section_title)
