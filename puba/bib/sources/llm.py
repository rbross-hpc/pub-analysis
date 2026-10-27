# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""LLM-based fallback metadata extractor from the PDF's initial pages."""
from __future__ import annotations

from typing import Any

from ..._common_prompts import BIB_EXTRACT_SYSTEM
from ... import llm


def extract_from_initial_pages(
    initial_pages_text: str,
    model: str | None = None,
) -> dict[str, Any] | None:
    try:
        if model is None:
            raise ValueError("A resolved model is required")
        result = llm.json_object(
            system=BIB_EXTRACT_SYSTEM,
            user=initial_pages_text[:3000],
            model=model,
            validate=lambda value: isinstance(value, dict),
        )
        return result.value
    except Exception:
        return None
