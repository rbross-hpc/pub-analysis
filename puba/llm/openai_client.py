# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Backend-neutral LLM client wrapper with retries."""
from __future__ import annotations

import atexit
import json
from collections.abc import Callable
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from .. import config


_client_instance: Any | None = None
_client_backend: str | None = None


def _new_client(backend: str) -> Any:
    if backend == "bridge":
        from oc_openai_bridge import OpenAI
    elif backend == "openai":
        from openai import OpenAI
    else:
        raise ValueError(f"Unsupported LLM backend: {backend!r}")
    return OpenAI()


def _client() -> Any:
    global _client_instance, _client_backend
    backend = config.llm_backend()
    if _client_instance is None or _client_backend != backend:
        close_client()
        _client_instance = _new_client(backend)
        _client_backend = backend
    return _client_instance


def close_client() -> None:
    """Close the process-wide LLM client, if one was created."""
    global _client_instance, _client_backend
    client = _client_instance
    _client_instance = None
    _client_backend = None
    if client is not None:
        client.close()


atexit.register(close_client)


def _model(role: str = "bib_extract") -> str:
    return config.models().get(role, "openai/gpt-5.6-sol")


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
    json_mode: bool = False,
) -> str:
    """Request and completely assemble one chat-completion stream."""
    backend = config.llm_backend()
    if backend == "bridge" and temperature != 0:
        raise ValueError("The bridge backend does not support temperature")
    client = _client()
    resolved = model if model is not None else _model(model_role)
    kwargs: dict[str, Any] = {
        "model": resolved,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "timeout": config.llm().get("timeout_s", 120),
        "stream": True,
    }
    if backend == "bridge" and json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if backend == "openai":
        kwargs["temperature"] = temperature
    stream = client.chat.completions.create(**kwargs)
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
    raw = _stream_completion(system, user, model_role, model, temperature, json_mode=True)
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
