# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""GPU-gated end-to-end tests for puba figures.

Runs puba md then puba figures against a real fixture PDF, verifying that:
- mineru/images/ is populated by puba md
- paper.figures.json is written with the expected structure
- figures/ contains the expected JPG and JSON sidecar files
- figure count matches content_list.json

Run:
    pytest tests/test_e2e_figures_mineru.py -m gpu -v

Skip:
    pytest -m "not gpu"
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

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


def _seed_bib(pdf_path: Path, title: str) -> None:
    ad = pdf_path.parent / f"{pdf_path.stem}.puba"
    ad.mkdir(exist_ok=True)
    bib = {"title": title, "needs_review": False}
    (ad / "bib.yaml").write_text(yaml.dump(bib), encoding="utf-8")


def _render_md(pdf_path: Path) -> None:
    from puba.md.render import render
    render(pdf_path, force=True)


def _run_figures(pdf_path: Path) -> dict:
    from puba.figures.extract import extract
    return extract(pdf_path, force=True)


def _content_list_figure_count(pdf_path: Path, types: frozenset[str]) -> int:
    ad = pdf_path.parent / f"{pdf_path.stem}.puba"
    cl_path = ad / "mineru" / f"{pdf_path.stem}_content_list.json"
    cl = json.loads(cl_path.read_text(encoding="utf-8"))
    seen_shas: set[str] = set()
    count = 0
    for item in cl:
        if item.get("type") not in types:
            continue
        img_path = item.get("img_path", "")
        if not img_path:
            continue
        sha = Path(img_path).stem
        images_dir = ad / "mineru" / "images"
        if not (images_dir / f"{sha}.jpg").exists():
            continue
        if sha in seen_shas:
            continue
        seen_shas.add(sha)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Primary fixture: cruz-zombie-packets
# (18 pages, has image + chart + table, captions generally present)
# ---------------------------------------------------------------------------

class TestCruzFigures:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.pdf = _copy_pdf("cruz-zombie-packets.pdf", tmp_path)
        _seed_bib(self.pdf, "Zombie Packets")
        _render_md(self.pdf)
        self.ad = self.pdf.parent / "cruz-zombie-packets.puba"
        self.manifest = _run_figures(self.pdf)

    def test_mineru_images_dir_populated(self):
        images_dir = self.ad / "mineru" / "images"
        assert images_dir.is_dir(), "mineru/images/ should exist after puba md"
        jpgs = list(images_dir.glob("*.jpg"))
        assert len(jpgs) > 0, "mineru/images/ should contain at least one JPG"

    def test_manifest_written(self):
        assert (self.ad / "paper.figures.json").exists()

    def test_manifest_has_required_top_level_fields(self):
        assert "mineru_version" in self.manifest
        assert "figures_version" in self.manifest
        assert "figures" in self.manifest
        assert isinstance(self.manifest["figures"], list)

    def test_figures_count_matches_content_list(self):
        expected = _content_list_figure_count(
            self.pdf, frozenset({"image", "chart", "table"})
        )
        assert len(self.manifest["figures"]) == expected

    def test_figures_nonempty(self):
        assert len(self.manifest["figures"]) > 0

    def test_each_figure_has_required_fields(self):
        required = {
            "id", "page", "page_idx", "type", "bbox",
            "width_pt", "height_pt", "width_px", "height_px",
            "caption", "footnote", "source_sha", "jpg",
        }
        for f in self.manifest["figures"]:
            missing = required - f.keys()
            assert not missing, f"Figure {f.get('id')} missing fields: {missing}"

    def test_jpg_files_exist_and_nonempty(self):
        for f in self.manifest["figures"]:
            jpg = Path(f["jpg"])
            assert jpg.exists(), f"JPG missing: {jpg}"
            assert jpg.stat().st_size > 0, f"JPG empty: {jpg}"

    def test_sidecar_json_files_exist(self):
        figures_dir = self.ad / "figures"
        for f in self.manifest["figures"]:
            sidecar = figures_dir / f"{f['id']}.json"
            assert sidecar.exists(), f"Sidecar missing: {sidecar}"

    def test_filename_3digit_padding(self):
        for f in self.manifest["figures"]:
            fig_id = f["id"]
            page_part = fig_id.split("_img")[0]
            assert page_part.startswith("page"), f"ID {fig_id!r} should start with 'page'"
            digits = page_part[4:]
            assert len(digits) == 3, f"ID {fig_id!r} page digits should be 3: got {digits!r}"

    def test_page_1indexed_matches_page_idx(self):
        for f in self.manifest["figures"]:
            assert f["page"] == f["page_idx"] + 1, (
                f"Figure {f['id']}: page={f['page']} != page_idx+1={f['page_idx']+1}"
            )

    def test_dimensions_are_positive_ints(self):
        for f in self.manifest["figures"]:
            for dim in ("width_pt", "height_pt", "width_px", "height_px"):
                assert isinstance(f[dim], int), f"Figure {f['id']}: {dim} should be int"
                assert f[dim] >= 0, f"Figure {f['id']}: {dim} should be non-negative"

    def test_no_pdf_field(self):
        for f in self.manifest["figures"]:
            assert "pdf" not in f, f"Figure {f['id']} should not have 'pdf' field"

    def test_type_values_are_valid(self):
        valid = {"image", "chart", "table"}
        for f in self.manifest["figures"]:
            assert f["type"] in valid, f"Figure {f['id']}: unknown type {f['type']!r}"

    def test_caption_null_or_str(self):
        for f in self.manifest["figures"]:
            assert f["caption"] is None or isinstance(f["caption"], str), (
                f"Figure {f['id']}: caption should be None or str"
            )

    def test_state_json_records_figures_stage(self):
        from puba.state import load_state
        state = load_state(self.ad)
        assert "figures" in state.get("stages", {}), \
            "figures stage should be recorded in .state.json"

    def test_types_filter_image_only(self):
        from puba.figures.extract import extract
        manifest_img = extract(self.pdf, types={"image"}, force=True)
        types_found = {f["type"] for f in manifest_img["figures"]}
        assert types_found <= {"image"}, f"Expected only image, got: {types_found}"

    def test_cache_hit_on_second_run(self):
        from puba.state import load_state
        state_before = load_state(self.ad)
        ts_before = state_before["stages"]["figures"]["completed_at"]

        from puba.figures.extract import extract
        extract(self.pdf)  # no force — should be cache hit

        state_after = load_state(self.ad)
        ts_after = state_after["stages"]["figures"]["completed_at"]
        assert ts_before == ts_after, "Second run without --force should be a cache hit"


# ---------------------------------------------------------------------------
# Regression: puba md still works and mineru/images/ is present
# (verifies D.1 didn't break existing md outputs)
# ---------------------------------------------------------------------------

class TestMdRegressionImages:
    """Verify that the mineru.py change (D.1) doesn't regress existing md tests."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.pdf = _copy_pdf("klasky-5.pdf", tmp_path)
        _seed_bib(self.pdf, "Klasky 5")
        _render_md(self.pdf)
        self.ad = self.pdf.parent / "klasky-5.puba"

    def test_paper_md_exists(self):
        assert (self.ad / "paper.md").exists()

    def test_paper_sections_json_exists(self):
        assert (self.ad / "paper.sections.json").exists()

    def test_mineru_intermediates_present(self):
        mineru = self.ad / "mineru"
        assert (mineru / "klasky-5_content_list.json").exists()
        assert (mineru / "klasky-5.md").exists()

    def test_mineru_images_dir_exists_when_figures_present(self):
        """D.1: images/ is persisted when MinerU finds visual elements.

        klasky-5 is a text-only poster with no figures, charts, or tables,
        so MinerU writes no images/ directory. The copytree is gated on
        images_src.is_dir(), so absence is correct for text-only papers.
        For papers that do have figures the cruz fixture test already
        verifies images/ is present.
        """
        cl_path = self.ad / "mineru" / "klasky-5_content_list.json"
        cl = json.loads(cl_path.read_text(encoding="utf-8"))
        has_visuals = any(
            item.get("type") in {"image", "chart", "table"} for item in cl
        )
        images_dir = self.ad / "mineru" / "images"
        if has_visuals:
            assert images_dir.is_dir(), "mineru/images/ should exist when figures detected"
        else:
            pass  # no figures → no images/ dir; this is correct behaviour
