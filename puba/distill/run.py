# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Run a single distillation query: build prompt, call LLM, post-process, write output."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .. import __version__
from .. import config as cfg
from ..io import atomic_write_text, now_iso, sha256_file, sha256_text
from ..llm import openai_client
from ..sidecar import load as load_bib
from ..state import (
    analysis_dir as get_analysis_dir,
    is_distill_current,
    mark_distill_complete,
)
from .queries import DistillQuery
from .scope import build_input, check_token_budget

_MAX_CHARS_INSTRUCTION = "Your response MUST be at most {n} characters. Be concise."


def _resolve_model(query: DistillQuery) -> str:
    if query.model:
        return query.model
    return cfg.distill().get("default_model") or cfg.models().get("distill", "GPT-5.4")


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
) -> dict[str, Any]:
    """Run one distillation query. Returns a result dict with status."""
    ad = get_analysis_dir(pdf_path)
    analyses_dir = ad / "analyses"
    analyses_dir.mkdir(exist_ok=True)

    output_path = analyses_dir / f"{query.name}.yaml"
    model = _resolve_model(query)

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
    bib_yaml_sha = sha256_file(ad / "bib.yaml")[:12] if (ad / "bib.yaml").exists() else None

    if not force and is_distill_current(ad, pdf_path, query.name, input_sha, prompt_sha, model):
        return {"status": "cached", "query": query.name}

    full_prompt = _build_prompt(query, content)

    try:
        raw_output = openai_client.chat_text(
            system="You are a precise academic assistant. Follow the user's instructions exactly.",
            user=full_prompt,
            model_role="distill",
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
        "bib_yaml_sha": bib_yaml_sha,
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
        "name": query.name,
        "scope": query.scope,
        "model": model,
        "generated_at": now,
        "output": output,
        "_provenance": prov,
    }
    if query.section:
        record["section"] = query.section

    body = yaml.dump(record, allow_unicode=True, sort_keys=False, default_flow_style=False)
    scope_tag = f"{query.scope}:{query.section}" if query.section else query.scope
    header = (
        f"# puba distill — {query.name}\n"
        f"# generated_at: {now}\n"
        f"# scope: {scope_tag}  model: {model}\n\n"
    )
    atomic_write_text(output_path, header + body)

    mark_distill_complete(ad, pdf_path, query.name, input_sha, prompt_sha, model)

    return {
        "status": "distilled",
        "query": query.name,
        "output_path": output_path,
        "chars": len(output),
        "truncated": truncated,
        "token_count": token_count,
    }


def list_distillations(pdf_path: Path) -> list[dict[str, Any]]:
    """Return info about all analyses/*.yaml files for a paper."""
    ad = get_analysis_dir(pdf_path)
    analyses_dir = ad / "analyses"
    if not analyses_dir.exists():
        return []

    results = []
    for f in sorted(analyses_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        output = data.get("output", "")
        results.append({
            "name": data.get("name", f.stem),
            "scope": data.get("scope", "?"),
            "section": data.get("section"),
            "model": data.get("model", "?"),
            "generated_at": data.get("generated_at", "?"),
            "chars": len(output),
            "path": f,
        })
    return results
