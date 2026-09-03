# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for distillation: query loading, scope building, max_chars, cache."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from tenacity import wait_none

from puba.distill.queries import DistillQuery, load_queries, validate_queries
from puba.distill.run import _post_process, _build_prompt, _resolve_model, effective_instruction_payload, effective_instruction_sha


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


def test_load_queries_parses_evidence_flag(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "evidence.yaml").write_text(
        "supported:\n  scope: abstract\n  prompt: Support.\n  evidence: true\n",
        encoding="utf-8",
    )
    assert load_queries(cwd=tmp_path)["supported"].evidence is True


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


def test_validate_evidence_requires_boolean():
    q = DistillQuery(name="q", scope="abstract", prompt="x", max_chars=None, model=None, section=None, source="test", evidence="true")  # type: ignore[arg-type]
    assert any("evidence must be a boolean" in error for error in validate_queries({"q": q}))


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


# ---------------------------------------------------------------------------
# Per-prompt model selection
# ---------------------------------------------------------------------------

def _make_query(model: str | None = None) -> DistillQuery:
    return DistillQuery(
        name="q", scope="abstract", prompt="Summarize.",
        max_chars=None, model=model, section=None, source="test"
    )


def test_resolve_model_uses_override_first():
    q = _make_query(model="Claude Sonnet 4.6")
    assert _resolve_model(q, model_override="GPT-5.5") == "GPT-5.5"


def test_resolve_model_uses_per_query_model():
    q = _make_query(model="Claude Opus 4.7")
    assert _resolve_model(q) == "Claude Opus 4.7"


def test_resolve_model_falls_back_to_config():
    q = _make_query(model=None)
    from puba import config as cfg
    expected = cfg.models().get("distill", "GPT-5.4")
    assert _resolve_model(q) == expected


def test_run_query_passes_model_override_to_llm(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    puba_dir = tmp_path / "paper.puba"
    puba_dir.mkdir()
    import yaml as _yaml
    (puba_dir / "bib.yaml").write_text(
        _yaml.dump({"title": "T", "authors": ["A"], "year": 2026,
                    "abstract": "Abstract text.", "needs_review": False}),
        encoding="utf-8",
    )
    (puba_dir / "paper.md").write_text("# T\n\n## Abstract\n\nAbstract text.\n", encoding="utf-8")
    (puba_dir / "paper.sections.json").write_text(
        '[{"title":"Abstract","short_name":"abstract","level":1,"start_offset":0,"end_offset":40}]',
        encoding="utf-8",
    )

    q = _make_query(model=None)
    captured: list[str] = []

    def fake_chat_text(system, user, model=None, model_role="distill", temperature=0):
        captured.append(model)
        return "Summary text."

    with patch("puba.distill.run.openai_client.chat_text", side_effect=fake_chat_text), \
         patch("puba.state.mark_distill_complete"):
        from puba.distill.run import run_query
        run_query(pdf, q, force=True, model_override="GPT-5.5")

    assert captured == ["GPT-5.5"]
    import json
    record = json.loads((puba_dir / "analyses" / "q.json").read_text(encoding="utf-8"))
    assert record["schema_version"] == 1
    assert record["prompt"] == "Summarize."
    assert record["instruction_version"] == "distill-v1"
    assert not (puba_dir / "analyses" / "q.yaml").exists()


def test_cli_bib_model_flag_passes_to_resolve(tmp_path):
    from typer.testing import CliRunner
    from puba.cli import app

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    captured: list[str | None] = []

    def fake_resolve(pdf_path, force, no_llm, bibtex_file, model):
        captured.append(model)
        puba_dir = pdf_path.parent / f"{pdf_path.stem}.puba"
        puba_dir.mkdir(exist_ok=True)
        import yaml as _yaml
        bib_path = puba_dir / "bib.yaml"
        bib_path.write_text(_yaml.dump({"title": "T", "needs_review": False}), encoding="utf-8")
        return bib_path, False

    runner = CliRunner()
    with patch("puba.bib.stub.resolve", side_effect=fake_resolve):
        result = runner.invoke(app, ["bib", str(pdf), "--model", "Claude Opus 4.7"])

    assert result.exit_code == 0
    assert captured == ["Claude Opus 4.7"]


def _make_cached_abstract_paper(tmp_path: Path) -> Path:
    """Create the minimal unchanged input needed to exercise run_query caching."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    (ad / "bib.yaml").write_text(yaml.dump({
        "title": "T", "authors": ["A"], "year": 2026,
        "abstract": "Unchanged abstract.", "needs_review": False,
    }), encoding="utf-8")
    return pdf


def test_effective_instruction_payload_reflects_evidence_request():
    plain = DistillQuery("summary", "abstract", "Prompt", None, None, None, "test")
    evidence = DistillQuery("summary", "abstract", "Prompt", None, None, None, "test", evidence=True)
    assert effective_instruction_payload(plain)["evidence"]["requested"] is False
    assert effective_instruction_payload(evidence)["evidence"]["requested"] is True
    assert effective_instruction_sha(plain) != effective_instruction_sha(evidence)


def test_run_query_reuses_unchanged_cache_and_misses_when_max_chars_changes(tmp_path, monkeypatch):
    """Changing only max_chars must invalidate the cache written by run_query."""
    import puba.distill.run as run

    pdf = _make_cached_abstract_paper(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: calls.append("call") or "Answer")
    original = DistillQuery("summary", "abstract", "Prompt", 100, None, None, "test")
    changed = DistillQuery("summary", "abstract", "Prompt", 200, None, None, "test")

    assert run.run_query(pdf, original)["status"] == "distilled"
    assert run.run_query(pdf, original)["status"] == "cached"
    assert run.run_query(pdf, changed)["status"] == "distilled"
    assert calls == ["call", "call"]


def test_run_query_misses_when_instruction_version_changes(tmp_path, monkeypatch):
    """Changing only the instruction version invalidates an otherwise identical run."""
    import puba.distill.run as run

    pdf = _make_cached_abstract_paper(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: calls.append("call") or "Answer")
    query = DistillQuery("summary", "abstract", "Prompt", None, None, None, "test")

    assert run.run_query(pdf, query)["status"] == "distilled"
    assert run.run_query(pdf, query)["status"] == "cached"
    monkeypatch.setattr(run, "INSTRUCTION_VERSION", "distill-v2")
    assert run.run_query(pdf, query)["status"] == "distilled"
    assert calls == ["call", "call"]


@pytest.mark.parametrize("evidence_status", ["partial"])
def test_cli_distill_cached_non_verified_evidence_warns_interactively_and_is_structured_in_json(tmp_path, evidence_status):
    from typer.testing import CliRunner
    from puba.cli import app

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    query = DistillQuery("supported", "abstract", "Prompt", None, None, None, "test", evidence=True)
    cached_result = {
        "status": "cached", "query": "supported", "evidence_status": evidence_status,
    }
    runner = CliRunner()

    with patch("puba.distill.queries.load_queries", return_value={"supported": query}), \
         patch("puba.distill.run.run_query", return_value=cached_result):
        interactive = runner.invoke(app, ["distill", str(pdf)])
        as_json = runner.invoke(app, ["distill", str(pdf), "--json"])

    assert interactive.exit_code == 0
    assert "cached" in interactive.output
    assert f"evidence {evidence_status}; some or all quotes unverified" in interactive.output
    json_output = json.loads(as_json.output)
    assert as_json.exit_code == 0
    assert json_output["ok"] is True
    assert json_output["results"][0]["evidence_status"] == evidence_status


def test_cli_distill_non_verified_evidence_warns_interactively_and_is_structured_in_json(tmp_path):
    """Fresh outcomes retain the same warning contract as cached outcomes."""
    from typer.testing import CliRunner
    from puba.cli import app

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    query = DistillQuery("supported", "abstract", "Prompt", None, None, None, "test", evidence=True)
    partial_result = {
        "status": "distilled", "query": "supported", "chars": 6,
        "truncated": False, "evidence_status": "partial",
    }
    runner = CliRunner()

    with patch("puba.distill.queries.load_queries", return_value={"supported": query}), \
         patch("puba.distill.run.run_query", return_value=partial_result):
        interactive = runner.invoke(app, ["distill", str(pdf)])
        as_json = runner.invoke(app, ["distill", str(pdf), "--json"])

    assert interactive.exit_code == 0
    assert "evidence partial; some or all quotes unverified" in interactive.output
    json_output = json.loads(as_json.output)
    assert as_json.exit_code == 0
    assert json_output["ok"] is True
    assert json_output["results"][0]["evidence_status"] == "partial"


def test_cli_distill_model_flag_passes_to_run_query(tmp_path):
    from typer.testing import CliRunner
    from puba.cli import app
    import yaml as _yaml

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    puba_dir = tmp_path / "paper.puba"
    puba_dir.mkdir()
    (puba_dir / "bib.yaml").write_text(
        _yaml.dump({"title": "T", "abstract": "Abstract.", "needs_review": False}),
        encoding="utf-8",
    )

    captured: list[str | None] = []

    def fake_run_query(pdf_path, query, force, model_override):
        captured.append(model_override)
        return {"status": "distilled", "chars": 10, "truncated": False}

    runner = CliRunner()
    with patch("puba.distill.run.run_query", side_effect=fake_run_query):
        result = runner.invoke(app, ["distill", str(pdf), "--model", "Gemini 2.5 Pro"])

    assert captured and captured[0] == "Gemini 2.5 Pro"


# ---------------------------------------------------------------------------
# Evidence-enabled run wiring
# ---------------------------------------------------------------------------

def _make_evidence_paper(tmp_path: Path, md_text: str | None = None) -> Path:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    abstract = "Canonical abstract finding."
    (ad / "bib.yaml").write_text(yaml.dump({
        "title": "T", "authors": ["A"], "year": 2026,
        "abstract": abstract, "needs_review": False,
    }), encoding="utf-8")
    if md_text is not None:
        (ad / "paper.md").write_text(md_text, encoding="utf-8")
        (ad / "paper.sections.json").write_text(json.dumps([{
            "short_name": "methods", "title": "Methods", "level": 1,
            "start_offset": md_text.index("Methods"), "end_offset": len(md_text),
        }]), encoding="utf-8")
    return pdf


def test_evidence_run_uses_json_and_persists_verified_abstract(tmp_path, monkeypatch):
    import json
    import puba.distill.run as run

    pdf = _make_evidence_paper(tmp_path)
    query = DistillQuery("supported", "abstract", "Prompt", 7, None, None, "test", evidence=True)
    captured = {}
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **kwargs: captured.update(kwargs) or {
        "answer": "A long answer", "evidence": [{"quote": "Canonical"}],
    })
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: pytest.fail("plain client called"))

    result = run.run_query(pdf, query)
    record = json.loads((tmp_path / "paper.puba" / "analyses" / "supported.json").read_text())
    assert result["evidence_status"] == "verified"
    assert captured["model_role"] == "distill"
    assert "Return only a JSON object" in captured["system"]
    assert record["instruction_version"] == "distill-evidence-v1"
    assert record["_provenance"]["instruction_version"] == "distill-evidence-v1"
    assert record["output"] == "A long…"
    assert record["evidence"] == [{"quote": "Canonical", "status": "verified", "offset": 0, "section": None, "page": None}]
    assert "answer string MUST be at most 7 characters" in captured["user"]
    assert "does not apply to the JSON structure or evidence quote strings" in captured["user"]
    assert "Your response MUST" not in captured["user"]


def test_evidence_run_persists_partial_empty_and_section_span_results(tmp_path, monkeypatch):
    import json
    import puba.distill.run as run

    md_text = "<!-- page 2 -->\nIntroduction outside.\n## Methods\nMethods exact."
    pdf = _make_evidence_paper(tmp_path, md_text)
    query = DistillQuery("supported", "section", "Prompt", None, None, "methods", "test", evidence=True)
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **_: {
        "answer": "Answer", "evidence": [
            {"quote": "Introduction outside."}, {"quote": "Methods exact."},
        ],
    })
    assert run.run_query(pdf, query)["evidence_status"] == "partial"
    record = json.loads((tmp_path / "paper.puba" / "analyses" / "supported.json").read_text())
    assert record["evidence"][0] == {"quote": "Introduction outside.", "status": "unverified", "reason": "no_match"}
    assert record["evidence"][1]["offset"] == md_text.index("Methods exact.")
    assert record["evidence"][1]["page"] == 2

    empty = DistillQuery("empty", "abstract", "Prompt", None, None, None, "test", evidence=True)
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **_: {"answer": "Answer", "evidence": []})
    assert run.run_query(pdf, empty)["evidence_status"] == "partial"
    empty_record = json.loads((tmp_path / "paper.puba" / "analyses" / "empty.json").read_text())
    assert empty_record["evidence"] == []
    assert empty_record["evidence_status"] == "partial"


def _llm_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = content
    return response


@pytest.mark.parametrize("invalid_response", [
    {"evidence": []},
    {"answer": 1, "evidence": []},
    {"answer": "bad", "evidence": "not a list"},
    {"answer": "bad", "evidence": [{}]},
    {"answer": "bad", "evidence": [{"quote": "   "}]},
])
def test_evidence_schema_invalid_responses_are_retried(tmp_path, monkeypatch, invalid_response):
    """Schema validation happens within chat_json's retry-decorated call."""
    import puba.distill.run as run

    pdf = _make_evidence_paper(tmp_path)
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _llm_response(json.dumps(invalid_response)),
        _llm_response('{"answer": "Answer", "evidence": []}'),
    ]
    monkeypatch.setattr(run.openai_client, "_client", lambda: client)
    retry_without_delay = getattr(run.openai_client.chat_json, "retry_with")(wait=wait_none())
    monkeypatch.setattr(run.openai_client, "chat_json", retry_without_delay)

    result = run.run_query(
        pdf, DistillQuery("supported", "abstract", "Prompt", None, None, None, "test", evidence=True),
    )

    assert result["status"] == "distilled"
    assert result["evidence_status"] == "partial"
    assert client.chat.completions.create.call_count == 2


def test_malformed_evidence_json_preserves_prior_artifact_and_state(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _make_evidence_paper(tmp_path)
    ad = tmp_path / "paper.puba"
    analyses = ad / "analyses"
    analyses.mkdir()
    artifact = analyses / "supported.json"
    artifact.write_text('{"output": "prior"}', encoding="utf-8")
    state = ad / ".state.json"
    state.write_text('{"prior": true}', encoding="utf-8")
    client = MagicMock()
    client.chat.completions.create.side_effect = [_llm_response("not JSON")] * 3
    monkeypatch.setattr(run.openai_client, "_client", lambda: client)
    retry_without_delay = getattr(run.openai_client.chat_json, "retry_with")(wait=wait_none())
    monkeypatch.setattr(run.openai_client, "chat_json", retry_without_delay)

    result = run.run_query(
        pdf, DistillQuery("supported", "abstract", "Prompt", None, None, None, "test", evidence=True), force=True,
    )

    assert result["status"] == "error"
    assert client.chat.completions.create.call_count == 3
    assert artifact.read_text(encoding="utf-8") == '{"output": "prior"}'
    assert state.read_text(encoding="utf-8") == '{"prior": true}'


def test_evidence_cache_misses_when_raw_canonical_source_changes_without_prompt_input_change(tmp_path, monkeypatch):
    import puba.distill.run as run

    md_text = "<!-- page 2 -->\n## Methods\nMethods exact."
    pdf = _make_evidence_paper(tmp_path, md_text)
    query = DistillQuery("supported", "narrative", "Prompt", None, None, None, "test", evidence=True)
    calls: list[str] = []
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **_: calls.append("json") or {
        "answer": "Answer", "evidence": [{"quote": "Methods exact."}],
    })

    assert run.run_query(pdf, query)["status"] == "distilled"
    assert run.run_query(pdf, query)["status"] == "cached"
    # Narrative input strips page markers, but evidence offsets/pages address raw
    # paper.md; this raw-only change must therefore invalidate its cache.
    ad = tmp_path / "paper.puba"
    (ad / "paper.md").write_text("<!-- page 8 -->\n## Methods\nMethods exact.", encoding="utf-8")
    assert run.run_query(pdf, query)["status"] == "distilled"
    assert calls == ["json", "json"]


def test_evidence_cache_misses_when_sections_sidecar_changes(tmp_path, monkeypatch):
    import puba.distill.run as run

    md_text = "<!-- page 2 -->\n## Methods\nMethods exact."
    pdf = _make_evidence_paper(tmp_path, md_text)
    query = DistillQuery("supported", "narrative", "Prompt", None, None, None, "test", evidence=True)
    calls: list[str] = []
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **_: calls.append("json") or {
        "answer": "Answer", "evidence": [{"quote": "Methods exact."}],
    })

    assert run.run_query(pdf, query)["status"] == "distilled"
    assert run.run_query(pdf, query)["status"] == "cached"
    ad = tmp_path / "paper.puba"
    sections_path = ad / "paper.sections.json"
    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    sections[0]["title"] = "Renamed Methods"
    sections_path.write_text(json.dumps(sections), encoding="utf-8")
    assert run.run_query(pdf, query)["status"] == "distilled"
    assert calls == ["json", "json"]


def test_cached_evidence_run_returns_persisted_partial_status(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _make_evidence_paper(tmp_path)
    query = DistillQuery("supported", "abstract", "Prompt", None, None, None, "test", evidence=True)
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **_: {
        "answer": "Answer", "evidence": [],
    })

    assert run.run_query(pdf, query)["evidence_status"] == "partial"
    cached = run.run_query(pdf, query)
    assert cached == {"status": "cached", "query": "supported", "evidence_status": "partial"}


def test_evidence_toggle_invalidates_cache_and_plain_record_has_no_evidence_fields(tmp_path, monkeypatch):
    import json
    import puba.distill.run as run

    pdf = _make_evidence_paper(tmp_path)
    plain = DistillQuery("summary", "abstract", "Prompt", None, None, None, "test")
    evidence = DistillQuery("summary", "abstract", "Prompt", None, None, None, "test", evidence=True)
    calls = []
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: calls.append("text") or "Answer")
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **_: calls.append("json") or {"answer": "Answer", "evidence": []})

    assert run.run_query(pdf, plain)["status"] == "distilled"
    plain_record = json.loads((tmp_path / "paper.puba" / "analyses" / "summary.json").read_text())
    assert "evidence" not in plain_record and "evidence_status" not in plain_record
    assert run.run_query(pdf, evidence)["status"] == "distilled"
    assert calls == ["text", "json"]
