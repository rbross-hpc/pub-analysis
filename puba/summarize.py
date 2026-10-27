# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Deterministic rendering for the human-facing publication summary vault."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
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
from .figures.extract import resolve_jpg
from .io import atomic_write_text, now_iso, replace_directory
from .state import analysis_dir, stage_status

_PROMPT_NOT_RECORDED = "not recorded (generated before prompt persistence was added)"
_ATTENTION_STATUSES = {"never-run", "stale", "invalid", "mixed"}
_DEFAULT_BIB_PROMPT_VERSION = "bib-1"
_DEFAULT_BIB_MODEL = "openai/gpt-5.6-sol"
_DEFAULT_LLM_IDENTITY = {"backend": "opencode", "llm_protocol_version": "oneshot-v1"}
_DEFAULT_MD_PROMPT_VERSION = "mineru-1"
_DEFAULT_FIGURES_PROMPT_VERSION = "figures-3"
_RESERVED_NOTE_NAMES = {"readme", "bibliography", "figures", "assets", "notes", "distillations"}


def _status_config() -> tuple[str, str, dict[str, str], str, str, dict[str, str]]:
    """Return status-key settings even when project-local config is malformed."""
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


def _frontmatter(data: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False,
    ).strip() + "\n---\n"


def _json_safe(value: Any) -> Any:
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
    return json.dumps(_json_safe(value), indent=indent, sort_keys=True, ensure_ascii=False)


def _aggregate_distill_status(records: list[dict[str, Any]]) -> str:
    statuses = {record["status"] for record in records}
    if not statuses or statuses == {"never-run"}:
        return "never-run"
    if len(statuses) > 1:
        return "mixed"
    return next(iter(statuses))


def _both_forms(ad: Path) -> list[str]:
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


def _display_name(name: str) -> str:
    words = re.sub(r"[-_]+", " ", name).strip()
    return words.title() or "Distillation"


def _markdown_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def _note_names(names: list[str]) -> dict[str, str]:
    """Return deterministic, case-insensitively unique filenames."""
    result: dict[str, str] = {}
    used = set(_RESERVED_NOTE_NAMES)
    for name in sorted(names):
        base = _slug(name, fallback="distillation")
        candidate = base
        if candidate in used:
            candidate = f"{base}-{hashlib.sha256(name.encode()).hexdigest()[:8]}"
        counter = 2
        while candidate in used:
            candidate = f"{base}-{counter}"
            counter += 1
        used.add(candidate)
        result[name] = f"{candidate}.md"
    return result


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _collect_summary(pdf: Path) -> dict[str, Any]:
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
            ad, pdf, "bib", bib_prompt_version, model=bib_model, extra_key=llm_cache_identity,
        )
        md_status = stage_status(ad, pdf, "md", md_prompt_version, extra_key=md_cache_identity)
        figures_status = stage_status(
            ad, pdf, "figures", figures_prompt_version,
            extra_key={"types": ["chart", "image", "table"]},
        )
        try:
            queries = load_queries()
            distillations = list_distillations(pdf, queries)
        except Exception:
            queries = {}
            distillations = list_distillations(pdf, {})

        status_by_name = {item["name"]: item["status"] for item in distillations}
        stored_names = set(list_distill_names(ad))
        records: list[dict[str, Any]] = []
        for name in sorted(status_by_name, key=lambda value: (value != "summary", value)):
            value = None
            if name in stored_names:
                try:
                    value = read_distill(ad, name)
                except Exception:
                    pass
            records.append({
                "name": name,
                "record": value if is_distill_record(value) else None,
                "status": status_by_name.get(name, "stale"),
                "artifact_exists": name in stored_names,
                "query": queries.get(name),
            })

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

    statuses = {
        "bib": bib_status,
        "md": md_status,
        "figures": figures_status,
        "distill": _aggregate_distill_status(distillations),
    }
    attention = [
        f"{stage} stage is {status}."
        for stage, status in statuses.items()
        if status in _ATTENTION_STATUSES
    ]
    if statuses["distill"] == "mixed":
        attention.extend(
            f"Distillation '{item['name']}' is {item['status']}."
            for item in distillations
            if item["status"] != "current"
        )
    attention.extend(_both_forms(ad))
    for warning in caught:
        if isinstance(warning.message, BothFormsPresentWarning):
            attention.append(str(warning.message))
    if bib and bib.get("needs_review"):
        attention.append("Bibliographic record has needs_review: true.")
        for reason in bib.get("_review_reasons") or []:
            attention.append(f"Bibliographic review reason: {reason}")

    for item in records:
        record = item["record"]
        if record is None:
            continue
        prompt = record.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            attention.append(f"Distillation '{item['name']}' is missing persisted prompt text.")
        quote_status = record.get("quote_presence_status")
        if quote_status not in (None, "complete"):
            attention.append(
                f"Distillation '{item['name']}' has quote_presence_status: {quote_status}."
            )
        claim_status = record.get("claim_support_status")
        if claim_status not in (None, "supported", "not_applicable"):
            attention.append(
                f"Distillation '{item['name']}' has model-assessed "
                f"claim_support_status: {claim_status}."
            )

    return {
        "pdf": pdf,
        "analysis_dir": ad,
        "generated_at": now_iso(),
        "bib": bib,
        "bib_exists": bib_json_path(ad).exists() or bib_yaml_path(ad).exists(),
        "figures": figures,
        "figures_invalid": figures_invalid,
        "records": records,
        "note_names": _note_names([item["name"] for item in records]),
        "statuses": statuses,
        "attention": _dedupe(attention),
    }


def _author_names(bib: dict[str, Any] | None) -> list[str]:
    if not bib:
        return []
    authors = bib.get("authors")
    if not isinstance(authors, list):
        return [str(authors)] if authors else []
    return [
        str(author.get("name") or "Unknown author") if isinstance(author, dict) else str(author)
        for author in authors
    ]


def _flow_text(value: Any) -> str:
    """Render stored prompt/answer text as one readable paragraph."""
    return " ".join(str(value).split())


def _prompt_for(item: dict[str, Any]) -> str:
    record = item["record"]
    if record is not None:
        prompt = record.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return _flow_text(prompt)
        return _PROMPT_NOT_RECORDED
    query = item.get("query")
    if query is not None and query.prompt.strip():
        return _flow_text(query.prompt)
    return "Prompt not available."


def _scope_for(item: dict[str, Any]) -> tuple[str, str | None]:
    record = item["record"]
    query = item.get("query")
    scope = record.get("scope") if record is not None else None
    section = record.get("section") if record is not None else None
    if not scope and query is not None:
        scope = query.scope
        section = query.section
    return str(scope or "not recorded"), str(section) if section else None


def _render_status_table(statuses: dict[str, str]) -> list[str]:
    lines = ["| Stage | Status |", "| --- | --- |"]
    lines.extend(f"| {stage} | {status} |" for stage, status in statuses.items())
    return lines


def _render_attention(attention: list[str]) -> list[str]:
    if not attention:
        return ["> [!success] No items need attention."]
    lines = ["> [!warning] Missing / Needs Attention"]
    lines.extend(f"> - {item}" for item in attention)
    return lines


def _render_readme(data: dict[str, Any]) -> str:
    bib = data["bib"]
    title = bib.get("title") if bib else None
    heading = str(title or "Publication Analysis Summary")
    frontmatter = _frontmatter({
        "type": "puba-summary",
        "title": heading,
        "generated_at": data["generated_at"],
        "source_pdf": "assets/paper.pdf",
        "puba_version": __version__,
        "stages": data["statuses"],
    })
    lines = [frontmatter, f"# {heading}\n", "This generated vault is a point-in-time snapshot. "
             "Run `puba summarize` to refresh it.\n", "[Open the source PDF](assets/paper.pdf)\n"]
    authors = _author_names(bib)
    if bib:
        lines.extend(["## Publication\n"])
        if authors:
            lines.append(f"**Authors:** {', '.join(authors)}  ")
        venue_year = ", ".join(
            str(value) for value in (bib.get("venue"), bib.get("year")) if value
        )
        if venue_year:
            lines.append(f"**Published:** {venue_year}  ")
        if bib.get("doi"):
            doi = str(bib["doi"])
            lines.append(f"**DOI:** [{doi}](https://doi.org/{doi})  ")
    lines.extend([
        "\n## Processing Status\n",
        *_render_status_table(data["statuses"]),
        "\n## Missing / Needs Attention\n",
        *_render_attention(data["attention"]),
        "\n## Navigate\n",
        "- [Bibliography](bibliography.md)",
        "- [Figures](figures.md)",
    ])
    if data["records"]:
        lines.append("- Distillations:")
        for item in data["records"]:
            name = item["name"]
            lines.append(
                f"  - [{_markdown_link_text(_display_name(name))}]"
                f"(distillations/{data['note_names'][name]})"
            )

    lines.append("\n# Distillation Summaries\n")
    if not data["records"]:
        lines.append("No distillations exist on disk.")
    for item in data["records"]:
        name = item["name"]
        record = item["record"]
        lines.extend([
            f"## {_display_name(name)}\n",
            f"**Status:** {item['status']}  ",
        ])
        if record is None:
            message = (
                "This distillation has not been run."
                if not item["artifact_exists"] and item["status"] == "never-run"
                else "This distillation artifact is invalid and could not be rendered."
            )
            lines.append(f"{message}\n")
        else:
            claim_status = record.get("claim_support_status")
            if claim_status:
                lines.append(f"**Claim support (model-assessed):** {claim_status}  ")
        lines.extend(["\n### Prompt\n", _prompt_for(item)])
        if record is not None:
            lines.extend(["\n### Answer\n", _flow_text(record.get("output", ""))])
        lines.extend([
            "",
            f"[Open details and evidence](distillations/{data['note_names'][name]})\n",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _render_bibliography(data: dict[str, Any]) -> str:
    bib = data["bib"]
    frontmatter = _frontmatter({
        "type": "puba-bibliography-summary",
        "title": "Bibliography",
        "generated_at": data["generated_at"],
        "source_pdf": "assets/paper.pdf",
        "status": data["statuses"]["bib"],
    })
    lines = [frontmatter, "# Bibliography\n", "[Back to summary](README.md)\n"]
    if bib is None:
        lines.append(
            "A bibliographic record exists but is invalid and could not be rendered."
            if data["bib_exists"] else "No bibliographic record exists yet."
        )
        return "\n".join(lines).rstrip() + "\n"

    title = bib.get("title")
    if title:
        lines.extend([f"## {title}\n"])
    common = (
        ("Year", "year"), ("Publication date", "publication_date"),
        ("Venue", "venue"), ("Category", "category"), ("Language", "language"),
        ("DOI", "doi"), ("URL", "url"), ("Open-access status", "oa_status"),
        ("License", "license"),
    )
    for label, key in common:
        value = bib.get(key)
        if value not in (None, ""):
            if key == "doi":
                lines.append(f"- **{label}:** [{value}](https://doi.org/{value})")
            elif key == "url":
                lines.append(f"- **{label}:** [{value}]({value})")
            else:
                lines.append(f"- **{label}:** {value}")

    authors = bib.get("authors")
    lines.append("\n## Authors\n")
    if not authors:
        lines.append("No authors recorded.")
    elif isinstance(authors, list):
        for author in authors:
            if not isinstance(author, dict):
                lines.append(f"- {author}")
                continue
            lines.append(f"- **{author.get('name') or 'Unknown author'}**")
            affiliations = author.get("affiliations") or []
            if isinstance(affiliations, list):
                for affiliation in affiliations:
                    name = affiliation.get("name") if isinstance(affiliation, dict) else affiliation
                    if name:
                        lines.append(f"  - {name}")
            identifiers = []
            if author.get("orcid"):
                identifiers.append(f"[ORCID](https://orcid.org/{author['orcid']})")
            if author.get("openalex_author_id"):
                identifiers.append(f"[OpenAlex]({author['openalex_author_id']})")
            if identifiers:
                lines.append(f"  - {' | '.join(identifiers)}")
    else:
        lines.append(str(authors))

    abstract = bib.get("abstract")
    if abstract:
        lines.extend(["\n## Abstract\n", str(abstract)])
    keywords = bib.get("keywords")
    if keywords:
        lines.extend(["\n## Keywords\n"])
        values = keywords if isinstance(keywords, list) else str(keywords).split(";")
        lines.extend(f"- {str(value).strip()}" for value in values if str(value).strip())

    handled = {"title", "authors", "abstract", "keywords", *(key for _, key in common)}
    remaining = {
        key: value for key, value in bib.items()
        if not key.startswith("_") and key not in handled and value not in (None, "", [])
    }
    if remaining:
        lines.extend([
            "\n## Additional Metadata\n",
            "```json",
            _safe_json(remaining, indent=2),
            "```",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _render_figures(data: dict[str, Any]) -> str:
    frontmatter = _frontmatter({
        "type": "puba-figures-summary",
        "title": "Figures",
        "generated_at": data["generated_at"],
        "source_pdf": "assets/paper.pdf",
        "status": data["statuses"]["figures"],
    })
    lines = [frontmatter, "# Figures\n", "[Back to summary](README.md)\n"]
    if data["figures_invalid"]:
        lines.append("The figures manifest exists but is invalid and could not be rendered.")
    elif not data["figures"]:
        lines.append("No figures have been extracted.")
    else:
        lines.append(f"{len(data['figures'])} figure(s) extracted.\n")
        for index, figure in enumerate(data["figures"], start=1):
            if not isinstance(figure, dict):
                lines.extend([f"## Figure {index}\n", "Invalid manifest entry."])
                continue
            label = figure.get("id") or f"Figure {index}"
            lines.extend([f"## {label}\n"])
            if figure.get("_vault_asset"):
                lines.append(f"![{label}](assets/figures/{figure['_vault_asset']})\n")
            elif figure.get("_asset_error"):
                lines.append(f"> [!warning] {figure['_asset_error']}\n")
            lines.append(str(figure.get("caption") or "_(No caption recorded.)_"))
            details = []
            for key, label_name in (("page", "Page"), ("type", "Type"),
                                    ("width_pt", "Width (pt)"), ("height_pt", "Height (pt)")):
                if figure.get(key) is not None:
                    details.append(f"**{label_name}:** {figure[key]}")
            if details:
                lines.append("  \n".join(details))
            if figure.get("footnote"):
                lines.append(f"\n**Footnote:** {figure['footnote']}")
    return "\n".join(lines).rstrip() + "\n"


def _render_evidence_summary(lines: list[str], record: dict[str, Any]) -> None:
    if record.get("schema_version") == 1 and "evidence_status" in record:
        lines.extend([
            "## Evidence Assessment\n",
            f"- **Evidence status:** {record.get('evidence_status')}",
            f"- **Quote presence:** {record.get('quote_presence_status') or 'not recorded'}",
        ])
        return
    mode = record.get("evidence_mode")
    if mode not in {"claims", "quotes"}:
        return
    lines.extend([
        "## Evidence Assessment\n",
        f"- **Evidence mode:** {mode}",
        f"- **Quote presence:** {record.get('quote_presence_status') or 'not recorded'}",
    ])
    if mode == "claims":
        lines.extend([
            f"- **Claim support (model-assessed):** "
            f"{record.get('claim_support_status') or 'not recorded'}",
            f"- **Verifier model:** {record.get('verifier_model') or 'not applicable'}",
        ])
    if record.get("quote_presence_counts") is not None:
        lines.append(f"- **Quote presence counts:** {_safe_json(record['quote_presence_counts'])}")
    if record.get("claim_support_counts") is not None:
        lines.append(f"- **Claim support counts:** {_safe_json(record['claim_support_counts'])}")


def _render_claims(lines: list[str], record: dict[str, Any]) -> None:
    claims = record.get("claims") or []
    lines.append("\n## Claims and Evidence\n")
    if not claims:
        lines.append("No factual claims were identified.")
        return
    for index, claim in enumerate(claims, start=1):
        claim_id = claim.get("id") or f"claim-{index}"
        assessment = claim.get("support_assessment") or {}
        linked_ids = set(assessment.get("evidence_ids") or [])
        status = assessment.get("status", "not recorded")
        lines.extend([
            f"### Claim {claim_id}: {status}\n",
            str(claim.get("claim", "")),
            "",
            f"**Rationale:** {assessment.get('rationale') or 'not recorded'}",
        ])
        evidence = claim.get("evidence") or []
        if not evidence:
            lines.append("\n> [!warning] No evidence recorded.")
        for evidence_index, item in enumerate(evidence, start=1):
            evidence_id = item.get("id") or f"{claim_id}-evidence-{evidence_index}"
            linkage = "linked" if evidence_id in linked_ids else "not linked by assessment"
            lines.extend([
                f"\n#### Evidence {evidence_id}\n",
                f"> {str(item.get('quote', '')).replace(chr(10), chr(10) + '> ')}",
                "",
                f"- **Assessment linkage:** {linkage}",
                f"- **Quote presence:** {item.get('quote_status', 'not recorded')}",
                f"- **Source location:** {_source_location(item)}",
            ])


def _render_distillation(data: dict[str, Any], item: dict[str, Any]) -> str:
    name = item["name"]
    record = item["record"]
    title = _display_name(name)
    scope, section = _scope_for(item)
    fm: dict[str, Any] = {
        "type": "puba-distillation-summary",
        "title": title,
        "analysis_name": name,
        "status": item["status"],
        "generated_at": data["generated_at"],
        "source_pdf": "../assets/paper.pdf",
        "scope": scope,
    }
    if record:
        fm["model"] = record.get("model") or "not recorded"
        if record.get("evidence_mode"):
            fm["evidence_mode"] = record["evidence_mode"]
        if record.get("claim_support_status"):
            fm["claim_support_status"] = record["claim_support_status"]
    lines = [
        _frontmatter(fm),
        f"# {title}\n",
        "[Back to summary](../README.md)\n",
        f"**Status:** {item['status']}  ",
    ]
    if record is not None:
        lines.append(f"**Model:** {record.get('model', 'not recorded')}  ")
    elif item.get("query") is not None:
        lines.append(f"**Model:** {item['query'].model or 'configured default'}  ")
    processed = scope if section is None else f"{scope} ({section})"
    lines.append(f"**Text processed:** {processed}  ")
    lines.extend(["\n## Prompt\n", _prompt_for(item), ""])
    if record is None:
        lines.append(
            "This distillation has not been run."
            if not item["artifact_exists"] and item["status"] == "never-run"
            else "This distillation artifact is invalid and could not be rendered."
        )
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(["## Answer\n", _flow_text(record.get("output", "")), ""])
    _render_evidence_summary(lines, record)
    if record.get("evidence_mode") == "claims":
        _render_claims(lines, record)
    elif record.get("evidence_mode") == "quotes" or (
        record.get("schema_version") == 1 and "evidence_status" in record
    ):
        lines.extend([
            "\n## Evidence\n",
            "```json",
            _safe_json(record.get("evidence", []), indent=2),
            "```",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _copy_owned_path(source: Path, destination: Path) -> None:
    if source.is_symlink():
        shutil.copy2(source, destination, follow_symlinks=False)
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    elif source.exists():
        shutil.copy2(source, destination)


def _prepare_assets(data: dict[str, Any], staged: Path) -> None:
    assets = staged / "assets"
    figure_assets = assets / "figures"
    figure_assets.mkdir(parents=True)
    shutil.copy2(data["pdf"], assets / "paper.pdf")

    used: set[str] = set()
    for index, figure in enumerate(data["figures"], start=1):
        if not isinstance(figure, dict) or not isinstance(figure.get("jpg"), str):
            if isinstance(figure, dict):
                figure["_asset_error"] = "No image path was recorded for this figure."
                data["attention"].append(
                    f"Figure '{figure.get('id') or index}' has no recorded image path."
                )
            continue
        source = resolve_jpg(figure, data["analysis_dir"])
        try:
            source.relative_to(data["analysis_dir"].resolve())
        except ValueError:
            figure["_asset_error"] = f"Image path is outside the analysis directory: {figure['jpg']}"
            data["attention"].append(
                f"Figure '{figure.get('id') or index}' image path is outside the analysis directory."
            )
            continue
        if not source.is_file():
            figure["_asset_error"] = f"Image file is missing: {figure['jpg']}"
            data["attention"].append(
                f"Figure '{figure.get('id') or index}' image is missing: {figure['jpg']}."
            )
            continue
        suffix = source.suffix.lower() or ".jpg"
        base = _slug(str(figure.get("id") or source.stem), fallback=f"figure-{index}")
        filename = f"{base}{suffix}"
        if filename.lower() in used:
            digest = hashlib.sha256(str(figure.get("jpg")).encode()).hexdigest()[:8]
            filename = f"{base}-{digest}{suffix}"
        used.add(filename.lower())
        shutil.copy2(source, figure_assets / filename)
        figure["_vault_asset"] = filename
    data["attention"] = _dedupe(data["attention"])


def _is_generated_legacy_summary(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if not text.startswith("---\n"):
        return False
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return False
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return False
    return isinstance(metadata, dict) and (
        metadata.get("type") == "puba-summary"
        or metadata.get("artifact_type") == "puba-summary"
    )


def write_summary(pdf: Path) -> Path:
    """Atomically write ``<pdf-stem>.vault/`` and return its README path."""
    data = _collect_summary(pdf)
    target = pdf.parent / f"{pdf.stem}.vault"
    legacy = pdf.parent / f"{pdf.stem}.summary.md"

    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise OSError(f"summary vault target exists but is not a directory: {target}")

    with tempfile.TemporaryDirectory(prefix=f".{pdf.stem}.vault-stage-", dir=pdf.parent) as tmp:
        staged = Path(tmp) / target.name
        (staged / "distillations").mkdir(parents=True)
        if target.is_dir():
            for owned in (".obsidian", "notes"):
                _copy_owned_path(target / owned, staged / owned)
        _prepare_assets(data, staged)
        atomic_write_text(staged / "README.md", _render_readme(data))
        atomic_write_text(staged / "bibliography.md", _render_bibliography(data))
        atomic_write_text(staged / "figures.md", _render_figures(data))
        for item in data["records"]:
            atomic_write_text(
                staged / "distillations" / data["note_names"][item["name"]],
                _render_distillation(data, item),
            )
        replace_directory(staged, target)

    if legacy.exists():
        if _is_generated_legacy_summary(legacy):
            legacy.unlink()
        else:
            warnings.warn(
                f"Existing {legacy.name} was not recognized as a generated puba summary; "
                "it was left untouched.",
                UserWarning,
                stacklevel=2,
            )
    return target / "README.md"
