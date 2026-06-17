# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Config-driven section detection from repaired full-text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .. import config as cfg


@dataclass
class Section:
    title: str
    level: int
    start: int
    end: int


@lru_cache(maxsize=1)
def _heading_words() -> set[str]:
    return {w.lower() for w in cfg.md().get("section_heading_words", [])}


@lru_cache(maxsize=1)
def _numbered_pattern() -> re.Pattern:
    pat = cfg.md().get("section_numbered_pattern", r"^(\d+(\.\d+)*)\s+[A-Z]")
    return re.compile(pat, re.MULTILINE)


def _is_heading_line(line: str) -> tuple[bool, int]:
    """Return (is_heading, level). Level 1=top, 2=sub, etc."""
    stripped = line.strip()
    if not stripped:
        return False, 0

    # Numbered heading: "1 Introduction", "2.1 Related Work"
    m = _numbered_pattern().match(stripped)
    if m:
        num = m.group(1)
        level = num.count(".") + 1
        return True, level

    # Known heading word (case-insensitive, standalone line or short line)
    lower = stripped.lower().rstrip(":")
    if lower in _heading_words() and len(stripped) < 80:
        return True, 1

    # Heading word at start of short all-caps or title-case line
    first_word = stripped.split()[0].rstrip(":").lower() if stripped.split() else ""
    if first_word in _heading_words() and len(stripped) < 80:
        return True, 1

    return False, 0


def detect_sections(full_text: str) -> list[Section]:
    """Detect sections in full_text. Returns list of Section objects."""
    lines = full_text.split("\n")
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    headings: list[tuple[int, str, int]] = []  # (char_offset, title, level)
    for i, line in enumerate(lines):
        is_h, level = _is_heading_line(line)
        if is_h:
            headings.append((offsets[i], line.strip(), level))

    sections: list[Section] = []
    for idx, (start, title, level) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(full_text)
        sections.append(Section(title=title, level=level, start=start, end=end))

    return sections


def sections_to_json(sections: list[Section]) -> list[dict[str, Any]]:
    return [
        {"title": s.title, "level": s.level, "start_offset": s.start, "end_offset": s.end}
        for s in sections
    ]
