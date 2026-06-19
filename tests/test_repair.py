# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
from puba.pdf.repair import repair, repair_pages, _strip_headers_footers


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


# ---------------------------------------------------------------------------
# _strip_headers_footers
# ---------------------------------------------------------------------------

def _make_pages(n: int, header: str, footer: str, body: str) -> list[str]:
    return [f"{header}\n{body}\n{footer}" for _ in range(n)]


def test_header_stripped_when_on_all_pages():
    pages = _make_pages(5, "Journal of Things · Smith et al.", "Page 1", "Body text here.")
    result = _strip_headers_footers(pages)
    for page in result:
        assert "Journal of Things" not in page
        assert "Body text here." in page


def test_footer_stripped_when_on_all_pages():
    pages = _make_pages(5, "Normal first line", "Page 42", "Body text here.")
    result = _strip_headers_footers(pages)
    for page in result:
        assert "Page 42" not in page
        assert "Body text here." in page


def test_body_line_not_stripped_even_if_repeated():
    body = "The method achieves O(N log N) complexity."
    pages = [f"Header\n{body}\nSome other body." for _ in range(5)]
    result = _strip_headers_footers(pages)
    for page in result:
        assert body in page


def test_single_page_unchanged():
    page = "Header\nBody text.\nFooter"
    result = _strip_headers_footers([page])
    assert result == [page]


def test_header_not_stripped_below_min_page_fraction():
    header = "Repeating header"
    pages = [f"{header}\nBody." for _ in range(2)] + ["Different\nBody." for _ in range(8)]
    result = _strip_headers_footers(pages)
    assert any(header in p for p in result)


def test_similar_but_varying_headers_stripped():
    bodies = [
        "The quick brown fox jumps.",
        "Neural networks are powerful.",
        "This section describes results.",
        "We compare against baselines.",
        "Conclusions are drawn here.",
    ]
    pages = [
        f"Smith et al. (2025) · Page {i+1}\n{body}\nEnd of page content."
        for i, body in enumerate(bodies)
    ]
    result = _strip_headers_footers(pages, sim_threshold=0.7)
    for i, page in enumerate(result):
        assert "Smith et al." not in page
        assert bodies[i] in page


def test_repair_pages_strips_headers():
    pages = _make_pages(6, "Running Header", "Running Footer", "Body content.")
    result = repair_pages(pages)
    for page in result:
        assert "Running Header" not in page
        assert "Body content." in page
