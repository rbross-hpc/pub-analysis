# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Build LLM input content for each distillation scope."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config as cfg
from ..io import sha256_text


@dataclass(frozen=True)
class ScopeInput:
    """Model input and the canonical coordinates permitted for evidence.

    ``allowed_start`` and ``allowed_end`` are offsets into ``canonical_source``.
    Model content is intentionally separate because prompt headers and removed
    page markers are not evidence.
    """

    model_content: str
    canonical_source: str
    allowed_start: int
    allowed_end: int
    sections: list[dict[str, Any]]
    source_hashes: dict[str, str]

    @property
    def allowed_span(self) -> tuple[int, int]:
        return self.allowed_start, self.allowed_end


def _bib_header(bib: dict[str, Any]) -> str:
    parts = []
    if bib.get("title"):
        parts.append(f"Title: {bib['title']}")
    authors = bib.get("authors") or []
    if authors:
        author_names = [a["name"] if isinstance(a, dict) else a for a in authors]
        author_str = ", ".join(author_names[:5])
        if len(authors) > 5:
            author_str += " et al."
        parts.append(f"Authors: {author_str}")
    if bib.get("venue"):
        parts.append(f"Venue: {bib['venue']}")
    if bib.get("year"):
        parts.append(f"Year: {bib['year']}")
    return "\n".join(parts)


def _narrative_end(md_text: str) -> int:
    """Return the raw offset of the first configured trailing heading."""
    strip_words = {
        w.lower() for w in cfg.md_distill_strip_sections()
    }

    offset = 0
    for line in md_text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped.startswith("#"):
            offset += len(line)
            continue
        heading = re.sub(r'^#+\s*', '', stripped).strip().lower().rstrip(":")
        if heading in strip_words:
            return offset
        offset += len(line)
    return len(md_text)


def _strip_narrative_sections(md_text: str) -> str:
    """Remove trailing sections and page markers from model content."""
    narrative = md_text[:_narrative_end(md_text)]
    lines = narrative.split("\n")

    page_marker_re = re.compile(r'^<!--\s*page\s+\d+\s*-->\s*$')
    lines = [l for l in lines if not page_marker_re.match(l)]

    return "\n".join(lines).strip()


_PAGE_MARKER_RE = re.compile(r'^<!--\s*page\s+\d+\s*-->\s*$', re.MULTILINE)


def _strip_page_markers(text: str) -> str:
    lines = [l for l in text.split("\n") if not _PAGE_MARKER_RE.match(l)]
    return "\n".join(lines).strip()


def build_scope_input(
    scope: str,
    bib: dict[str, Any],
    analysis_dir: Path,
    section_name: str | None = None,
) -> ScopeInput:
    """Build model content together with its canonical evidence boundary.

    Raises RuntimeError if required artifacts are missing or section not found.
    """
    from ..pdf.sections import load_sections_json

    bib_header = _bib_header(bib)

    if scope == "abstract":
        abstract = bib.get("abstract")
        if not isinstance(abstract, str) or not abstract.strip():
            raise RuntimeError(
                "scope=abstract requires an abstract in the bibliographic record, but abstract is empty. "
                "Try running puba bib --force to re-resolve, or use scope=narrative/full."
            )
        content = f"{bib_header}\n\nAbstract:\n{abstract.strip()}"
        return ScopeInput(
            model_content=content,
            canonical_source=abstract,
            allowed_start=0,
            allowed_end=len(abstract),
            sections=[],
            source_hashes={
                "canonical_source_sha256": sha256_text(abstract),
                "allowed_source_sha256": sha256_text(abstract),
                "model_content_sha256": sha256_text(content),
            },
        )

    if scope not in {"narrative", "full", "section"}:
        raise ValueError(f"Unknown scope: {scope!r}")

    paper_md = analysis_dir / "paper.md"
    if not paper_md.exists():
        raise RuntimeError(
            f"scope={scope!r} requires paper.md. Run `puba md <pdf>` first."
        )

    md_text = paper_md.read_text(encoding="utf-8")
    sections_path = analysis_dir / "paper.sections.json"
    sections_raw = sections_path.read_text(encoding="utf-8") if sections_path.exists() else None
    sections = load_sections_json(analysis_dir) if sections_raw is not None else []
    hashes = {
        "canonical_source_sha256": sha256_text(md_text),
        "paper_md_sha256": sha256_text(md_text),
    }
    if sections_raw is not None:
        hashes["sections_sha256"] = sha256_text(sections_raw)

    if scope == "narrative":
        allowed_start, allowed_end = 0, _narrative_end(md_text)
        body = _strip_narrative_sections(md_text)
    elif scope == "full":
        allowed_start, allowed_end = 0, len(md_text)
        body = md_text
    else:
        if not section_name:
            raise RuntimeError(
                "scope=section requires a 'section' field in the query definition."
            )
        if not sections:
            raise RuntimeError(
                "paper.sections.json is empty or not found. Run `puba md <pdf>` first."
            )
        match = next((s for s in sections if s.get("short_name") == section_name), None)
        if not match:
            available = sorted(s["short_name"] for s in sections if s.get("short_name"))
            raise RuntimeError(
                f"Section {section_name!r} not found in this paper.\n"
                f"Available sections: {', '.join(available)}\n"
                f"Run `puba sections <pdf>` to see the full list."
            )
        start = match["start_offset"]
        end = match["end_offset"]
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or not 0 <= start <= end <= len(md_text)
        ):
            raise RuntimeError(f"Section {section_name!r} has an invalid source span.")
        allowed_start, allowed_end = start, end
        section_body = _strip_page_markers(md_text[start:end])
        section_title = match.get("title", section_name)
        body = f"Section: {section_title}\n\n{section_body}"

    content = f"{bib_header}\n\n---\n\n{body}"
    hashes["allowed_source_sha256"] = sha256_text(md_text[allowed_start:allowed_end])
    hashes["model_content_sha256"] = sha256_text(content)
    return ScopeInput(
        model_content=content,
        canonical_source=md_text,
        allowed_start=allowed_start,
        allowed_end=allowed_end,
        sections=sections,
        source_hashes=hashes,
    )


def build_input(
    scope: str,
    bib: dict[str, Any],
    analysis_dir: Path,
    section_name: str | None = None,
) -> tuple[str, str | None]:
    """Compatibility wrapper returning the historical input tuple."""
    scope_input = build_scope_input(scope, bib, analysis_dir, section_name)
    paper_hash = scope_input.source_hashes.get("paper_md_sha256")
    return scope_input.model_content, paper_hash[:12] if paper_hash else None


def estimate_tokens(content: str) -> int:
    """Return a deterministic best-effort token estimate for request accounting."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(content))
    except Exception:
        return (len(content) + 3) // 4


def check_token_budget(content: str) -> int:
    """Return approximate token count. Raises RuntimeError if over budget."""
    count = estimate_tokens(content)

    max_tokens = cfg.load().get("distill", {}).get("max_input_tokens", 100000)
    if count > max_tokens:
        raise RuntimeError(
            f"Input is approximately {count:,} tokens, which exceeds "
            f"max_input_tokens={max_tokens:,}. "
            f"Switch to a narrower scope ('abstract' or 'narrative'), "
            f"use a larger-context model, or raise distill.max_input_tokens in config."
        )
    return count
