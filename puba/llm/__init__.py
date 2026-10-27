"""Configured process-wide owner of puba's one-shot LLM client."""
from __future__ import annotations

import atexit
import threading
from typing import Any

from .. import config
from .oneshot import (
    OneShotClient,
    OneShotConfigurationError,
    OneShotResult,
    Validator,
)

_client_instance: OneShotClient | None = None
_client_backend: str | None = None
_client_lock = threading.RLock()
_client_condition = threading.Condition(_client_lock)
_client_active_calls = 0


def _new_client(backend: str) -> OneShotClient:
    if backend == "opencode":
        from .opencode import OpenCodeAdapter

        adapter = OpenCodeAdapter()
    elif backend == "openai":
        from .openai import OpenAIAdapter

        adapter = OpenAIAdapter()
    else:
        raise OneShotConfigurationError(f"Unsupported LLM backend: {backend!r}")
    return OneShotClient(adapter, timeout=float(config.llm().get("timeout_s", 120)))


def _client() -> OneShotClient:
    global _client_instance, _client_backend
    backend = config.llm_backend()
    with _client_condition:
        if _client_instance is None or _client_backend != backend:
            while _client_active_calls:
                _client_condition.wait()
            old_client = _client_instance
            _client_instance = None
            _client_backend = None
            if old_client is not None:
                old_client.close()
            _client_instance = _new_client(backend)
            _client_backend = backend
        return _client_instance


def _acquire_client() -> OneShotClient:
    global _client_active_calls
    with _client_condition:
        client = _client()
        _client_active_calls += 1
        return client


def _release_client() -> None:
    global _client_active_calls
    with _client_condition:
        _client_active_calls -= 1
        if not _client_active_calls:
            _client_condition.notify_all()


def close_client() -> None:
    """Close and forget the configured process-wide client."""
    global _client_instance, _client_backend
    with _client_condition:
        while _client_active_calls:
            _client_condition.wait()
        client = _client_instance
        _client_instance = None
        _client_backend = None
        if client is not None:
            client.close()


def text(*, system: str, user: str, model: str) -> OneShotResult[str]:
    client = _acquire_client()
    try:
        return client.text(system=system, user=user, model=model)
    finally:
        _release_client()


def json_object(
    *, system: str, user: str, model: str, validate: Validator,
) -> OneShotResult[dict[str, Any]]:
    client = _acquire_client()
    try:
        return client.json_object(
            system=system, user=user, model=model, validate=validate,
        )
    finally:
        _release_client()


atexit.register(close_client)

__all__ = ["OneShotResult", "close_client", "json_object", "text"]
