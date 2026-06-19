# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for puba/pdf/mineru.py and puba/md/render.py."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

SAMPLE_MD = """\
# Lawrence Berkeley National Laboratory LBL Publications

## Abstract

We present a new algorithm for efficient computation.

## 1. Introduction

This paper introduces a novel approach.

## 2. Methods

We describe the experimental setup.

## 3. Results

Results demonstrate improvement.

## References

[1] Smith et al., 2020.
"""

SAMPLE_CONTENT_LIST = [
    {"type": "text", "text": "Lawrence Berkeley National Laboratory LBL Publications",
     "text_level": 1, "page_idx": 0},
    {"type": "text", "text": "Abstract", "text_level": 2, "page_idx": 0},
    {"type": "text", "text": "We present a new algorithm for efficient computation.",
     "page_idx": 0},
    {"type": "text", "text": "1. Introduction", "text_level": 2, "page_idx": 1},
    {"type": "text", "text": "This paper introduces a novel approach.", "page_idx": 1},
    {"type": "text", "text": "2. Methods", "text_level": 2, "page_idx": 2},
    {"type": "text", "text": "We describe the experimental setup.", "page_idx": 2},
    {"type": "text", "text": "3. Results", "text_level": 2, "page_idx": 3},
    {"type": "text", "text": "Results demonstrate improvement.", "page_idx": 3},
    {"type": "text", "text": "References", "text_level": 2, "page_idx": 4},
    {"type": "ref_text", "text": "[1] Smith et al., 2020.", "page_idx": 4},
]


@pytest.fixture
def fake_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "mypaper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return pdf


@pytest.fixture
def puba_dir(fake_pdf: Path) -> Path:
    d = fake_pdf.parent / "mypaper.puba"
    d.mkdir(exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# run_mineru unit tests (mock shutil.which + subprocess.run)
# ---------------------------------------------------------------------------

def test_missing_binary_raises_runtime_error(tmp_path):
    from puba.pdf.mineru import run_mineru
    with patch("puba.pdf.mineru.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="MinerU not installed"):
            run_mineru(tmp_path / "paper.pdf")


def test_nonzero_exit_raises_runtime_error(fake_pdf):
    from puba.pdf.mineru import run_mineru
    failed = MagicMock(spec=subprocess.CompletedProcess)
    failed.returncode = 1
    failed.stderr = "Error: something went wrong"
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run", return_value=failed):
        with pytest.raises(RuntimeError, match="MinerU failed"):
            run_mineru(fake_pdf)


def test_missing_output_md_raises_runtime_error(fake_pdf, tmp_path):
    from puba.pdf.mineru import run_mineru
    ok = MagicMock(spec=subprocess.CompletedProcess)
    ok.returncode = 0
    ok.stderr = ""
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run", return_value=ok):
        with pytest.raises(RuntimeError, match="expected output not found"):
            run_mineru(fake_pdf)


def _make_successful_run(pdf_path: Path, md_text: str, content_list: list) -> MagicMock:
    """Return a mock subprocess.run that writes the expected MinerU output layout."""
    stem = pdf_path.stem

    def _fake_run(cmd, capture_output, text):
        out_dir = Path(cmd[cmd.index("-o") + 1])
        dest = out_dir / stem / "auto"
        dest.mkdir(parents=True)
        (dest / f"{stem}.md").write_text(md_text, encoding="utf-8")
        (dest / f"{stem}_content_list.json").write_text(
            json.dumps(content_list), encoding="utf-8"
        )
        r = MagicMock(spec=subprocess.CompletedProcess)
        r.returncode = 0
        r.stderr = ""
        return r

    return _fake_run


def test_returns_markdown_string(fake_pdf):
    from puba.pdf.mineru import run_mineru
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run",
               side_effect=_make_successful_run(fake_pdf, SAMPLE_MD, SAMPLE_CONTENT_LIST)):
        md, cl = run_mineru(fake_pdf)
    assert isinstance(md, str)
    assert "Abstract" in md


def test_returns_content_list(fake_pdf):
    from puba.pdf.mineru import run_mineru
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run",
               side_effect=_make_successful_run(fake_pdf, SAMPLE_MD, SAMPLE_CONTENT_LIST)):
        md, cl = run_mineru(fake_pdf)
    assert isinstance(cl, list)
    assert len(cl) > 0
    assert cl[0]["page_idx"] == 0


# ---------------------------------------------------------------------------
# render() integration tests (mock run_mineru directly)
# ---------------------------------------------------------------------------

def _mock_run_mineru(md=SAMPLE_MD, cl=SAMPLE_CONTENT_LIST):
    return MagicMock(return_value=(md, cl))


def test_render_produces_paper_md(fake_pdf, puba_dir):
    from puba.md.render import render
    with patch("puba.state.ensure_analysis_dir", return_value=puba_dir), \
         patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"), \
         patch("puba.md.render.run_mineru", _mock_run_mineru()):
        md_path, was_cached = render(fake_pdf)

    assert md_path.exists()
    assert not was_cached
    content = md_path.read_text(encoding="utf-8")
    assert "# " in content


def test_render_sections_json_offsets_slice_paper_md(fake_pdf, puba_dir):
    from puba.md.render import render
    with patch("puba.state.ensure_analysis_dir", return_value=puba_dir), \
         patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"), \
         patch("puba.md.render.run_mineru", _mock_run_mineru()):
        md_path, _ = render(fake_pdf)

    md_text = md_path.read_text(encoding="utf-8")
    sections = json.loads((puba_dir / "paper.sections.json").read_text())
    assert len(sections) > 0

    for sec in sections:
        start = sec["start_offset"]
        end = sec["end_offset"]
        assert 0 <= start < end <= len(md_text), (
            f"Section {sec['short_name']!r} offsets ({start}, {end}) "
            f"out of range for md len {len(md_text)}"
        )
        assert md_text[start:end].strip(), f"Section {sec['short_name']!r} slice is empty"


def test_render_section_short_names_present(fake_pdf, puba_dir):
    from puba.md.render import render
    with patch("puba.state.ensure_analysis_dir", return_value=puba_dir), \
         patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"), \
         patch("puba.md.render.run_mineru", _mock_run_mineru()):
        render(fake_pdf)

    sections = json.loads((puba_dir / "paper.sections.json").read_text())
    names = {s["short_name"] for s in sections}
    assert "abstract" in names
    assert "references" in names


def test_render_page_markers_in_output(fake_pdf, puba_dir):
    from puba.md.render import render
    with patch("puba.state.ensure_analysis_dir", return_value=puba_dir), \
         patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"), \
         patch("puba.md.render.run_mineru", _mock_run_mineru()):
        md_path, _ = render(fake_pdf)

    content = md_path.read_text(encoding="utf-8")
    assert "<!-- page" in content


def test_render_uses_cache_when_current(fake_pdf, puba_dir):
    from puba.md.render import render
    paper_md = puba_dir / "paper.md"
    paper_md.write_text("cached content", encoding="utf-8")

    mock_run = MagicMock()
    with patch("puba.state.ensure_analysis_dir", return_value=puba_dir), \
         patch("puba.state.is_stage_current", return_value=True), \
         patch("puba.md.render.run_mineru", mock_run):
        md_path, was_cached = render(fake_pdf)

    assert was_cached
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# _strip_cover_headings unit tests
# ---------------------------------------------------------------------------

_COVER_MD = """\
# Lawrence Berkeley National Laboratory LBL Publications

<!-- page 1 -->

## Title

Some title content

## OPEN ACCESS

## CITATION

Citation content

<!-- page 2 -->

# Real Paper Title

## Abstract

Abstract text here.

## Introduction

Introduction text.
"""

_REAL_TITLE = "Real Paper Title"


def test_cover_strip_keeps_real_title_match():
    from puba.md.render import _strip_cover_headings
    result = _strip_cover_headings(_COVER_MD, _REAL_TITLE)
    assert "# Real Paper Title" not in result
    assert "## Abstract" in result
    assert "LBL Publications" not in result
    assert "OPEN ACCESS" not in result


def test_cover_strip_no_op_without_bib_title():
    from puba.md.render import _strip_cover_headings
    for title in (None, "", "  "):
        result = _strip_cover_headings(_COVER_MD, title)
        assert result == _COVER_MD


def test_cover_strip_no_op_single_word_title():
    from puba.md.render import _strip_cover_headings
    result = _strip_cover_headings(_COVER_MD, "X")
    assert result == _COVER_MD


def test_cover_strip_no_op_without_match():
    from puba.md.render import _strip_cover_headings
    result = _strip_cover_headings(_COVER_MD, "Completely Different Paper About Elephants")
    assert result == _COVER_MD
    assert "LBL Publications" in result


def test_cover_strip_handles_punctuation_drift():
    from puba.md.render import _strip_cover_headings
    md = "# cover junk\n\n# Foo-Bar: A Method for Things and More\n\n## Body\n\nContent.\n"
    result = _strip_cover_headings(md, "Foo-Bar: A Method for Things")
    assert "Foo-Bar" not in result
    assert "## Body" in result


def test_cover_strip_match_is_first_heading():
    from puba.md.render import _strip_cover_headings
    md = "# Real Title\n\n## Section\n\nBody.\n"
    result = _strip_cover_headings(md, "Real Title")
    assert "# Real Title" not in result
    assert "## Section" in result


def test_cover_strip_within_heading_window():
    from puba.md.render import _strip_cover_headings

    lines = []
    for i in range(19):
        lines.append(f"# Junk Heading {i}\n\nContent {i}.\n")
    lines.append("# Target Paper Title Here\n\n## Real Section\n\nContent.\n")
    md = "\n".join(lines)

    result_at_20 = _strip_cover_headings(md, "Target Paper Title Here")
    assert "# Target Paper Title Here" not in result_at_20
    assert "## Real Section" in result_at_20

    lines2 = []
    for i in range(20):
        lines2.append(f"# Junk Heading {i}\n\nContent {i}.\n")
    lines2.append("# Target Paper Title Here\n\n## Real Section\n\nContent.\n")
    md2 = "\n".join(lines2)

    result_at_21 = _strip_cover_headings(md2, "Target Paper Title Here")
    assert result_at_21 == md2


def test_cover_strip_within_page_window():
    from puba.md.render import _strip_cover_headings
    md_before = (
        "# Cover\n\n<!-- page 1 -->\n\n# Junk\n\n<!-- page 2 -->\n\n"
        "# Target Title Paper\n\n## Body\n\nContent.\n"
    )
    result = _strip_cover_headings(md_before, "Target Title Paper")
    assert "# Target Title Paper" not in result
    assert "## Body" in result

    md_after = (
        "# Cover\n\n<!-- page 1 -->\n\n# Junk\n\n<!-- page 2 -->\n\n"
        "# Other\n\n<!-- page 3 -->\n\n# Target Title Paper\n\n## Body\n\nContent.\n"
    )
    result2 = _strip_cover_headings(md_after, "Target Title Paper")
    assert result2 == md_after


def test_render_integration_cover_stripped(fake_pdf, puba_dir):
    from puba.md.render import render
    import yaml

    cover_md = (
        "# Lawrence Berkeley National Laboratory LBL Publications\n\n"
        "## OPEN ACCESS\n\nOpen access content.\n\n"
        "## CITATION\n\nCitation text.\n\n"
        "# Scalable Foundation Models for HPC\n\n"
        "## Abstract\n\nAbstract text.\n\n"
        "## Introduction\n\nIntro text.\n"
    )
    content_list = [
        {"type": "text", "text": "Lawrence Berkeley National Laboratory LBL Publications",
         "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "OPEN ACCESS", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "Open access content.", "page_idx": 0},
        {"type": "text", "text": "CITATION", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "Citation text.", "page_idx": 0},
        {"type": "text", "text": "Scalable Foundation Models for HPC",
         "text_level": 1, "page_idx": 1},
        {"type": "text", "text": "Abstract text.", "page_idx": 1},
        {"type": "text", "text": "Intro text.", "page_idx": 1},
    ]
    bib_content = {
        "title": "Scalable Foundation Models for HPC",
        "authors": ["Alice Smith"],
        "year": 2026,
    }
    (puba_dir / "bib.yaml").write_text(
        yaml.dump(bib_content), encoding="utf-8"
    )

    with patch("puba.state.ensure_analysis_dir", return_value=puba_dir), \
         patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"), \
         patch("puba.md.render.run_mineru", return_value=(cover_md, content_list)):
        md_path, _ = render(fake_pdf)

    import json as _json
    md_text = md_path.read_text(encoding="utf-8")
    sections = _json.loads((puba_dir / "paper.sections.json").read_text())
    names = {s["short_name"] for s in sections}

    assert "open_access" not in names
    assert "citation" not in names
    assert "abstract" in names
    assert "introduction" in names
    assert "LBL Publications" not in md_text
    assert "OPEN ACCESS" not in md_text
