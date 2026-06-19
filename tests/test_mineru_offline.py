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
            run_mineru(tmp_path / "paper.pdf", tmp_path)


def test_nonzero_exit_raises_runtime_error(fake_pdf, puba_dir):
    from puba.pdf.mineru import run_mineru
    failed = MagicMock(spec=subprocess.CompletedProcess)
    failed.returncode = 1
    failed.stderr = "Error: something went wrong"
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run", return_value=failed):
        with pytest.raises(RuntimeError, match="MinerU failed"):
            run_mineru(fake_pdf, puba_dir)


def test_missing_output_md_raises_runtime_error(fake_pdf, puba_dir):
    from puba.pdf.mineru import run_mineru
    ok = MagicMock(spec=subprocess.CompletedProcess)
    ok.returncode = 0
    ok.stderr = ""
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run", return_value=ok):
        with pytest.raises(RuntimeError, match="expected output not found"):
            run_mineru(fake_pdf, puba_dir)


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
        (dest / f"{stem}_content_list_v2.json").write_text("[]", encoding="utf-8")
        (dest / f"{stem}_middle.json").write_text("{}", encoding="utf-8")
        (dest / f"{stem}_layout.pdf").write_bytes(b"%PDF-1.4\n")
        r = MagicMock(spec=subprocess.CompletedProcess)
        r.returncode = 0
        r.stderr = ""
        return r

    return _fake_run


def test_returns_markdown_string(fake_pdf, puba_dir):
    from puba.pdf.mineru import run_mineru
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run",
               side_effect=_make_successful_run(fake_pdf, SAMPLE_MD, SAMPLE_CONTENT_LIST)):
        md, cl = run_mineru(fake_pdf, puba_dir)
    assert isinstance(md, str)
    assert "Abstract" in md


def test_returns_content_list(fake_pdf, puba_dir):
    from puba.pdf.mineru import run_mineru
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run",
               side_effect=_make_successful_run(fake_pdf, SAMPLE_MD, SAMPLE_CONTENT_LIST)):
        md, cl = run_mineru(fake_pdf, puba_dir)
    assert isinstance(cl, list)
    assert len(cl) > 0
    assert cl[0]["page_idx"] == 0


def test_intermediates_persisted(fake_pdf, puba_dir):
    from puba.pdf.mineru import run_mineru
    with patch("puba.pdf.mineru.shutil.which", return_value="/usr/bin/mineru"), \
         patch("puba.pdf.mineru.subprocess.run",
               side_effect=_make_successful_run(fake_pdf, SAMPLE_MD, SAMPLE_CONTENT_LIST)):
        run_mineru(fake_pdf, puba_dir)
    mineru_dir = puba_dir / "mineru"
    assert mineru_dir.is_dir()
    assert (mineru_dir / f"{fake_pdf.stem}_content_list.json").exists()
    assert (mineru_dir / f"{fake_pdf.stem}.md").exists()
    assert (mineru_dir / f"{fake_pdf.stem}_content_list_v2.json").exists()
    assert (mineru_dir / f"{fake_pdf.stem}_layout.pdf").exists()


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


def test_inject_page_markers_no_empty_marker_stacking():
    from puba.md.render import _inject_page_markers
    md = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n"
    content_list = [
        {"page_idx": 0, "text": "First paragraph."},
        {"page_idx": 1, "text": ""},
        {"page_idx": 1, "text": "Second paragraph."},
        {"page_idx": 2, "text": ""},
        {"page_idx": 2, "text": "Third paragraph."},
    ]
    result, _ = _inject_page_markers(md, content_list)
    import re
    markers = re.findall(r'<!--\s*page\s+\d+\s*-->', result)
    assert len(markers) == 3
    for i in range(len(markers) - 1):
        between = result[result.index(markers[i]) + len(markers[i]):result.index(markers[i + 1])]
        assert between.strip(), (
            f"Empty body between {markers[i]} and {markers[i+1]}: markers are stacked"
        )


def test_inject_page_markers_anchor_on_first_nonempty_block():
    from puba.md.render import _inject_page_markers
    md = "Page one content.\n\nPage two content.\n"
    content_list = [
        {"page_idx": 0, "text": "Page one content."},
        {"page_idx": 1, "text": ""},
        {"page_idx": 1, "text": "Page two content."},
    ]
    result, _ = _inject_page_markers(md, content_list)
    page2_pos = result.index("<!-- page 2 -->")
    page2_content_pos = result.index("Page two content.")
    assert page2_pos < page2_content_pos, "page 2 marker should appear before page 2 content"
    assert "Page one content." in result[:page2_pos], "page 1 content should appear before page 2 marker"


def test_inject_page_markers_skips_page_with_no_surviving_anchor():
    from puba.md.render import _inject_page_markers
    md = "Abstract text here.\n\nIntroduction text.\n"
    content_list = [
        {"page_idx": 0, "text": "Cover content that was stripped."},
        {"page_idx": 1, "text": "Abstract text here."},
        {"page_idx": 1, "text": "Introduction text."},
    ]
    result, _ = _inject_page_markers(md, content_list)
    assert "<!-- page 1 -->" not in result
    assert "<!-- page 2 -->" in result


def test_inject_page_markers_anchors_to_later_block_when_first_stripped():
    from puba.md.render import _inject_page_markers
    md = "Abstract text.\n\nBody text.\n"
    content_list = [
        {"page_idx": 0, "text": "Title that was stripped."},
        {"page_idx": 0, "text": "Abstract text."},
        {"page_idx": 1, "text": "Body text."},
    ]
    result, _ = _inject_page_markers(md, content_list)
    page1_pos = result.index("<!-- page 1 -->")
    abstract_pos = result.index("Abstract text.")
    assert page1_pos < abstract_pos, "page 1 marker should appear before abstract"
    assert "<!-- page 2 -->" in result


def test_inject_page_markers_pure_image_page_no_marker():
    from puba.md.render import _inject_page_markers
    md = "Page one text.\n\nPage three text.\n"
    content_list = [
        {"page_idx": 0, "text": "Page one text."},
        {"page_idx": 1, "text": ""},
        {"page_idx": 1, "text": ""},
        {"page_idx": 2, "text": "Page three text."},
    ]
    result, fallbacks = _inject_page_markers(md, content_list)
    import re
    markers = re.findall(r'<!--\s*page\s+(\d+)\s*-->', result)
    assert "2" not in markers, "pure-image page with no long text should emit no marker"
    assert "3" in markers
    assert fallbacks == []


def test_inject_page_markers_fallback_on_consumed_anchor():
    from puba.md.render import _inject_page_markers
    repeated = "Running header text here."
    md = repeated + "\n\nUnique body text for page one.\n\n" + repeated + "\n\nUnique body for page two.\n"
    content_list = [
        {"page_idx": 0, "text": repeated},
        {"page_idx": 0, "text": "Unique body text for page one."},
        {"page_idx": 1, "text": repeated},
        {"page_idx": 1, "text": "Unique body for page two."},
    ]
    result, fallbacks = _inject_page_markers(md, content_list)
    import re
    markers = re.findall(r'<!--\s*page\s+(\d+)\s*-->', result)
    assert "1" in markers
    assert "2" in markers
    assert fallbacks == []


def test_inject_page_markers_returns_fallback_list():
    from puba.md.render import _inject_page_markers
    shared = "Shared text present in markdown once."
    late = "Late anchor text that appears after shared text."
    md = shared + "\n\n" + late + "\n"
    content_list = [
        {"page_idx": 0, "text": late},
        {"page_idx": 1, "text": shared},
    ]
    result, fallbacks = _inject_page_markers(md, content_list)
    import re
    markers = re.findall(r'<!--\s*page\s+(\d+)\s*-->', result)
    assert "1" in markers
    assert "2" in markers
    assert 1 in fallbacks


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


def test_cover_strip_within_char_window():
    from puba.md.render import _strip_cover_headings
    md_within = (
        "# Cover\n\n# Junk\n\n# Target Title Paper\n\n## Body\n\nContent.\n"
    )
    result = _strip_cover_headings(md_within, "Target Title Paper")
    assert "# Target Title Paper" not in result
    assert "## Body" in result

    padding = "x" * 6000
    md_beyond = f"# Cover\n\n{padding}\n\n# Target Title Paper\n\n## Body\n\nContent.\n"
    result2 = _strip_cover_headings(md_beyond, "Target Title Paper")
    assert result2 == md_beyond


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
    assert "<!-- page 1 -->" not in md_text
    assert "<!-- page 2 -->" in md_text
