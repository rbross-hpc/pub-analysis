# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for puba.bib.sources.bibtex — load_bib_file and lookup_*."""
from __future__ import annotations

from pathlib import Path

import pytest

from puba.bib.sources.bibtex import (
    BibtexParseError,
    load_bib_file,
    lookup_by_doi,
    lookup_by_title,
)

_VALID_BIB = """\
@article{smith2020fast,
  title     = {Fast Algorithms for Deep Learning},
  author    = {Smith, Alice and Jones, Bob},
  journal   = {Journal of Machine Learning},
  year      = {2020},
  doi       = {10.1234/jml.2020.001},
}

@inproceedings{lee2019graph,
  title     = {Graph Neural Networks at Scale},
  author    = {Lee, Carol},
  booktitle = {Proceedings of NeurIPS},
  year      = {2019},
}
"""


# ---------------------------------------------------------------------------
# load_bib_file — success
# ---------------------------------------------------------------------------

def test_load_bib_file_valid(tmp_path):
    f = tmp_path / "refs.bib"
    f.write_text(_VALID_BIB, encoding="utf-8")
    entries = load_bib_file(f)
    assert len(entries) == 2
    titles = {e["title"] for e in entries}
    assert "Fast Algorithms for Deep Learning" in titles
    assert "Graph Neural Networks at Scale" in titles


# ---------------------------------------------------------------------------
# load_bib_file — hard failures
# ---------------------------------------------------------------------------

def test_load_bib_file_nonexistent_raises(tmp_path):
    with pytest.raises(BibtexParseError, match="not found"):
        load_bib_file(tmp_path / "ghost.bib")


def test_load_bib_file_directory_raises(tmp_path):
    with pytest.raises(BibtexParseError, match="is a directory"):
        load_bib_file(tmp_path)


def test_load_bib_file_empty_file_raises(tmp_path):
    f = tmp_path / "empty.bib"
    f.write_bytes(b"")
    with pytest.raises(BibtexParseError, match="empty"):
        load_bib_file(f)


def test_load_bib_file_whitespace_only_raises(tmp_path):
    f = tmp_path / "whitespace.bib"
    f.write_text("\n\n   \n", encoding="utf-8")
    with pytest.raises(BibtexParseError, match="empty"):
        load_bib_file(f)


def test_load_bib_file_unparseable_raises(tmp_path):
    f = tmp_path / "garbage.bib"
    f.write_text("this is not bibtex at all", encoding="utf-8")
    with pytest.raises(BibtexParseError, match="no parseable entries"):
        load_bib_file(f)


# ---------------------------------------------------------------------------
# lookup_by_doi
# ---------------------------------------------------------------------------

def test_lookup_by_doi_returns_entry_on_match(tmp_path):
    f = tmp_path / "refs.bib"
    f.write_text(_VALID_BIB, encoding="utf-8")
    result = lookup_by_doi("10.1234/jml.2020.001", f)
    assert result is not None
    assert result["title"] == "Fast Algorithms for Deep Learning"


def test_lookup_by_doi_returns_none_when_not_in_valid_file(tmp_path):
    f = tmp_path / "refs.bib"
    f.write_text(_VALID_BIB, encoding="utf-8")
    assert lookup_by_doi("10.9999/nomatch", f) is None


def test_lookup_by_doi_propagates_parse_error(tmp_path):
    f = tmp_path / "bad.bib"
    f.write_text("not bibtex", encoding="utf-8")
    with pytest.raises(BibtexParseError):
        lookup_by_doi("10.1234/anything", f)


# ---------------------------------------------------------------------------
# lookup_by_title
# ---------------------------------------------------------------------------

def test_lookup_by_title_returns_entry_on_match(tmp_path):
    f = tmp_path / "refs.bib"
    f.write_text(_VALID_BIB, encoding="utf-8")
    result, sim = lookup_by_title("Fast Algorithms for Deep Learning", f)
    assert result is not None
    assert sim is not None and sim > 0.95


def test_lookup_by_title_returns_none_below_threshold(tmp_path):
    f = tmp_path / "refs.bib"
    f.write_text(_VALID_BIB, encoding="utf-8")
    result, sim = lookup_by_title("Completely Unrelated Topic in Biology", f)
    assert result is None
    assert sim is None


def test_lookup_by_title_propagates_parse_error(tmp_path):
    f = tmp_path / "bad.bib"
    f.write_text("not bibtex", encoding="utf-8")
    with pytest.raises(BibtexParseError):
        lookup_by_title("Some Title", f)
