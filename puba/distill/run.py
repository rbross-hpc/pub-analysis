# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Run a single distillation query: build prompt, call LLM, post-process, write output."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .. import __version__
from ..artifacts import (
    bib_record_path,
    distill_record_path,
    distill_json_path,
    distill_yaml_path,
    list_distill_names,
    read_distill,
    write_distill,
)
from .. import config as cfg
from ..io import now_iso, sha256_file, sha256_text
from ..llm import openai_client
from ..sidecar import load as load_bib
from ..state import (
    analysis_dir as get_analysis_dir,
    distill_status,
    is_distill_current,
    mark_distill_complete,
)
from .queries import DistillQuery
from .scope import build_input, check_token_budget

_MAX_CHARS_INSTRUCTION = "Your response MUST be at most {n} characters. Be concise."
# Versioned independently so a future format/system-instruction change invalidates cache.
INSTRUCTION_VERSION = "distill-v1"
EVIDENCE_RESPONSE_SCHEMA_VERSION = 1


def effective_instruction_payload(query: DistillQuery) -> dict[str, Any]:
    """Stable request-affecting instruction material, extensible for evidence."""
    return {
        "evidence": {"requested": False, "response_schema_version": EVIDENCE_RESPONSE_SCHEMA_VERSION},
        "instruction_version": INSTRUCTION_VERSION,
        "max_chars": query.max_chars,
    }


def effective_instruction_sha(query: DistillQuery) -> str:
    payload = json.dumps(effective_instruction_payload(query), sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)[:16]


def _resolve_model(query: DistillQuery, model_override: str | None = None) -> str:
    if model_override:
        return model_override
    if query.model:
        return query.model
    return cfg.models().get("distill", "GPT-5.4")


def _build_prompt(query: DistillQuery, content: str) -> str:
    parts = [query.prompt.strip()]
    if query.max_chars:
        parts.append(_MAX_CHARS_INSTRUCTION.format(n=query.max_chars))
    parts.append("\n---\n")
    parts.append(content)
    return "\n\n".join(parts)


def _post_process(text: str, max_chars: int | None) -> tuple[str, bool, int | None]:
    """Strip trailing whitespace per line, strip leading/trailing blank lines.
    Apply hard truncation if max_chars exceeded.
    Returns (processed_text, truncated, original_length).
    """
    original_length = None
    truncated = False

    if max_chars and len(text) > max_chars:
        original_length = len(text)
        truncated = True
        cut = text[:max_chars]
        last_space = cut.rfind(" ")
        if last_space > max_chars * 0.8:
            cut = cut[:last_space]
        text = cut.rstrip() + "…"

    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()

    return text, truncated, original_length


def run_query(
    pdf_path: Path,
    query: DistillQuery,
    force: bool = False,
    model_override: str | None = None,
) -> dict[str, Any]:
    """Run one distillation query. Returns a result dict with status."""
    ad = get_analysis_dir(pdf_path)
    output_path = distill_json_path(ad, query.name)
    model = _resolve_model(query, model_override)

    bib = load_bib(pdf_path)

    try:
        content, paper_md_sha = build_input(query.scope, bib, ad, section_name=query.section)
    except RuntimeError as e:
        err = str(e)
        if query.scope == "section" and "not found in this paper" in err:
            return {"status": "missing-section", "query": query.name, "error": err}
        return {"status": "error", "query": query.name, "error": err}

    try:
        token_count = check_token_budget(content)
    except RuntimeError as e:
        return {"status": "error", "query": query.name, "error": str(e)}

    input_sha = sha256_text(content)[:16]
    prompt_sha = sha256_text(query.prompt)[:16]
    instruction_sha = effective_instruction_sha(query)
    bib_path = bib_record_path(ad)
    bib_sha = sha256_file(bib_path)[:12] if bib_path.exists() else None

    if not force and is_distill_current(ad, pdf_path, query.name, input_sha, prompt_sha, model, instruction_sha):
        return {"status": "cached", "query": query.name}

    full_prompt = _build_prompt(query, content)

    try:
        raw_output = openai_client.chat_text(
            system="You are a precise academic assistant. Follow the user's instructions exactly.",
            user=full_prompt,
            model=model,
        )
    except Exception as e:
        return {"status": "error", "query": query.name, "error": f"LLM call failed: {e}"}

    output, truncated, original_length = _post_process(raw_output, query.max_chars)

    now = now_iso()

    prov: dict[str, Any] = {
        "source": f"openai/{model}",
        "at": now,
        "prompt_sha256": prompt_sha,
        "input_sha256": input_sha,
        "effective_instruction_sha256": instruction_sha,
        "instruction_version": INSTRUCTION_VERSION,
        "bib_sha": bib_sha,
        "paper_md_sha": paper_md_sha,
        "tool_version": __version__,
        "prompt_source": query.source,
        "token_count_estimate": token_count,
    }
    if truncated:
        prov["truncated"] = True
        prov["original_length"] = original_length
        prov["max_chars"] = query.max_chars
    else:
        prov["truncated"] = False

    record: dict[str, Any] = {
        "schema_version": 1,
        "name": query.name,
        "scope": query.scope,
        "model": model,
        "generated_at": now,
        "output": output,
        "_provenance": prov,
    }
    if query.section:
        record["section"] = query.section

    # Write JSON atomically before deleting a stale legacy YAML counterpart.
    write_distill(ad, query.name, record, fmt="json")
    legacy_path = distill_yaml_path(ad, query.name)
    if legacy_path.exists():
        legacy_path.unlink()

    mark_distill_complete(ad, pdf_path, query.name, input_sha, prompt_sha, model, instruction_sha)

    return {
        "status": "distilled",
        "query": query.name,
        "output_path": output_path,
        "chars": len(output),
        "truncated": truncated,
        "token_count": token_count,
    }


def query_currency_status(pdf_path: Path, query: DistillQuery, model_override: str | None = None) -> str:
    """Use the same cache check as ``run_query`` for a configured query."""
    ad = get_analysis_dir(pdf_path)
    try:
        content, _ = build_input(query.scope, load_bib(pdf_path), ad, section_name=query.section)
    except Exception:
        # The configured query cannot be current when its required input is
        # absent or malformed.  Its output/status is still accurately
        # classified below from state/artifact.
        content = ""
    return distill_status(
        ad, pdf_path, query.name, sha256_text(content)[:16], sha256_text(query.prompt)[:16],
        _resolve_model(query, model_override), effective_instruction_sha(query),
    )


def list_distillations(
    pdf_path: Path, queries: dict[str, DistillQuery] | None = None, model_override: str | None = None,
) -> list[dict[str, Any]]:
    """Return metadata and shared currency status for configured and stored queries.

    Configured names are included even when their artifact has disappeared: this
    lets ``distill_status`` expose a dangling state entry as ``invalid``.
    """
    ad = get_analysis_dir(pdf_path)
    configured = queries or {}
    from ..state import load_state
    state_names = (load_state(ad).get("stages", {}).get("distill", {}) or {}).keys()
    names = sorted(set(list_distill_names(ad)) | set(configured) | set(state_names))
    results = []
    for name in names:
        query = configured.get(name)
        try:
            data = read_distill(ad, name)
        except Exception:
            data = None
        if query:
            status = query_currency_status(pdf_path, query, model_override)
        else:
            # An unconfigured on-disk record cannot be judged current, but the
            # same helper still reliably detects malformed and missing output.
            status = distill_status(ad, pdf_path, name, "", "", "", "")
        if data is None:
            results.append({"name": name, "status": status, "path": distill_record_path(ad, name)})
            continue
        output = data.get("output", "")
        results.append({
            "name": data.get("name", name), "scope": data.get("scope", "?"),
            "section": data.get("section"), "model": data.get("model", "?"),
            "generated_at": data.get("generated_at", "?"), "chars": len(output),
            "status": status, "evidence_status": data.get("evidence_status"),
            "path": distill_record_path(ad, name),
        })
    return results
