# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Section data model, short-name derivation, and JSON I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def load_sections_json(analysis_dir: Path) -> list[dict[str, Any]]:
    """Load paper.sections.json, back-filling short_name if missing (backwards compat)."""
    import json

    p = Path(analysis_dir) / "paper.sections.json"
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
