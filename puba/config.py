# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Configuration loading, override resolution, show, and validate."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

_PKG_ROOT = Path(__file__).parent.parent
_PACKAGED_CONFIG = _PKG_ROOT / "config.yaml"
_LOCAL_CONFIG = Path.cwd() / "puba.config.yaml"

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

    if _LOCAL_CONFIG.exists():
        with open(_LOCAL_CONFIG, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        for k in _flatten_keys(local):
            cfg["_source"][k] = f"project-local ({_LOCAL_CONFIG})"
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


def show() -> str:
    cfg = load()
    sources = cfg.get("_source", {})
    lines = ["Resolved puba configuration:\n"]
    lines.append(f"  Packaged config : {_PACKAGED_CONFIG}")
    if _LOCAL_CONFIG.exists():
        lines.append(f"  Local override  : {_LOCAL_CONFIG}")
    else:
        lines.append(f"  Local override  : (none — {_LOCAL_CONFIG} not found)")
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

    for key in ("section_numbered_pattern",):
        pattern = cfg.get("md", {}).get(key, "")
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"md.{key}: invalid regex {pattern!r}: {e}")

    known_sources = set(bib_cfg.get("source_priority", []))
    required_sources = {"human", "openalex", "crossref", "arxiv", "pdf", "llm", "derived", "unknown"}
    missing = required_sources - known_sources
    if missing:
        errors.append(f"bib.source_priority missing required sources: {sorted(missing)}")

    rate_limits = bib_cfg.get("rate_limits_s", {})
    for source in ("openalex", "crossref", "arxiv", "osti"):
        if source not in rate_limits:
            errors.append(f"bib.rate_limits_s missing entry for: {source}")

    return errors
