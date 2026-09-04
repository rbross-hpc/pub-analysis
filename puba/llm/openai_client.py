# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""OpenAI-compatible LLM client wrapper with retries."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from .. import config


def _client() -> OpenAI:
    return OpenAI()


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


def _stream_completion(
    system: str,
    user: str,
    model_role: str,
    model: str | None,
    temperature: float,
) -> str:
    """Request and completely assemble one OpenAI-compatible chat stream."""
    client = _client()
    resolved = model if model is not None else _model(model_role)
    stream = client.chat.completions.create(
        model=resolved,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        stream=True,
    )
    parts: list[str] = []
    for chunk in stream:
        for choice in getattr(chunk, "choices", None) or []:
            content = getattr(getattr(choice, "delta", None), "content", None)
            if content:
                parts.append(content)

    response = "".join(parts).strip()
    if not response:
        raise ValueError("LLM stream completed without response content")
    return response


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20), reraise=True)
def chat_json(
    system: str,
    user: str,
    model_role: str = "bib_extract",
    model: str | None = None,
    temperature: float = 0,
    validate: Callable[[Any], bool] | None = None,
) -> Any:
    raw = _stream_completion(system, user, model_role, model, temperature)
    data = json.loads(_strip_markdown_fence(raw))
    if validate is not None and not validate(data):
        raise ValueError("JSON response did not match the required schema")
    return data


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20), reraise=True)
def chat_text(
    system: str,
    user: str,
    model_role: str = "distill",
    model: str | None = None,
    temperature: float = 0,
) -> str:
    return _stream_completion(system, user, model_role, model, temperature)
