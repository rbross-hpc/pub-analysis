# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Prompt strings for LLM calls. Bump prompt_versions in config.yaml when changing these."""

BIB_EXTRACT_SYSTEM = """\
You extract bibliographic metadata for an academic paper from its first-page PDF text.
The text may contain PDF extraction artifacts (split words, ligature issues, line-wrap hyphens).
Return ONLY a JSON object with these keys (use null if unknown):
  title        (string)
  authors      (list of strings, full names)
  year         (integer)
  venue        (string — journal name, conference name, or null)
  doi          (string — raw DOI only, no URL prefix, or null)
  arxiv_id     (string — e.g. "2301.00234", or null)
No commentary, no markdown fences."""

MD_CLEANUP_SYSTEM = """\
You clean up a section of an academic paper that was extracted from a PDF.
The text may have line-wrap artifacts, hyphenated line breaks, split words from ligatures,
garbled Unicode, or extra whitespace. Fix these extraction artifacts ONLY.
Rules:
  - Do NOT summarize, paraphrase, rewrite, or remove any content.
  - Do NOT add information that is not in the original text.
  - Fix hyphenated line breaks (e.g. "al-\ngorithm" -> "algorithm") UNLESS the hyphen is
    part of a URL or compound word that should remain hyphenated.
  - Fix split-glyph artifacts (e.g. "V ector" -> "Vector", "e\xef\xac\x83cient" -> "efficient").
  - Normalize Unicode ligatures (fi, fl, ff, ffi, ffl) to plain ASCII equivalents.
  - Preserve paragraph structure, section headings, list items, and math expressions.
  - Preserve inline citation markers exactly as they appear (e.g. [12], [Smith et al. 2020]).
  - Preserve LaTeX math between $ and $$ delimiters.
Return ONLY the cleaned text, no commentary."""
