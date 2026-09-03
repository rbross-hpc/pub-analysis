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
from pathlib import Path
from typing import Any, Literal

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


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def read_bib(analysis_dir: Path) -> dict[str, Any] | None:
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
            return json.load(fh)

    if yaml_exists:
        with open(yaml_p, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    return None


def read_distill(analysis_dir: Path, name: str) -> dict[str, Any] | None:
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
            return json.load(fh)

    if yaml_exists:
        with open(yaml_p, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    return None


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
