# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Build LLM input content for each distillation scope."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..io import sha256_text
from .. import config as cfg


def _bib_header(bib: dict[str, Any]) -> str:
    parts = []
    if bib.get("title"):
        parts.append(f"Title: {bib['title']}")
    authors = bib.get("authors") or []
    if authors:
        author_str = ", ".join(authors[:5])
        if len(authors) > 5:
            author_str += " et al."
        parts.append(f"Authors: {author_str}")
    if bib.get("venue"):
        parts.append(f"Venue: {bib['venue']}")
    if bib.get("year"):
        parts.append(f"Year: {bib['year']}")
    return "\n".join(parts)


def _strip_narrative_sections(md_text: str) -> str:
    """Remove trailing sections (References, Acknowledgments, etc.) from paper.md."""
    strip_words = {
        w.lower() for w in cfg.md_distill_strip_sections()
    }

    lines = md_text.split("\n")
    cut_at: int | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = re.sub(r'^#+\s*', '', stripped).strip().lower().rstrip(":")
        if heading in strip_words:
            cut_at = i
            break

    if cut_at is not None:
        lines = lines[:cut_at]

    page_marker_re = re.compile(r'^<!--\s*page\s+\d+\s*-->\s*$')
    lines = [l for l in lines if not page_marker_re.match(l)]

    return "\n".join(lines).strip()


def build_input(
    scope: str,
    bib: dict[str, Any],
    analysis_dir: Path,
) -> tuple[str, str | None]:
    """Build the LLM input string and return (content, paper_md_sha|None).

    Raises RuntimeError if required artifacts are missing.
    """
    from ..io import sha256_file

    bib_header = _bib_header(bib)

    if scope == "abstract":
        abstract = (bib.get("abstract") or "").strip()
        if not abstract:
            raise RuntimeError(
                "scope=abstract requires an abstract in bib.yaml, but bib.yaml.abstract is empty. "
                "Try running puba bib --force to re-resolve, or use scope=narrative/full."
            )
        content = f"{bib_header}\n\nAbstract:\n{abstract}"
        return content, None

    paper_md = analysis_dir / "paper.md"
    if not paper_md.exists():
        raise RuntimeError(
            f"scope={scope!r} requires paper.md. Run `puba md <pdf>` first."
        )

    paper_md_sha = sha256_file(paper_md)[:12]
    md_text = paper_md.read_text(encoding="utf-8")

    if scope == "narrative":
        body = _strip_narrative_sections(md_text)
    elif scope == "full":
        body = md_text
    else:
        raise ValueError(f"Unknown scope: {scope!r}")

    content = f"{bib_header}\n\n---\n\n{body}"
    return content, paper_md_sha


def check_token_budget(content: str) -> int:
    """Return approximate token count. Raises RuntimeError if over budget."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        count = len(enc.encode(content))
    except Exception:
        count = len(content) // 4

    max_tokens = cfg.load().get("distill", {}).get("max_input_tokens", 100000)
    if count > max_tokens:
        raise RuntimeError(
            f"Input is approximately {count:,} tokens, which exceeds "
            f"max_input_tokens={max_tokens:,}. "
            f"Switch to a narrower scope ('abstract' or 'narrative'), "
            f"use a larger-context model, or raise distill.max_input_tokens in config."
        )
    return count
