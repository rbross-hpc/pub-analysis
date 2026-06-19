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
