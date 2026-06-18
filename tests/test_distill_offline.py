# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for distillation: query loading, scope building, max_chars, cache."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from puba.distill.queries import DistillQuery, load_queries, validate_queries
from puba.distill.run import _post_process, _build_prompt


# ---------------------------------------------------------------------------
# Query loading from config
# ---------------------------------------------------------------------------

def test_load_queries_returns_summary_by_default():
    queries = load_queries()
    assert "summary" in queries


def test_summary_query_has_required_fields():
    queries = load_queries()
    q = queries["summary"]
    assert q.scope == "abstract"
    assert q.prompt.strip()
    assert q.max_chars == 600


def test_load_queries_from_prompts_dir(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "my_query.yaml").write_text(
        "my_query:\n  scope: narrative\n  prompt: |\n    Summarize.\n  max_chars: 300\n",
        encoding="utf-8",
    )
    queries = load_queries(cwd=tmp_path)
    assert "my_query" in queries
    q = queries["my_query"]
    assert q.scope == "narrative"
    assert q.max_chars == 300
    assert "prompts/my_query.yaml" in q.source


def test_prompts_dir_query_overrides_config(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "override.yaml").write_text(
        "summary:\n  scope: full\n  prompt: |\n    Override prompt.\n",
        encoding="utf-8",
    )
    queries = load_queries(cwd=tmp_path)
    assert queries["summary"].scope == "full"


def test_multiple_queries_in_one_prompts_file(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "suite.yaml").write_text(
        "alpha:\n  scope: abstract\n  prompt: |\n    Alpha.\n"
        "beta:\n  scope: narrative\n  prompt: |\n    Beta.\n",
        encoding="utf-8",
    )
    queries = load_queries(cwd=tmp_path)
    assert "alpha" in queries
    assert "beta" in queries


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_bad_name():
    q = DistillQuery(name="bad name!", scope="abstract", prompt="x", max_chars=None, model=None, section=None, source="test")
    errors = validate_queries({"bad name!": q})
    assert any("name must match" in e for e in errors)


def test_validate_bad_scope():
    q = DistillQuery(name="q", scope="unknown", prompt="x", max_chars=None, model=None, section=None, source="test")
    errors = validate_queries({"q": q})
    assert any("scope" in e for e in errors)


def test_validate_empty_prompt():
    q = DistillQuery(name="q", scope="abstract", prompt="   ", max_chars=None, model=None, section=None, source="test")
    errors = validate_queries({"q": q})
    assert any("empty" in e for e in errors)


def test_validate_max_chars_zero():
    q = DistillQuery(name="q", scope="abstract", prompt="x", max_chars=0, model=None, section=None, source="test")
    errors = validate_queries({"q": q})
    assert any("positive" in e for e in errors)


def test_validate_max_chars_small_warns():
    q = DistillQuery(name="q", scope="abstract", prompt="x", max_chars=50, model=None, section=None, source="test")
    errors = validate_queries({"q": q})
    assert any("very small" in e for e in errors)


def test_validate_valid_query_no_errors():
    q = DistillQuery(name="my_query", scope="abstract", prompt="Summarize.", max_chars=600, model=None, section=None, source="config.yaml")
    errors = validate_queries({"my_query": q})
    assert not errors


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def test_build_prompt_includes_max_chars_instruction():
    q = DistillQuery(name="q", scope="abstract", prompt="Summarize.", max_chars=300, model=None, section=None, source="test")
    prompt = _build_prompt(q, "Some content.")
    assert "300 characters" in prompt
    assert "Summarize." in prompt
    assert "Some content." in prompt


def test_build_prompt_no_max_chars_no_instruction():
    q = DistillQuery(name="q", scope="abstract", prompt="Summarize.", max_chars=None, model=None, section=None, source="test")
    prompt = _build_prompt(q, "Content.")
    assert "characters" not in prompt.lower()


def test_build_prompt_content_appended():
    q = DistillQuery(name="q", scope="abstract", prompt="My prompt.", max_chars=None, model=None, section=None, source="test")
    prompt = _build_prompt(q, "Paper content here.")
    assert "Paper content here." in prompt


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def test_post_process_strips_trailing_whitespace():
    text = "Line one   \nLine two  "
    result, truncated, _ = _post_process(text, None)
    assert not any(line != line.rstrip() for line in result.split("\n"))
    assert not truncated


def test_post_process_strips_leading_trailing_blank_lines():
    text = "\n\n  Hello world.  \n\n"
    result, _, _ = _post_process(text, None)
    assert result == "Hello world."


def test_post_process_no_truncation_under_limit():
    text = "Short text."
    result, truncated, original = _post_process(text, 1000)
    assert result == text
    assert not truncated
    assert original is None


def test_post_process_truncates_at_word_boundary():
    text = "This is a somewhat long sentence that needs truncating."
    result, truncated, original = _post_process(text, 20)
    assert truncated
    assert len(result) <= 22
    assert result.endswith("…")
    assert original == len(text)


def test_post_process_no_max_chars_no_truncation():
    long_text = "word " * 1000
    result, truncated, _ = _post_process(long_text, None)
    assert not truncated


# ---------------------------------------------------------------------------
# Scope building (offline — no network, no LLM)
# ---------------------------------------------------------------------------

def test_scope_abstract_requires_abstract_field(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "Test", "abstract": ""}
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    with pytest.raises(RuntimeError, match="abstract"):
        build_input("abstract", bib, ad)


def test_scope_narrative_requires_paper_md(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "Test", "abstract": "Some abstract."}
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    with pytest.raises(RuntimeError, match="paper.md"):
        build_input("narrative", bib, ad)


def test_scope_abstract_returns_content_with_header(tmp_path):
    from puba.distill.scope import build_input
    bib = {
        "title": "My Paper",
        "authors": ["Alice Smith", "Bob Jones"],
        "venue": "My Journal",
        "year": 2025,
        "abstract": "This is the abstract.",
    }
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    content, sha = build_input("abstract", bib, ad)
    assert "My Paper" in content
    assert "Alice Smith" in content
    assert "This is the abstract." in content
    assert sha is None


def test_scope_narrative_strips_references(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "T", "authors": ["A"], "year": 2025, "abstract": "x"}
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    paper_md = ad / "paper.md"
    paper_md.write_text(
        "# Title\n\n## Introduction\n\nBody text.\n\n## References\n\n[1] Smith 2020.\n",
        encoding="utf-8",
    )
    content, sha = build_input("narrative", bib, ad)
    assert "Body text." in content
    assert "[1] Smith 2020." not in content
    assert sha is not None


def test_scope_full_includes_references(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "T", "authors": ["A"], "year": 2025, "abstract": "x"}
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    paper_md = ad / "paper.md"
    paper_md.write_text(
        "# Title\n\n## Introduction\n\nBody.\n\n## References\n\n[1] Smith.\n",
        encoding="utf-8",
    )
    content, sha = build_input("full", bib, ad)
    assert "[1] Smith." in content


# ---------------------------------------------------------------------------
# scope: section
# ---------------------------------------------------------------------------

def _make_sections_json(tmp_path, entries):
    import json
    ad = tmp_path / "paper.puba"
    ad.mkdir(exist_ok=True)
    (ad / "paper.sections.json").write_text(
        json.dumps(entries), encoding="utf-8"
    )
    return ad


def test_scope_section_requires_section_field(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "T", "abstract": "x"}
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    (ad / "paper.md").write_text("body", encoding="utf-8")
    (ad / "paper.sections.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="section"):
        build_input("section", bib, ad, section_name=None)


def test_scope_section_error_when_not_found(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "T", "abstract": "x"}
    md_text = "Some body text here about methods and results."
    ad = _make_sections_json(tmp_path, [
        {"short_name": "introduction", "title": "Introduction",
         "level": 1, "start_offset": 0, "end_offset": 10},
        {"short_name": "methods", "title": "Methods",
         "level": 1, "start_offset": 10, "end_offset": len(md_text)},
    ])
    (ad / "paper.md").write_text(md_text, encoding="utf-8")
    with pytest.raises(RuntimeError, match="not found"):
        build_input("section", bib, ad, section_name="results")


def test_scope_section_error_lists_available(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "T", "abstract": "x"}
    md_text = "Some body text."
    ad = _make_sections_json(tmp_path, [
        {"short_name": "introduction", "title": "Introduction",
         "level": 1, "start_offset": 0, "end_offset": len(md_text)},
    ])
    (ad / "paper.md").write_text(md_text, encoding="utf-8")
    try:
        build_input("section", bib, ad, section_name="methods")
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "introduction" in str(e)


def test_scope_section_extracts_correct_body(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "T", "abstract": "x"}
    md_text = "Introduction text here. " + "Methods body content. " + "Results here."
    intro_end = len("Introduction text here. ")
    methods_end = intro_end + len("Methods body content. ")
    ad = _make_sections_json(tmp_path, [
        {"short_name": "introduction", "title": "Introduction",
         "level": 1, "start_offset": 0, "end_offset": intro_end},
        {"short_name": "methods", "title": "Methods",
         "level": 1, "start_offset": intro_end, "end_offset": methods_end},
        {"short_name": "results", "title": "Results",
         "level": 1, "start_offset": methods_end, "end_offset": len(md_text)},
    ])
    (ad / "paper.md").write_text(md_text, encoding="utf-8")
    content, sha = build_input("section", bib, ad, section_name="methods")
    assert "Methods body content." in content
    assert "Introduction text here." not in content
    assert "Results here." not in content
    assert sha is not None


def test_scope_section_strips_page_markers(tmp_path):
    from puba.distill.scope import build_input
    bib = {"title": "T", "abstract": "x"}
    md_text = "Body text.\n<!-- page 3 -->\nMore body text."
    ad = _make_sections_json(tmp_path, [
        {"short_name": "methods", "title": "Methods",
         "level": 1, "start_offset": 0, "end_offset": len(md_text)},
    ])
    (ad / "paper.md").write_text(md_text, encoding="utf-8")
    content, _ = build_input("section", bib, ad, section_name="methods")
    assert "<!-- page" not in content
    assert "Body text." in content
    assert "More body text." in content


def test_validate_section_scope_missing_field():
    from puba.distill.queries import DistillQuery, validate_queries
    q = DistillQuery(
        name="my_query", scope="section", prompt="Summarize.",
        max_chars=None, model=None, section=None, source="test"
    )
    errors = validate_queries({"my_query": q})
    assert any("section" in e and "requires" in e for e in errors)


def test_validate_section_scope_bad_name():
    from puba.distill.queries import DistillQuery, validate_queries
    q = DistillQuery(
        name="my_query", scope="section", prompt="Summarize.",
        max_chars=None, model=None, section="bad name!", source="test"
    )
    errors = validate_queries({"my_query": q})
    assert any("section" in e for e in errors)


def test_validate_section_scope_valid():
    from puba.distill.queries import DistillQuery, validate_queries
    q = DistillQuery(
        name="my_query", scope="section", prompt="Summarize.",
        max_chars=None, model=None, section="methods", source="test"
    )
    errors = validate_queries({"my_query": q})
    assert not errors
