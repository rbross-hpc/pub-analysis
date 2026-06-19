# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Tests for the extra_key parameter of is_stage_current in puba/state.py."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return pdf


@pytest.fixture
def analysis_dir(fake_pdf: Path) -> Path:
    ad = fake_pdf.parent / "paper.puba"
    ad.mkdir()
    return ad


def _complete(analysis_dir, fake_pdf, extra=None):
    from puba.state import mark_stage_complete
    mark_stage_complete(analysis_dir, fake_pdf, "figures", "figures-1", extra=extra)


def test_extra_key_match_is_cache_hit(analysis_dir, fake_pdf):
    _complete(analysis_dir, fake_pdf, extra={"types": ["chart", "image", "table"]})
    from puba.state import is_stage_current
    assert is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        extra_key={"types": ["chart", "image", "table"]},
    )


def test_extra_key_mismatch_is_cache_miss(analysis_dir, fake_pdf):
    _complete(analysis_dir, fake_pdf, extra={"types": ["chart", "image", "table"]})
    from puba.state import is_stage_current
    assert not is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        extra_key={"types": ["image"]},
    )


def test_extra_key_missing_in_state_is_cache_miss(analysis_dir, fake_pdf):
    _complete(analysis_dir, fake_pdf, extra=None)
    from puba.state import is_stage_current
    assert not is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        extra_key={"types": ["image"]},
    )


def test_no_extra_key_ignores_extras_in_state(analysis_dir, fake_pdf):
    _complete(analysis_dir, fake_pdf, extra={"types": ["image"]})
    from puba.state import is_stage_current
    assert is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        extra_key=None,
    )


def test_extra_key_combined_with_model(analysis_dir, fake_pdf):
    from puba.state import mark_stage_complete, is_stage_current
    mark_stage_complete(
        analysis_dir, fake_pdf, "figures", "figures-1",
        model="GPT-5.4",
        extra={"types": ["image"]},
    )
    assert is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        model="GPT-5.4",
        extra_key={"types": ["image"]},
    )
    assert not is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        model="GPT-5.4",
        extra_key={"types": ["chart"]},
    )
    assert not is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        model="Claude Sonnet 4.6",
        extra_key={"types": ["image"]},
    )


def test_extra_key_multiple_fields_all_must_match(analysis_dir, fake_pdf):
    _complete(analysis_dir, fake_pdf, extra={"types": ["image"], "version": 2})
    from puba.state import is_stage_current
    assert is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        extra_key={"types": ["image"], "version": 2},
    )
    assert not is_stage_current(
        analysis_dir, fake_pdf, "figures", "figures-1",
        extra_key={"types": ["image"], "version": 3},
    )
