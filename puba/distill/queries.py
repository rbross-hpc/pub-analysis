# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Load and merge distillation query definitions from config and prompts/ directory."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
_VALID_SCOPES = {"abstract", "narrative", "full", "section"}


@dataclass
class DistillQuery:
    name: str
    scope: str
    prompt: str
    max_chars: int | None
    model: str | None
    section: str | None
    source: str


def _parse_query(name: str, defn: dict[str, Any], source: str) -> DistillQuery:
    return DistillQuery(
        name=name,
        scope=str(defn.get("scope", "abstract")),
        prompt=str(defn.get("prompt", "")),
        max_chars=int(defn["max_chars"]) if defn.get("max_chars") is not None else None,
        model=str(defn["model"]) if defn.get("model") else None,
        section=str(defn["section"]) if defn.get("section") else None,
        source=source,
    )


def load_queries(cwd: Path | None = None) -> dict[str, DistillQuery]:
    """Load all defined distillation queries.

    Load order (later wins on name collision):
      1. Packaged config.yaml  distill.queries.*
      2. Project-local ./puba.config.yaml  distill.queries.*
      3. ./prompts/*.yaml  (top-level keys are query definitions)
    """
    from .. import config as cfg

    queries: dict[str, DistillQuery] = {}

    cfg_distill = cfg.load().get("distill", {})
    for name, defn in (cfg_distill.get("queries") or {}).items():
        source = cfg.load().get("_source", {}).get(f"distill.queries.{name}", "config.yaml")
        queries[name] = _parse_query(name, defn, source)

    prompts_dir = (cwd or Path.cwd()) / "prompts"
    if prompts_dir.is_dir():
        for yaml_file in sorted(prompts_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            except Exception as e:
                raise ValueError(f"Failed to parse {yaml_file}: {e}") from e
            for name, defn in raw.items():
                if not isinstance(defn, dict):
                    continue
                queries[name] = _parse_query(name, defn, str(yaml_file))

    return queries


def validate_queries(queries: dict[str, DistillQuery]) -> list[str]:
    """Return a list of validation error strings."""
    errors: list[str] = []
    seen_from_prompts: dict[str, str] = {}

    for name, q in queries.items():
        if not _NAME_RE.match(name):
            errors.append(
                f"distill query {name!r}: name must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
            )
        if q.scope not in _VALID_SCOPES:
            errors.append(
                f"distill query {name!r}: scope {q.scope!r} must be one of {sorted(_VALID_SCOPES)}"
            )
        if q.scope == "section":
            if not q.section:
                errors.append(
                    f"distill query {name!r}: scope=section requires a 'section' field"
                )
            elif not _NAME_RE.match(q.section):
                errors.append(
                    f"distill query {name!r}: section {q.section!r} must match "
                    f"^[a-zA-Z_][a-zA-Z0-9_]*$"
                )
        if not q.prompt.strip():
            errors.append(f"distill query {name!r}: prompt is empty")
        if q.max_chars is not None:
            if q.max_chars <= 0:
                errors.append(
                    f"distill query {name!r}: max_chars must be a positive integer"
                )
            elif q.max_chars < 100:
                errors.append(
                    f"distill query {name!r}: max_chars={q.max_chars} is very small (< 100) — verify this is intentional"
                )

        if q.source not in ("config.yaml", "puba.config.yaml") and "prompts" in q.source:
            if name in seen_from_prompts and seen_from_prompts[name] != q.source:
                errors.append(
                    f"distill query {name!r}: defined in both {seen_from_prompts[name]} "
                    f"and {q.source} — remove one"
                )
            seen_from_prompts[name] = q.source

    return errors
