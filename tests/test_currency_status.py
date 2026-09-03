"""Acceptance coverage for shared four-state artifact currency checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puba.state import mark_distill_complete, mark_stage_complete, stage_status, distill_status


@pytest.fixture
def paper(tmp_path: Path) -> tuple[Path, Path]:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ad = tmp_path / "paper.puba"
    (ad / "analyses").mkdir(parents=True)
    return pdf, ad


def test_md_currency_all_four_states(paper):
    pdf, ad = paper
    assert stage_status(ad, pdf, "md", "v1") == "never-run"

    mark_stage_complete(ad, pdf, "md", "v1")
    assert stage_status(ad, pdf, "md", "v1") == "invalid"  # state points at absent output

    (ad / "paper.md").write_text("# paper", encoding="utf-8")
    (ad / "paper.sections.json").write_text("not json", encoding="utf-8")
    assert stage_status(ad, pdf, "md", "v1") == "invalid"

    (ad / "paper.sections.json").write_text("[]", encoding="utf-8")
    assert stage_status(ad, pdf, "md", "v2") == "stale"
    assert stage_status(ad, pdf, "md", "v1") == "current"


@pytest.mark.parametrize("partial_artifact", ["paper.md", "paper.sections.json"])
def test_md_partially_present_without_state_is_invalid(paper, partial_artifact):
    """A composite output is not never-run once either required file exists."""
    pdf, ad = paper
    (ad / partial_artifact).write_text("# paper" if partial_artifact.endswith(".md") else "[]", encoding="utf-8")
    assert stage_status(ad, pdf, "md", "v1") == "invalid"


def test_distill_currency_all_four_states(paper):
    pdf, ad = paper
    args = (ad, pdf, "summary", "input", "prompt", "model", "instruction")
    assert distill_status(*args) == "never-run"

    mark_distill_complete(*args)
    assert distill_status(*args) == "invalid"

    path = ad / "analyses" / "summary.json"
    path.write_text("{bad", encoding="utf-8")
    assert distill_status(*args) == "invalid"

    path.write_text(json.dumps({"name": "summary", "output": "ok"}), encoding="utf-8")
    assert distill_status(ad, pdf, "summary", "input", "prompt", "model", "changed") == "stale"
    assert distill_status(*args) == "current"


def test_effective_instruction_hash_is_canonical_and_invalidates_max_chars(paper):
    from puba.distill.queries import DistillQuery
    from puba.distill.run import effective_instruction_payload, effective_instruction_sha

    q = DistillQuery("q", "abstract", "Prompt", 100, None, None, "test")
    q_changed = DistillQuery("q", "abstract", "Prompt", 200, None, None, "test")
    assert effective_instruction_sha(q) != effective_instruction_sha(q_changed)
    assert list(effective_instruction_payload(q)) == ["evidence", "instruction_version", "max_chars"]


def test_effective_instruction_version_change_invalidates_cache(monkeypatch, paper):
    from puba.distill.queries import DistillQuery
    import puba.distill.run as run

    q = DistillQuery("q", "abstract", "Prompt", None, None, None, "test")
    before = run.effective_instruction_sha(q)
    monkeypatch.setattr(run, "INSTRUCTION_VERSION", "distill-v2")
    assert before != run.effective_instruction_sha(q)


def test_invalidly_encoded_state_is_treated_as_unusable(paper):
    pdf, ad = paper
    (ad / ".state.json").write_bytes(b"\xff\xfe")
    assert stage_status(ad, pdf, "md", "v1") == "never-run"
