# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Config-driven section detection from repaired full-text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .. import config as cfg

_SLUG_LEADING_NUM_RE = re.compile(r'^\d+(\.\d+)*\s+')
_SLUG_NONALPHA_RE = re.compile(r'[^\w]+')
_SLUG_MULTI_UNDER_RE = re.compile(r'_+')


def derive_short_name(title: str) -> str:
    """Slugify a section title into a filesystem-safe short name.

    Rules:
    - Strip leading numeric prefix ("2.1 Related Work" → "Related Work")
    - Lowercase
    - Split into words (non-alphanumeric as separators)
    - Keep at most 4 words
    - Join with underscores
    - If first char is a digit, prefix with "s_"
    - Empty result → "section"
    """
    s = title.strip()
    if not s:
        return "section"
    s = _SLUG_LEADING_NUM_RE.sub("", s)
    s = s.lower()
    s = _SLUG_NONALPHA_RE.sub("_", s)
    s = _SLUG_MULTI_UNDER_RE.sub("_", s)
    s = s.strip("_")
    if not s:
        return "section"
    words = [w for w in s.split("_") if w]
    s = "_".join(words[:4])
    if not s:
        return "section"
    if s[0].isdigit():
        s = "s_" + s
    return s


def short_names(sections: list["Section"]) -> list[str]:
    """Return collision-free short names, parallel to the input list.

    Disambiguates by appending _2, _3, ... in document order.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for sec in sections:
        base = derive_short_name(sec.title)
        if base not in seen:
            seen[base] = 1
            result.append(base)
        else:
            seen[base] += 1
            result.append(f"{base}_{seen[base]}")
    return result


@dataclass
class Section:
    title: str
    level: int
    start: int
    end: int
    short_name: str = ""


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

    # Known heading word: only match if the entire line IS the heading word/phrase.
    # Require ≤ 6 words total so we don't pick up mid-sentence lines that happen
    # to start with a heading word (common in two-column PDF reflow).
    words = stripped.split()
    if not words:
        return False, 0

    lower = stripped.lower().rstrip(":")
    if lower in _heading_words():
        return True, 1

    # Multi-word heading: first word is a known heading word AND line is short (≤6 words)
    # AND does not look like a prose continuation (no comma, no verb endings mid-line).
    first_word = words[0].rstrip(":").lower()
    if (
        first_word in _heading_words()
        and len(words) <= 6
        and len(stripped) < 80
        and not stripped.endswith((",", ";", "and", "or", "the", "a", "an"))
    ):
        return True, 1

    return False, 0


def detect_sections(full_text: str) -> list[Section]:
    """Detect sections in full_text. Returns list of Section objects with short_names."""
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

    names = short_names(sections)
    for sec, name in zip(sections, names):
        sec.short_name = name

    return sections


def sections_to_json(sections: list[Section]) -> list[dict[str, Any]]:
    return [
        {
            "short_name": s.short_name,
            "title": s.title,
            "level": s.level,
            "start_offset": s.start,
            "end_offset": s.end,
        }
        for s in sections
    ]


def load_sections_json(analysis_dir: "Path") -> list[dict[str, Any]]:  # type: ignore[name-defined]
    """Load paper.sections.json, back-filling short_name if missing (backwards compat)."""
    import json
    from pathlib import Path as _Path

    p = _Path(analysis_dir) / "paper.sections.json"
    if not p.exists():
        return []
    data: list[dict[str, Any]] = json.loads(p.read_text(encoding="utf-8"))

    seen: dict[str, int] = {}
    for entry in data:
        if not entry.get("short_name"):
            base = derive_short_name(entry.get("title", ""))
            if base not in seen:
                seen[base] = 1
                entry["short_name"] = base
            else:
                seen[base] += 1
                entry["short_name"] = f"{base}_{seen[base]}"
        else:
            name = entry["short_name"]
            seen[name] = seen.get(name, 0) + 1

    return data
