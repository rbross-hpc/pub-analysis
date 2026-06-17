# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
from puba.pdf.sections import detect_sections


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
