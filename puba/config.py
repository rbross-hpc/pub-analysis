# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Configuration loading, override resolution, show, and validate."""
from __future__ import annotations

import math
import os
import re
import shutil
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

load_dotenv()

_PACKAGED_CONFIG = files("puba") / "config.yaml"
_LOCAL_CONFIG_NAME = "puba.config.yaml"

_VALID_EVIDENCE_MODES = {"none", "quotes", "claims"}
_DISTILL_LIMITS = {
    "claims": (
        "max_claims",
        "max_evidence_per_claim",
        "max_quote_chars",
        "max_atomizer_input_tokens",
    ),
    "verification": (
        "max_context_tokens_per_claim",
        "max_total_input_tokens",
        "max_rationale_chars",
    ),
}


def _local_config() -> Path:
    return Path.cwd() / _LOCAL_CONFIG_NAME

_CATEGORY_ENUM = {
    "journal article",
    "conference paper",
    "workshop paper",
    "arxiv preprint",
    "preprint",
    "book chapter",
    "book",
    "technical report",
    "thesis",
    "other",
}

_OPENAI_REQUIRED_ENVS = {
    "OPENAI_API_KEY": "LLM API key (set in .env or environment)",
    "OPENAI_BASE_URL": "OpenAI-compatible API endpoint URL (e.g. https://apps.inside.anl.gov/argoapi/v1 for Argo; https://api.openai.com/v1 for real OpenAI)",
}

_RECOMMENDED_ENVS = {
    "OPENALEX_MAILTO": "Your email for OpenAlex/CrossRef polite pool (faster, more reliable)",
    "SEMANTICSCHOLAR_API_KEY": "Semantic Scholar API key (without one, unauthenticated requests are rate-limited)",
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    if not _PACKAGED_CONFIG.exists():
        raise FileNotFoundError(f"Packaged config.yaml not found at {_PACKAGED_CONFIG}")
    with open(_PACKAGED_CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_source"] = {k: "packaged" for k in _flatten_keys(cfg)}

    _lc = _local_config()
    if _lc.exists():
        with open(_lc, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        for k in _flatten_keys(local):
            cfg["_source"][k] = f"project-local ({_lc})"
        cfg = _deep_merge(cfg, local)

    return cfg


def reload() -> dict[str, Any]:
    load.cache_clear()
    return load()


def _flatten_keys(d: dict, prefix: str = "") -> list[str]:
    keys = []
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        keys.append(full)
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, full))
    return keys


def llm() -> dict[str, Any]:
    return load().get("llm", {})


def llm_backend() -> str:
    return str(llm().get("backend", "bridge"))


def models() -> dict[str, str]:
    selected = load().get("models", {}).get(llm_backend(), {})
    return selected if isinstance(selected, dict) else {}


def bib() -> dict[str, Any]:
    return load()["bib"]


def md() -> dict[str, Any]:
    return load()["md"]


def md_cache_identity() -> dict[str, str]:
    """Return extraction settings that affect Markdown cache identity."""
    md_cfg = md()
    extractor = str(md_cfg.get("extractor", "service"))
    identity = {"extractor": extractor}
    if extractor == "service":
        service = md_cfg.get("service", {})
        if isinstance(service, dict):
            identity["service_url"] = str(service.get("base_url", "")).rstrip("/")
    return identity


def prompt_versions() -> dict[str, str]:
    return load()["prompt_versions"]


def distill() -> dict[str, Any]:
    return load().get("distill", {})


def figures() -> dict[str, Any]:
    return load().get("figures", {})


def md_distill_strip_sections() -> list[str]:
    return distill().get("narrative_strip_sections", [])


def packaged_config_path() -> Path:
    return Path(str(_PACKAGED_CONFIG))


def local_config_path() -> Path:
    return _local_config()


def show() -> str:
    cfg = load()
    sources = cfg.get("_source", {})
    lines = ["Resolved puba configuration:\n"]
    _lc = _local_config()
    lines.append(f"  Packaged config : {_PACKAGED_CONFIG}")
    if _lc.exists():
        lines.append(f"  Local override  : {_lc}")
    else:
        lines.append(f"  Local override  : (none — {_lc} not found)")
    lines.append("")

    def _render(d: dict, indent: int = 2) -> None:
        pad = " " * indent
        for k, v in d.items():
            if k.startswith("_"):
                continue
            full_key = k
            src = sources.get(full_key, "")
            src_tag = f"  [{src}]" if src else ""
            if isinstance(v, dict):
                lines.append(f"{pad}{k}:{src_tag}")
                _render(v, indent + 2)
            elif isinstance(v, list):
                lines.append(f"{pad}{k}: [{', '.join(str(i) for i in v[:5])}{'...' if len(v) > 5 else ''}]{src_tag}")
            else:
                lines.append(f"{pad}{k}: {v}{src_tag}")

    _render(cfg)
    return "\n".join(lines)


def validate() -> list[str]:
    errors: list[str] = []
    cfg = load()

    backend = llm_backend()
    if backend not in {"bridge", "openai"}:
        errors.append(f"llm.backend must be 'bridge' or 'openai', got {backend!r}")
    elif backend == "openai":
        for env, desc in _OPENAI_REQUIRED_ENVS.items():
            if not os.environ.get(env):
                errors.append(f"Missing required env var {env}: {desc}")
    elif shutil.which("opencode") is None:
        errors.append("OpenCode executable not found on PATH (required by llm.backend=bridge)")

    timeout = cfg.get("llm", {}).get("timeout_s")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        errors.append("llm.timeout_s must be a positive number")

    md_cfg = cfg.get("md", {})
    if not isinstance(md_cfg, dict):
        errors.append("md must be a mapping")
        md_cfg = {}
    extractor = md_cfg.get("extractor")
    if extractor not in {"service", "local"}:
        errors.append(f"md.extractor must be 'service' or 'local', got {extractor!r}")
    service_cfg = md_cfg.get("service", {})
    if extractor == "service" and not isinstance(service_cfg, dict):
        errors.append("md.service must be a mapping when md.extractor is 'service'")
    elif extractor == "service":
        base_url = service_cfg.get("base_url")
        parsed_url = urlparse(base_url) if isinstance(base_url, str) else None
        if (
            not parsed_url
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.query
            or parsed_url.fragment
        ):
            errors.append("md.service.base_url must be an HTTP(S) URL")
        for key in ("timeout_s", "poll_interval_s"):
            value = service_cfg.get(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                errors.append(f"md.service.{key} must be a positive number")

    selected_models = models()
    model_cfg = cfg.get("models", {})
    legacy_roles = {"bib_extract", "distill"} & set(model_cfg) if isinstance(model_cfg, dict) else set()
    if legacy_roles:
        errors.append(
            "Legacy model keys must move under models.bridge or models.openai: "
            f"{sorted(legacy_roles)}"
        )
    for role in ("bib_extract", "distill", "claim_verify"):
        if not isinstance(selected_models.get(role), str) or not selected_models[role].strip():
            errors.append(f"models.{backend}.{role} must be a non-empty string")

    distill_cfg = cfg.get("distill", {})
    if not isinstance(distill_cfg, dict):
        errors.append("distill must be a mapping")
        distill_cfg = {}
    default_evidence_mode = distill_cfg.get("default_evidence_mode")
    if default_evidence_mode not in _VALID_EVIDENCE_MODES:
        errors.append(
            "distill.default_evidence_mode must be one of "
            f"{sorted(_VALID_EVIDENCE_MODES)}"
        )
    max_input_tokens = distill_cfg.get("max_input_tokens")
    if (
        not isinstance(max_input_tokens, int)
        or isinstance(max_input_tokens, bool)
        or max_input_tokens <= 0
    ):
        errors.append("distill.max_input_tokens must be a positive integer")
    for group, keys in _DISTILL_LIMITS.items():
        group_cfg = distill_cfg.get(group, {})
        if not isinstance(group_cfg, dict):
            errors.append(f"distill.{group} must be a mapping")
            continue
        for key in keys:
            value = group_cfg.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"distill.{group}.{key} must be a positive integer")

    bib_cfg = cfg.get("bib", {})
    cls = bib_cfg.get("classification", {})

    for key in ("conference_venue_patterns", "workshop_patterns", "thesis_venue_patterns", "technical_report_venue_patterns"):
        for pattern in cls.get(key, []):
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"bib.classification.{key}: invalid regex {pattern!r}: {e}")

    known_sources = set(bib_cfg.get("source_priority", []))
    required_sources = {"human", "openalex", "crossref", "arxiv", "pdf", "llm", "derived", "unknown"}
    missing = required_sources - known_sources
    if missing:
        errors.append(f"bib.source_priority missing required sources: {sorted(missing)}")

    rate_limits = bib_cfg.get("rate_limits_s", {})
    for source in ("openalex", "crossref", "arxiv", "osti"):
        if source not in rate_limits:
            errors.append(f"bib.rate_limits_s missing entry for: {source}")

    try:
        from .distill.queries import load_queries, validate_queries
        queries = load_queries()
        errors.extend(validate_queries(queries))
        if not selected_models.get("distill") and not all(
            q.model for q in queries.values()
        ):
            errors.append(
                "models.distill is not set and at least one distill query has no per-query model override"
            )
    except Exception as e:
        errors.append(f"distill query loading failed: {e}")

    return errors
