# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
from puba.pdf.repair import repair


def test_dehyphenation_basic():
    assert repair("algo-\nrithm") == "algorithm"


def test_dehyphenation_preserves_urls():
    text = "see https://example.com/some-path for details"
    assert "https://example.com/some-path" in repair(text)


def test_ligature_fi():
    assert repair("\ufb01le") == "file"


def test_ligature_fl():
    assert repair("\ufb02ow") == "flow"


def test_soft_hyphen_stripped():
    assert repair("co\xadoperate") == "cooperate"


def test_split_glyph():
    assert repair("V ector space") == "Vector space"


def test_no_damage_normal_text():
    text = "This is a normal sentence with no artifacts."
    assert repair(text) == text
