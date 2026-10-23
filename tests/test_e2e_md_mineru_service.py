# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Network-gated end-to-end coverage for the MinerU service extractor."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


pytestmark = pytest.mark.network
FIXTURES = Path(__file__).parent / "fixtures"


def test_mineru_service_renders_and_persists_intermediates(tmp_path):
    from puba.md.render import render

    pdf = tmp_path / "klasky-5.pdf"
    shutil.copy2(FIXTURES / pdf.name, pdf)

    paper_md, cached = render(pdf, force=True)

    assert not cached
    assert paper_md.exists()
    analysis_dir = tmp_path / "klasky-5.puba"
    mineru_dir = analysis_dir / "mineru"
    assert (mineru_dir / "klasky-5.md").exists()
    content_list = json.loads(
        (mineru_dir / "klasky-5_content_list.json").read_text(encoding="utf-8")
    )
    assert isinstance(content_list, list) and content_list
    service = json.loads((mineru_dir / "service.json").read_text(encoding="utf-8"))
    assert service["base_url"] == "http://host-localhost:8001"
    assert service["version"]
    assert service["protocol_version"] == 2

    state = json.loads((analysis_dir / ".state.json").read_text(encoding="utf-8"))
    assert state["stages"]["md"]["extractor"] == "service"
    assert state["stages"]["md"]["mineru_service_version"] == service["version"]

    _, cached = render(pdf)
    assert cached
