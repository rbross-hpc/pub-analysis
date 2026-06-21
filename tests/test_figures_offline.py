# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for puba/figures/extract.py and puba show figures / show figure."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from puba.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_CONTENT_LIST = [
    {
        "type": "image",
        "img_path": "images/aaa111.jpg",
        "image_caption": ["Fig. 1. An illustration."],
        "image_footnote": [],
        "bbox": [100, 200, 400, 350],
        "page_idx": 5,
    },
    {
        "type": "chart",
        "img_path": "images/bbb222.jpg",
        "chart_caption": ["Fig. 2. Performance results."],
        "chart_footnote": [],
        "bbox": [50, 100, 600, 300],
        "page_idx": 9,
    },
    {
        "type": "chart",
        "img_path": "images/ccc333.jpg",
        "chart_caption": [],
        "chart_footnote": [],
        "bbox": [50, 320, 600, 500],
        "page_idx": 9,
    },
    {
        "type": "table",
        "img_path": "images/ddd444.jpg",
        "table_caption": ["Table 1. Summary of results."],
        "table_footnote": ["Note: values are normalized."],
        "bbox": [80, 150, 500, 280],
        "page_idx": 7,
    },
]


def _make_pdf_and_puba(tmp_path: Path, needs_review: bool = False) -> tuple[Path, Path]:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    (ad / "analyses").mkdir()

    bib: dict = {
        "title": "A Test Paper",
        "authors": ["Alice Smith"],
        "year": 2026,
        "needs_review": needs_review,
    }
    (ad / "bib.yaml").write_text(yaml.dump(bib), encoding="utf-8")
    (ad / "paper.md").write_text("# A Test Paper\n\n## Abstract\n\nContent.\n", encoding="utf-8")
    (ad / "paper.sections.json").write_text(
        json.dumps([{"title": "Abstract", "short_name": "abstract", "level": 1, "start": 0, "end": 50}]),
        encoding="utf-8",
    )
    return pdf, ad


def _seed_mineru(ad: Path, stem: str, content_list: list[dict], sha_map: dict[str, bytes]) -> None:
    mineru_dir = ad / "mineru"
    mineru_dir.mkdir(exist_ok=True)
    (mineru_dir / f"{stem}_content_list.json").write_text(
        json.dumps(content_list), encoding="utf-8"
    )
    images_dir = mineru_dir / "images"
    images_dir.mkdir(exist_ok=True)
    for sha, data in sha_map.items():
        (images_dir / f"{sha}.jpg").write_bytes(data)


_TINY_JPG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
    b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
    b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1eC\x00\x08\x06\x06\x07"
    b"\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00"
    b"\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01"
    b"\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00"
    b"\xf5\x0a\xff\xd9"
)


# ---------------------------------------------------------------------------
# extract() unit tests
# ---------------------------------------------------------------------------

def test_extract_manifest_shape(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG, "bbb222": _TINY_JPG, "ccc333": _TINY_JPG, "ddd444": _TINY_JPG}
    _seed_mineru(ad, "paper", SAMPLE_CONTENT_LIST, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    assert "figures" in manifest
    assert "mineru_version" in manifest
    assert "figures_version" in manifest
    figs = manifest["figures"]
    assert len(figs) == 4

    for f in figs:
        for field in ("id", "page", "page_idx", "type", "bbox", "width_pt", "height_pt",
                      "width_px", "height_px", "caption", "footnote", "source_sha", "jpg"):
            assert field in f, f"Missing field {field!r} in figure entry"


def test_extract_filename_3digit_padding(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    fig = manifest["figures"][0]
    assert fig["id"] == "page006_img1"
    assert Path(fig["jpg"]).name == "page006_img1.jpg"


def test_extract_caption_null_when_absent(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"ccc333": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[2]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    assert manifest["figures"][0]["caption"] is None


def test_extract_caption_present_when_given(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    assert manifest["figures"][0]["caption"] == "Fig. 1. An illustration."


def test_extract_footnote_null_when_absent(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    assert manifest["figures"][0]["footnote"] is None


def test_extract_footnote_present_when_given(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"ddd444": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[3]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    assert manifest["figures"][0]["footnote"] == "Note: values are normalized."


def test_extract_dimensions_present_and_int(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"), \
         patch("puba.figures.extract._pixel_dims", return_value=(300, 150)):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    f = manifest["figures"][0]
    assert isinstance(f["width_pt"], int)
    assert isinstance(f["height_pt"], int)
    assert isinstance(f["width_px"], int)
    assert isinstance(f["height_px"], int)
    assert f["width_px"] == 300
    assert f["height_px"] == 150
    assert f["width_pt"] == 300   # bbox: [100, 200, 400, 350] → 400-100=300
    assert f["height_pt"] == 150  # 350-200=150


def test_extract_types_filter_excludes(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG, "bbb222": _TINY_JPG, "ccc333": _TINY_JPG, "ddd444": _TINY_JPG}
    _seed_mineru(ad, "paper", SAMPLE_CONTENT_LIST, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf, types={"image"})

    types_found = {f["type"] for f in manifest["figures"]}
    assert types_found == {"image"}
    assert len(manifest["figures"]) == 1


def test_extract_force_reextracts(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    mark_mock = MagicMock()
    with patch("puba.state.is_stage_current", return_value=True), \
         patch("puba.state.mark_stage_complete", mark_mock):
        from puba.figures.extract import extract
        extract(pdf, force=True)

    mark_mock.assert_called_once()


def test_extract_cache_hit_skips_work(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    manifest_data = {"mineru_version": "mineru-5", "figures_version": "figures-1", "figures": []}
    (ad / "paper.figures.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    mark_mock = MagicMock()
    with patch("puba.state.is_stage_current", return_value=True), \
         patch("puba.state.mark_stage_complete", mark_mock):
        from puba.figures.extract import extract
        result = extract(pdf)

    mark_mock.assert_not_called()
    assert result == manifest_data


def test_extract_cache_invalidates_on_types_change(tmp_path):
    """Changing --types triggers a re-extract via extra_key mismatch."""
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG, "bbb222": _TINY_JPG, "ccc333": _TINY_JPG, "ddd444": _TINY_JPG}
    _seed_mineru(ad, "paper", SAMPLE_CONTENT_LIST, sha_map)

    mark_calls = []

    def fake_is_current(ad_, pdf_, stage, version, extra_key=None):
        if extra_key and extra_key.get("types") == ["chart", "image", "table"]:
            return True
        return False

    with patch("puba.state.is_stage_current", side_effect=fake_is_current), \
         patch("puba.state.mark_stage_complete", side_effect=lambda *a, **k: mark_calls.append(a)):
        from puba.figures.extract import extract
        extract(pdf, types={"image"})

    assert len(mark_calls) == 1


def test_extract_records_clean_prompt_version(tmp_path):
    """State.json should record 'figures-1', not a composite version string."""
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False):
        from puba.figures.extract import extract
        extract(pdf)

    from puba.state import load_state
    state = load_state(ad)
    stage_state = state.get("stages", {}).get("figures", {})
    assert stage_state.get("prompt_version") == "figures-2"
    assert ":" not in stage_state.get("prompt_version", "")


def test_extract_no_pdf_field_in_manifest(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    for f in manifest["figures"]:
        assert "pdf" not in f


def test_extract_per_page_seq_resets(tmp_path):
    """Two figures on different pages should both be _img1."""
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG, "ddd444": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0], SAMPLE_CONTENT_LIST[3]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    ids = [f["id"] for f in manifest["figures"]]
    assert ids == ["page006_img1", "page008_img1"]


def test_extract_two_figures_same_page_increments_seq(tmp_path):
    """Two charts on the same page: _img1 and _img2."""
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"bbb222": _TINY_JPG, "ccc333": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[1], SAMPLE_CONTENT_LIST[2]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        manifest = extract(pdf)

    ids = [f["id"] for f in manifest["figures"]]
    assert ids == ["page010_img1", "page010_img2"]


def test_extract_missing_content_list_raises(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    (ad / "mineru").mkdir()

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        with pytest.raises(RuntimeError, match="MinerU content list not found"):
            extract(pdf)


def test_extract_writes_sidecar_json(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    sha_map = {"aaa111": _TINY_JPG}
    cl = [SAMPLE_CONTENT_LIST[0]]
    _seed_mineru(ad, "paper", cl, sha_map)

    with patch("puba.state.is_stage_current", return_value=False), \
         patch("puba.state.mark_stage_complete"):
        from puba.figures.extract import extract
        extract(pdf)

    sidecar = ad / "figures" / "page006_img1.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["id"] == "page006_img1"
    assert data["caption"] == "Fig. 1. An illustration."


# ---------------------------------------------------------------------------
# Helper for CLI tests: build a manifest + figures dir
# ---------------------------------------------------------------------------

def _make_figures_setup(tmp_path: Path, needs_review: bool = False) -> tuple[Path, Path]:
    pdf, ad = _make_pdf_and_puba(tmp_path, needs_review=needs_review)

    figures_dir = ad / "figures"
    figures_dir.mkdir()

    figs = [
        {
            "id": "page006_img1", "page": 6, "page_idx": 5, "type": "image",
            "bbox": [100, 200, 400, 350], "width_pt": 300, "height_pt": 150,
            "width_px": 1200, "height_px": 600,
            "caption": "Fig. 1. An illustration.", "footnote": None,
            "source_sha": "aaa111", "jpg": "figures/page006_img1.jpg",
        },
        {
            "id": "page010_img1", "page": 10, "page_idx": 9, "type": "chart",
            "bbox": [50, 100, 600, 300], "width_pt": 550, "height_pt": 200,
            "width_px": 2200, "height_px": 800,
            "caption": "Fig. 2. Performance results.", "footnote": None,
            "source_sha": "bbb222", "jpg": "figures/page010_img1.jpg",
        },
        {
            "id": "page010_img2", "page": 10, "page_idx": 9, "type": "chart",
            "bbox": [50, 320, 600, 500], "width_pt": 550, "height_pt": 180,
            "width_px": 2200, "height_px": 720,
            "caption": None, "footnote": None,
            "source_sha": "ccc333", "jpg": "figures/page010_img2.jpg",
        },
    ]

    manifest = {
        "mineru_version": "mineru-5",
        "figures_version": "figures-2",
        "figures": figs,
    }
    (ad / "paper.figures.json").write_text(json.dumps(manifest), encoding="utf-8")

    for f in figs:
        (ad / f["jpg"]).write_bytes(_TINY_JPG)

    return pdf, ad


# ---------------------------------------------------------------------------
# puba figures CLI tests
# ---------------------------------------------------------------------------

def test_figures_cli_bib_gate(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = runner.invoke(app, ["figures", str(pdf), "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 3
    assert data["error_type"] == "BibMissing"


def test_figures_cli_bib_gate_needs_review(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path, needs_review=True)
    result = runner.invoke(app, ["figures", str(pdf), "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 3
    assert data["error_type"] == "ReviewNeeded"


def test_figures_cli_invalid_types(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["figures", str(pdf), "--types", "image,bogus", "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 2
    assert "bogus" in data["error"]


def test_figures_cli_success_json(tmp_path):
    import sys, types as _types
    pdf, ad = _make_pdf_and_puba(tmp_path)
    fake_manifest = {"mineru_version": "mineru-5", "figures_version": "figures-1",
                     "figures": [{"id": "page006_img1"}]}

    def fake_is_stage_current(analysis_dir, pdf_path, stage, prompt_version, **kwargs):
        return stage == "md"

    fake_extract_mod = _types.ModuleType("puba.figures.extract")
    fake_extract_mod.extract = lambda pdf_path, types=None, force=False: fake_manifest

    with patch("puba.state.is_stage_current", side_effect=fake_is_stage_current), \
         patch.dict(sys.modules, {"puba.figures.extract": fake_extract_mod}):
        result = runner.invoke(app, ["figures", str(pdf), "--json"])

    data = json.loads(result.output)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["figures_count"] == 1
    assert "manifest" in data


# ---------------------------------------------------------------------------
# puba show figures CLI tests
# ---------------------------------------------------------------------------

def test_show_figures_lists_manifest(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figures", str(pdf)])
    assert result.exit_code == 0
    assert "page006_img1" in result.output
    assert "page010_img1" in result.output
    assert "image" in result.output
    assert "chart" in result.output


def test_show_figures_json_emits_manifest(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figures", str(pdf), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "figures" in data
    assert len(data["figures"]) == 3



def test_show_figures_missing_manifest_errors(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figures", str(pdf), "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert "FileNotFoundError" in data["error_type"]


def test_show_figures_errors_when_md_not_rendered(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = runner.invoke(app, ["show", "figures", str(pdf), "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 1
    assert data["error_type"] == "CacheError"
    assert "puba md" in data["error"]


def test_show_figures_md_gate(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    (ad / "paper.md").unlink()
    (ad / "paper.sections.json").unlink()
    with patch("puba.state.is_stage_current", return_value=False):
        result = runner.invoke(app, ["show", "figures", str(pdf), "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 1
    assert data["ok"] is False


# ---------------------------------------------------------------------------
# puba show figure ID CLI tests
# ---------------------------------------------------------------------------

def test_show_figure_detail_format(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1"])
    assert result.exit_code == 0
    assert "page006_img1" in result.output
    assert "1200" in result.output
    assert "Fig. 1. An illustration." in result.output


def test_show_figure_json_single_entry(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "page006_img1"
    assert data["caption"] == "Fig. 1. An illustration."
    assert "data_url" not in data


def test_show_figure_embed_adds_data_url(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--json", "--embed"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "data_url" in data
    assert data["data_url"].startswith("data:image/jpeg;base64,")


def test_show_figure_embed_requires_json(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--embed"])
    assert result.exit_code == 2


def test_show_figure_path_flag(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--path"])
    assert result.exit_code == 0
    assert "page006_img1.jpg" in result.output.strip()
    assert result.output.strip().endswith(".jpg")


def test_show_figure_path_mutually_exclusive_with_json(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--path", "--json"])
    assert result.exit_code == 2


def test_show_figure_unknown_id_errors(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page099_img1", "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 1
    assert data["ok"] is False
    assert "page099_img1" in data["error"]


def test_show_figure_missing_manifest_errors(tmp_path):
    pdf, ad = _make_pdf_and_puba(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 1
    assert data["ok"] is False


def test_show_figure_errors_when_md_not_rendered(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--json"])
    data = json.loads(result.output)
    assert result.exit_code == 1
    assert data["error_type"] == "CacheError"
    assert "puba md" in data["error"]


# ---------------------------------------------------------------------------
# figures-2 schema: relative paths + relocation safety
# ---------------------------------------------------------------------------

def test_manifest_jpg_field_is_relative(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    manifest = json.loads((ad / "paper.figures.json").read_text(encoding="utf-8"))
    for f in manifest["figures"]:
        assert not Path(f["jpg"]).is_absolute(), f"jpg should be relative, got: {f['jpg']}"
        assert f["jpg"].startswith("figures/")


def test_show_figure_json_includes_jpg_abs(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "jpg" in data
    assert "jpg_abs" in data
    assert not Path(data["jpg"]).is_absolute()
    assert Path(data["jpg_abs"]).is_absolute()
    assert data["jpg_abs"].endswith("page006_img1.jpg")


def test_show_figures_json_includes_jpg_abs(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figures", str(pdf), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    for f in data["figures"]:
        assert "jpg_abs" in f
        assert Path(f["jpg_abs"]).is_absolute()


def test_show_figure_path_returns_absolute(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--path"])
    assert result.exit_code == 0
    out = result.output.strip()
    assert Path(out).is_absolute()
    assert out.endswith("page006_img1.jpg")


def test_relocation_safe(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)

    new_root = tmp_path / "relocated"
    new_root.mkdir()
    new_pdf = new_root / "paper.pdf"
    new_pdf.write_bytes(b"%PDF-1.4\n")
    import shutil as _shutil
    new_ad = new_root / "paper.puba"
    _shutil.copytree(ad, new_ad)

    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(new_pdf), "page006_img1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert str(new_ad) in data["jpg_abs"]
    assert str(ad) not in data["jpg_abs"]


def test_legacy_absolute_jpg_still_resolves(tmp_path):
    pdf, ad = _make_figures_setup(tmp_path)
    figures_dir = ad / "figures"
    abs_jpg = str(figures_dir / "page006_img1.jpg")
    legacy_manifest = {
        "mineru_version": "mineru-5",
        "figures_version": "figures-1",
        "figures": [{
            "id": "page006_img1", "page": 6, "page_idx": 5, "type": "image",
            "bbox": [0, 0, 100, 100], "width_pt": 100, "height_pt": 100,
            "width_px": 100, "height_px": 100,
            "caption": None, "footnote": None,
            "source_sha": "aaa111", "jpg": abs_jpg,
        }],
    }
    (ad / "paper.figures.json").write_text(json.dumps(legacy_manifest), encoding="utf-8")

    with patch("puba.state.is_stage_current", return_value=True):
        result = runner.invoke(app, ["show", "figure", str(pdf), "page006_img1", "--path"])
    assert result.exit_code == 0
    assert result.output.strip() == abs_jpg


def test_figures_version_bump_invalidates_cache(tmp_path):
    from puba.state import mark_stage_complete, is_stage_current
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    mark_stage_complete(ad, pdf, "figures", "figures-1",
                        extra={"types": ["chart", "image", "table"]})
    assert not is_stage_current(ad, pdf, "figures", "figures-2",
                                extra_key={"types": ["chart", "image", "table"]})
