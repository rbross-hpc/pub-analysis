# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
# Adapted from annual-report/annual_report/sidecar.py and extract_publication_stub_one.py
"""bib.yaml read/write with per-field provenance merge."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .io import atomic_write_text, now_iso, sha256_file


_SOURCE_PRIORITY: dict[str, int] = {
    "human":          100,
    "osti":            80,
    "openalex":        70,
    "crossref":        65,
    "dblp":            60,
    "bibtex":          55,
    "arxiv":           50,
    "pdf":             30,
    "llm":             20,
    "semanticscholar": 15,
    "derived":         10,
    "unknown":          0,
}

_STICKY_SOURCE_RE = re.compile(r"^tool:[\w.-]+$")
EDIT_SOURCE_RE = re.compile(r"^(human|tool:[\w.-]+)$")


def is_sticky_source(source: str) -> bool:
    """Return True for sources that must never be overwritten by automatic resolution."""
    return source == "human" or bool(_STICKY_SOURCE_RE.match(source))

_CATEGORY_VALUES = {
    "journal article",
    "conference paper",
    "workshop paper",
    "arxiv preprint",
    "preprint",
    "book chapter",
    "book",
    "technical report",
    "thesis",
    "other",
}

_ALL_FIELDS = [
    "title", "authors", "year", "publication_date", "venue", "venue_short",
    "category", "doi", "arxiv_id", "osti_id", "isbn", "issn", "url",
    "abstract", "bibtex_key", "keywords", "language", "license", "oa_status",
    "references_count", "pages",
]


def priority(source: str) -> int:
    if is_sticky_source(source):
        return 100
    return _SOURCE_PRIORITY.get(source, 0)


def make_prov(
    source: str,
    lookup_key: str | None = None,
    similarity: float | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    p: dict[str, Any] = {"source": source, "lookup_key": lookup_key, "at": now_iso()}
    if similarity is not None:
        p["similarity"] = round(similarity, 4)
    if note is not None:
        p["note"] = note
    return p


def set_field(
    fields: dict[str, Any],
    prov: dict[str, Any],
    field: str,
    value: Any,
    source: str,
    lookup_key: str | None = None,
    similarity: float | None = None,
    note: str | None = None,
) -> bool:
    """Set fields[field] if source has strictly higher priority than current. Returns True if updated."""
    if value is None or value == [] or value == "":
        return False
    if field == "category" and value not in _CATEGORY_VALUES:
        return False
    current_source = prov.get(field, {}).get("source", "unknown")
    if field not in prov or priority(source) >= priority(current_source):
        if is_sticky_source(prov.get(field, {}).get("source", "")):
            return False
        fields[field] = value
        prov[field] = make_prov(source, lookup_key, similarity, note)
        return True
    return False


def bib_path(analysis_dir: Path) -> Path:
    return analysis_dir / "bib.yaml"


def load_bib(analysis_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load bib.yaml. Returns (fields, provenance). Both dicts are empty if file absent."""
    p = bib_path(analysis_dir)
    if not p.exists():
        return {}, {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    prov = raw.pop("_provenance", {}) or {}
    raw.pop("_conflicts", None)
    raw.pop("_review_reasons", None)
    raw.pop("_lookup_log", None)
    raw.pop("_meta", None)
    return raw, prov


def save_bib(
    analysis_dir: Path,
    pdf_path: Path,
    fields: dict[str, Any],
    prov: dict[str, Any],
    lookup_log: dict[str, Any],
    conflicts: dict[str, Any],
    tool_version: str,
    prompt_version: str,
    needs_review: bool,
    review_reasons: list[str],
    edit_log: list[dict[str, Any]] | None = None,
    preserve_meta: bool = False,
) -> None:
    from . import __version__

    pdf_sha = sha256_file(pdf_path)

    ordered: dict[str, Any] = {}
    for f in _ALL_FIELDS:
        ordered[f] = fields.get(f)

    ordered["needs_review"] = needs_review
    ordered["notes"] = fields.get("notes", "")

    ordered["_provenance"] = prov

    if conflicts:
        ordered["_conflicts"] = conflicts

    if review_reasons:
        ordered["_review_reasons"] = review_reasons

    if edit_log:
        ordered["_edit_log"] = edit_log

    ordered["_lookup_log"] = lookup_log

    if preserve_meta:
        existing_raw = _load_raw(analysis_dir)
        existing_meta = existing_raw.get("_meta") or {}
        ordered["_meta"] = {
            "schema_version": existing_meta.get("schema_version", 1),
            "tool_version": existing_meta.get("tool_version", tool_version),
            "prompt_version": existing_meta.get("prompt_version", prompt_version),
            "generated_at": now_iso(),
            "pdf_sha256": existing_meta.get("pdf_sha256", pdf_sha),
        }
    else:
        ordered["_meta"] = {
            "schema_version": 1,
            "tool_version": tool_version,
            "prompt_version": prompt_version,
            "generated_at": now_iso(),
            "pdf_sha256": pdf_sha,
        }

    log_lines = []
    for src, info in lookup_log.items():
        status = info.get("status", "?")
        sim = info.get("sim")
        sim_str = f" sim={sim:.2f}" if sim is not None else ""
        key = info.get("key", "")
        reason = info.get("reason", "")
        if reason:
            log_lines.append(f"#   {src:<18} {status}  {reason}")
        else:
            log_lines.append(f"#   {src:<18} {status}  {key}{sim_str}")

    header = "\n".join([
        f"# Generated by puba bib on {now_iso()}",
        f"# Source PDF: {pdf_path.name}  sha256:{pdf_sha[:12]}...",
        "# Lookup status:",
        *log_lines,
        "#",
        "# Fields with _provenance.<field>.source: human (or tool:*) are sticky and",
        "# will not be overwritten by future runs. Edit freely; re-run puba bib to refresh.",
        "",
        "",
    ])

    body = yaml.dump(ordered, allow_unicode=True, sort_keys=False, default_flow_style=False)
    atomic_write_text(bib_path(analysis_dir), header + body)


_PATCH_FIELD_TYPES: dict[str, type | tuple] = {
    "title": str,
    "authors": list,
    "year": int,
    "publication_date": str,
    "venue": str,
    "venue_short": str,
    "category": str,
    "doi": str,
    "arxiv_id": str,
    "osti_id": str,
    "isbn": str,
    "issn": str,
    "url": str,
    "abstract": str,
    "bibtex_key": str,
    "keywords": list,
    "language": str,
    "license": str,
    "oa_status": str,
    "references_count": int,
    "pages": dict,
    "notes": str,
    "needs_review": bool,
}

_DOI_RE = re.compile(r"^10\.\d{4,}/\S+$")
_ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$|^[a-z\-]+/\d{7}(v\d+)?$")


def _validate_patch_field(field: str, value: Any) -> None:
    """Raise ValueError with a human-readable message if value is invalid for field."""
    if field not in _PATCH_FIELD_TYPES:
        raise ValueError(f"Unknown field: {field!r}. Valid fields: {sorted(_PATCH_FIELD_TYPES)}")
    if value is None:
        return
    expected = _PATCH_FIELD_TYPES[field]
    if not isinstance(value, expected):
        raise ValueError(
            f"Field {field!r}: expected {expected.__name__}, got {type(value).__name__}"
        )
    if field == "category" and value not in _CATEGORY_VALUES:
        raise ValueError(
            f"Field 'category': {value!r} is not a valid category. "
            f"Valid: {sorted(_CATEGORY_VALUES)}"
        )
    if field == "doi" and value and not _DOI_RE.match(value):
        raise ValueError(f"Field 'doi': {value!r} does not look like a DOI (expected 10.XXXX/...)")
    if field == "arxiv_id" and value and not _ARXIV_RE.match(value):
        raise ValueError(f"Field 'arxiv_id': {value!r} does not look like an arXiv ID")
    if field == "authors" and value:
        if not all(isinstance(a, str) for a in value):
            raise ValueError("Field 'authors': must be a list of strings")
    if field == "keywords" and value:
        if not all(isinstance(k, str) for k in value):
            raise ValueError("Field 'keywords': must be a list of strings")


def _load_raw(analysis_dir: Path) -> dict[str, Any]:
    """Load bib.yaml as a raw dict including all underscore-prefixed keys."""
    p = bib_path(analysis_dir)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def apply_patch(
    analysis_dir: Path,
    pdf_path: Path,
    patch_fields: dict[str, Any],
    source: str,
    note: str | None = None,
    clear_review: bool = False,
) -> dict[str, Any]:
    """Apply a field-level patch to bib.yaml, stamping sticky provenance.

    patch_fields: {field: value}. Explicit None deletes the field and its
    provenance entry. All other values must pass type/format validation.

    source: must match ^(human|tool:[\\w.-]+)$.
    Returns a result dict with fields_changed, cleared_review, bib_yaml.
    """
    if not EDIT_SOURCE_RE.match(source):
        raise ValueError(
            f"Invalid source {source!r}. Must be 'human' or match 'tool:<name>' "
            f"where <name> contains only word chars, dots, hyphens."
        )

    for field in patch_fields:
        if field.startswith("_"):
            raise ValueError(
                f"Cannot patch underscore-prefixed key {field!r}. "
                "These are tool-managed; edit bib.yaml directly if needed."
            )

    for field, value in patch_fields.items():
        _validate_patch_field(field, value)

    raw = _load_raw(analysis_dir)
    prov: dict[str, Any] = raw.get("_provenance") or {}
    conflicts: dict[str, Any] = raw.get("_conflicts") or {}
    lookup_log: dict[str, Any] = raw.get("_lookup_log") or {}
    existing_edit_log: list[dict[str, Any]] = list(raw.get("_edit_log") or [])
    meta: dict[str, Any] = raw.get("_meta") or {}

    fields: dict[str, Any] = {
        k: v for k, v in raw.items()
        if not k.startswith("_") and k not in ("needs_review", "notes")
    }
    needs_review: bool = bool(raw.get("needs_review", False))
    review_reasons: list[str] = list(raw.get("_review_reasons") or [])
    notes: str = raw.get("notes", "") or ""

    fields_changed: list[str] = []
    now = now_iso()

    for field, value in patch_fields.items():
        if field == "needs_review":
            needs_review = bool(value) if value is not None else False
            fields_changed.append(field)
            continue
        if field == "notes":
            notes = value if value is not None else ""
            fields_changed.append(field)
            continue

        previous = fields.get(field)
        if value is None:
            fields.pop(field, None)
            prov.pop(field, None)
        else:
            fields[field] = value
            prov[field] = {
                "source": source,
                "lookup_key": None,
                "at": now,
                "note": note,
                "previous": previous,
            }
        fields_changed.append(field)

    if clear_review:
        needs_review = False
        review_reasons = []

    edit_entry: dict[str, Any] = {
        "at": now,
        "source": source,
        "fields_changed": fields_changed,
        "note": note,
        "cleared_review": clear_review,
    }
    new_edit_log = existing_edit_log + [edit_entry]

    fields["notes"] = notes

    save_bib(
        analysis_dir=analysis_dir,
        pdf_path=pdf_path,
        fields=fields,
        prov=prov,
        lookup_log=lookup_log,
        conflicts=conflicts,
        tool_version=meta.get("tool_version", ""),
        prompt_version=meta.get("prompt_version", ""),
        needs_review=needs_review,
        review_reasons=review_reasons,
        edit_log=new_edit_log,
        preserve_meta=True,
    )

    return {
        "fields_changed": fields_changed,
        "cleared_review": clear_review,
        "bib_yaml": str(bib_path(analysis_dir)),
    }


def load(pdf_path: Path) -> dict[str, Any]:
    """Public API: load the full bib.yaml for a paper as a flat dict."""
    from .state import analysis_dir as get_analysis_dir
    d = get_analysis_dir(pdf_path)
    p = bib_path(d)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_clean(
    pdf_path: Path,
    include_verbose: bool = False,
) -> dict[str, Any]:
    """Load bib.yaml and return a shaped dict suitable for the show API.

    Always includes:
      - ``fields``: resolved bibliographic fields (no ``_``-prefixed keys)
      - ``provenance``: per-field provenance dict
      - ``needs_review``: bool

    With ``include_verbose=True`` also includes:
      - ``conflicts``: conflict records (may be empty dict)
      - ``lookup_log``: per-source resolution log
      - ``meta``: schema/tool/prompt version metadata
    """
    from .state import analysis_dir as get_analysis_dir
    d = get_analysis_dir(pdf_path)
    p = bib_path(d)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    fields = {k: v for k, v in raw.items() if not k.startswith("_") and k != "needs_review" and k != "notes"}
    fields["notes"] = raw.get("notes", "")

    result: dict[str, Any] = {
        "fields": fields,
        "provenance": raw.get("_provenance") or {},
        "needs_review": bool(raw.get("needs_review")),
        "review_reasons": raw.get("_review_reasons") or [],
    }

    if include_verbose:
        result["conflicts"] = raw.get("_conflicts") or {}
        result["lookup_log"] = raw.get("_lookup_log") or {}
        result["meta"] = raw.get("_meta") or {}

    return result
