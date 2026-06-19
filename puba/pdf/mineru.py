# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Subprocess wrapper for MinerU pipeline PDF extraction (formula recognition disabled)."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


_INSTALL_HINT = (
    "MinerU not installed or not on PATH. "
    "Install with: pip install 'mineru[pipeline]>=3.4' 'accelerate>=1.14' "
    "'opencv-python-headless>=4.13'"
)

_INTERMEDIATE_FILES = (
    "{stem}.md",
    "{stem}_content_list.json",
    "{stem}_content_list_v2.json",
    "{stem}_middle.json",
    "{stem}_layout.pdf",
)


def run_mineru(pdf_path: Path, analysis_dir: Path) -> tuple[str, list[dict]]:
    """Run MinerU on pdf_path and return (markdown_text, content_list).

    Uses pipeline backend with formula recognition disabled.

    Intermediate files are copied into analysis_dir/mineru/ for debugging:
    <stem>.md, <stem>_content_list.json, <stem>_content_list_v2.json,
    <stem>_middle.json, <stem>_layout.pdf. Missing files are silently skipped.

    Raises RuntimeError if MinerU is not installed or exits non-zero.
    """
    if not shutil.which("mineru"):
        raise RuntimeError(_INSTALL_HINT)

    with tempfile.TemporaryDirectory(prefix="puba-mineru-") as tmp:
        tmp_path = Path(tmp)
        cmd = [
            "mineru",
            "-p", str(pdf_path),
            "-o", str(tmp_path),
            "-b", "pipeline",
            "-f", "false",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr_tail = result.stderr.strip().splitlines()
            tail = "\n".join(stderr_tail[-20:]) if stderr_tail else "(no stderr)"
            raise RuntimeError(
                f"MinerU failed (exit {result.returncode}) on {pdf_path.name}:\n{tail}"
            )

        stem = pdf_path.stem
        out_dir = tmp_path / stem / "auto"

        md_path = out_dir / f"{stem}.md"
        cl_path = out_dir / f"{stem}_content_list.json"

        if not md_path.exists():
            raise RuntimeError(
                f"MinerU completed but expected output not found: {md_path}"
            )

        markdown_text = md_path.read_text(encoding="utf-8")
        content_list: list[dict] = []
        if cl_path.exists():
            content_list = json.loads(cl_path.read_text(encoding="utf-8"))

        mineru_dir = analysis_dir / "mineru"
        mineru_dir.mkdir(parents=True, exist_ok=True)
        for pattern in _INTERMEDIATE_FILES:
            src = out_dir / pattern.format(stem=stem)
            if src.exists():
                shutil.copy2(src, mineru_dir / src.name)

        images_src = out_dir / "images"
        if images_src.is_dir():
            shutil.copytree(images_src, mineru_dir / "images", dirs_exist_ok=True)

        return markdown_text, content_list
