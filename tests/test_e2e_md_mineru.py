# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""GPU-gated end-to-end tests for the MinerU markdown rendering pipeline.

Primary suite (TestThornadoMd, TestKlaskyMd):
    pytest tests/test_e2e_md_mineru.py -m gpu -v

Exhaustive fixture sweep (all 6 papers):
    pytest tests/test_e2e_md_mineru.py -m "gpu and exhaustive" -v

Skip entirely:
    pytest -m "not gpu"

Each test class copies its fixture PDF into a tmp directory (never modifying
the fixtures tree) and calls render() directly without bib resolution —
frontmatter will be empty, which is fine for these assertions.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.gpu


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _copy_pdf(name: str, dest: Path) -> Path:
    src = FIXTURES / name
    dst = dest / name
    shutil.copy2(src, dst)
    return dst


def _render(pdf_path: Path) -> Path:
    """Run render() and return the path to paper.md."""
    from puba.md.render import render
    md_path, _ = render(pdf_path, force=True)
    return md_path


def _load_sections(pdf_path: Path) -> list[dict]:
    ad = pdf_path.parent / f"{pdf_path.stem}.puba"
    return json.loads((ad / "paper.sections.json").read_text(encoding="utf-8"))


def _count_headings(md_text: str) -> int:
    return len(re.findall(r"^#{1,6} ", md_text, re.MULTILINE))


def _bad_offsets(md_text: str, sections: list[dict]) -> list[str]:
    """Return short_names whose offset slice is empty or out of range."""
    bad = []
    for s in sections:
        start, end = s["start_offset"], s["end_offset"]
        if start < 0 or end > len(md_text) or start >= end:
            bad.append(s["short_name"])
        elif not md_text[start:end].strip():
            bad.append(s["short_name"])
    return bad


# ---------------------------------------------------------------------------
# Class-scoped fixtures: render once per class, reuse across test methods
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def thornado_render(tmp_path_factory):
    """Render endeve-thornado.pdf once; return (pdf_path, md_text, sections)."""
    tmp = tmp_path_factory.mktemp("thornado")
    pdf = _copy_pdf("endeve-thornado.pdf", tmp)
    md_path = _render(pdf)
    md_text = md_path.read_text(encoding="utf-8")
    sections = _load_sections(pdf)
    return pdf, md_text, sections


@pytest.fixture(scope="class")
def klasky_render(tmp_path_factory):
    """Render klasky-5.pdf once; return (pdf_path, md_text, sections)."""
    tmp = tmp_path_factory.mktemp("klasky")
    pdf = _copy_pdf("klasky-5.pdf", tmp)
    md_path = _render(pdf)
    md_text = md_path.read_text(encoding="utf-8")
    sections = _load_sections(pdf)
    return pdf, md_text, sections


# ---------------------------------------------------------------------------
# Primary: Thornado (endeve-thornado.pdf)
# 52-page two-column ApJS article; dense math; expected ~56 sections
# ---------------------------------------------------------------------------

class TestThornadoMd:

    def test_paper_md_exists(self, thornado_render):
        pdf, md_text, _ = thornado_render
        assert (pdf.parent / "endeve-thornado.puba" / "paper.md").exists()

    def test_paper_md_size(self, thornado_render):
        _, md_text, _ = thornado_render
        assert len(md_text) > 100_000

    def test_heading_count(self, thornado_render):
        _, md_text, _ = thornado_render
        assert _count_headings(md_text) >= 40

    def test_sections_json_count(self, thornado_render):
        _, _, sections = thornado_render
        assert len(sections) >= 40

    def test_abstract_short_name_present(self, thornado_render):
        _, _, sections = thornado_render
        names = {s["short_name"] for s in sections}
        assert "abstract" in names

    def test_references_short_name_present(self, thornado_render):
        _, _, sections = thornado_render
        names = {s["short_name"] for s in sections}
        assert any("ref" in n for n in names)

    def test_numeric_section_slugs_present(self, thornado_render):
        _, _, sections = thornado_render
        names = {s["short_name"] for s in sections}
        assert any(n.startswith("s_") for n in names)

    def test_page_markers_present(self, thornado_render):
        _, md_text, _ = thornado_render
        count = md_text.count("<!-- page")
        assert 48 <= count <= 56

    def test_all_section_offsets_valid(self, thornado_render):
        _, md_text, sections = thornado_render
        bad = _bad_offsets(md_text, sections)
        assert not bad, f"Sections with bad offsets: {bad}"

    def test_no_tnum_glyph_artifacts(self, thornado_render):
        _, md_text, _ = thornado_render
        assert "/zero.tnum" not in md_text
        assert "/one.tnum" not in md_text

    def test_render_uses_cache_on_rerun(self, thornado_render):
        pdf, _, _ = thornado_render
        from puba.md.render import render
        mock_run = MagicMock()
        with patch("puba.md.render.run_mineru", mock_run):
            _, was_cached = render(pdf, force=False)
        assert was_cached
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Primary: Klasky-5 (klasky-5.pdf)
# ~15-page Frontiers journal article; no "Abstract" heading (body prose style)
# ---------------------------------------------------------------------------

class TestKlaskyMd:

    def test_paper_md_exists(self, klasky_render):
        pdf, _, _ = klasky_render
        assert (pdf.parent / "klasky-5.puba" / "paper.md").exists()

    def test_paper_md_size(self, klasky_render):
        _, md_text, _ = klasky_render
        assert len(md_text) > 15_000

    def test_heading_count(self, klasky_render):
        _, md_text, _ = klasky_render
        assert _count_headings(md_text) >= 5

    def test_sections_json_non_empty(self, klasky_render):
        _, _, sections = klasky_render
        assert len(sections) >= 5

    def test_references_short_name_present(self, klasky_render):
        _, _, sections = klasky_render
        names = {s["short_name"] for s in sections}
        assert any("ref" in n for n in names)

    def test_page_markers_present(self, klasky_render):
        _, md_text, _ = klasky_render
        assert "<!-- page" in md_text

    def test_no_tnum_glyph_artifacts(self, klasky_render):
        _, md_text, _ = klasky_render
        assert "/zero.tnum" not in md_text
        assert "/one.tnum" not in md_text

    def test_all_section_offsets_valid(self, klasky_render):
        _, md_text, sections = klasky_render
        bad = _bad_offsets(md_text, sections)
        assert not bad, f"Sections with bad offsets: {bad}"

    def test_render_uses_cache_on_rerun(self, klasky_render):
        pdf, _, _ = klasky_render
        from puba.md.render import render
        mock_run = MagicMock()
        with patch("puba.md.render.run_mineru", mock_run):
            _, was_cached = render(pdf, force=False)
        assert was_cached
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Exhaustive: remaining 4 fixtures — lightweight smoke assertions
# Run with: pytest -m "gpu and exhaustive"
# ---------------------------------------------------------------------------

@pytest.mark.exhaustive
class TestExhaustiveMd:
    """Lightweight sweep over the remaining fixtures.

    Asserts only that render() completes without error, paper.md is non-trivial,
    and paper.sections.json is non-empty. Quality details are verified manually
    via the fixture sweep documented in PLAN.md.
    """

    @pytest.mark.parametrize("pdf_name,min_kb,min_sections", [
        ("zfp-spectral-report.pdf", 20,  5),
        ("dorier-mofka.pdf",        80, 20),
        ("cruz-zombie-packets.pdf", 30,  5),
        ("wan-e3smv2-clouds.pdf",   80, 10),
    ])
    def test_render_produces_valid_output(self, pdf_name, min_kb, min_sections, tmp_path):
        pdf = _copy_pdf(pdf_name, tmp_path)
        md_path = _render(pdf)

        assert md_path.exists(), f"paper.md not created for {pdf_name}"

        md = md_path.read_text(encoding="utf-8")
        assert len(md) >= min_kb * 1024, (
            f"{pdf_name}: paper.md is {len(md)//1024} KB, expected >= {min_kb} KB"
        )

        sections = _load_sections(pdf)
        assert len(sections) >= min_sections, (
            f"{pdf_name}: got {len(sections)} sections, expected >= {min_sections}"
        )

        bad = _bad_offsets(md, sections)
        assert not bad, f"{pdf_name}: sections with bad offsets: {bad}"
