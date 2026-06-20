# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Per-paper .state.json management — sha256, stage timestamps, prompt versions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import __version__
from .io import atomic_write_json, now_iso, sha256_file


def state_path(analysis_dir: Path) -> Path:
    return analysis_dir / ".state.json"


def load_state(analysis_dir: Path) -> dict[str, Any]:
    p = state_path(analysis_dir)
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(analysis_dir: Path, state: dict[str, Any]) -> None:
    atomic_write_json(state_path(analysis_dir), state)


def is_stage_current(
    analysis_dir: Path,
    pdf_path: Path,
    stage: str,
    prompt_version: str,
    model: str | None = None,
    extra_key: dict[str, Any] | None = None,
) -> bool:
    """Return True if the stage output is current and can be reused.

    extra_key: optional dict of additional cache-key components.  Each
    (k, v) pair must match the corresponding field written by
    mark_stage_complete(..., extra={...}) for the stage to be considered
    current.  Use this for stage-specific parameters (e.g. figure types)
    that should invalidate the cache when changed.
    """
    state = load_state(analysis_dir)
    pdf_sha = sha256_file(pdf_path)

    if state.get("pdf_sha256") != pdf_sha:
        return False

    stage_state = state.get("stages", {}).get(stage, {})
    if not stage_state.get("completed_at"):
        return False
    if stage_state.get("prompt_version") != prompt_version:
        return False
    if stage_state.get("input_sha") != pdf_sha:
        return False
    if model is not None and stage_state.get("model") != model:
        return False
    if extra_key is not None:
        for k, v in extra_key.items():
            if stage_state.get(k) != v:
                return False
    return True


def mark_stage_complete(
    analysis_dir: Path,
    pdf_path: Path,
    stage: str,
    prompt_version: str,
    model: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    state = load_state(analysis_dir)
    pdf_sha = sha256_file(pdf_path)
    state["pdf_sha256"] = pdf_sha
    state["tool_version"] = __version__

    stages = state.setdefault("stages", {})
    entry: dict[str, Any] = {
        "completed_at": now_iso(),
        "prompt_version": prompt_version,
        "tool_version": __version__,
        "input_sha": pdf_sha,
    }
    if model is not None:
        entry["model"] = model
    if extra:
        entry.update(extra)
    stages[stage] = entry

    save_state(analysis_dir, state)


def is_distill_current(
    analysis_dir: Path,
    pdf_path: Path,
    query_name: str,
    input_sha: str,
    prompt_sha: str,
    model: str,
) -> bool:
    """Return True if the distillation for query_name is cached and up-to-date."""
    output_yaml = analysis_dir / "analyses" / f"{query_name}.yaml"
    if not output_yaml.exists():
        return False
    state = load_state(analysis_dir)
    pdf_sha = sha256_file(pdf_path)
    if state.get("pdf_sha256") != pdf_sha:
        return False
    entry = state.get("stages", {}).get("distill", {}).get(query_name, {})
    return (
        entry.get("completed_at")
        and entry.get("input_sha") == input_sha
        and entry.get("prompt_sha") == prompt_sha
        and entry.get("model") == model
    )


def mark_distill_complete(
    analysis_dir: Path,
    pdf_path: Path,
    query_name: str,
    input_sha: str,
    prompt_sha: str,
    model: str,
) -> None:
    state = load_state(analysis_dir)
    pdf_sha = sha256_file(pdf_path)
    state["pdf_sha256"] = pdf_sha
    state["tool_version"] = __version__

    distill_stages = state.setdefault("stages", {}).setdefault("distill", {})
    distill_stages[query_name] = {
        "completed_at": now_iso(),
        "input_sha": input_sha,
        "prompt_sha": prompt_sha,
        "model": model,
        "tool_version": __version__,
    }
    save_state(analysis_dir, state)


def invalidate_stage(analysis_dir: Path, stage: str) -> None:
    """Remove a single stage's cache entry from .state.json.

    A no-op when .state.json is absent, corrupt, or does not contain the stage.
    """
    state = load_state(analysis_dir)
    stages = state.get("stages", {})
    if stage not in stages:
        return
    del stages[stage]
    state["stages"] = stages
    save_state(analysis_dir, state)


def analysis_dir(pdf_path: Path) -> Path:
    return pdf_path.parent / f"{pdf_path.stem}.puba"


def ensure_analysis_dir(pdf_path: Path) -> Path:
    d = analysis_dir(pdf_path)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"Cannot create analysis directory {d}: {e}\n"
            "If this path is on a read-only filesystem, the PDF cannot be analyzed here."
        ) from e
    analyses = d / "analyses"
    analyses.mkdir(exist_ok=True)
    gitkeep = analyses / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("")
    return d
