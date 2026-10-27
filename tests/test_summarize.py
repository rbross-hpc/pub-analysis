# BSD 3-Clause License
"""Offline coverage for the deterministic ``puba summarize`` vault."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from puba.cli import app
from puba.summarize import _aggregate_distill_status

runner = CliRunner()


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "never-run"),
        (["never-run"], "never-run"),
        (["current", "current"], "current"),
        (["stale", "stale"], "stale"),
        (["current", "never-run"], "mixed"),
        (["current", "invalid"], "mixed"),
    ],
)
def test_aggregate_distill_status_reports_mixed_states(statuses, expected):
    records = [{"status": status} for status in statuses]
    assert _aggregate_distill_status(records) == expected


def _paper(tmp_path: Path) -> tuple[Path, Path]:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 summary test")
    ad = tmp_path / "paper.puba"
    (ad / "analyses").mkdir(parents=True)
    return pdf, ad


def _vault(pdf: Path) -> Path:
    return pdf.parent / f"{pdf.stem}.vault"


def _read(pdf: Path, relative: str = "README.md") -> str:
    return (_vault(pdf) / relative).read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict:
    return yaml.safe_load(text.split("---\n", 2)[1])


def _assert_relative_links_resolve(vault: Path) -> None:
    pattern = re.compile(r"!?\[(?:\\.|[^]\\])*]\(([^)]+)\)")
    for note in vault.rglob("*.md"):
        for target in pattern.findall(note.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            assert (note.parent / path).resolve().exists(), f"broken link in {note}: {target}"


def test_summarize_builds_linked_self_contained_vault(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    (ad / "bib.json").write_text(json.dumps({
        "title": "A Test Paper",
        "authors": [{
            "name": "Ada Lovelace",
            "orcid": "0000-0000-0000-0001",
            "affiliations": [{"name": "Analytical Engine Institute"}],
        }],
        "year": 1843,
        "venue": "Notes",
        "doi": "10.1000/test",
        "abstract": "A readable abstract.",
        "keywords": "engines; computation",
        "needs_review": True,
    }), encoding="utf-8")
    (ad / "paper.md").write_text("# Paper", encoding="utf-8")
    (ad / "paper.sections.json").write_text("[]", encoding="utf-8")
    (ad / "figures").mkdir()
    (ad / "figures" / "figure.jpg").write_bytes(b"jpg bytes")
    (ad / "paper.figures.json").write_text(json.dumps({"figures": [{
        "id": "fig-1", "caption": "An important chart", "page": 3,
        "type": "chart", "jpg": "figures/figure.jpg",
    }]}), encoding="utf-8")
    (ad / "analyses" / "summary.json").write_text(json.dumps({
        "name": "summary", "scope": "narrative", "model": "test-model",
        "prompt": "Explain the\ncontribution.",
        "output": "It contributes\na test.", "evidence_status": "partial",
        "evidence": [{"quote": "contributes", "status": "unverified", "reason": "no_match"}],
    }), encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    vault = _vault(pdf)
    assert "paper.vault" in result.output
    assert "README" in result.output
    assert (vault / "assets" / "paper.pdf").read_bytes() == pdf.read_bytes()
    assert (vault / "assets" / "figures" / "fig-1.jpg").read_bytes() == b"jpg bytes"
    readme = _read(pdf)
    assert _frontmatter(readme)["type"] == "puba-summary"
    assert "A Test Paper" in readme
    assert "### Prompt" in readme
    assert "Explain the contribution." in readme
    assert "It contributes a test." in readme
    assert "Explain the\ncontribution." not in readme
    assert "It contributes\na test." not in readme
    assert readme.index("Explain the contribution.") < readme.index("It contributes a test.")
    assert "test-model" not in readme
    assert "Bibliographic record has needs_review: true." in readme
    bibliography = _read(pdf, "bibliography.md")
    assert "Ada Lovelace" in bibliography
    assert "Analytical Engine Institute" in bibliography
    assert "A readable abstract." in bibliography
    figures = _read(pdf, "figures.md")
    assert "![fig-1](assets/figures/fig-1.jpg)" in figures
    assert "An important chart" in figures
    detail = _read(pdf, "distillations/summary.md")
    assert "## Prompt" in detail
    assert "## Answer" in detail
    assert "It contributes a test." in detail
    assert "Explain the contribution." in detail
    assert "test-model" in detail
    assert "**Text processed:** narrative" in detail
    assert _frontmatter(detail)["scope"] == "narrative"
    assert detail.index("test-model") < detail.index("## Prompt") < detail.index("## Answer")
    assert "<details>" not in detail
    assert '"quote": "contributes"' in detail
    _assert_relative_links_resolve(vault)


def test_summarize_nothing_run_yet_succeeds(tmp_path: Path):
    pdf, _ = _paper(tmp_path)

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    readme = _read(pdf)
    for stage in ("bib", "md", "figures", "distill"):
        assert f"| {stage} | never-run |" in readme
    assert "## Summary" in readme
    assert "This distillation has not been run." in readme
    assert "Summarize this paper in 3 sentences" in readme
    summary_detail = _read(pdf, "distillations/summary.md")
    assert "This distillation has not been run." in summary_detail
    assert "**Text processed:** abstract" in summary_detail
    assert "No bibliographic record exists yet." in _read(pdf, "bibliography.md")
    assert "No figures have been extracted." in _read(pdf, "figures.md")


def test_summarize_flags_invalid_artifacts_and_missing_figure(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    (ad / "bib.json").write_text("[]", encoding="utf-8")
    (ad / "paper.figures.json").write_text(json.dumps({"figures": [{
        "id": "gone", "caption": "Missing image", "jpg": "figures/gone.jpg",
    }]}), encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    readme = _read(pdf)
    assert "| bib | invalid |" in readme
    assert "Figure 'gone' image is missing" in readme
    assert "bibliographic record exists but is invalid" in _read(pdf, "bibliography.md")
    assert "Image file is missing" in _read(pdf, "figures.md")


def test_summarize_renders_claims_in_query_note_and_answer_in_readme(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    (ad / "analyses" / "claims.json").write_text(json.dumps({
        "schema_version": 2,
        "name": "claims",
        "model": "generator",
        "verifier_model": "verifier",
        "prompt": "Assess claims.",
        "output": "Answer.",
        "evidence_mode": "claims",
        "claims": [
            {
                "id": "c1", "claim": "First claim.",
                "evidence": [{
                    "id": "c1e1", "quote": "First quote.", "quote_status": "present",
                    "offset": 42, "section": "Results", "page": 7,
                }],
                "support_assessment": {
                    "status": "supported", "rationale": "Directly stated.",
                    "evidence_ids": ["c1e1"],
                },
            },
            {
                "id": "c2", "claim": "Second claim.",
                "evidence": [{
                    "id": "c2e1", "quote": "Missing quote.", "quote_status": "not_found",
                }],
                "support_assessment": {
                    "status": "not_assessed", "rationale": "No uniquely present evidence.",
                    "evidence_ids": [],
                },
            },
        ],
        "quote_presence_status": "partial",
        "quote_presence_counts": {"total": 2, "present": 1, "not_found": 1, "ambiguous": 0},
        "claim_support_status": "partial",
        "claim_support_counts": {
            "total": 2, "supported": 1, "partially_supported": 0, "unsupported": 0,
            "contradicted": 0, "insufficient_evidence": 0, "not_assessed": 1,
        },
        "_provenance": {"calls": {"verification": {"model": "verifier"}}},
    }), encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    readme = _read(pdf)
    assert "Answer." in readme
    assert "model-assessed claim_support_status: partial" in readme
    detail = _read(pdf, "distillations/claims.md")
    for value in (
        "First claim.", "Second claim.", "Directly stated.",
        "No uniquely present evidence.", "First quote.", "Missing quote.",
    ):
        assert value in detail
    assert "section Results, page 7, offset 42" in detail
    assert "Model-assessment linkage:** not linked by assessment" in detail
    assert _frontmatter(detail)["claim_support_status"] == "partial"


def test_summarize_renders_effective_answer_human_reviews_and_removed_claims(tmp_path: Path):
    from puba.distill.review import apply_packet, artifact_digest, claim_fingerprint, write_review

    pdf, ad = _paper(tmp_path)
    record = {
        "schema_version": 2, "name": "impact", "scope": "narrative",
        "model": "generator", "verifier_model": "verifier", "prompt": "Assess impact.",
        "generated_at": "2026-10-27T00:00:00+00:00",
        "output": "Generated answer with two claims.", "evidence_mode": "claims",
        "claims": [
            {"id": "c1", "claim": "First claim.",
             "evidence": [{"id": "c1e1", "quote": "First quote.",
                           "quote_status": "present", "offset": 1}],
             "support_assessment": {"status": "partially_supported",
                                    "rationale": "Partial.", "evidence_ids": ["c1e1"]}},
            {"id": "c2", "claim": "Duplicate claim.",
             "evidence": [{"id": "c2e1", "quote": "First quote.",
                           "quote_status": "present", "offset": 1}],
             "support_assessment": {"status": "supported",
                                    "rationale": "Supported.", "evidence_ids": ["c2e1"]}},
        ],
        "quote_presence_status": "complete",
        "quote_presence_counts": {"total": 2, "present": 2, "not_found": 0, "ambiguous": 0},
        "claim_support_status": "partial",
        "claim_support_counts": {"total": 2, "supported": 1, "partially_supported": 1,
                                 "unsupported": 0, "contradicted": 0,
                                 "insufficient_evidence": 0, "not_assessed": 0},
    }
    (ad / "analyses" / "impact.json").write_text(json.dumps(record), encoding="utf-8")
    first, second = [claim_fingerprint(claim) for claim in record["claims"]]
    review = apply_packet(record, {
        "base_artifact_sha256": artifact_digest(record), "reviewer": "Author",
        "claim_reviews": [
            {"claim_fingerprint": first, "decision": "correct", "status": "supported",
             "rationale": "Narrowed claim is supported.", "evidence_ids": ["c1e1"]},
            {"claim_fingerprint": second, "decision": "remove",
             "removal_reason": "duplicate", "rationale": "Duplicates c1.",
             "duplicate_of": first},
        ],
        "answer_override": {"value": "Human-edited answer.", "no_new_claims": True,
                            "removed_claim_fingerprints": [], "reason": "Clearer wording."},
    })
    write_review(ad, "impact", review)

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    readme = _read(pdf)
    assert "Human-edited answer." in readme
    assert "Generated answer with two claims." not in readme
    assert "Human review:** complete (0 accepted, 1 corrected, 1 removed)" in readme
    detail = _read(pdf, "distillations/impact.md")
    assert "## Effective Answer (Human-edited)" in detail
    assert "## Original Generated Answer" in detail
    assert "Generated answer with two claims." in detail
    assert "**Human review:** corrected to supported" in detail
    assert "## Removed Claims" in detail
    assert "**Removal reason:** duplicate" in detail
    assert _frontmatter(detail)["effective_output_source"] == "human_override"


def test_summarize_flags_stale_review_and_does_not_apply_override(tmp_path: Path):
    from puba.distill.review import apply_packet, artifact_digest, write_review

    pdf, ad = _paper(tmp_path)
    record = {"schema_version": 2, "name": "plain", "scope": "abstract",
              "evidence_mode": "none", "model": "generator",
              "generated_at": "2026-10-27T00:00:00+00:00", "prompt": "Explain.",
              "output": "Generated answer."}
    path = ad / "analyses" / "plain.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    review = apply_packet(record, {
        "base_artifact_sha256": artifact_digest(record),
        "answer_override": {"value": "Human answer.", "no_new_claims": True,
                            "removed_claim_fingerprints": [], "reason": "Clarity."},
    })
    write_review(ad, "plain", review)
    record["output"] = "New generated answer."
    path.write_text(json.dumps(record), encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    assert "has a stale human review" in _read(pdf)
    detail = _read(pdf, "distillations/plain.md")
    assert "New generated answer." in detail
    assert "Human answer." not in detail
    assert "- **Status:** stale" in detail


def test_summarize_marks_invalid_review_in_frontmatter_and_detail(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    record = {"schema_version": 2, "name": "plain", "scope": "abstract",
              "evidence_mode": "none", "model": "generator",
              "generated_at": "2026-10-27T00:00:00+00:00", "prompt": "Explain.",
              "output": "Generated answer."}
    (ad / "analyses" / "plain.json").write_text(json.dumps(record), encoding="utf-8")
    (ad / "reviews").mkdir()
    (ad / "reviews" / "plain.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    detail = _read(pdf, "distillations/plain.md")
    assert _frontmatter(detail)["human_review_status"] == "invalid"
    assert "## Human Review" in detail
    assert "- **Status:** invalid" in detail
    assert "has an invalid human review" in _read(pdf)


def test_summarize_uses_safe_unique_note_names(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    for name in ("A B", "a-b", "README", "!!!", "review](missing"):
        (ad / "analyses" / f"{name}.json").write_text(
            json.dumps({"name": name, "output": f"answer {name}"}), encoding="utf-8",
        )

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    notes = sorted(path.name for path in (_vault(pdf) / "distillations").iterdir())
    assert len(notes) == 6
    assert len({name.lower() for name in notes}) == 6
    assert "readme.md" not in {name.lower() for name in notes}
    readme = _read(pdf)
    assert "Review\\](Missing" in readme
    assert readme.index("  - [Summary]") < readme.index("  - [A B]")
    assert readme.index("## Summary") < readme.index("## A B")
    _assert_relative_links_resolve(_vault(pdf))


def test_regeneration_preserves_owned_paths_and_removes_other_content(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    record = ad / "analyses" / "old.json"
    record.write_text(json.dumps({"output": "old answer"}), encoding="utf-8")
    first = runner.invoke(app, ["summarize", str(pdf)])
    assert first.exit_code == 0, first.output
    vault = _vault(pdf)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (vault / "notes").mkdir()
    (vault / "notes" / "mine.md").write_text("Keep me", encoding="utf-8")
    (vault / "unknown.md").write_text("discard me", encoding="utf-8")
    assert (vault / "distillations" / "old.md").exists()
    record.unlink()

    second = runner.invoke(app, ["summarize", str(pdf)])

    assert second.exit_code == 0, second.output
    assert (vault / ".obsidian" / "app.json").read_text(encoding="utf-8") == "{}"
    assert (vault / "notes" / "mine.md").read_text(encoding="utf-8") == "Keep me"
    assert not (vault / "unknown.md").exists()
    assert not (vault / "distillations" / "old.md").exists()


def test_regeneration_preserves_symlinked_owned_paths(tmp_path: Path):
    pdf, _ = _paper(tmp_path)
    vault = _vault(pdf)
    vault.mkdir()
    external = tmp_path / "obsidian-settings"
    external.mkdir()
    (external / "app.json").write_text("{}", encoding="utf-8")
    (vault / ".obsidian").symlink_to(external, target_is_directory=True)

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    assert (vault / ".obsidian").is_symlink()
    assert (vault / ".obsidian").resolve() == external.resolve()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_summarize_refuses_non_directory_vault_target(tmp_path: Path, kind: str):
    pdf, _ = _paper(tmp_path)
    target = _vault(pdf)
    if kind == "file":
        target.write_text("personal data", encoding="utf-8")
    else:
        destination = tmp_path / "other-vault"
        destination.mkdir()
        target.symlink_to(destination, target_is_directory=True)

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 1
    assert "not a directory" in result.output
    if kind == "file":
        assert target.read_text(encoding="utf-8") == "personal data"
    else:
        assert target.is_symlink()


def test_summarize_does_not_copy_figure_outside_analysis_directory(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    private = tmp_path / "private.jpg"
    private.write_bytes(b"private")
    (ad / "paper.figures.json").write_text(json.dumps({"figures": [{
        "id": "external", "jpg": str(private),
    }]}), encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    assert "outside the analysis directory" in _read(pdf)
    assert "outside the analysis directory" in _read(pdf, "figures.md")
    assert not any((_vault(pdf) / "assets" / "figures").iterdir())


def test_summary_vault_is_deterministic_except_timestamp(tmp_path: Path, monkeypatch):
    from puba import summarize

    pdf, _ = _paper(tmp_path)
    monkeypatch.setattr(summarize, "now_iso", lambda: "2026-09-03T00:00:00+00:00")
    first = runner.invoke(app, ["summarize", str(pdf)])
    assert first.exit_code == 0, first.output
    vault = _vault(pdf)
    before = {
        path.relative_to(vault): path.read_bytes()
        for path in vault.rglob("*") if path.is_file()
    }

    second = runner.invoke(app, ["summarize", str(pdf)])
    after = {
        path.relative_to(vault): path.read_bytes()
        for path in vault.rglob("*") if path.is_file()
    }

    assert second.exit_code == 0, second.output
    assert before == after


def test_summary_publication_failure_preserves_previous_vault(tmp_path: Path, monkeypatch):
    from puba import summarize

    pdf, _ = _paper(tmp_path)
    vault = _vault(pdf)
    vault.mkdir()
    (vault / "marker.txt").write_text("previous", encoding="utf-8")
    monkeypatch.setattr(
        summarize,
        "replace_directory",
        lambda staged, target: (_ for _ in ()).throw(OSError("publish failed")),
    )

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 1
    assert "publish failed" in result.output
    assert (vault / "marker.txt").read_text(encoding="utf-8") == "previous"


@pytest.mark.parametrize("marker", ["type", "artifact_type"])
def test_summarize_migrates_only_recognized_legacy_summary(tmp_path: Path, marker: str):
    pdf, _ = _paper(tmp_path)
    legacy = pdf.with_name("paper.summary.md")
    legacy.write_text(f"---\n{marker}: puba-summary\n---\nold", encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    assert not legacy.exists()

    legacy.write_text("personal notes", encoding="utf-8")
    with pytest.warns(UserWarning, match="left untouched"):
        result = runner.invoke(app, ["summarize", str(pdf)])
    assert result.exit_code == 0, result.output
    assert legacy.read_text(encoding="utf-8") == "personal notes"


def test_summarize_tolerates_legacy_values_and_malformed_config(
    tmp_path: Path, monkeypatch,
):
    from puba import config

    pdf, ad = _paper(tmp_path)
    (ad / "bib.yaml").write_text(
        "title: YAML date\nprovenance:\n  observed: 2026-09-03\n", encoding="utf-8",
    )
    (ad / "analyses" / "legacy.yaml").write_text(
        "model: older-model\noutput: legacy answer\nevidence_status: partial\n"
        "evidence:\n  - quote: quoted text\n    status: unverified\n"
        "    reason: no_match\n    observed: 2026-09-03\n",
        encoding="utf-8",
    )
    (tmp_path / "puba.config.yaml").write_text("models: [unterminated", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config.load.cache_clear()

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    assert "2026-09-03" in _read(pdf, "bibliography.md")
    detail = _read(pdf, "distillations/legacy.md")
    assert "legacy answer" in detail
    assert '"observed": "2026-09-03"' in detail
    assert "not recorded (generated before prompt persistence was added)" in detail
    assert "missing persisted prompt text" in _read(pdf)
    config.load.cache_clear()


def test_summarize_flags_json_and_yaml_forms_present(tmp_path: Path):
    pdf, ad = _paper(tmp_path)
    (ad / "bib.json").write_text(json.dumps({"title": "JSON wins"}), encoding="utf-8")
    (ad / "bib.yaml").write_text("title: YAML loses\n", encoding="utf-8")
    (ad / "analyses" / "summary.json").write_text(
        json.dumps({"output": "json answer"}), encoding="utf-8",
    )
    (ad / "analyses" / "summary.yaml").write_text("output: yaml loses\n", encoding="utf-8")

    result = runner.invoke(app, ["summarize", str(pdf)])

    assert result.exit_code == 0, result.output
    readme = _read(pdf)
    assert "Both bib.json and legacy bib.yaml are present" in readme
    assert "Both analyses/summary.json and legacy analyses/summary.yaml are present" in readme
    assert "JSON wins" in readme
    assert "json answer" in readme
    assert "yaml loses" not in readme
