# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Direct OpenAI-compatible one-shot adapter."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .oneshot import (
    OneShotAuthenticationError,
    OneShotCompletionError,
    OneShotConfigurationError,
    OneShotConnectionError,
    OneShotEmptyResponseError,
    OneShotResult,
    OneShotTimeoutError,
)


class OpenAIAdapter:
    """Assemble an internal OpenAI stream into one normalized result."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._client = client
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._closed = False

    def _get_client(self) -> Any:
        with self._lock:
            if self._closed:
                raise OneShotConfigurationError("OpenAI adapter is closed")
            if self._client is None:
                try:
                    if self._client_factory is not None:
                        self._client = self._client_factory()
                    else:
                        from openai import OpenAI

                        self._client = OpenAI()
                except Exception as exc:
                    raise OneShotConfigurationError(
                        f"Could not configure the OpenAI client: {exc}"
                    ) from exc
            return self._client

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        timeout: float,
    ) -> OneShotResult[str]:
        client = self._get_client()
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                timeout=timeout,
                stream=True,
            )
            parts: list[str] = []
            request_id: str | None = None
            finish_reason: str | None = None
            usage: dict[str, int] | None = None
            for chunk in stream:
                chunk_id = getattr(chunk, "id", None)
                if isinstance(chunk_id, str):
                    request_id = chunk_id
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = _normalize_usage(chunk_usage)
                for choice in getattr(chunk, "choices", None) or []:
                    content = getattr(getattr(choice, "delta", None), "content", None)
                    if isinstance(content, str) and content:
                        parts.append(content)
                    reason = getattr(choice, "finish_reason", None)
                    if isinstance(reason, str):
                        finish_reason = reason
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is exc:
                raise
            raise mapped from exc
        value = "".join(parts).strip()
        if not value:
            raise OneShotEmptyResponseError("OpenAI stream completed without text content")
        return OneShotResult(
            value=value,
            backend="openai",
            model=model,
            usage=usage,
            request_id=request_id,
            finish_reason=finish_reason,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def __enter__(self) -> OpenAIAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _normalize_usage(value: Any) -> dict[str, int] | None:
    usage: dict[str, int] = {}
    for source, target in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        item = getattr(value, source, None)
        if isinstance(item, int):
            usage[target] = item
    return usage or None


def _map_error(exc: Exception) -> Exception:
    """Map SDK errors without making SDK classes part of the public API."""
    try:
        import openai
    except ImportError:
        return exc
    if isinstance(exc, getattr(openai, "AuthenticationError", ())):
        return OneShotAuthenticationError("OpenAI authentication failed")
    if isinstance(exc, getattr(openai, "APITimeoutError", ())):
        return OneShotTimeoutError("OpenAI request timed out")
    if isinstance(exc, getattr(openai, "APIConnectionError", ())):
        return OneShotConnectionError("Could not connect to the OpenAI-compatible endpoint")
    if isinstance(exc, getattr(openai, "RateLimitError", ())):
        return OneShotCompletionError("OpenAI rate limit exceeded", retryable=True)
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return OneShotAuthenticationError("OpenAI authentication failed")
    if status == 408:
        return OneShotTimeoutError("OpenAI request timed out")
    if status in (409, 429) or isinstance(exc, getattr(openai, "InternalServerError", ())):
        return OneShotCompletionError(str(exc), retryable=True)
    if isinstance(exc, getattr(openai, "BadRequestError", ())) or isinstance(status, int):
        return OneShotCompletionError(str(exc), retryable=status is not None and status >= 500)
    if isinstance(exc, (TimeoutError,)):
        return OneShotTimeoutError("OpenAI request timed out")
    if isinstance(exc, (ConnectionError,)):
        return OneShotConnectionError("Could not connect to the OpenAI-compatible endpoint")
    return exc
