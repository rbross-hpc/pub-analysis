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
    assert config.llm_backend() == "opencode"
    assert config.models()["bib_extract"] == "openai/gpt-5.6-sol"
    assert config.models()["claim_verify"] == config.models()["distill"]
    assert config.md()["extractor"] == "service"
    assert config.md()["service"]["base_url"] == "http://host-localhost:8001"


def test_distill_schema_v2_defaults():
    distill = config.distill()
    assert distill["default_evidence_mode"] == "claims"
    assert distill["claims"] == {
        "max_claims": 20,
        "max_evidence_per_claim": 4,
        "max_quote_chars": 4000,
        "max_atomizer_input_tokens": 100000,
    }
    assert distill["verification"] == {
        "max_context_tokens_per_claim": 4000,
        "max_total_input_tokens": 50000,
        "max_rationale_chars": 1000,
    }


def test_config_has_required_sources():
    priority = config.bib().get("source_priority", [])
    for src in ("human", "openalex", "crossref", "arxiv", "pdf", "llm", "derived", "unknown"):
        assert src in priority, f"Missing source: {src}"


def test_config_regexes_compile():
    errors = config.validate()
    regex_errors = [e for e in errors if "invalid regex" in e]
    assert not regex_errors, f"Regex errors: {regex_errors}"


def test_opencode_validation_requires_opencode_not_openai_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    errors = config.validate()
    assert any("OpenCode executable" in error for error in errors)
    assert not any("OPENAI_" in error for error in errors)


def test_bridge_backend_has_actionable_migration_error(monkeypatch):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded, "llm": {**loaded["llm"], "backend": "bridge"},
    })
    errors = config.validate()
    assert any("change it to 'opencode'" in error and "models.bridge" in error for error in errors)


def test_bridge_model_namespace_has_actionable_migration_error(monkeypatch):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "models": {**loaded["models"], "bridge": loaded["models"]["opencode"]},
    })
    errors = config.validate()
    assert any("models.bridge" in error and "models.opencode" in error for error in errors)


@pytest.mark.parametrize("model", ["model", "/model", "provider/", " provider/model", "provider/ model"])
def test_opencode_models_require_strict_provider_model(monkeypatch, model):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "models": {
            **loaded["models"],
            "opencode": {**loaded["models"]["opencode"], "distill": model},
        },
    })
    assert any("models.opencode.distill" in error for error in config.validate())


def test_llm_cache_identity_includes_protocol():
    assert config.llm_cache_identity() == {
        "backend": "opencode", "llm_protocol_version": "oneshot-v1",
    }


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


def test_validation_requires_claim_verify_model(monkeypatch):
    loaded = config.load()
    backend = config.llm_backend()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "models": {
            **loaded["models"],
            backend: {**loaded["models"][backend], "claim_verify": " "},
        },
    })
    assert any(
        f"models.{backend}.claim_verify" in error for error in config.validate()
    )


@pytest.mark.parametrize("mode", ["", "quote", None, True])
def test_validation_rejects_invalid_default_evidence_mode(monkeypatch, mode):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "distill": {**loaded["distill"], "default_evidence_mode": mode},
    })
    assert any("default_evidence_mode" in error for error in config.validate())


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2", None])
def test_validation_rejects_non_positive_integer_distill_limits(monkeypatch, value):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "distill": {
            **loaded["distill"],
            "claims": {**loaded["distill"]["claims"], "max_claims": value},
        },
    })
    assert any("distill.claims.max_claims" in error for error in config.validate())


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2", None])
def test_validation_rejects_invalid_max_input_tokens(monkeypatch, value):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "distill": {**loaded["distill"], "max_input_tokens": value},
    })
    assert any("distill.max_input_tokens" in error for error in config.validate())


@pytest.mark.parametrize(("group", "key"), [
    ("claims", "max_claims"),
    ("claims", "max_evidence_per_claim"),
    ("claims", "max_quote_chars"),
    ("claims", "max_atomizer_input_tokens"),
    ("verification", "max_context_tokens_per_claim"),
    ("verification", "max_total_input_tokens"),
    ("verification", "max_rationale_chars"),
])
def test_validation_covers_every_distill_limit(monkeypatch, group, key):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "distill": {
            **loaded["distill"],
            group: {**loaded["distill"][group], key: 0},
        },
    })
    assert any(f"distill.{group}.{key}" in error for error in config.validate())


@pytest.mark.parametrize("extractor", ["remote", "", None])
def test_validation_rejects_unknown_md_extractor(monkeypatch, extractor):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "md": {**loaded["md"], "extractor": extractor},
    })
    errors = config.validate()
    assert any("md.extractor" in error for error in errors)


@pytest.mark.parametrize("key,value", [
    ("base_url", "mineru.local"),
    ("timeout_s", 0),
    ("poll_interval_s", -1),
])
def test_validation_rejects_invalid_md_service_settings(monkeypatch, key, value):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "md": {
            **loaded["md"],
            "service": {**loaded["md"]["service"], key: value},
        },
    })
    errors = config.validate()
    assert any(f"md.service.{key}" in error for error in errors)


def test_md_cache_identity_normalizes_service_url(monkeypatch):
    monkeypatch.setattr(config, "md", lambda: {
        "extractor": "service",
        "service": {"base_url": "http://mineru.test/"},
    })
    assert config.md_cache_identity() == {
        "extractor": "service",
        "service_url": "http://mineru.test",
    }


def test_local_md_extractor_does_not_require_service_settings(monkeypatch):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {
        **loaded,
        "md": {"extractor": "local", "mineru_version": "test"},
    })
    errors = config.validate()
    assert not any(error.startswith("md.service") for error in errors)


def test_validation_reports_non_mapping_md_config(monkeypatch):
    loaded = config.load()
    monkeypatch.setattr(config, "load", lambda: {**loaded, "md": None})
    errors = config.validate()
    assert "md must be a mapping" in errors


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
