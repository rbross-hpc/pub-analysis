# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""JSON-capable artifact I/O compatibility layer.

This module is the **only** place in puba/ that is allowed to reference
``bib.yaml``/``analyses/*.yaml`` paths directly for the purpose of reading or
writing generated artifacts.  All other code must go through this layer.

Reader contract
---------------
* Prefers ``bib.json`` / ``analyses/<name>.json`` (the new format).
* Transparently falls back to the legacy ``bib.yaml`` /
  ``analyses/<name>.yaml`` form when the JSON form is absent.
* When **both** a JSON and a YAML form exist for the same artifact, JSON is
  treated as authoritative.  The caller is signalled via a
  :class:`BothFormsPresentWarning` that a stale YAML file is present;
  they can choose to log it, expose it in a status report, or ignore it.
  The stale YAML is NOT silently ignored — it is surfaced so that
  Milestone 5's "Needs Attention" section can flag it.

Writer contract
---------------
* Callers choose the format explicitly (``"json"`` or ``"yaml"``).
* All writes are made via the atomic-rename primitives in :mod:`puba.io`
  so there is no partial-write window.
* This milestone does **not** change which format existing call sites use.
  That switch happens in Milestone 2.

ADR: docs/decisions/0003-json-over-yaml-for-generated-artifacts.md
"""
from __future__ import annotations

import json
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TypeGuard

import yaml

from .io import atomic_write_json, atomic_write_text


# ---------------------------------------------------------------------------
# Public warning type
# ---------------------------------------------------------------------------


class BothFormsPresentWarning(UserWarning):
    """Raised (via warnings.warn) when both a .json and a .yaml form of the
    same artifact are found on disk.

    Callers that need to detect this condition programmatically can install a
    warnings filter or use ``warnings.catch_warnings``.

    Attributes
    ----------
    json_path : Path
        The authoritative JSON file that was read.
    yaml_path : Path
        The stale YAML file that was ignored.
    """

    def __init__(self, json_path: Path, yaml_path: Path) -> None:
        self.json_path = json_path
        self.yaml_path = yaml_path
        super().__init__(
            f"Both {json_path} and {yaml_path} exist on disk; "
            f"{json_path.name} is authoritative and {yaml_path.name} is stale. "
            "The stale YAML should be removed the next time this artifact is rewritten "
            "(see Milestone 2)."
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def bib_json_path(analysis_dir: Path) -> Path:
    """Return the path for the new-format bib record."""
    return analysis_dir / "bib.json"


def bib_yaml_path(analysis_dir: Path) -> Path:
    """Return the path for the legacy bib record."""
    return analysis_dir / "bib.yaml"


def distill_json_path(analysis_dir: Path, name: str) -> Path:
    """Return the path for the new-format distillation record."""
    return analysis_dir / "analyses" / f"{name}.json"


def distill_yaml_path(analysis_dir: Path, name: str) -> Path:
    """Return the path for the legacy distillation record."""
    return analysis_dir / "analyses" / f"{name}.yaml"


def bib_record_path(analysis_dir: Path) -> Path:
    """Return the authoritative bib path (JSON preferred, YAML fallback)."""
    json_path = bib_json_path(analysis_dir)
    return json_path if json_path.exists() else bib_yaml_path(analysis_dir)


def distill_record_path(analysis_dir: Path, name: str) -> Path:
    """Return the authoritative distillation path (JSON preferred, YAML fallback)."""
    json_path = distill_json_path(analysis_dir, name)
    return json_path if json_path.exists() else distill_yaml_path(analysis_dir, name)


# ---------------------------------------------------------------------------
# Readers and structural validation
# ---------------------------------------------------------------------------


_QUOTE_STATUSES = ("present", "not_found", "ambiguous")
_SUPPORT_STATUSES = (
    "supported",
    "partially_supported",
    "unsupported",
    "contradicted",
    "insufficient_evidence",
    "not_assessed",
)


def _quote_aggregate(statuses: list[str]) -> tuple[str, dict[str, int]]:
    counts = {"total": len(statuses), **{status: statuses.count(status) for status in _QUOTE_STATUSES}}
    overall = "complete" if statuses and counts["present"] == counts["total"] else "partial"
    return overall, counts


def _support_aggregate(statuses: list[str]) -> tuple[str, dict[str, int]]:
    counts = {"total": len(statuses), **{status: statuses.count(status) for status in _SUPPORT_STATUSES}}
    if not statuses:
        overall = "not_applicable"
    elif counts["contradicted"]:
        overall = "contradicted"
    elif counts["supported"] == counts["total"]:
        overall = "supported"
    elif counts["unsupported"] == counts["total"]:
        overall = "unsupported"
    else:
        overall = "partial"
    return overall, counts


def _require_aggregate(record: dict[str, Any], prefix: str, expected_status: str,
                       expected_counts: dict[str, int]) -> None:
    if record.get(f"{prefix}_status") != expected_status:
        raise ValueError(f"{prefix}_status is inconsistent with its items")
    counts = record.get(f"{prefix}_counts")
    if not isinstance(counts, dict):
        raise ValueError(f"{prefix}_counts must be an object")
    for key, expected in expected_counts.items():
        if type(counts.get(key)) is not int or counts[key] != expected:
            raise ValueError(f"{prefix}_counts.{key} is inconsistent with its items")


def _validate_quote_item(item: Any, *, require_id: bool) -> str:
    if not isinstance(item, dict):
        raise ValueError("evidence items must be objects")
    if not isinstance(item.get("quote"), str) or not item["quote"].strip():
        raise ValueError("evidence items must contain a non-blank quote")
    if require_id and (not isinstance(item.get("id"), str) or not item["id"].strip()):
        raise ValueError("claim evidence items must contain a non-blank id")
    status = item.get("quote_status")
    if status not in _QUOTE_STATUSES:
        raise ValueError(f"quote_status must be one of {', '.join(_QUOTE_STATUSES)}")
    return status


def _normalize_v1(record: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(record)
    normalized["schema_version"] = 1
    if "evidence" not in normalized:
        return normalized

    evidence = normalized["evidence"]
    if not isinstance(evidence, list):
        raise ValueError("schema-v1 evidence must be a list")
    statuses: list[str] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("schema-v1 evidence items must be objects")
        if not isinstance(item.get("quote"), str) or not item["quote"].strip():
            raise ValueError("schema-v1 evidence items must contain a non-blank quote")
        if item.get("status") == "verified":
            quote_status = "present"
        elif item.get("status") == "unverified" and item.get("reason") == "no_match":
            quote_status = "not_found"
        elif item.get("status") == "unverified" and item.get("reason") == "ambiguous_match":
            quote_status = "ambiguous"
        else:
            raise ValueError("schema-v1 evidence item has an invalid status/reason")
        existing = item.get("quote_status", quote_status)
        if existing != quote_status:
            raise ValueError("schema-v1 quote_status conflicts with status/reason")
        item["quote_status"] = quote_status
        statuses.append(quote_status)

    normalized["evidence_mode"] = "quotes"
    presence_status, counts = _quote_aggregate(statuses)
    normalized["quote_presence_status"] = presence_status
    normalized["quote_presence_counts"] = counts
    expected_legacy_status = "verified" if presence_status == "complete" else "partial"
    if normalized.get("evidence_status") != expected_legacy_status:
        raise ValueError("schema-v1 evidence_status is inconsistent with its items")
    return normalized


def _validate_v2(record: dict[str, Any]) -> dict[str, Any]:
    if not record["output"].strip():
        raise ValueError("schema-v2 output must be non-blank")
    mode = record.get("evidence_mode")
    if mode not in ("none", "quotes", "claims"):
        raise ValueError("schema-v2 evidence_mode must be none, quotes, or claims")
    if "evidence_status" in record:
        raise ValueError("schema-v2 records must use quote-presence terminology")

    quote_fields = {"evidence", "quote_presence_status", "quote_presence_counts"}
    claim_fields = {"claims", "claim_support_status", "claim_support_counts"}
    if mode == "none":
        if any(field in record for field in quote_fields | claim_fields):
            raise ValueError("schema-v2 none mode must omit quote and claim fields")
        return deepcopy(record)

    if mode == "quotes":
        if any(field in record for field in claim_fields):
            raise ValueError("schema-v2 quotes mode must omit claim fields")
        evidence = record.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("schema-v2 quotes mode requires an evidence list")
        statuses = [_validate_quote_item(item, require_id=False) for item in evidence]
        _require_aggregate(record, "quote_presence", *_quote_aggregate(statuses))
        return deepcopy(record)

    if "evidence" in record:
        raise ValueError("schema-v2 claims mode must use claim evidence, not flat evidence")
    claims = record.get("claims")
    if not isinstance(claims, list):
        raise ValueError("schema-v2 claims mode requires a claims list")

    claim_ids: set[str] = set()
    all_evidence_ids: set[str] = set()
    quote_statuses: list[str] = []
    support_statuses: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("claims must be objects")
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id.strip() or claim_id in claim_ids:
            raise ValueError("claim ids must be non-blank and unique")
        claim_ids.add(claim_id)
        if not isinstance(claim.get("claim"), str) or not claim["claim"].strip():
            raise ValueError("claims must contain non-blank claim text")
        evidence = claim.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("claims must contain an evidence list")
        evidence_ids: set[str] = set()
        present_evidence_ids: set[str] = set()
        for item in evidence:
            quote_status = _validate_quote_item(item, require_id=True)
            quote_statuses.append(quote_status)
            evidence_id = item["id"]
            if evidence_id in all_evidence_ids:
                raise ValueError("evidence ids must be unique")
            evidence_ids.add(evidence_id)
            all_evidence_ids.add(evidence_id)
            if quote_status == "present":
                present_evidence_ids.add(evidence_id)

        assessment = claim.get("support_assessment")
        if not isinstance(assessment, dict) or assessment.get("status") not in _SUPPORT_STATUSES:
            raise ValueError("claims must contain a valid support_assessment")
        support_status = assessment["status"]
        rationale = assessment.get("rationale")
        if support_status != "not_assessed" and (
            not isinstance(rationale, str) or not rationale.strip()
        ):
            raise ValueError("assessed claims must contain a non-blank rationale")
        if rationale is not None and (not isinstance(rationale, str) or not rationale.strip()):
            raise ValueError("support rationale must be a non-blank string when present")
        linked_ids = assessment.get("evidence_ids")
        if not isinstance(linked_ids, list) or any(
            not isinstance(value, str) or value not in present_evidence_ids for value in linked_ids
        ) or len(linked_ids) != len(set(linked_ids)):
            raise ValueError("support evidence_ids must be unique present evidence ids")
        if not present_evidence_ids and support_status != "not_assessed":
            raise ValueError("claims without present evidence must be not_assessed")
        if present_evidence_ids and support_status == "not_assessed":
            raise ValueError("claims with present evidence must be model-assessed")
        support_statuses.append(support_status)

    _require_aggregate(record, "quote_presence", *_quote_aggregate(quote_statuses))
    _require_aggregate(record, "claim_support", *_support_aggregate(support_statuses))
    return deepcopy(record)


def _normalize_distill_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("distillation record must be an object")
    if not isinstance(record.get("output"), str):
        raise ValueError("distillation record must contain a string output")
    version = record.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("distillation schema_version must be integer 1 or 2")
    return _normalize_v1(record) if version == 1 else _validate_v2(record)


def is_distill_record(record: Any) -> TypeGuard[dict[str, Any]]:
    """Return whether *record* passes the distillation compatibility boundary."""
    try:
        _normalize_distill_record(record)
    except ValueError:
        return False
    return True


def read_bib(analysis_dir: Path) -> Any | None:
    """Read the bib record for *analysis_dir*, preferring JSON over YAML.

    Returns
    -------
    dict | None
        Parsed record as a plain dict, or ``None`` if neither form exists.

    Warns
    -----
    BothFormsPresentWarning
        When both ``bib.json`` and ``bib.yaml`` exist.  The JSON form is
        returned; the YAML form is ignored but surfaced as a warning so
        callers can flag the stale file.
    """
    json_p = bib_json_path(analysis_dir)
    yaml_p = bib_yaml_path(analysis_dir)

    json_exists = json_p.exists()
    yaml_exists = yaml_p.exists()

    if json_exists and yaml_exists:
        warnings.warn(BothFormsPresentWarning(json_p, yaml_p), stacklevel=2)

    if json_exists:
        with open(json_p, encoding="utf-8") as fh:
            record = json.load(fh)
    elif yaml_exists:
        with open(yaml_p, encoding="utf-8") as fh:
            record = yaml.safe_load(fh) or {}
    else:
        return None

    # Records written before schema versions existed are format-equivalent to v1.
    if record:
        record.setdefault("schema_version", 1)
    return record


def read_distill(analysis_dir: Path, name: str) -> Any | None:
    """Read a distillation record, preferring JSON over YAML.

    Parameters
    ----------
    analysis_dir:
        The ``<stem>.puba/`` directory for the paper.
    name:
        The distillation query name (filename stem, e.g. ``"summary"``).

    Returns
    -------
    dict | None
        Parsed record as a plain dict, or ``None`` if neither form exists.

    Warns
    -----
    BothFormsPresentWarning
        When both ``analyses/<name>.json`` and ``analyses/<name>.yaml``
        exist.  The JSON form is returned; the YAML form is surfaced as a
        warning.
    """
    json_p = distill_json_path(analysis_dir, name)
    yaml_p = distill_yaml_path(analysis_dir, name)

    json_exists = json_p.exists()
    yaml_exists = yaml_p.exists()

    if json_exists and yaml_exists:
        warnings.warn(BothFormsPresentWarning(json_p, yaml_p), stacklevel=2)

    if json_exists:
        with open(json_p, encoding="utf-8") as fh:
            record = json.load(fh)
    elif yaml_exists:
        with open(yaml_p, encoding="utf-8") as fh:
            record = yaml.safe_load(fh) or {}
    else:
        return None

    return _normalize_distill_record(record)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

ArtifactFormat = Literal["json", "yaml"]


def write_bib(
    analysis_dir: Path,
    data: Any,
    *,
    fmt: ArtifactFormat,
    yaml_header: str = "",
) -> Path:
    """Atomically write the bib record in the requested format.

    Parameters
    ----------
    analysis_dir:
        The ``<stem>.puba/`` directory for the paper.
    data:
        The record to serialise (must be JSON-serialisable).
    fmt:
        ``"json"`` to write ``bib.json``; ``"yaml"`` to write ``bib.yaml``.
    yaml_header:
        Optional comment header prepended to the YAML body (only used when
        ``fmt="yaml"``).

    Returns
    -------
    Path
        The path that was written.
    """
    if fmt == "json":
        path = bib_json_path(analysis_dir)
        atomic_write_json(path, data)
    else:
        path = bib_yaml_path(analysis_dir)
        body = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        atomic_write_text(path, yaml_header + body)
    return path


def write_distill(
    analysis_dir: Path,
    name: str,
    data: Any,
    *,
    fmt: ArtifactFormat,
    yaml_header: str = "",
) -> Path:
    """Atomically write a distillation record in the requested format.

    Parameters
    ----------
    analysis_dir:
        The ``<stem>.puba/`` directory for the paper.
    name:
        The distillation query name (filename stem, e.g. ``"summary"``).
    data:
        The record to serialise (must be JSON-serialisable).
    fmt:
        ``"json"`` to write ``analyses/<name>.json``; ``"yaml"`` to write
        ``analyses/<name>.yaml``.
    yaml_header:
        Optional comment header prepended to the YAML body (only used when
        ``fmt="yaml"``).

    Returns
    -------
    Path
        The path that was written.
    """
    if fmt == "json":
        path = distill_json_path(analysis_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data)
    else:
        path = distill_yaml_path(analysis_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        atomic_write_text(path, yaml_header + body)
    return path


# ---------------------------------------------------------------------------
# Listing helpers
# ---------------------------------------------------------------------------


def list_distill_names(analysis_dir: Path) -> list[str]:
    """Return deduplicated distillation names found on disk (JSON preferred).

    Looks for both ``analyses/*.json`` and ``analyses/*.yaml`` files and
    returns a sorted, deduplicated list of query names (stems).  A name
    present in both formats is listed once.
    """
    analyses_dir = analysis_dir / "analyses"
    if not analyses_dir.exists():
        return []

    names: set[str] = set()
    for p in analyses_dir.iterdir():
        if p.suffix in (".json", ".yaml") and not p.name.startswith("."):
            names.add(p.stem)
    return sorted(names)
