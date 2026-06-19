# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Extract per-figure artifacts from MinerU's already-computed layout output."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import fitz

_DEFAULT_TYPES: frozenset[str] = frozenset({"image", "chart", "table"})

_CAPTION_KEYS: dict[str, str] = {
    "image": "image_caption",
    "chart": "chart_caption",
    "table": "table_caption",
}
_FOOTNOTE_KEYS: dict[str, str] = {
    "image": "image_footnote",
    "chart": "chart_footnote",
    "table": "table_footnote",
}


def _join_or_none(lst: list[str] | None) -> str | None:
    if not lst:
        return None
    joined = "\n".join(s for s in lst if s and s.strip())
    return joined if joined.strip() else None


def _sha_from_img_path(img_path: str) -> str:
    return Path(img_path).stem


def _pixel_dims(jpg_path: Path) -> tuple[int, int]:
    """Return (width, height) in pixels using fitz.Pixmap."""
    try:
        pm = fitz.Pixmap(str(jpg_path))
        return pm.width, pm.height
    except Exception:
        return 0, 0


def extract(
    pdf_path: Path,
    *,
    types: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Extract per-figure artifacts derived from MinerU's content_list.json.

    Reads paper.puba/mineru/<stem>_content_list.json and mineru/images/*.jpg.
    Writes:
      paper.puba/figures/page{NNN}_img{M}.jpg   — copy of MinerU crop
      paper.puba/figures/page{NNN}_img{M}.json  — per-figure sidecar
      paper.puba/paper.figures.json             — manifest

    Returns the manifest dict.
    Raises RuntimeError if the md stage has not been run yet.
    """
    from ..state import analysis_dir as _ad, is_stage_current, mark_stage_complete
    from .. import config as cfg

    figures_version = cfg.figures().get("figures_version", "figures-1")
    active_types = frozenset(types) if types is not None else _DEFAULT_TYPES
    sorted_types = sorted(active_types)

    ad = _ad(pdf_path)

    if not force and is_stage_current(
        ad, pdf_path, "figures", figures_version,
        extra_key={"types": sorted_types},
    ):
        manifest_path = ad / "paper.figures.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))

    cl_path = ad / "mineru" / f"{pdf_path.stem}_content_list.json"
    if not cl_path.exists():
        raise RuntimeError(
            f"MinerU content list not found: {cl_path}\n"
            "Run 'puba md <pdf>' first."
        )

    content_list: list[dict] = json.loads(cl_path.read_text(encoding="utf-8"))
    mineru_images_dir = ad / "mineru" / "images"
    figures_dir = ad / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    mineru_version = cfg.md().get("mineru_version", "mineru-1")

    figures: list[dict[str, Any]] = []
    seq_per_page: dict[int, int] = {}

    for item in content_list:
        item_type = item.get("type", "")
        if item_type not in active_types:
            continue

        img_path_rel = item.get("img_path", "")
        if not img_path_rel:
            continue

        sha = _sha_from_img_path(img_path_rel)
        src_jpg = mineru_images_dir / f"{sha}.jpg"
        if not src_jpg.exists():
            continue

        page_idx: int = item.get("page_idx", 0)
        page: int = page_idx + 1

        seq = seq_per_page.get(page_idx, 0) + 1
        seq_per_page[page_idx] = seq

        fig_id = f"page{page:03d}_img{seq}"

        bbox: list[float] = item.get("bbox", [])
        width_pt = int(round(bbox[2] - bbox[0])) if len(bbox) == 4 else 0
        height_pt = int(round(bbox[3] - bbox[1])) if len(bbox) == 4 else 0

        dst_jpg = figures_dir / f"{fig_id}.jpg"
        shutil.copy2(src_jpg, dst_jpg)

        width_px, height_px = _pixel_dims(dst_jpg)

        cap_key = _CAPTION_KEYS.get(item_type, f"{item_type}_caption")
        fn_key = _FOOTNOTE_KEYS.get(item_type, f"{item_type}_footnote")
        caption = _join_or_none(item.get(cap_key))
        footnote = _join_or_none(item.get(fn_key))

        entry: dict[str, Any] = {
            "id": fig_id,
            "page": page,
            "page_idx": page_idx,
            "type": item_type,
            "bbox": bbox,
            "width_pt": width_pt,
            "height_pt": height_pt,
            "width_px": width_px,
            "height_px": height_px,
            "caption": caption,
            "footnote": footnote,
            "source_sha": sha,
            "jpg": str(dst_jpg),
        }

        sidecar = figures_dir / f"{fig_id}.json"
        sidecar.write_text(json.dumps(entry, indent=2), encoding="utf-8")

        figures.append(entry)

    manifest: dict[str, Any] = {
        "mineru_version": mineru_version,
        "figures_version": figures_version,
        "figures": figures,
    }
    manifest_path = ad / "paper.figures.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    mark_stage_complete(
        ad, pdf_path, "figures", figures_version,
        extra={"types": sorted_types},
    )

    return manifest
