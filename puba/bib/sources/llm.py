# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""LLM-based fallback metadata extractor from page-1 text."""
from __future__ import annotations

from typing import Any

from ..._common_prompts import BIB_EXTRACT_SYSTEM
from ...llm import argo as _argo


def extract_from_page1(page1_text: str) -> dict[str, Any] | None:
    try:
        data = _argo.chat_json(
            system=BIB_EXTRACT_SYSTEM,
            user=page1_text[:3000],
            model_role="bib_extract",
        )
        return data if isinstance(data, dict) else None
    except Exception:
        return None
