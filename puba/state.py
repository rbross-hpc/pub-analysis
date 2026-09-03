# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Per-paper state and shared artifact-currency status helpers."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from . import __version__
from .artifacts import is_distill_record, read_bib, read_distill
from .io import atomic_write_json, now_iso, sha256_file

CurrencyStatus = Literal["current", "stale", "never-run", "invalid"]


def state_path(analysis_dir: Path) -> Path:
    return analysis_dir / ".state.json"


def load_state(analysis_dir: Path) -> dict[str, Any]:
    """Load only the structurally usable portion of ``.state.json``.

    State is a cache hint, not an authoritative artifact.  A syntactically valid
    but malformed state file must therefore behave like no usable prior state,
    rather than making read-only status commands fail while traversing it.
    """
    p = state_path(analysis_dir)
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, UnicodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}

    state = dict(raw)
    raw_stages = raw.get("stages", {})
    if not isinstance(raw_stages, dict):
        state["stages"] = {}
        return state

    stages: dict[str, dict[str, Any]] = {}
    for stage, entry in raw_stages.items():
        if not isinstance(entry, dict):
            continue
        if stage == "distill":
            # Distillation state is one mapping per filename/configured query
            # name.  Ignore malformed members independently of good records.
            stages[stage] = {
                name: record for name, record in entry.items()
                if isinstance(name, str) and isinstance(record, dict)
            }
        else:
            stages[stage] = entry
    state["stages"] = stages
    return state


def save_state(analysis_dir: Path, state: dict[str, Any]) -> None:
    atomic_write_json(state_path(analysis_dir), state)


def _stage_entry(state: dict[str, Any], stage: str, query_name: str | None = None) -> dict[str, Any] | None:
    entry: Any = state.get("stages", {}).get(stage)
    if query_name is not None:
        entry = entry.get(query_name) if isinstance(entry, dict) else None
    return entry if isinstance(entry, dict) else None


def _parse_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_md(analysis_dir: Path) -> None:
    (analysis_dir / "paper.md").read_text(encoding="utf-8")
    sections = _parse_json(analysis_dir / "paper.sections.json")
    if not isinstance(sections, list):
        raise ValueError("sections sidecar is not a list")


def _parse_figures(analysis_dir: Path) -> None:
    manifest = _parse_json(analysis_dir / "paper.figures.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("figures"), list):
        raise ValueError("figures manifest must contain a figures list")


def _parse_bib(analysis_dir: Path) -> None:
    record = read_bib(analysis_dir)
    if not isinstance(record, dict):
        raise ValueError("bibliographic record is invalid")


def _parse_distill(analysis_dir: Path, name: str) -> None:
    record = read_distill(analysis_dir, name)
    if not is_distill_record(record):
        raise ValueError("distillation record must contain a string output")


def _status(
    entry: dict[str, Any] | None,
    artifact_exists: bool,
    parse_artifact: Callable[[], Any],
    cache_matches: Callable[[], bool],
) -> CurrencyStatus:
    """Apply the one project-wide four-state artifact-status vocabulary."""
    if artifact_exists:
        try:
            parse_artifact()
        except Exception:
            return "invalid"
    elif entry is not None:
        return "invalid"
    elif entry is None:
        return "never-run"

    return "current" if cache_matches() else "stale"


def stage_status(
    analysis_dir: Path,
    pdf_path: Path,
    stage: str,
    prompt_version: str,
    model: str | None = None,
    extra_key: dict[str, Any] | None = None,
) -> CurrencyStatus:
    """Return currency for bib, md, or figures using artifact and state checks."""
    state = load_state(analysis_dir)
    entry = _stage_entry(state, stage)
    if stage == "bib":
        from .artifacts import bib_record_path
        path = bib_record_path(analysis_dir)
        exists = path.exists()
        parser = lambda: _parse_bib(analysis_dir)
    elif stage == "md":
        # Markdown is a composite artifact.  Any one of its two required files
        # means a partially present output that must be classified invalid,
        # rather than looking indistinguishable from a stage never run.
        exists = (analysis_dir / "paper.md").exists() or (analysis_dir / "paper.sections.json").exists()
        parser = lambda: _parse_md(analysis_dir)
    elif stage == "figures":
        path = analysis_dir / "paper.figures.json"
        exists = path.exists()
        parser = lambda: _parse_figures(analysis_dir)
    else:
        raise ValueError(f"Unsupported stage status: {stage}")

    def matches() -> bool:
        pdf_sha = sha256_file(pdf_path)
        if state.get("pdf_sha256") != pdf_sha or not entry:
            return False
        if not entry.get("completed_at") or entry.get("prompt_version") != prompt_version:
            return False
        if entry.get("input_sha") != pdf_sha or (model is not None and entry.get("model") != model):
            return False
        return extra_key is None or all(entry.get(k) == v for k, v in extra_key.items())

    return _status(entry, exists, parser, matches)


def is_stage_current(
    analysis_dir: Path, pdf_path: Path, stage: str, prompt_version: str,
    model: str | None = None, extra_key: dict[str, Any] | None = None,
) -> bool:
    return stage_status(analysis_dir, pdf_path, stage, prompt_version, model, extra_key) == "current"


def is_bib_current(analysis_dir: Path, pdf_path: Path, prompt_version: str, model: str | None = None) -> bool:
    return stage_status(analysis_dir, pdf_path, "bib", prompt_version, model) == "current"


def is_md_current(analysis_dir: Path, pdf_path: Path, prompt_version: str) -> bool:
    return stage_status(analysis_dir, pdf_path, "md", prompt_version) == "current"


def is_figures_current(analysis_dir: Path, pdf_path: Path, prompt_version: str, extra_key: dict[str, Any] | None = None) -> bool:
    return stage_status(analysis_dir, pdf_path, "figures", prompt_version, extra_key=extra_key) == "current"


def distill_status(
    analysis_dir: Path, pdf_path: Path, query_name: str, input_sha: str,
    prompt_sha: str, model: str, effective_instruction_sha: str,
) -> CurrencyStatus:
    """Return currency for one distillation, including compatibility artifacts."""
    state = load_state(analysis_dir)
    entry = _stage_entry(state, "distill", query_name)
    from .artifacts import distill_record_path
    path = distill_record_path(analysis_dir, query_name)

    def matches() -> bool:
        return bool(entry and state.get("pdf_sha256") == sha256_file(pdf_path)
                    and entry.get("completed_at") and entry.get("input_sha") == input_sha
                    and entry.get("prompt_sha") == prompt_sha and entry.get("model") == model
                    and entry.get("effective_instruction_sha") == effective_instruction_sha)

    return _status(entry, path.exists(), lambda: _parse_distill(analysis_dir, query_name), matches)


def is_distill_current(analysis_dir: Path, pdf_path: Path, query_name: str, input_sha: str, prompt_sha: str, model: str, effective_instruction_sha: str = "") -> bool:
    return distill_status(analysis_dir, pdf_path, query_name, input_sha, prompt_sha, model, effective_instruction_sha) == "current"


def mark_stage_complete(analysis_dir: Path, pdf_path: Path, stage: str, prompt_version: str, model: str | None = None, extra: dict[str, Any] | None = None) -> None:
    state = load_state(analysis_dir)
    pdf_sha = sha256_file(pdf_path)
    state["pdf_sha256"] = pdf_sha
    state["tool_version"] = __version__
    entry: dict[str, Any] = {"completed_at": now_iso(), "prompt_version": prompt_version, "tool_version": __version__, "input_sha": pdf_sha}
    if model is not None:
        entry["model"] = model
    if extra:
        entry.update(extra)
    state.setdefault("stages", {})[stage] = entry
    save_state(analysis_dir, state)


def mark_distill_complete(analysis_dir: Path, pdf_path: Path, query_name: str, input_sha: str, prompt_sha: str, model: str, effective_instruction_sha: str = "") -> None:
    state = load_state(analysis_dir)
    state["pdf_sha256"] = sha256_file(pdf_path)
    state["tool_version"] = __version__
    state.setdefault("stages", {}).setdefault("distill", {})[query_name] = {
        "completed_at": now_iso(), "input_sha": input_sha, "prompt_sha": prompt_sha,
        "model": model, "effective_instruction_sha": effective_instruction_sha, "tool_version": __version__,
    }
    save_state(analysis_dir, state)


def invalidate_stage(analysis_dir: Path, stage: str) -> None:
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
        raise RuntimeError(f"Cannot create analysis directory {d}: {e}\nIf this path is on a read-only filesystem, the PDF cannot be analyzed here.") from e
    analyses = d / "analyses"
    analyses.mkdir(exist_ok=True)
    gitkeep = analyses / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("")
    return d
