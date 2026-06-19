# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Configuration loading, override resolution, show, and validate."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

_PACKAGED_CONFIG = files("puba") / "config.yaml"
_LOCAL_CONFIG_NAME = "puba.config.yaml"


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

_REQUIRED_ENVS = {
    "OPENAI_API_KEY": "Argo LLM API key (set in .env or environment)",
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


def argo_base_url() -> str:
    return load()["argo"]["base_url"]


def argo_api_key() -> str:
    env_var = load()["argo"]["api_key_env"]
    key = os.environ.get(env_var, "")
    if not key:
        raise EnvironmentError(
            f"Argo API key not set. Expected env var: {env_var}\n"
            "Set it in .env or your shell environment."
        )
    return key


def models() -> dict[str, str]:
    return load()["models"]


def bib() -> dict[str, Any]:
    return load()["bib"]


def md() -> dict[str, Any]:
    return load()["md"]


def prompt_versions() -> dict[str, str]:
    return load()["prompt_versions"]


def distill() -> dict[str, Any]:
    return load().get("distill", {})


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

    for env, desc in _REQUIRED_ENVS.items():
        if not os.environ.get(env):
            errors.append(f"Missing required env var {env}: {desc}")

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
        if not cfg.get("models", {}).get("distill") and not all(
            q.model for q in queries.values()
        ):
            errors.append(
                "models.distill is not set and at least one distill query has no per-query model override"
            )
    except Exception as e:
        errors.append(f"distill query loading failed: {e}")

    return errors
