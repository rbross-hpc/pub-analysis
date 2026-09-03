# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Deterministic rendering for the human-facing paper summary artifact."""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import yaml

from . import __version__, config as cfg
from .artifacts import (
    BothFormsPresentWarning,
    bib_json_path,
    bib_yaml_path,
    distill_json_path,
    distill_yaml_path,
    is_distill_record,
    list_distill_names,
    read_bib,
    read_distill,
)
from .distill.queries import load_queries
from .distill.run import list_distillations
from .io import atomic_write_text, now_iso
from .state import analysis_dir, stage_status

_PROMPT_NOT_RECORDED = "not recorded (generated before prompt persistence was added)"
_ATTENTION_STATUSES = {"never-run", "stale", "invalid"}


def _yaml_frontmatter(pdf: Path, statuses: dict[str, str]) -> str:
    data = {
        "artifact_type": "puba-summary",
        "generated_at": now_iso(),
        "source_pdf": str(pdf),
        "puba_version": __version__,
        "stages": statuses,
    }
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip() + "\n---\n"


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _aggregate_distill_status(records: list[dict[str, Any]]) -> str:
    statuses = {record["status"] for record in records}
    if not statuses or statuses == {"never-run"}:
        return "never-run"
    for status in ("invalid", "stale", "never-run"):
        if status in statuses:
            return status
    return "current"


def _both_forms(ad: Path) -> list[str]:
    """Return coexistence conditions while readers emit their compatibility warning."""
    conditions: list[str] = []
    if bib_json_path(ad).exists() and bib_yaml_path(ad).exists():
        conditions.append("Both bib.json and legacy bib.yaml are present (JSON is authoritative).")
    for name in list_distill_names(ad):
        if distill_json_path(ad, name).exists() and distill_yaml_path(ad, name).exists():
            conditions.append(
                f"Both analyses/{name}.json and legacy analyses/{name}.yaml are present "
                "(JSON is authoritative)."
            )
    return conditions


def render_summary(pdf: Path) -> str:
    """Render a point-in-time report from existing artifacts without mutating them."""
    ad = analysis_dir(pdf)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", BothFormsPresentWarning)
        try:
            raw_bib = read_bib(ad)
            bib = raw_bib if isinstance(raw_bib, dict) else None
        except Exception:
            bib = None

        bib_status = stage_status(
            ad, pdf, "bib", cfg.prompt_versions().get("bib_extract", "bib-1"),
            model=cfg.models().get("bib_extract", "GPT-5.4"),
        )
        md_status = stage_status(ad, pdf, "md", cfg.md().get("mineru_version", "mineru-1"))
        figures_status = stage_status(
            ad, pdf, "figures", cfg.figures().get("figures_version", "figures-2"),
            extra_key={"types": ["chart", "image", "table"]},
        )
        try:
            distillations = list_distillations(pdf, load_queries())
        except Exception:
            # A broken user configuration must not prevent inspection of already
            # persisted records; classify them through the same state helper.
            distillations = list_distillations(pdf, {})

        records: list[tuple[str, dict[str, Any] | None]] = []
        for name in list_distill_names(ad):
            try:
                record = read_distill(ad, name)
            except Exception:
                record = None
            records.append((name, record if is_distill_record(record) else None))

    statuses = {
        "bib": bib_status,
        "md": md_status,
        "figures": figures_status,
        "distill": _aggregate_distill_status(distillations),
    }
    attention = [f"{stage} stage is {status}." for stage, status in statuses.items()
                 if status in _ATTENTION_STATUSES]
    attention.extend(_both_forms(ad))
    # Keep warning-derived conditions in case a future compatibility reader adds
    # artifact forms not covered by the current filename scan.
    for warning in caught:
        if isinstance(warning.message, BothFormsPresentWarning):
            message = str(warning.message)
            if message not in attention:
                attention.append(message)

    if bib and bib.get("needs_review"):
        attention.append("Bibliographic record has needs_review: true.")
        for reason in bib.get("_review_reasons") or []:
            attention.append(f"Bibliographic review reason: {reason}")

    lines = [_yaml_frontmatter(pdf, statuses), "# Publication Analysis Summary\n",
             "This is a point-in-time snapshot. It is not auto-regenerated and can go stale.\n",
             "## Processing Status\n",
             "| Stage | Status |", "| --- | --- |"]
    lines.extend(f"| {stage} | {status} |" for stage, status in statuses.items())

    lines.extend(["\n## Bibliography\n"])
    if bib is None:
        lines.append("No bibliographic record exists yet.")
    else:
        public_fields = [(key, value) for key, value in bib.items() if not key.startswith("_")]
        if public_fields:
            lines.extend(f"- **{key}:** {_format_value(value)}" for key, value in public_fields)
        else:
            lines.append("Bibliographic record is empty or invalid.")

    lines.extend(["\n## Figures\n"])
    figures_path = ad / "paper.figures.json"
    figures: list[Any] = []
    try:
        manifest = json.loads(figures_path.read_text(encoding="utf-8"))
        if isinstance(manifest, dict) and isinstance(manifest.get("figures"), list):
            figures = manifest["figures"]
    except Exception:
        pass
    if not figures:
        lines.append("No figures have been extracted.")
    else:
        lines.append(f"{len(figures)} figure(s) extracted.")
        for index, figure in enumerate(figures, start=1):
            if not isinstance(figure, dict):
                lines.append(f"- Figure {index}: invalid manifest entry")
                continue
            label = figure.get("id") or f"Figure {index}"
            caption = figure.get("caption") or "(no caption)"
            lines.append(f"- **{label}:** {caption}")

    lines.extend(["\n## Distillations\n"])
    if not records:
        lines.append("No distillations exist on disk.")
    for name, record in records:
        lines.append(f"### {name}\n")
        if record is None:
            lines.append("This distillation artifact is invalid and could not be rendered.")
            continue
        prompt = record.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            prompt = _PROMPT_NOT_RECORDED
            attention.append(f"Distillation '{name}' is missing persisted prompt text.")
        lines.extend([f"- **Status:** {next((d['status'] for d in distillations if d['name'] == name), 'stale')}",
                      f"- **Model:** {record.get('model', 'not recorded')}",
                      "\n#### Prompt\n", prompt, "\n#### Answer\n", record["output"]])
        if "evidence_status" in record:
            evidence_status = record.get("evidence_status")
            lines.extend(["\n#### Evidence\n", f"Evidence status: **{evidence_status}**."])
            evidence = record.get("evidence", [])
            lines.extend(["```json", json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False), "```"])
            if evidence_status == "partial":
                attention.append(f"Distillation '{name}' has evidence_status: partial.")

    lines.extend(["\n## Missing / Needs Attention\n"])
    if attention:
        lines.extend(f"- {item}" for item in attention)
    else:
        lines.append("None.")
    return "\n".join(lines).rstrip() + "\n"


def write_summary(pdf: Path) -> Path:
    """Atomically write ``<pdf-stem>.summary.md`` beside *pdf*."""
    output = pdf.parent / f"{pdf.stem}.summary.md"
    atomic_write_text(output, render_summary(pdf))
    return output
