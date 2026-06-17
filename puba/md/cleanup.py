# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""LLM section cleanup — strict artifact-only correction."""
from __future__ import annotations

from .._common_prompts import MD_CLEANUP_SYSTEM
from ..llm import argo as _argo


def clean_section(body: str, section_title: str) -> str:
    """Send one section body through the LLM cleanup prompt. Raises on failure."""
    user = f"Section: {section_title}\n\n{body}"
    return _argo.chat_text(
        system=MD_CLEANUP_SYSTEM,
        user=user,
        model_role="md_cleanup",
    )
