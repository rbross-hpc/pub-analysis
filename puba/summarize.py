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
# These are the shipped configuration values.  A summary is an inspection
# artifact, so a malformed project-local override must not prevent it from
# reporting the persisted artifacts that are still readable.
_DEFAULT_BIB_PROMPT_VERSION = "bib-1"
_DEFAULT_BIB_MODEL = "openai/gpt-5.6-sol"
_DEFAULT_LLM_IDENTITY = {"backend": "opencode", "llm_protocol_version": "oneshot-v1"}
_DEFAULT_MD_PROMPT_VERSION = "mineru-1"
_DEFAULT_FIGURES_PROMPT_VERSION = "figures-3"


def _status_config() -> tuple[str, str, dict[str, str], str, str, dict[str, str]]:
    """Return status-key settings without making summary depend on valid config.

    The values only parameterize the shared currency helpers.  Falling back to
    the packaged defaults is deliberately conservative: an existing artifact
    cannot be called current unless its persisted state matches those defaults.
    """
    try:
        return (
            cfg.prompt_versions().get("bib_extract", _DEFAULT_BIB_PROMPT_VERSION),
            cfg.models().get("bib_extract", _DEFAULT_BIB_MODEL),
            cfg.llm_cache_identity(),
            cfg.md().get("mineru_version", _DEFAULT_MD_PROMPT_VERSION),
            cfg.figures().get("figures_version", _DEFAULT_FIGURES_PROMPT_VERSION),
            cfg.md_cache_identity(),
        )
    except Exception:
        return (
            _DEFAULT_BIB_PROMPT_VERSION,
            _DEFAULT_BIB_MODEL,
            _DEFAULT_LLM_IDENTITY,
            _DEFAULT_MD_PROMPT_VERSION,
            _DEFAULT_FIGURES_PROMPT_VERSION,
            {"extractor": "service", "service_url": "http://host-localhost:8001"},
        )


def _yaml_frontmatter(pdf: Path, statuses: dict[str, str]) -> str:
    data = {
        "type": "puba-summary",
        "generated_at": now_iso(),
        "source_pdf": str(pdf),
        "puba_version": __version__,
        "stages": statuses,
    }
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip() + "\n---\n"


def _json_safe(value: Any) -> Any:
    """Convert YAML-native values into deterministic JSON-renderable data.

    Legacy YAML may contain values such as ``datetime.date`` that JSON does not
    support.  A summary must remain available for those parseable artifacts, so
    preserve their readable representation rather than letting formatting fail.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_safe(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    return str(value)


def _safe_json(value: Any, *, indent: int | None = None) -> str:
    """Render arbitrary parsed artifact data without JSON serialization errors."""
    return json.dumps(_json_safe(value), indent=indent, sort_keys=True, ensure_ascii=False)


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(_format_value(item) for item in value)
    if isinstance(value, dict):
        return _safe_json(value)
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


def _source_location(evidence: dict[str, Any]) -> str:
    parts = []
    if evidence.get("section") is not None:
        parts.append(f"section {evidence['section']}")
    if evidence.get("page") is not None:
        parts.append(f"page {evidence['page']}")
    if evidence.get("offset") is not None:
        parts.append(f"offset {evidence['offset']}")
    return ", ".join(parts) if parts else "not available"


def _render_claims(
    lines: list[str], name: str, record: dict[str, Any], attention: list[str],
) -> None:
    """Render schema-v2 claims without interpreting model assessments as verification."""
    quote_status = record.get("quote_presence_status")
    claim_status = record.get("claim_support_status")
    lines.extend([
        "\n#### Evidence Assessment\n",
        f"- **Evidence mode:** {record.get('evidence_mode', 'not recorded')}",
        f"- **Quote presence:** {quote_status or 'not recorded'}",
        f"- **Claim support (model-assessed):** {claim_status or 'not recorded'}",
        f"- **Verifier model:** {record.get('verifier_model') or 'not applicable'}",
    ])
    if record.get("quote_presence_counts") is not None:
        lines.append(f"- **Quote presence counts:** {_safe_json(record['quote_presence_counts'])}")
    if record.get("claim_support_counts") is not None:
        lines.append(f"- **Claim support counts:** {_safe_json(record['claim_support_counts'])}")

    if quote_status not in (None, "complete"):
        attention.append(f"Distillation '{name}' has quote_presence_status: {quote_status}.")
    if claim_status not in (None, "supported", "not_applicable"):
        attention.append(
            f"Distillation '{name}' has model-assessed claim_support_status: {claim_status}."
        )

    claims = record.get("claims") or []
    lines.append("\n#### Claims\n")
    if not claims:
        lines.append("No factual claims were identified.")
        return
    for index, claim in enumerate(claims, start=1):
        claim_id = claim.get("id") or f"claim-{index}"
        assessment = claim.get("support_assessment") or {}
        linked_ids = set(assessment.get("evidence_ids") or [])
        lines.extend([
            f"##### Claim {claim_id}\n",
            str(claim.get("claim", "")),
            f"- **Support label (model-assessed):** {assessment.get('status', 'not recorded')}",
            f"- **Rationale:** {assessment.get('rationale') or 'not recorded'}",
        ])
        evidence = claim.get("evidence") or []
        if not evidence:
            lines.append("- **Evidence:** none")
        for evidence_index, item in enumerate(evidence, start=1):
            evidence_id = item.get("id") or f"{claim_id}-evidence-{evidence_index}"
            linkage = "linked" if evidence_id in linked_ids else "not linked by assessment"
            lines.extend([
                f"- **Evidence {evidence_id} ({linkage}):** {item.get('quote', '')}",
                f"- **Quote presence:** {item.get('quote_status', 'not recorded')}",
                f"- **Source location:** {_source_location(item)}",
            ])


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

        (
            bib_prompt_version,
            bib_model,
            llm_cache_identity,
            md_prompt_version,
            figures_prompt_version,
            md_cache_identity,
        ) = _status_config()
        bib_status = stage_status(
            ad, pdf, "bib", bib_prompt_version, model=bib_model,
            extra_key=llm_cache_identity,
        )
        md_status = stage_status(
            ad, pdf, "md", md_prompt_version, extra_key=md_cache_identity
        )
        figures_status = stage_status(
            ad, pdf, "figures", figures_prompt_version,
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
    bib_exists = bib_json_path(ad).exists() or bib_yaml_path(ad).exists()
    if bib is None:
        lines.append(
            "Bibliographic record exists but is invalid and could not be rendered."
            if bib_exists else "No bibliographic record exists yet."
        )
    else:
        public_fields = [(key, value) for key, value in bib.items() if not key.startswith("_")]
        if public_fields:
            lines.extend(f"- **{key}:** {_format_value(value)}" for key, value in public_fields)
        else:
            lines.append("Bibliographic record is empty or invalid.")

    lines.extend(["\n## Figures\n"])
    figures_path = ad / "paper.figures.json"
    figures: list[Any] = []
    figures_invalid = False
    if figures_path.exists():
        try:
            manifest = json.loads(figures_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict) and isinstance(manifest.get("figures"), list):
                figures = manifest["figures"]
            else:
                figures_invalid = True
        except Exception:
            figures_invalid = True
    if figures_invalid:
        lines.append("Figures manifest exists but is invalid and could not be rendered.")
    elif not figures:
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
        if not isinstance(prompt, str) or not prompt.strip():
            prompt = _PROMPT_NOT_RECORDED
            attention.append(f"Distillation '{name}' is missing persisted prompt text.")
        lines.extend([f"- **Status:** {next((d['status'] for d in distillations if d['name'] == name), 'stale')}",
                      f"- **Model:** {record.get('model', 'not recorded')}",
                      "\n#### Prompt\n", prompt, "\n#### Answer\n", record["output"]])
        if record.get("schema_version") == 1 and "evidence_status" in record:
            # Retain familiar labels for normalized schema-v1 artifacts while
            # also exposing the local quote-presence terminology.
            evidence_status = record.get("evidence_status")
            quote_status = record.get("quote_presence_status")
            lines.extend([
                "\n#### Evidence\n",
                f"Evidence status: **{evidence_status}**.",
                f"Quote presence: **{quote_status or 'not recorded'}**.",
                "```json", _safe_json(record.get("evidence", []), indent=2), "```",
            ])
            if quote_status not in (None, "complete"):
                attention.append(f"Distillation '{name}' has evidence_status: {evidence_status}.")
        elif record.get("evidence_mode") == "claims":
            _render_claims(lines, name, record, attention)
        elif record.get("evidence_mode") == "quotes":
            quote_status = record.get("quote_presence_status")
            lines.extend([
                "\n#### Evidence\n",
                f"- **Evidence mode:** quotes",
                f"- **Quote presence:** {quote_status or 'not recorded'}",
            ])
            if record.get("quote_presence_counts") is not None:
                lines.append(f"- **Quote presence counts:** {_safe_json(record['quote_presence_counts'])}")
            lines.extend(["```json", _safe_json(record.get("evidence", []), indent=2), "```"])
            if quote_status not in (None, "complete"):
                attention.append(f"Distillation '{name}' has quote_presence_status: {quote_status}.")

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
