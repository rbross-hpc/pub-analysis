# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Run distillation queries through the schema-v2 evidence pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import __version__
from .. import config as cfg
from ..artifacts import (
    bib_record_path,
    distill_json_path,
    distill_record_path,
    distill_yaml_path,
    is_distill_record,
    list_distill_names,
    read_distill,
    write_distill,
)
from ..io import now_iso, sha256_file, sha256_text
from ..llm import openai_client
from ..sidecar import load as load_bib
from ..state import (
    analysis_dir,
    distill_status,
    is_distill_current,
    mark_distill_complete,
)
from .evidence import (
    AtomizationLimits,
    aggregate_claim_support,
    apply_quote_presence,
    build_verifier_context,
    is_valid_atomizer_response,
    is_valid_response,
    is_valid_verifier_response,
    normalize_atomizer_response,
    normalize_verifier_response,
    process_quote_presence,
)
from .queries import DistillQuery, effective_evidence_mode
from .scope import ScopeInput, build_scope_input, estimate_tokens

_MAX_CHARS_INSTRUCTION = "Your response MUST be at most {n} characters. Be concise."
_EVIDENCE_MAX_CHARS_INSTRUCTION = (
    "The answer string MUST be at most {n} characters. Be concise. This limit does not "
    "apply to the JSON structure or evidence quote strings."
)

# Every request/local transformation that can affect persisted output is versioned.
INSTRUCTION_VERSION = "distill-v1"
EVIDENCE_INSTRUCTION_VERSION = "distill-evidence-v1"
EVIDENCE_RESPONSE_SCHEMA_VERSION = 1
ATOMIZER_INSTRUCTION_VERSION = "distill-atomizer-v1"
ATOMIZER_RESPONSE_SCHEMA_VERSION = 1
VERIFIER_INSTRUCTION_VERSION = "claim-verifier-v1"
VERIFIER_RESPONSE_SCHEMA_VERSION = 1
LOCAL_RESULT_VERSION = "distill-local-v2"
SUPPORT_TAXONOMY_VERSION = "claim-support-v1"
CONTEXT_ALGORITHM_VERSION = "paragraph-context-v1"

_PLAIN_SYSTEM = "You are a precise academic assistant. Follow the user's instructions exactly."
_EVIDENCE_SYSTEM = """You are a precise academic assistant. Follow the user's instructions exactly.
Return only a JSON object with this exact required structure:
{"answer": "...", "evidence": [{"quote": "..."}, ...]}.
Each quote must be a non-blank exact verbatim passage from the supplied source content.
Do not include offsets, page numbers, section names, markdown fences, or any other fields."""
_ATOMIZER_SYSTEM = """Atomize the supplied answer into independent factual claims.
Return only JSON with this exact shape: {"claims":[{"claim":"...","evidence":[{"quote":"..."}]}]}.
Claims and quotes must be non-blank. Quotes must be exact verbatim passages from the supplied source.
Do not invent IDs or add fields. Return an empty claims list when the answer has no factual claims."""
_VERIFIER_SYSTEM = """Assess whether the supplied evidence supports one factual claim.
Return only JSON with exactly status, rationale, and evidence_ids. Status must be one of supported,
partially_supported, unsupported, contradicted, or insufficient_evidence. Rationale must be concise
and non-blank. evidence_ids may contain only IDs supplied in this request. Do not add fields."""


def _instruction_version(mode: str) -> str:
    return EVIDENCE_INSTRUCTION_VERSION if mode == "quotes" else INSTRUCTION_VERSION


def _distill_limits() -> tuple[dict[str, int], dict[str, int]]:
    distill = cfg.distill()
    claims = distill.get("claims", {})
    verification = distill.get("verification", {})
    return (
        {
            "max_claims": int(claims.get("max_claims", 20)),
            "max_evidence_per_claim": int(claims.get("max_evidence_per_claim", 4)),
            "max_quote_chars": int(claims.get("max_quote_chars", 4000)),
            "max_atomizer_input_tokens": int(claims.get("max_atomizer_input_tokens", 100000)),
        },
        {
            "max_context_tokens_per_claim": int(verification.get("max_context_tokens_per_claim", 4000)),
            "max_total_input_tokens": int(verification.get("max_total_input_tokens", 50000)),
            "max_rationale_chars": int(verification.get("max_rationale_chars", 1000)),
        },
    )


def _resolve_model(query: DistillQuery, model_override: str | None = None) -> str:
    if model_override:
        return model_override
    if query.model:
        return query.model
    return cfg.models().get("distill", "openai/gpt-5.6-sol")


def _resolve_verifier_model(
    query: DistillQuery, verifier_model_override: str | None = None,
) -> str:
    if verifier_model_override:
        return verifier_model_override
    if query.verifier_model:
        return query.verifier_model
    return cfg.models().get("claim_verify", "openai/gpt-5.6-sol")


def effective_instruction_payload(
    query: DistillQuery,
    generation_model: str | None = None,
    verifier_model: str | None = None,
) -> dict[str, Any]:
    """Return all request-affecting settings used by execution and currency."""
    mode = effective_evidence_mode(query)
    generation_model = generation_model or _resolve_model(query)
    effective_verifier = (
        verifier_model or _resolve_verifier_model(query) if mode == "claims" else None
    )
    claim_limits, verification_limits = _distill_limits()
    return {
        "schema_version": 2,
        "evidence_mode": mode,
        "generation_model": generation_model,
        "verifier_model": effective_verifier,
        "max_chars": query.max_chars,
        "max_input_tokens": int(cfg.distill().get("max_input_tokens", 100000)),
        "generation": {
            "instruction_version": _instruction_version(mode),
            "response_schema_version": EVIDENCE_RESPONSE_SCHEMA_VERSION if mode == "quotes" else 1,
        },
        "atomization": {
            "instruction_version": ATOMIZER_INSTRUCTION_VERSION,
            "response_schema_version": ATOMIZER_RESPONSE_SCHEMA_VERSION,
        },
        "verification": {
            "instruction_version": VERIFIER_INSTRUCTION_VERSION,
            "response_schema_version": VERIFIER_RESPONSE_SCHEMA_VERSION,
        },
        "local_result_version": LOCAL_RESULT_VERSION,
        "support_taxonomy_version": SUPPORT_TAXONOMY_VERSION,
        "context_algorithm_version": CONTEXT_ALGORITHM_VERSION,
        "claims_limits": claim_limits,
        "verification_limits": verification_limits,
    }


def effective_instruction_sha(
    query: DistillQuery,
    generation_model: str | None = None,
    verifier_model: str | None = None,
) -> str:
    payload = json.dumps(
        effective_instruction_payload(query, generation_model, verifier_model),
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(payload)[:16]


def _build_prompt(query: DistillQuery, content: str, mode: str | None = None) -> str:
    mode = mode or effective_evidence_mode(query)
    parts = [query.prompt.strip()]
    if query.max_chars:
        instruction = _EVIDENCE_MAX_CHARS_INSTRUCTION if mode == "quotes" else _MAX_CHARS_INSTRUCTION
        parts.append(instruction.format(n=query.max_chars))
    parts.extend(["\n---\n", content])
    return "\n\n".join(parts)


def _post_process(text: str, max_chars: int | None) -> tuple[str, bool, int | None]:
    """Normalize whitespace and hard-limit output without exceeding max_chars."""
    original_length = None
    truncated = False
    if max_chars and len(text) > max_chars:
        original_length = len(text)
        truncated = True
        content_limit = max_chars - 1
        cut = text[:content_limit]
        last_space = cut.rfind(" ")
        if last_space > content_limit * 0.8:
            cut = cut[:last_space]
        text = cut.rstrip() + "…"

    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip(), truncated, original_length


def _request_tokens(system: str, user: str) -> int:
    # Include a small fixed allowance for chat roles and message framing.
    return estimate_tokens(system) + estimate_tokens(user) + 8


def _check_limit(count: int, limit: int, setting: str, request: str) -> None:
    if count > limit:
        raise RuntimeError(
            f"{request} is approximately {count:,} input tokens, which exceeds "
            f"{setting}={limit:,}."
        )


def _input_sha(scope_input: ScopeInput) -> str:
    payload = {
        "model_content": scope_input.model_content,
        "allowed_start": scope_input.allowed_start,
        "allowed_end": scope_input.allowed_end,
        "source_hashes": scope_input.source_hashes,
    }
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))[:16]


def _execution_identity(
    pdf_path: Path,
    query: DistillQuery,
    model_override: str | None,
    verifier_model_override: str | None,
) -> dict[str, Any]:
    ad = analysis_dir(pdf_path)
    bib = load_bib(pdf_path)
    if not isinstance(bib, dict):
        raise RuntimeError("A bibliographic record is required before distillation")
    scope_input = build_scope_input(query.scope, bib, ad, section_name=query.section)
    mode = effective_evidence_mode(query)
    model = _resolve_model(query, model_override)
    verifier_model = _resolve_verifier_model(query, verifier_model_override) if mode == "claims" else None
    return {
        "analysis_dir": ad,
        "bib": bib,
        "scope_input": scope_input,
        "mode": mode,
        "model": model,
        "verifier_model": verifier_model,
        "backend": cfg.llm_backend(),
        "input_sha": _input_sha(scope_input),
        "prompt_sha": sha256_text(query.prompt)[:16],
        "instruction_sha": effective_instruction_sha(query, model, verifier_model),
    }


def _call_provenance(
    backend: str,
    model: str,
    instruction_version: str,
    response_schema_version: int,
    input_tokens: int,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "model": model,
        "instruction_version": instruction_version,
        "response_schema_version": response_schema_version,
        "input_token_estimate": input_tokens,
        **extra,
    }


def _result_metadata(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "evidence_mode", "evidence_status", "quote_presence_status", "quote_presence_counts",
        "claim_support_status", "claim_support_counts", "verifier_model",
    )
    return {key: record[key] for key in keys if key in record}


def run_query(
    pdf_path: Path,
    query: DistillQuery,
    force: bool = False,
    model_override: str | None = None,
    verifier_model_override: str | None = None,
) -> dict[str, Any]:
    """Run one query atomically through generation and optional evidence stages."""
    mode = effective_evidence_mode(query)
    if mode not in {"none", "quotes", "claims"}:
        return {
            "status": "error", "query": query.name,
            "error": f"Invalid evidence_mode: {mode!r}",
        }
    if query.verifier_model is not None and (
        not isinstance(query.verifier_model, str) or not query.verifier_model.strip()
    ):
        return {
            "status": "error", "query": query.name,
            "error": "verifier_model must be a non-empty string",
        }
    try:
        identity = _execution_identity(
            pdf_path, query, model_override, verifier_model_override,
        )
    except RuntimeError as e:
        err = str(e)
        if query.scope == "section" and "not found in this paper" in err:
            return {"status": "missing-section", "query": query.name, "error": err}
        return {"status": "error", "query": query.name, "error": err}
    except Exception as e:
        return {"status": "error", "query": query.name, "error": str(e)}

    ad: Path = identity["analysis_dir"]
    scope_input: ScopeInput = identity["scope_input"]
    mode: str = identity["mode"]
    model: str = identity["model"]
    verifier_model: str | None = identity["verifier_model"]
    backend: str = identity["backend"]
    output_path = distill_json_path(ad, query.name)

    if not force and is_distill_current(
        ad, pdf_path, query.name, identity["input_sha"], identity["prompt_sha"],
        model, identity["instruction_sha"], backend=backend,
        verifier_model=verifier_model,
    ):
        cached = {"status": "cached", "query": query.name}
        try:
            record = read_distill(ad, query.name)
            if isinstance(record, dict):
                cached.update(_result_metadata(record))
        except Exception:
            pass
        return cached

    claim_limits, verification_limits = _distill_limits()
    full_prompt = _build_prompt(query, scope_input.model_content, mode)
    generation_system = _EVIDENCE_SYSTEM if mode == "quotes" else _PLAIN_SYSTEM
    generation_tokens = _request_tokens(generation_system, full_prompt)
    try:
        _check_limit(
            generation_tokens, int(cfg.distill().get("max_input_tokens", 100000)),
            "max_input_tokens", "Generation request",
        )

        if mode == "quotes":
            response = openai_client.chat_json(
                system=generation_system,
                user=full_prompt,
                model_role="distill",
                model=model,
                validate=is_valid_response,
            )
            raw_output = response["answer"]
        else:
            response = None
            raw_output = openai_client.chat_text(
                system=generation_system,
                user=full_prompt,
                model_role="distill",
                model=model,
            )

        output, truncated, original_length = _post_process(raw_output, query.max_chars)
        evidence_result: dict[str, Any] = {}
        calls: dict[str, Any] = {
            "generation": _call_provenance(
                backend, model, _instruction_version(mode),
                EVIDENCE_RESPONSE_SCHEMA_VERSION if mode == "quotes" else 1,
                generation_tokens,
            ),
        }

        if mode == "quotes":
            assert isinstance(response, dict)
            evidence_result = process_quote_presence(
                response["evidence"], scope_input.canonical_source,
                allowed_start=scope_input.allowed_start,
                allowed_end=scope_input.allowed_end,
                sections=scope_input.sections,
                abstract=query.scope == "abstract",
            )
        elif mode == "claims":
            atomizer_user = json.dumps({
                "answer": output,
                "source": scope_input.canonical_source[
                    scope_input.allowed_start:scope_input.allowed_end
                ],
            }, sort_keys=True, separators=(",", ":"))
            atomizer_tokens = _request_tokens(_ATOMIZER_SYSTEM, atomizer_user)
            _check_limit(
                atomizer_tokens, claim_limits["max_atomizer_input_tokens"],
                "max_atomizer_input_tokens", "Atomizer request",
            )
            atomization_limits = AtomizationLimits(
                max_claims=claim_limits["max_claims"],
                max_evidence_per_claim=claim_limits["max_evidence_per_claim"],
                max_quote_chars=claim_limits["max_quote_chars"],
            )
            atomized = openai_client.chat_json(
                system=_ATOMIZER_SYSTEM,
                user=atomizer_user,
                model_role="distill",
                model=model,
                validate=lambda value: is_valid_atomizer_response(value, atomization_limits),
            )
            claims = normalize_atomizer_response(atomized, atomization_limits)
            presence = apply_quote_presence(
                claims, scope_input.canonical_source,
                allowed_start=scope_input.allowed_start,
                allowed_end=scope_input.allowed_end,
                sections=scope_input.sections,
                abstract=query.scope == "abstract",
            )
            calls["atomization"] = _call_provenance(
                backend, model, ATOMIZER_INSTRUCTION_VERSION,
                ATOMIZER_RESPONSE_SCHEMA_VERSION, atomizer_tokens,
            )

            planned: list[tuple[dict[str, Any], str, int, list[str]]] = []
            total_verifier_tokens = 0
            for claim in presence["claims"]:
                present = [
                    item for item in claim["evidence"]
                    if item["quote_status"] == "present"
                ]
                if not present:
                    claim["support_assessment"] = {
                        "status": "not_assessed",
                        "rationale": "No uniquely present evidence was available.",
                        "evidence_ids": [],
                    }
                    continue
                contexts = build_verifier_context(
                    scope_input.canonical_source, present,
                    allowed_start=scope_input.allowed_start,
                    allowed_end=scope_input.allowed_end,
                )
                context_tokens = estimate_tokens(json.dumps(contexts, sort_keys=True, separators=(",", ":")))
                _check_limit(
                    context_tokens, verification_limits["max_context_tokens_per_claim"],
                    "max_context_tokens_per_claim", f"Verifier context for {claim['id']}",
                )
                verifier_user = json.dumps({
                    "claim": claim["claim"],
                    "evidence": [{"id": item["id"], "quote": item["quote"]} for item in present],
                    "contexts": contexts,
                }, sort_keys=True, separators=(",", ":"))
                request_tokens = _request_tokens(_VERIFIER_SYSTEM, verifier_user)
                total_verifier_tokens += request_tokens
                planned.append((claim, verifier_user, request_tokens, [item["id"] for item in present]))

            _check_limit(
                total_verifier_tokens, verification_limits["max_total_input_tokens"],
                "max_total_input_tokens", "All verifier requests",
            )
            assert verifier_model is not None
            for claim, verifier_user, _, evidence_ids in planned:
                assessment = openai_client.chat_json(
                    system=_VERIFIER_SYSTEM,
                    user=verifier_user,
                    model_role="claim_verify",
                    model=verifier_model,
                    validate=lambda value, ids=set(evidence_ids): is_valid_verifier_response(
                        value, ids,
                        max_rationale_chars=verification_limits["max_rationale_chars"],
                    ),
                )
                claim["support_assessment"] = normalize_verifier_response(
                    assessment, evidence_ids,
                    max_rationale_chars=verification_limits["max_rationale_chars"],
                )

            evidence_result = {
                "claims": presence["claims"],
                "quote_presence_status": presence["quote_presence_status"],
                "quote_presence_counts": presence["quote_presence_counts"],
                **aggregate_claim_support(presence["claims"]),
            }
            calls["verification"] = _call_provenance(
                backend, verifier_model, VERIFIER_INSTRUCTION_VERSION,
                VERIFIER_RESPONSE_SCHEMA_VERSION, total_verifier_tokens,
                request_count=len(planned),
            )

        now = now_iso()
        bib_path = bib_record_path(ad)
        paper_hash = scope_input.source_hashes.get("paper_md_sha256")
        provenance: dict[str, Any] = {
            "source": f"{backend}/{model}",
            "backend": backend,
            "at": now,
            "prompt_sha256": identity["prompt_sha"],
            "input_sha256": identity["input_sha"],
            "effective_instruction_sha256": identity["instruction_sha"],
            "instruction_version": _instruction_version(mode),
            "bib_sha": sha256_file(bib_path)[:12] if bib_path.exists() else None,
            "paper_md_sha": paper_hash[:12] if paper_hash else None,
            "tool_version": __version__,
            "prompt_source": query.source,
            "token_count_estimate": generation_tokens,
            "calls": calls,
            "truncated": truncated,
        }
        if truncated:
            provenance.update({"original_length": original_length, "max_chars": query.max_chars})

        record: dict[str, Any] = {
            "schema_version": 2,
            "name": query.name,
            "scope": query.scope,
            "evidence_mode": mode,
            "model": model,
            "verifier_model": verifier_model,
            "backend": backend,
            "generated_at": now,
            "output": output,
            "prompt": query.prompt,
            "instruction_version": _instruction_version(mode),
            "_provenance": provenance,
            **evidence_result,
        }
        if query.section:
            record["section"] = query.section
        if not is_distill_record(record):
            raise ValueError("Complete schema-v2 record failed artifact validation")
    except Exception as e:
        return {"status": "error", "query": query.name, "error": f"Distillation failed: {e}"}

    write_distill(ad, query.name, record, fmt="json")
    legacy_path = distill_yaml_path(ad, query.name)
    if legacy_path.exists():
        legacy_path.unlink()
    mark_distill_complete(
        ad, pdf_path, query.name, identity["input_sha"], identity["prompt_sha"],
        model, identity["instruction_sha"], backend=backend,
        verifier_model=verifier_model,
    )
    return {
        "status": "distilled",
        "query": query.name,
        "output_path": output_path,
        "chars": len(record["output"]),
        "truncated": record["_provenance"]["truncated"],
        "token_count": generation_tokens,
        **_result_metadata(record),
    }


def query_currency_status(
    pdf_path: Path,
    query: DistillQuery,
    model_override: str | None = None,
    verifier_model_override: str | None = None,
) -> str:
    """Use exactly the execution identity used by ``run_query``."""
    ad = analysis_dir(pdf_path)
    try:
        identity = _execution_identity(
            pdf_path, query, model_override, verifier_model_override,
        )
        return distill_status(
            ad, pdf_path, query.name, identity["input_sha"], identity["prompt_sha"],
            identity["model"], identity["instruction_sha"],
            backend=identity["backend"], verifier_model=identity["verifier_model"],
        )
    except Exception:
        mode = effective_evidence_mode(query)
        model = _resolve_model(query, model_override)
        verifier_model = _resolve_verifier_model(query, verifier_model_override) if mode == "claims" else None
        return distill_status(
            ad, pdf_path, query.name, sha256_text("")[:16], sha256_text(query.prompt)[:16],
            model, effective_instruction_sha(query, model, verifier_model),
            backend=cfg.llm_backend(), verifier_model=verifier_model,
        )


def list_distillations(
    pdf_path: Path,
    queries: dict[str, DistillQuery] | None = None,
    model_override: str | None = None,
    verifier_model_override: str | None = None,
) -> list[dict[str, Any]]:
    """Return metadata and shared currency status for configured and stored queries."""
    ad = analysis_dir(pdf_path)
    configured = queries or {}
    from ..state import load_state
    state_names = load_state(ad).get("stages", {}).get("distill", {}).keys()
    names = sorted(set(list_distill_names(ad)) | set(configured) | set(state_names))
    results = []
    for name in names:
        query = configured.get(name)
        try:
            data = read_distill(ad, name)
        except Exception:
            data = None
        if query:
            status = query_currency_status(
                pdf_path, query, model_override, verifier_model_override,
            )
        else:
            status = distill_status(ad, pdf_path, name, "", "", "", "")
        if not is_distill_record(data):
            results.append({"name": name, "status": status, "path": distill_record_path(ad, name)})
            continue
        results.append({
            "name": name,
            "scope": data.get("scope", "?"),
            "section": data.get("section"),
            "model": data.get("model", "?"),
            "verifier_model": data.get("verifier_model"),
            "generated_at": data.get("generated_at", "?"),
            "chars": len(data["output"]),
            "status": status,
            **_result_metadata(data),
            "path": distill_record_path(ad, name),
        })
    return results
