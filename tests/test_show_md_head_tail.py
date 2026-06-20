# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Tests for puba/md/slicing.py and puba show md --head/--tail."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from puba.cli import app
from puba.md.slicing import slice_md, _PAGE_MARKER_RE

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MARKER = "<!-- page 7 -->"
_MARKER2 = "<!-- page 8 -->"


def _make_md_setup(tmp_path: Path, md_content: str) -> tuple[Path, Path]:
    """Create paper.pdf + paper.puba/paper.md, return (pdf, ad)."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    (ad / "paper.md").write_text(md_content, encoding="utf-8")
    (ad / "paper.sections.json").write_text("[]", encoding="utf-8")
    return pdf, ad


# ---------------------------------------------------------------------------
# Unit tests: slice_md — no markers
# ---------------------------------------------------------------------------

class TestSliceMdNoMarkers:

    def test_head_returns_first_n(self):
        text = "abcdefghij"
        s, req = slice_md(text, head=4)
        assert s == "abcd"
        assert req == 4

    def test_tail_returns_last_n(self):
        text = "abcdefghij"
        s, req = slice_md(text, tail=4)
        assert s == "ghij"
        assert req == 4

    def test_head_n_exceeds_total_returns_whole(self):
        text = "abc"
        s, req = slice_md(text, head=100)
        assert s == "abc"
        assert req == 100

    def test_tail_n_exceeds_total_returns_whole(self):
        text = "abc"
        s, req = slice_md(text, tail=100)
        assert s == "abc"
        assert req == 100

    def test_head_exactly_total(self):
        text = "abc"
        s, req = slice_md(text, head=3)
        assert s == "abc"
        assert req == 3

    def test_tail_exactly_total(self):
        text = "abc"
        s, req = slice_md(text, tail=3)
        assert s == "abc"
        assert req == 3

    def test_head_zero_returns_empty(self):
        s, req = slice_md("abc", head=0)
        assert s == ""
        assert req == 0

    def test_both_raises(self):
        with pytest.raises(ValueError):
            slice_md("abc", head=2, tail=2)

    def test_neither_raises(self):
        with pytest.raises(ValueError):
            slice_md("abc")


# ---------------------------------------------------------------------------
# Unit tests: slice_md — marker straddling (head)
# ---------------------------------------------------------------------------

class TestSliceMdHeadMarker:

    def test_cut_before_marker_unchanged(self):
        text = "AAAA" + _MARKER + "BBBB"
        cut = 2
        s, _ = slice_md(text, head=cut)
        assert s == "AA"
        assert _MARKER not in s

    def test_cut_after_marker_unchanged(self):
        text = "AAAA" + _MARKER + "BBBB"
        cut = len("AAAA") + len(_MARKER) + 2
        s, _ = slice_md(text, head=cut)
        assert s == "AAAA" + _MARKER + "BB"

    def test_cut_inside_marker_retracts_to_before(self):
        text = "AAAA" + _MARKER + "BBBB"
        marker_start = len("AAAA")
        cut = marker_start + 3
        s, req = slice_md(text, head=cut)
        assert s == "AAAA"
        assert req == cut
        assert len(s) == marker_start

    def test_cut_at_marker_start_not_straddling(self):
        text = "AAAA" + _MARKER + "BBBB"
        marker_start = len("AAAA")
        s, _ = slice_md(text, head=marker_start)
        assert s == "AAAA"

    def test_cut_at_marker_end_not_straddling(self):
        text = "AAAA" + _MARKER + "BBBB"
        marker_end = len("AAAA") + len(_MARKER)
        s, _ = slice_md(text, head=marker_end)
        assert s == "AAAA" + _MARKER

    def test_cut_inside_first_marker_gives_empty(self):
        text = _MARKER + "BBBB"
        cut = 3
        s, _ = slice_md(text, head=cut)
        assert s == ""

    def test_multiple_markers_only_straddled_one_matters(self):
        text = "AA" + _MARKER + "BB" + _MARKER2 + "CC"
        marker1_start = len("AA")
        marker1_end = marker1_start + len(_MARKER)
        cut = marker1_start + 2
        s, _ = slice_md(text, head=cut)
        assert s == "AA"
        assert s.endswith("AA")

    def test_cut_inside_second_marker_retracts_correctly(self):
        text = "AA" + _MARKER + "BB" + _MARKER2 + "CC"
        marker2_start = len("AA") + len(_MARKER) + len("BB")
        cut = marker2_start + 4
        s, _ = slice_md(text, head=cut)
        assert s == "AA" + _MARKER + "BB"


# ---------------------------------------------------------------------------
# Unit tests: slice_md — marker straddling (tail)
# ---------------------------------------------------------------------------

class TestSliceMdTailMarker:

    def test_start_after_marker_unchanged(self):
        text = "AAAA" + _MARKER + "BBBB"
        s, _ = slice_md(text, tail=4)
        assert s == "BBBB"

    def test_start_before_marker_unchanged(self):
        text = "AAAA" + _MARKER + "BBBB"
        s, _ = slice_md(text, tail=len(_MARKER) + len("BBBB") + 2)
        assert s.startswith("AA")

    def test_start_inside_marker_advances_past_it(self):
        text = "AAAA" + _MARKER + "BBBB"
        marker_start = len("AAAA")
        marker_end = marker_start + len(_MARKER)
        tail_n = len(text) - (marker_start + 3)
        s, req = slice_md(text, tail=tail_n)
        assert s == "BBBB"
        assert req == tail_n

    def test_start_at_marker_start_not_straddling(self):
        text = "AAAA" + _MARKER + "BBBB"
        tail_n = len(_MARKER) + len("BBBB")
        s, _ = slice_md(text, tail=tail_n)
        assert s == _MARKER + "BBBB"

    def test_start_inside_last_marker_gives_empty(self):
        text = "AAAA" + _MARKER
        tail_n = 3
        s, _ = slice_md(text, tail=tail_n)
        assert s == ""


# ---------------------------------------------------------------------------
# CLI tests: puba show md --head / --tail
# ---------------------------------------------------------------------------

class TestShowMdHeadTailCli:

    def test_head_plain_output(self, tmp_path):
        md = "Hello world, this is a test document.\n"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", "5"])
        assert result.exit_code == 0
        assert result.output.startswith("Hello")
        assert len(result.output.rstrip("\n")) == 5

    def test_tail_plain_output(self, tmp_path):
        md = "Hello world, this is a test document.\n"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--tail", "4"])
        assert result.exit_code == 0
        assert "nt.\n" in result.output

    def test_plain_output_ends_with_newline(self, tmp_path):
        md = "abcdefgh"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", "4"])
        assert result.exit_code == 0
        assert result.output.endswith("\n")

    def test_head_json_shape(self, tmp_path):
        md = "abcdefghij"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", "4", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["command"] == "show.md"
        assert data["content"] == "abcd"
        assert data["chars"] == 4
        assert data["requested_chars"] == 4
        assert data["total_chars"] == 10
        assert data["truncated"] is True

    def test_tail_json_shape(self, tmp_path):
        md = "abcdefghij"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--tail", "4", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["content"] == "ghij"
        assert data["chars"] == 4
        assert data["total_chars"] == 10
        assert data["truncated"] is True

    def test_head_n_equals_total_not_truncated(self, tmp_path):
        md = "abcdef"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", "6", "--json"])
        data = json.loads(result.output)
        assert data["truncated"] is False
        assert data["chars"] == 6

    def test_head_n_exceeds_total_not_truncated(self, tmp_path):
        md = "abcdef"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", "100", "--json"])
        data = json.loads(result.output)
        assert data["truncated"] is False
        assert data["content"] == "abcdef"

    def test_head_retracts_at_page_marker(self, tmp_path):
        md = "AAAA" + "<!-- page 3 -->" + "BBBB"
        marker_start = len("AAAA")
        cut = marker_start + 3
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", str(cut), "--json"])
        data = json.loads(result.output)
        assert data["content"] == "AAAA"
        assert data["chars"] == marker_start
        assert data["requested_chars"] == cut

    def test_tail_advances_past_page_marker(self, tmp_path):
        md = "AAAA" + "<!-- page 3 -->" + "BBBB"
        marker_start = len("AAAA")
        marker_len = len("<!-- page 3 -->")
        tail_n = marker_len + len("BBBB") - 3
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--tail", str(tail_n), "--json"])
        data = json.loads(result.output)
        assert data["content"] == "BBBB"
        assert data["requested_chars"] == tail_n

    def test_head_and_tail_mutually_exclusive(self, tmp_path):
        pdf, ad = _make_md_setup(tmp_path, "hello")
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", "3", "--tail", "3"])
        assert result.exit_code == 2

    def test_head_zero_rejected(self, tmp_path):
        pdf, ad = _make_md_setup(tmp_path, "hello")
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--head", "0"])
        assert result.exit_code != 0

    def test_no_head_tail_json_shape_unchanged(self, tmp_path):
        md = "# Title\n\nContent.\n"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "chars" not in data
        assert "total_chars" not in data
        assert "truncated" not in data
        assert "content" not in data

    def test_no_head_tail_include_content_unchanged(self, tmp_path):
        md = "# Title\n\nContent.\n"
        pdf, ad = _make_md_setup(tmp_path, md)
        with patch("puba.state.is_stage_current", return_value=True):
            result = runner.invoke(app, ["show", "md", str(pdf), "--json", "--include-content"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["content"] == md
        assert "chars" not in data
        assert "truncated" not in data

    def test_head_errors_when_md_not_rendered(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        result = runner.invoke(app, ["show", "md", str(pdf), "--head", "100", "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["ok"] is False
