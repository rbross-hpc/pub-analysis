# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Provider-neutral, synchronous one-shot LLM interface."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Generic, Protocol, TypeVar


PROTOCOL_VERSION = "oneshot-v1"
JSON_INSTRUCTION_VERSION = "oneshot-json-v1"
_JSON_INSTRUCTION = (
    f"[{JSON_INSTRUCTION_VERSION}] Return exactly one JSON object and no "
    "surrounding prose or Markdown."
)


def json_system_prompt(system: str) -> str:
    """Return the exact system prompt used for structured requests."""
    return f"{system.rstrip()}\n\n{_JSON_INSTRUCTION}"


class OneShotError(Exception):
    """Base class for failures exposed by the one-shot boundary."""


class OneShotConfigurationError(OneShotError):
    pass


class OneShotAuthenticationError(OneShotError):
    pass


class OneShotConnectionError(OneShotError):
    pass


class OneShotTimeoutError(OneShotError):
    pass


class OneShotEmptyResponseError(OneShotError):
    pass


class OneShotDecodeError(OneShotError):
    pass


class OneShotValidationError(OneShotError):
    pass


class OneShotCompletionError(OneShotError):
    """A provider completion failure, optionally safe to retry."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


T = TypeVar("T")


@dataclass(frozen=True)
class OneShotResult(Generic[T]):
    value: T
    backend: str
    model: str
    usage: dict[str, int] | None = None
    cost: float | None = None
    request_id: str | None = None
    finish_reason: str | None = None


class OneShotAdapter(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        timeout: float,
    ) -> OneShotResult[str]: ...

    def close(self) -> None: ...


Validator = Callable[[dict[str, Any]], bool]


class OneShotClient:
    """Retrying facade over a single provider adapter."""

    def __init__(
        self,
        adapter: OneShotAdapter,
        *,
        timeout: float = 120,
        attempts: int = 3,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least one")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._adapter = adapter
        self._timeout = timeout
        self._attempts = attempts
        self._wait = wait

    def text(self, *, system: str, user: str, model: str) -> OneShotResult[str]:
        return self._run(lambda: self._adapter.complete(
            system=system, user=user, model=model, timeout=self._timeout
        ))

    def json_object(
        self,
        *,
        system: str,
        user: str,
        model: str,
        validate: Validator,
    ) -> OneShotResult[dict[str, Any]]:
        instructed_system = json_system_prompt(system)

        def attempt() -> OneShotResult[dict[str, Any]]:
            result = self._adapter.complete(
                system=instructed_system,
                user=user,
                model=model,
                timeout=self._timeout,
            )
            text = _strip_json_fence(result.value)
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise OneShotDecodeError("LLM response is not valid JSON") from exc
            if not isinstance(value, dict):
                raise OneShotValidationError("LLM response is not a JSON object")
            if not validate(value):
                raise OneShotValidationError(
                    "JSON response did not match the required schema"
                )
            return replace(result, value=value)

        return self._run(attempt)

    def close(self) -> None:
        self._adapter.close()

    def __enter__(self) -> OneShotClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _run(self, operation: Callable[[], OneShotResult[T]]) -> OneShotResult[T]:
        for attempt in range(self._attempts):
            try:
                return operation()
            except Exception as exc:
                if not _retryable(exc) or attempt + 1 == self._attempts:
                    raise
                self._wait(float(2**attempt))
        raise AssertionError("unreachable")


def _retryable(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            OneShotConnectionError,
            OneShotTimeoutError,
            OneShotEmptyResponseError,
            OneShotDecodeError,
            OneShotValidationError,
        ),
    ):
        return True
    return isinstance(exc, OneShotCompletionError) and exc.retryable


def _strip_json_fence(text: str) -> str:
    """Remove whitespace and at most one complete enclosing JSON fence."""
    value = text.strip()
    lines = value.splitlines()
    if len(lines) >= 2 and lines[0].strip().lower() == "```json" and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return value
