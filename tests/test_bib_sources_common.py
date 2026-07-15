"""Tests for puba.bib.sources._common: is_placeholder_doi and extract_doi."""
from __future__ import annotations

import pytest

from puba.bib.sources._common import extract_doi, is_placeholder_doi, normalize_doi


class TestIsPlaceholderDoi:
    def test_acm_nnnnnnn(self):
        assert is_placeholder_doi("10.1145/nnnnnnn.nnnnnnn")

    def test_acm_nnnnnnn_uppercase(self):
        assert is_placeholder_doi("10.1145/NNNNNNN.NNNNNNN")

    def test_acm_zeros(self):
        assert is_placeholder_doi("10.1145/0000000.0000000")

    def test_acm_zeros_short_prefix(self):
        assert is_placeholder_doi("10.1145/00000.00000")

    def test_acm_xxxx(self):
        assert is_placeholder_doi("10.1145/xxxxxxx.xxxxxxx")

    def test_bare_prefix_only(self):
        assert is_placeholder_doi("10.1234/")

    def test_bare_prefix_whitespace(self):
        assert is_placeholder_doi("10.1234/  ")

    def test_example_suffix(self):
        assert is_placeholder_doi("10.5555/example")

    def test_sample_suffix(self):
        assert is_placeholder_doi("10.5555/sample")

    def test_placeholder_suffix(self):
        assert is_placeholder_doi("10.5555/placeholder")

    def test_template_suffix(self):
        assert is_placeholder_doi("10.5555/template")

    def test_todo_suffix(self):
        assert is_placeholder_doi("10.1109/todo")

    def test_your_doi_hyphen(self):
        assert is_placeholder_doi("10.1234/your-doi")

    def test_your_doi_underscore(self):
        assert is_placeholder_doi("10.1234/your_doi")

    def test_with_https_prefix(self):
        assert is_placeholder_doi("https://doi.org/10.1145/nnnnnnn.nnnnnnn")

    def test_with_doi_colon_prefix(self):
        assert is_placeholder_doi("doi:10.1145/nnnnnnn.nnnnnnn")

    def test_none_returns_false(self):
        assert not is_placeholder_doi(None)

    def test_empty_string_returns_false(self):
        assert not is_placeholder_doi("")

    def test_real_doi_returns_false(self):
        assert not is_placeholder_doi("10.1145/3503222.3507734")

    def test_real_doi_with_prefix_returns_false(self):
        assert not is_placeholder_doi("https://doi.org/10.1145/3503222.3507734")

    def test_real_doi_arxiv_returns_false(self):
        assert not is_placeholder_doi("10.48550/arxiv.2106.10165")

    def test_real_doi_short_suffix_returns_false(self):
        assert not is_placeholder_doi("10.1126/science.abc1234")

    def test_three_n_not_placeholder(self):
        assert not is_placeholder_doi("10.1145/nnn.nnn")

    def test_three_zeros_not_placeholder(self):
        assert not is_placeholder_doi("10.1145/000.000")


class TestExtractDoi:
    def test_real_doi_extracted(self):
        text = "See https://doi.org/10.1145/3503222.3507734 for details."
        assert extract_doi(text) == "10.1145/3503222.3507734"

    def test_real_doi_bare(self):
        text = "DOI: 10.1109/SC.2019.00005"
        result = extract_doi(text)
        assert result == "10.1109/sc.2019.00005"

    def test_acm_placeholder_returns_none(self):
        text = (
            "ACM Reference Format:\n"
            "Author Name. 2024. Paper Title. In Proceedings. "
            "https://doi.org/10.1145/nnnnnnn.nnnnnnn"
        )
        assert extract_doi(text) is None

    def test_acm_zeros_placeholder_returns_none(self):
        text = "doi:10.1145/0000000.0000000"
        assert extract_doi(text) is None

    def test_xxxx_placeholder_returns_none(self):
        text = "10.1145/xxxxxxx.xxxxxxx"
        assert extract_doi(text) is None

    def test_no_doi_returns_none(self):
        text = "This paper has no DOI anywhere in the text."
        assert extract_doi(text) is None

    def test_empty_text_returns_none(self):
        assert extract_doi("") is None

    def test_real_doi_with_trailing_period(self):
        text = "Published at 10.1145/3503222.3507734."
        result = extract_doi(text)
        assert result == "10.1145/3503222.3507734"
