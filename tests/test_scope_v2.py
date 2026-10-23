# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Focused tests for canonical scope metadata and boundaries."""
from __future__ import annotations

import hashlib
import json

from puba.distill.scope import ScopeInput, build_input, build_scope_input


def test_abstract_keeps_literal_canonical_value_separate_from_model_header(tmp_path):
    abstract = "  Literal abstract with surrounding space.  "
    result = build_scope_input("abstract", {"title": "Header title", "abstract": abstract}, tmp_path)

    assert isinstance(result, ScopeInput)
    assert result.canonical_source == abstract
    assert result.allowed_span == (0, len(abstract))
    assert "Header title" in result.model_content
    assert result.sections == []
    assert result.source_hashes["canonical_source_sha256"] == hashlib.sha256(abstract.encode()).hexdigest()


def test_full_exposes_all_raw_markdown_and_sections(tmp_path):
    md = "<!-- page 1 -->\n# Intro\n\nBody.\n"
    sections = [{"short_name": "intro", "title": "Intro", "start_offset": 16, "end_offset": len(md)}]
    (tmp_path / "paper.md").write_text(md, encoding="utf-8")
    sections_raw = json.dumps(sections)
    (tmp_path / "paper.sections.json").write_text(sections_raw, encoding="utf-8")

    result = build_scope_input("full", {"title": "Paper"}, tmp_path)

    assert result.canonical_source == md
    assert result.allowed_span == (0, len(md))
    assert result.sections == sections
    assert result.model_content.endswith(md)
    assert result.source_hashes["sections_sha256"] == hashlib.sha256(sections_raw.encode()).hexdigest()


def test_section_boundary_is_exact_raw_span_while_model_removes_marker(tmp_path):
    md = "Before.\n## Methods\n<!-- page 3 -->\nExact body.\n## Results\nAfter."
    start = md.index("## Methods")
    end = md.index("## Results")
    sections = [{"short_name": "methods", "title": "Methods", "start_offset": start, "end_offset": end}]
    (tmp_path / "paper.md").write_text(md, encoding="utf-8")
    (tmp_path / "paper.sections.json").write_text(json.dumps(sections), encoding="utf-8")

    result = build_scope_input("section", {"title": "Paper"}, tmp_path, "methods")

    assert result.canonical_source[result.allowed_start:result.allowed_end] == md[start:end]
    assert "<!-- page 3 -->" not in result.model_content
    assert "Exact body." in result.model_content
    assert "Before." not in result.model_content
    assert "After." not in result.model_content


def test_narrative_ends_at_heading_start_and_compatibility_wrapper_survives(tmp_path, monkeypatch):
    md = "<!-- page 1 -->\n# Intro\n\nBody.\n\n## References:\n\nCitation.\n"
    (tmp_path / "paper.md").write_text(md, encoding="utf-8")
    monkeypatch.setattr("puba.distill.scope.cfg.md_distill_strip_sections", lambda: ["references"])

    result = build_scope_input("narrative", {"title": "Paper"}, tmp_path)
    heading_start = md.index("## References")

    assert result.allowed_span == (0, heading_start)
    assert result.canonical_source == md
    assert result.canonical_source[:result.allowed_end] == md[:heading_start]
    assert "Citation." not in result.model_content
    assert "<!-- page 1 -->" not in result.model_content
    old_content, old_hash = build_input("narrative", {"title": "Paper"}, tmp_path)
    assert old_content == result.model_content
    assert old_hash == result.source_hashes["paper_md_sha256"][:12]
