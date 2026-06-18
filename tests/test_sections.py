# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
from puba.pdf.sections import detect_sections, derive_short_name, short_names, Section


_SAMPLE = """\
Abstract

We propose a new method for doing things efficiently.

1 Introduction

This paper is about things. We show that things can be done.

2 Methods

We use the following approach.

2.1 Data Collection

We collected data from various sources.

3 Results

Our results show improvement.

4 Conclusion

We conclude that things work.

References

[1] Smith, J. (2020). A paper. Journal, 1(1), 1-10.
"""


def test_detects_abstract():
    sections = detect_sections(_SAMPLE)
    titles = [s.title for s in sections]
    assert any("abstract" in t.lower() for t in titles)


def test_detects_introduction():
    sections = detect_sections(_SAMPLE)
    titles = [s.title for s in sections]
    assert any("introduction" in t.lower() for t in titles)


def test_detects_numbered_sections():
    sections = detect_sections(_SAMPLE)
    numbered = [s for s in sections if s.title.startswith(("1 ", "2 ", "3 ", "4 "))]
    assert len(numbered) >= 3


def test_section_spans_are_ordered():
    sections = detect_sections(_SAMPLE)
    for i in range(len(sections) - 1):
        assert sections[i].start < sections[i + 1].start


def test_section_ends_before_next_starts():
    sections = detect_sections(_SAMPLE)
    for i in range(len(sections) - 1):
        assert sections[i].end == sections[i + 1].start


def test_sections_have_short_names():
    sections = detect_sections(_SAMPLE)
    for s in sections:
        assert s.short_name
        assert s.short_name[0].isalpha() or s.short_name[0] == "_"


# ---------------------------------------------------------------------------
# derive_short_name
# ---------------------------------------------------------------------------

def test_slug_basic():
    assert derive_short_name("Methods") == "methods"


def test_slug_strips_leading_number():
    assert derive_short_name("2.1 Related Work") == "related_work"


def test_slug_single_number():
    assert derive_short_name("1 Introduction") == "introduction"


def test_slug_punctuation():
    assert derive_short_name("Results & Discussion") == "results_discussion"


def test_slug_digit_safety():
    s = derive_short_name("3D Printing")
    assert s[0].isalpha() or s[0] == "_"
    assert "3d" in s or s.startswith("s_")


def test_slug_empty():
    assert derive_short_name("") == "section"


def test_slug_only_numbers():
    s = derive_short_name("123")
    assert s[0].isalpha() or s[0] == "_"


def test_slug_truncation():
    long_title = "This is a very long section title with many words that exceeds the maximum"
    s = derive_short_name(long_title)
    assert s == "this_is_a_very"


def test_slug_no_trailing_underscore():
    s = derive_short_name("Methods:")
    assert not s.endswith("_")


# ---------------------------------------------------------------------------
# short_names — collision disambiguation
# ---------------------------------------------------------------------------

def _make_sections(*titles: str) -> list[Section]:
    return [Section(title=t, level=1, start=0, end=0) for t in titles]


def test_short_names_unique():
    secs = _make_sections("Methods", "Results", "Discussion")
    names = short_names(secs)
    assert len(names) == len(set(names))


def test_short_names_collision_disambiguated():
    secs = _make_sections("Methods", "Methods", "Methods")
    names = short_names(secs)
    assert names[0] == "methods"
    assert names[1] == "methods_2"
    assert names[2] == "methods_3"


def test_short_names_mixed_collision():
    secs = _make_sections("1 Introduction", "2 Methods", "3 Methods")
    names = short_names(secs)
    assert names[1] == "methods"
    assert names[2] == "methods_2"


def test_short_names_all_valid_identifiers():
    secs = _make_sections("Abstract", "1 Introduction", "2.1 Data Collection",
                          "3 Results & Discussion", "4 Conclusion", "References")
    names = short_names(secs)
    import re
    pat = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    for name in names:
        assert pat.match(name), f"Invalid short name: {name!r}"


def test_sections_to_json_includes_short_name():
    from puba.pdf.sections import sections_to_json
    secs = detect_sections(_SAMPLE)
    data = sections_to_json(secs)
    for entry in data:
        assert "short_name" in entry
        assert entry["short_name"]
