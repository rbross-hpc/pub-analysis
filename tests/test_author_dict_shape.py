# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Tests for uniform author dict shape across all bib sources.

Every source must emit authors as list[dict] with keys:
  name, orcid, orcid_authenticated, openalex_author_id, author_position, affiliations

Sources that don't have a field populate it as None / [].
"""
from __future__ import annotations

import pytest

from puba.bib.sources._common import _normalize_orcid, make_author
from puba.bib.sources.openalex import _summarize as oa_summarize
from puba.bib.sources.crossref import _summarize as cr_summarize, _extract_authors
from puba.bib.sources.semanticscholar import _summarize as ss_summarize
from puba.bib.sources.osti import _summarize as osti_summarize
from puba.bib.sources.dblp import _summarize as dblp_summarize
from puba.bib.sources.bibtex import load_bib_file
from pathlib import Path
import tempfile


_AUTHOR_KEYS = {"name", "orcid", "orcid_authenticated", "openalex_author_id",
                "author_position", "affiliations"}


def _assert_author_shape(a: dict, *, name: str = None) -> None:
    assert isinstance(a, dict), f"Expected dict, got {type(a)}"
    assert _AUTHOR_KEYS == set(a.keys()), f"Key mismatch: {set(a.keys())} vs {_AUTHOR_KEYS}"
    assert isinstance(a["name"], str) and a["name"], "name must be a non-empty string"
    assert isinstance(a["affiliations"], list), "affiliations must be a list"
    if name:
        assert a["name"] == name


class TestNormalizeOrcid:
    def test_strips_url_prefix(self):
        assert _normalize_orcid("https://orcid.org/0000-0002-1234-567X") == "0000-0002-1234-567X"

    def test_strips_http_prefix(self):
        assert _normalize_orcid("http://orcid.org/0000-0002-1234-5678") == "0000-0002-1234-5678"

    def test_bare_orcid_passthrough(self):
        assert _normalize_orcid("0000-0002-1234-567X") == "0000-0002-1234-567X"

    def test_invalid_format_returns_none(self):
        assert _normalize_orcid("0000000152944116") is None  # no hyphens

    def test_none_returns_none(self):
        assert _normalize_orcid(None) is None

    def test_empty_returns_none(self):
        assert _normalize_orcid("") is None


class TestMakeAuthor:
    def test_minimal(self):
        a = make_author("Alice Smith")
        _assert_author_shape(a, name="Alice Smith")
        assert a["orcid"] is None
        assert a["affiliations"] == []

    def test_with_all_fields(self):
        a = make_author(
            "Bob Jones",
            orcid="0000-0002-1234-5678",
            orcid_authenticated=True,
            openalex_author_id="https://openalex.org/A123",
            author_position="first",
            affiliations=[{"name": "ANL", "ror": None, "openalex_id": None, "country_code": "US"}],
        )
        _assert_author_shape(a, name="Bob Jones")
        assert a["orcid"] == "0000-0002-1234-5678"
        assert a["orcid_authenticated"] is True
        assert a["author_position"] == "first"
        assert len(a["affiliations"]) == 1


class TestOpenAlexAuthorShape:
    _BASE = {
        "id": "https://openalex.org/W123",
        "display_name": "Test Paper",
        "publication_year": 2024,
        "publication_date": "2024-01-01",
        "type": "journal-article",
        "doi": None,
        "primary_location": {},
        "keywords": [],
        "open_access": {},
        "abstract_inverted_index": None,
    }

    def test_empty_authorships(self):
        work = dict(self._BASE, authorships=[])
        result = oa_summarize(work)
        assert result["authors"] == []

    def test_author_with_orcid(self):
        work = dict(self._BASE, authorships=[{
            "author": {
                "id": "https://openalex.org/A9876",
                "display_name": "Alice Smith",
                "orcid": "https://orcid.org/0000-0002-1234-5678",
            },
            "author_position": "first",
            "institutions": [],
        }])
        result = oa_summarize(work)
        assert len(result["authors"]) == 1
        a = result["authors"][0]
        _assert_author_shape(a, name="Alice Smith")
        assert a["orcid"] == "0000-0002-1234-5678"
        assert a["openalex_author_id"] == "https://openalex.org/A9876"
        assert a["author_position"] == "first"
        assert a["affiliations"] == []

    def test_author_with_institution(self):
        work = dict(self._BASE, authorships=[{
            "author": {"id": "https://openalex.org/A1", "display_name": "Bob Jones", "orcid": None},
            "author_position": "last",
            "institutions": [{
                "id": "https://openalex.org/I27837315",
                "display_name": "Argonne National Laboratory",
                "ror": "https://ror.org/05gvnxz63",
                "country_code": "US",
            }],
        }])
        result = oa_summarize(work)
        a = result["authors"][0]
        _assert_author_shape(a, name="Bob Jones")
        assert a["author_position"] == "last"
        assert len(a["affiliations"]) == 1
        aff = a["affiliations"][0]
        assert aff["name"] == "Argonne National Laboratory"
        assert aff["ror"] == "https://ror.org/05gvnxz63"
        assert aff["country_code"] == "US"

    def test_author_multi_institution(self):
        work = dict(self._BASE, authorships=[{
            "author": {"id": "https://openalex.org/A2", "display_name": "Carol", "orcid": None},
            "author_position": "middle",
            "institutions": [
                {"id": "OA/I1", "display_name": "MIT", "ror": None, "country_code": "US"},
                {"id": "OA/I2", "display_name": "Harvard", "ror": None, "country_code": "US"},
            ],
        }])
        result = oa_summarize(work)
        assert len(result["authors"][0]["affiliations"]) == 2

    def test_author_no_display_name_skipped(self):
        work = dict(self._BASE, authorships=[
            {"author": {"id": "OA/A1", "display_name": "", "orcid": None},
             "author_position": "first", "institutions": []},
            {"author": {"id": "OA/A2", "display_name": "Real Author", "orcid": None},
             "author_position": "last", "institutions": []},
        ])
        result = oa_summarize(work)
        assert len(result["authors"]) == 1
        assert result["authors"][0]["name"] == "Real Author"

    def test_multiple_authors_positions(self):
        work = dict(self._BASE, authorships=[
            {"author": {"id": "OA/A1", "display_name": "First", "orcid": None},
             "author_position": "first", "institutions": []},
            {"author": {"id": "OA/A2", "display_name": "Mid", "orcid": None},
             "author_position": "middle", "institutions": []},
            {"author": {"id": "OA/A3", "display_name": "Last", "orcid": None},
             "author_position": "last", "institutions": []},
        ])
        result = oa_summarize(work)
        positions = [a["author_position"] for a in result["authors"]]
        assert positions == ["first", "middle", "last"]


class TestCrossRefAuthorShape:
    _BASE = {
        "type": "journal-article",
        "DOI": "10.1145/3682060",
        "title": ["Test Paper"],
        "published": {"date-parts": [[2024, 1, 1]]},
        "container-title": ["Some Journal"],
    }

    def test_basic_author(self):
        item = dict(self._BASE, author=[{"given": "Alice", "family": "Smith"}])
        result = cr_summarize(item)
        assert len(result["authors"]) == 1
        _assert_author_shape(result["authors"][0], name="Alice Smith")

    def test_orcid_extracted(self):
        result = _extract_authors({
            "author": [{"given": "Bob", "family": "Jones",
                        "ORCID": "https://orcid.org/0000-0002-9999-8888",
                        "authenticated-orcid": True}]
        })
        a = result[0]
        _assert_author_shape(a, name="Bob Jones")
        assert a["orcid"] == "0000-0002-9999-8888"
        assert a["orcid_authenticated"] is True

    def test_sequence_first_maps_to_first(self):
        result = _extract_authors({
            "author": [{"given": "Alice", "family": "Smith", "sequence": "first"}]
        })
        assert result[0]["author_position"] == "first"

    def test_sequence_additional_maps_to_none(self):
        result = _extract_authors({
            "author": [{"given": "Bob", "family": "Jones", "sequence": "additional"}]
        })
        assert result[0]["author_position"] is None

    def test_affiliation_name_only(self):
        result = _extract_authors({
            "author": [{
                "given": "Alice", "family": "Smith",
                "affiliation": [{"name": "MIT"}, {"name": "Harvard"}],
            }]
        })
        affs = result[0]["affiliations"]
        assert len(affs) == 2
        assert affs[0]["name"] == "MIT"
        assert affs[0]["ror"] is None

    def test_no_orcid_is_none(self):
        result = _extract_authors({
            "author": [{"given": "A", "family": "B"}]
        })
        assert result[0]["orcid"] is None
        assert result[0]["orcid_authenticated"] is None

    def test_family_only_name(self):
        result = _extract_authors({"author": [{"family": "Smith"}]})
        assert result[0]["name"] == "Smith"

    def test_empty_name_skipped(self):
        result = _extract_authors({"author": [{"given": "", "family": ""}]})
        assert result == []


class TestOtherSourcesShape:
    def test_ss_author_shape(self):
        result = ss_summarize({
            "title": "Test",
            "authors": [{"name": "Alice Smith", "authorId": "123"}],
            "year": 2024,
            "publicationDate": "2024-01-01",
            "venue": "IPDPS",
            "externalIds": {},
            "publicationTypes": ["Conference"],
        })
        assert len(result["authors"]) == 1
        _assert_author_shape(result["authors"][0], name="Alice Smith")
        assert result["authors"][0]["orcid"] is None

    def test_dblp_author_shape(self):
        result = dblp_summarize({
            "info": {
                "title": "Test",
                "type": "Journal Articles",
                "year": "2024",
                "authors": {"author": [{"text": "Alice Smith"}, {"text": "Bob Jones"}]},
                "doi": "10.1145/test",
            }
        })
        for a in result["authors"]:
            _assert_author_shape(a)
        assert result["authors"][0]["name"] == "Alice Smith"

    def test_bibtex_author_shape(self):
        bib = "@article{test2024, title={Test}, author={Smith, Alice and Jones, Bob}, year={2024}, journal={X}}"
        with tempfile.NamedTemporaryFile(suffix=".bib", mode="w", delete=False) as f:
            f.write(bib)
            path = Path(f.name)
        try:
            from puba.bib.sources.bibtex import load_bib_file
            entries = load_bib_file(path)
            assert len(entries) == 1
            for a in entries[0]["authors"]:
                _assert_author_shape(a)
        finally:
            path.unlink()
