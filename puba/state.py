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
) -> bool:
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
    return True


def mark_stage_complete(
    analysis_dir: Path,
    pdf_path: Path,
    stage: str,
    prompt_version: str,
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
    if extra:
        entry.update(extra)
    stages[stage] = entry

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
