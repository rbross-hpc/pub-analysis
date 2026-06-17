# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""OpenAI-compatible Argo client wrapper with retries."""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from .. import config


def _client() -> OpenAI:
    return OpenAI(
        base_url=config.argo_base_url(),
        api_key=config.argo_api_key(),
    )


def _model(role: str = "bib_extract") -> str:
    return config.models().get(role, "GPT-5.4")


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return text


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20))
def chat_json(
    system: str,
    user: str,
    model_role: str = "bib_extract",
    temperature: float = 0,
) -> Any:
    client = _client()
    model = _model(model_role)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    raw = (response.choices[0].message.content or "").strip()
    raw = _strip_markdown_fence(raw)
    return json.loads(raw)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20))
def chat_text(
    system: str,
    user: str,
    model_role: str = "md_cleanup",
    temperature: float = 0,
) -> str:
    client = _client()
    model = _model(model_role)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()
