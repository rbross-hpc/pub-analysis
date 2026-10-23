# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Tests for config loading and validation."""
import os
import pytest
from puba import config


def test_config_loads():
    cfg = config.load()
    assert "bib" in cfg
    assert "md" in cfg
    assert "models" in cfg
    assert "prompt_versions" in cfg
    assert config.llm_backend() == "bridge"
    assert config.models()["bib_extract"] == "openai/gpt-5.6-sol"


def test_config_has_required_sources():
    priority = config.bib().get("source_priority", [])
    for src in ("human", "openalex", "crossref", "arxiv", "pdf", "llm", "derived", "unknown"):
        assert src in priority, f"Missing source: {src}"


def test_config_regexes_compile():
    errors = config.validate()
    regex_errors = [e for e in errors if "invalid regex" in e]
    assert not regex_errors, f"Regex errors: {regex_errors}"


def test_bridge_validation_requires_opencode_not_openai_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    errors = config.validate()
    assert any("OpenCode executable" in error for error in errors)
    assert not any("OPENAI_" in error for error in errors)


def test_openai_validation_requires_openai_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(config, "llm_backend", lambda: "openai")
    errors = config.validate()
    assert any("OPENAI_API_KEY" in error for error in errors)
    assert any("OPENAI_BASE_URL" in error for error in errors)


def test_validation_rejects_legacy_model_shape(monkeypatch):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "models": {**loaded["models"], "distill": "old-model"},
    })
    errors = config.validate()
    assert any("Legacy model keys" in error for error in errors)


def test_config_show_returns_string():
    s = config.show()
    assert isinstance(s, str)
    assert "puba" in s.lower() or "config" in s.lower()


def test_conflict_thresholds_present():
    thresholds = config.bib().get("conflict_thresholds", {})
    assert "title_sim_min" in thresholds
    assert "year_diff_max" in thresholds
    assert "venue_sim_min" in thresholds


def test_classification_lists_present():
    cls = config.bib().get("classification", {})
    assert "conference_acronyms" in cls
    assert "preprint_hosts" in cls
    assert "preprint_doi_prefixes" in cls
