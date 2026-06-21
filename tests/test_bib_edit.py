# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Tests for puba bib edit, puba show bib --writable, and sidecar.apply_patch."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from puba.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bib(tmp_path: Path, extra: dict | None = None) -> tuple[Path, Path]:
    """Create paper.pdf + paper.puba/bib.yaml with a cached bib state entry."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    ad = tmp_path / "paper.puba"
    ad.mkdir()

    bib: dict = {
        "title": "Original Title",
        "authors": ["Alice Smith", "Bob Jones"],
        "year": 2025,
        "venue": "Journal of Testing",
        "category": "journal article",
        "doi": "10.1234/test.2025",
        "arxiv_id": None,
        "osti_id": None,
        "url": None,
        "abstract": "This is the abstract.",
        "keywords": None,
        "language": "en",
        "license": None,
        "oa_status": None,
        "references_count": None,
        "pages": None,
        "bibtex_key": None,
        "isbn": None,
        "issn": None,
        "publication_date": None,
        "venue_short": None,
        "needs_review": False,
        "notes": "",
        "_provenance": {
            "title": {"source": "openalex", "lookup_key": "doi", "at": "2026-01-01"},
            "doi": {"source": "pdf", "lookup_key": "regex", "at": "2026-01-01"},
        },
        "_lookup_log": {},
        "_meta": {
            "schema_version": 1,
            "tool_version": "0.1.0",
            "prompt_version": "bib-1",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "pdf_sha256": "deadbeef",
        },
    }
    if extra:
        bib.update(extra)
    (ad / "bib.yaml").write_text(yaml.dump(bib, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return pdf, ad


def _load_bib_raw(ad: Path) -> dict:
    return yaml.safe_load((ad / "bib.yaml").read_text(encoding="utf-8")) or {}


def _load_state(ad: Path) -> dict:
    p = ad / ".state.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _seed_state(ad: Path, pdf: Path) -> None:
    from puba.state import mark_stage_complete
    from puba import config as _cfg
    prompt_version = _cfg.prompt_versions().get("bib_extract", "bib-1")
    mark_stage_complete(ad, pdf, "bib", prompt_version)


# ---------------------------------------------------------------------------
# sidecar.apply_patch unit tests
# ---------------------------------------------------------------------------

class TestApplyPatch:

    def test_basic_field_update(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        result = apply_patch(ad, pdf, {"title": "Corrected Title"}, source="human")
        raw = _load_bib_raw(ad)
        assert raw["title"] == "Corrected Title"
        assert raw["_provenance"]["title"]["source"] == "human"
        assert raw["_provenance"]["title"]["previous"] == "Original Title"
        assert "title" in result["fields_changed"]
        assert result["cleared_review"] is False

    def test_tool_source(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        apply_patch(ad, pdf, {"title": "Agent Title"}, source="tool:my-agent")
        raw = _load_bib_raw(ad)
        assert raw["_provenance"]["title"]["source"] == "tool:my-agent"

    def test_note_recorded_in_provenance(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        apply_patch(ad, pdf, {"title": "Fixed"}, source="human", note="truncated in OpenAlex")
        raw = _load_bib_raw(ad)
        assert raw["_provenance"]["title"]["note"] == "truncated in OpenAlex"

    def test_edit_log_appended(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        apply_patch(ad, pdf, {"title": "First"}, source="human", note="first edit")
        apply_patch(ad, pdf, {"year": 2024}, source="tool:agent")
        raw = _load_bib_raw(ad)
        log = raw.get("_edit_log", [])
        assert len(log) == 2
        assert log[0]["fields_changed"] == ["title"]
        assert log[0]["source"] == "human"
        assert log[1]["fields_changed"] == ["year"]
        assert log[1]["source"] == "tool:agent"

    def test_meta_generated_at_bumped(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        original_at = _load_bib_raw(ad)["_meta"]["generated_at"]
        apply_patch(ad, pdf, {"title": "X"}, source="human")
        new_at = _load_bib_raw(ad)["_meta"]["generated_at"]
        assert new_at != original_at

    def test_meta_other_fields_preserved(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        apply_patch(ad, pdf, {"title": "X"}, source="human")
        raw = _load_bib_raw(ad)
        assert raw["_meta"]["tool_version"] == "0.1.0"
        assert raw["_meta"]["prompt_version"] == "bib-1"
        assert raw["_meta"]["pdf_sha256"] == "deadbeef"

    def test_null_deletes_field_and_provenance(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        apply_patch(ad, pdf, {"title": None}, source="human")
        raw = _load_bib_raw(ad)
        assert raw.get("title") is None
        assert "title" not in (raw.get("_provenance") or {})

    def test_clear_review(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path, extra={
            "needs_review": True,
            "_review_reasons": ["title missing"],
        })
        result = apply_patch(ad, pdf, {}, source="human", clear_review=True)
        raw = _load_bib_raw(ad)
        assert raw["needs_review"] is False
        assert "_review_reasons" not in raw or not raw["_review_reasons"]
        assert result["cleared_review"] is True
        assert raw["_edit_log"][-1]["cleared_review"] is True

    def test_clear_review_combined_with_field_patch(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path, extra={"needs_review": True, "_review_reasons": ["year missing"]})
        apply_patch(ad, pdf, {"year": 2024}, source="human", clear_review=True)
        raw = _load_bib_raw(ad)
        assert raw["year"] == 2024
        assert raw["needs_review"] is False

    def test_needs_review_via_patch(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path, extra={"needs_review": True})
        apply_patch(ad, pdf, {"needs_review": False}, source="human")
        raw = _load_bib_raw(ad)
        assert raw["needs_review"] is False

    def test_invalid_source_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="Invalid source"):
            apply_patch(ad, pdf, {"title": "X"}, source="weird")

    def test_underscore_key_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="underscore"):
            apply_patch(ad, pdf, {"_provenance": {}}, source="human")

    def test_unknown_field_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="Unknown field"):
            apply_patch(ad, pdf, {"nonexistent_field": "x"}, source="human")

    def test_bad_year_type_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="year"):
            apply_patch(ad, pdf, {"year": "twentyfour"}, source="human")

    def test_bad_category_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="category"):
            apply_patch(ad, pdf, {"category": "not a real category"}, source="human")

    def test_bad_doi_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="doi"):
            apply_patch(ad, pdf, {"doi": "not-a-doi"}, source="human")

    def test_bad_arxiv_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="arxiv_id"):
            apply_patch(ad, pdf, {"arxiv_id": "notanid"}, source="human")

    def test_bad_authors_type_raises(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        with pytest.raises(ValueError, match="authors"):
            apply_patch(ad, pdf, {"authors": "not a list"}, source="human")


# ---------------------------------------------------------------------------
# sidecar: priority and sticky for tool:* sources
# ---------------------------------------------------------------------------

class TestToolSourcePriority:

    def test_tool_source_priority_is_100(self):
        from puba.sidecar import priority
        assert priority("tool:my-agent") == 100
        assert priority("tool:agent.v1-2") == 100

    def test_tool_source_is_sticky(self):
        from puba.sidecar import set_field, is_sticky_source
        assert is_sticky_source("tool:anything")
        assert is_sticky_source("human")
        assert not is_sticky_source("openalex")

    def test_tool_pinned_field_not_overwritten_by_openalex(self):
        from puba.sidecar import set_field
        fields: dict = {}
        prov: dict = {}
        set_field(fields, prov, "title", "Tool Title", "tool:agent")
        set_field(fields, prov, "title", "OpenAlex Title", "openalex")
        assert fields["title"] == "Tool Title"
        assert prov["title"]["source"] == "tool:agent"

    def test_human_pinned_field_not_overwritten_by_tool(self):
        from puba.sidecar import set_field
        fields: dict = {}
        prov: dict = {}
        set_field(fields, prov, "title", "Human Title", "human")
        set_field(fields, prov, "title", "Tool Title", "tool:agent")
        assert fields["title"] == "Human Title"


# ---------------------------------------------------------------------------
# CLI: puba bib edit
# ---------------------------------------------------------------------------

class TestBibEditCli:

    def test_edit_sets_field_json(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=New Title",
            "--json",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert "title" in data["fields_changed"]
        raw = _load_bib_raw(ad)
        assert raw["title"] == "New Title"

    def test_edit_source_tool(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=Agent Title",
            "--source", "tool:agent-x",
            "--json",
        ])
        raw = _load_bib_raw(ad)
        assert raw["_provenance"]["title"]["source"] == "tool:agent-x"

    def test_edit_invalid_source_rejected(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=X",
            "--source", "bad source",
            "--json",
        ])
        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["ok"] is False

    def test_edit_unknown_field_rejected(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "bogus_field=x",
            "--json",
        ])
        assert result.exit_code == 2

    def test_edit_underscore_key_rejected(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        patch = {"_provenance": {}}
        patch_file = tmp_path / "patch.json"
        patch_file.write_text(json.dumps(patch), encoding="utf-8")
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--json-file", str(patch_file),
            "--json",
        ])
        assert result.exit_code == 2

    def test_edit_bad_type_rejected(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "year=notanint",
            "--json",
        ])
        assert result.exit_code == 2

    def test_edit_json_file(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        patch = {"title": "From File", "year": 2024}
        patch_file = tmp_path / "patch.json"
        patch_file.write_text(json.dumps(patch), encoding="utf-8")
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--json-file", str(patch_file),
            "--json",
        ])
        assert result.exit_code == 0
        raw = _load_bib_raw(ad)
        assert raw["title"] == "From File"
        assert raw["year"] == 2024

    def test_edit_json_file_stdin(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        patch = json.dumps({"title": "Stdin Title"})
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--json-file", "-",
            "--json",
        ], input=patch)
        assert result.exit_code == 0
        raw = _load_bib_raw(ad)
        assert raw["title"] == "Stdin Title"

    def test_edit_null_deletes_field(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        patch = {"doi": None}
        patch_file = tmp_path / "patch.json"
        patch_file.write_text(json.dumps(patch), encoding="utf-8")
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--json-file", str(patch_file),
            "--json",
        ])
        assert result.exit_code == 0
        raw = _load_bib_raw(ad)
        assert raw.get("doi") is None
        assert "doi" not in (raw.get("_provenance") or {})

    def test_edit_set_null_literal(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "doi=null",
            "--json",
        ])
        assert result.exit_code == 0
        raw = _load_bib_raw(ad)
        assert raw.get("doi") is None

    def test_edit_clear_review(self, tmp_path):
        pdf, ad = _make_bib(tmp_path, extra={
            "needs_review": True,
            "_review_reasons": ["title missing"],
        })
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--clear-review",
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cleared_review"] is True
        raw = _load_bib_raw(ad)
        assert raw["needs_review"] is False

    def test_edit_dry_run_no_write(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        original_bib = (ad / "bib.yaml").read_text(encoding="utf-8")
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=Dry Title",
            "--dry-run",
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert (ad / "bib.yaml").read_text(encoding="utf-8") == original_bib

    def test_edit_dry_run_shows_diff(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=Dry Title",
            "--dry-run",
            "--json",
        ])
        data = json.loads(result.output)
        assert "diff" in data
        assert data["diff"]["title"]["before"] == "Original Title"
        assert data["diff"]["title"]["after"] == "Dry Title"

    def test_edit_cache_untouched(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        state_before = _load_state(ad)
        runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=X",
            "--json",
        ])
        state_after = _load_state(ad)
        assert state_before.get("stages", {}).get("bib") == state_after.get("stages", {}).get("bib")

    def test_edit_without_resolved_bib_errors(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=X",
            "--json",
        ])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["error_type"] in ("CacheError", "FileNotFoundError")

    def test_edit_json_file_and_set_mutually_exclusive(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        patch_file = tmp_path / "patch.json"
        patch_file.write_text('{"title":"x"}', encoding="utf-8")
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--json-file", str(patch_file),
            "--set", "year=2024",
            "--json",
        ])
        assert result.exit_code == 2

    def test_edit_set_int_parsed(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "year=2024",
        ])
        raw = _load_bib_raw(ad)
        assert raw["year"] == 2024
        assert isinstance(raw["year"], int)

    def test_edit_set_bool_parsed(self, tmp_path):
        pdf, ad = _make_bib(tmp_path, extra={"needs_review": True})
        _seed_state(ad, pdf)
        runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "needs_review=false",
        ])
        raw = _load_bib_raw(ad)
        assert raw["needs_review"] is False

    def test_edit_sticky_survives_puba_bib_force(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=Pinned Title",
            "--json",
        ])
        assert result.exit_code == 0, result.output
        from puba.sidecar import load_bib, set_field
        fields, prov = load_bib(ad)
        assert prov["title"]["source"] == "human"
        updated = set_field(fields, prov, "title", "OpenAlex Override", "openalex")
        assert updated is False
        assert fields["title"] == "Pinned Title"


# ---------------------------------------------------------------------------
# puba bib (producer) still works after sub-app restructure
# ---------------------------------------------------------------------------

class TestBibSubAppRouting:

    def test_bib_producer_routes_correctly(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        captured = []

        def fake_resolve(pdf_path, force, no_llm, bibtex_file, model):
            captured.append(pdf_path)
            ad = pdf_path.parent / "paper.puba"
            ad.mkdir(exist_ok=True)
            bib_p = ad / "bib.yaml"
            bib_p.write_text(yaml.dump({"title": "T", "needs_review": False}), encoding="utf-8")
            return bib_p, False

        with patch("puba.bib.stub.resolve", side_effect=fake_resolve):
            result = runner.invoke(app, ["bib", str(pdf)])

        assert result.exit_code == 0
        assert len(captured) == 1

    def test_bib_edit_routes_correctly(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=Via Edit",
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "bib.edit"

    def test_bib_no_args_shows_help_or_error(self, tmp_path):
        result = runner.invoke(app, ["bib"])
        assert result.exit_code != 0 or "Missing" in result.output or "Error" in result.output


# ---------------------------------------------------------------------------
# puba show bib --writable
# ---------------------------------------------------------------------------

class TestShowBibWritable:

    def test_writable_emits_fields_only(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "bib", str(pdf), "--writable"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "title" in data
        assert "ok" not in data
        assert "_provenance" not in data
        assert "needs_review" not in data

    def test_writable_round_trips_to_edit(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        with patch("puba.state.is_stage_current", return_value=True):
            show_result = runner.invoke(app, ["show", "bib", str(pdf), "--writable"])
        assert show_result.exit_code == 0
        fields_json = show_result.output
        edit_result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--json-file", "-",
            "--json",
        ], input=fields_json)
        assert edit_result.exit_code == 0

    def test_writable_mutually_exclusive_with_verbose(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        result = runner.invoke(app, ["show", "bib", str(pdf), "--writable", "--verbose"])
        assert result.exit_code == 2

    def test_writable_implies_json(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "bib", str(pdf), "--writable"])
        assert result.exit_code == 0
        json.loads(result.output)


# ---------------------------------------------------------------------------
# Skip-unchanged and --restamp
# ---------------------------------------------------------------------------

class TestSkipUnchanged:

    def test_unchanged_field_is_noop(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        before = (ad / "bib.yaml").read_text(encoding="utf-8")
        result = apply_patch(ad, pdf, {"title": "Original Title"}, source="human")
        assert result["fields_changed"] == []
        assert result["cleared_review"] is False
        assert (ad / "bib.yaml").read_text(encoding="utf-8") == before

    def test_unchanged_field_no_edit_log_entry(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        apply_patch(ad, pdf, {"title": "Original Title"}, source="human")
        raw = _load_bib_raw(ad)
        assert "_edit_log" not in raw

    def test_unchanged_field_no_provenance_change(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        apply_patch(ad, pdf, {"title": "Original Title"}, source="human")
        raw = _load_bib_raw(ad)
        assert raw["_provenance"]["title"]["source"] == "openalex"

    def test_meta_generated_at_unchanged_on_noop(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        original_at = _load_bib_raw(ad)["_meta"]["generated_at"]
        apply_patch(ad, pdf, {"title": "Original Title"}, source="human")
        assert _load_bib_raw(ad)["_meta"]["generated_at"] == original_at

    def test_explicit_null_for_absent_field_is_noop(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        before = (ad / "bib.yaml").read_text(encoding="utf-8")
        result = apply_patch(ad, pdf, {"arxiv_id": None}, source="human")
        assert result["fields_changed"] == []
        assert (ad / "bib.yaml").read_text(encoding="utf-8") == before

    def test_explicit_null_for_present_field_is_change(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        result = apply_patch(ad, pdf, {"title": None}, source="human")
        assert "title" in result["fields_changed"]

    def test_changed_field_in_round_trip_only_stamps_that_field(self, tmp_path):
        from puba.sidecar import apply_patch, _load_raw
        pdf, ad = _make_bib(tmp_path)
        raw = _load_raw(ad)
        writable = {k: v for k, v in raw.items()
                    if not k.startswith("_") and k not in ("needs_review", "notes")}
        writable["title"] = "New Title"
        result = apply_patch(ad, pdf, writable, source="human")
        assert result["fields_changed"] == ["title"]
        raw2 = _load_bib_raw(ad)
        assert raw2["_provenance"]["title"]["source"] == "human"
        assert raw2["_provenance"]["doi"]["source"] == "pdf"

    def test_clear_review_is_change_when_flagged(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path, extra={"needs_review": True,
                                              "_review_reasons": ["title missing"]})
        result = apply_patch(ad, pdf, {}, source="human", clear_review=True)
        assert result["cleared_review"] is True
        raw = _load_bib_raw(ad)
        assert "_edit_log" in raw

    def test_clear_review_already_false_is_noop(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        before = (ad / "bib.yaml").read_text(encoding="utf-8")
        result = apply_patch(ad, pdf, {}, source="human", clear_review=True)
        assert result["fields_changed"] == []
        assert result["cleared_review"] is False
        assert (ad / "bib.yaml").read_text(encoding="utf-8") == before

    def test_restamp_forces_stamp_on_unchanged_value(self, tmp_path):
        from puba.sidecar import apply_patch
        pdf, ad = _make_bib(tmp_path)
        result = apply_patch(ad, pdf, {"title": "Original Title"}, source="human",
                             restamp=True)
        assert "title" in result["fields_changed"]
        raw = _load_bib_raw(ad)
        assert raw["_provenance"]["title"]["source"] == "human"
        assert "_edit_log" in raw

    def test_writable_round_trip_is_noop(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        before = (ad / "bib.yaml").read_text(encoding="utf-8")
        with patch("puba.state.is_stage_current", return_value=True):
            show_result = runner.invoke(app, ["show", "bib", str(pdf), "--writable"])
        assert show_result.exit_code == 0
        edit_result = runner.invoke(app, [
            "bib", "edit", str(pdf), "--json-file", "-", "--json",
        ], input=show_result.output)
        assert edit_result.exit_code == 0
        data = json.loads(edit_result.output)
        assert data["fields_changed"] == []
        assert (ad / "bib.yaml").read_text(encoding="utf-8") == before

    def test_cli_noop_rich_output(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf), "--set", "title=Original Title",
        ])
        assert result.exit_code == 0
        assert "unchanged" in result.output

    def test_cli_restamp_flag(self, tmp_path):
        pdf, ad = _make_bib(tmp_path)
        _seed_state(ad, pdf)
        result = runner.invoke(app, [
            "bib", "edit", str(pdf),
            "--set", "title=Original Title",
            "--restamp", "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "title" in data["fields_changed"]
        raw = _load_bib_raw(ad)
        assert raw["_provenance"]["title"]["source"] == "human"
