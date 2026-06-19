# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""LLM-based fallback metadata extractor from the PDF's initial pages."""
from __future__ import annotations

from typing import Any

from ..._common_prompts import BIB_EXTRACT_SYSTEM
from ...llm import openai_client


def extract_from_initial_pages(
    initial_pages_text: str,
    model: str | None = None,
) -> dict[str, Any] | None:
    try:
        data = openai_client.chat_json(
            system=BIB_EXTRACT_SYSTEM,
            user=initial_pages_text[:3000],
            model_role="bib_extract",
            model=model,
        )
        return data if isinstance(data, dict) else None
    except Exception:
        return None
