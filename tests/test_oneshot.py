from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from puba.llm.oneshot import (
    PROTOCOL_VERSION,
    OneShotAuthenticationError,
    OneShotClient,
    OneShotCompletionError,
    OneShotConfigurationError,
    OneShotConnectionError,
    OneShotDecodeError,
    OneShotEmptyResponseError,
    OneShotResult,
    OneShotTimeoutError,
    OneShotValidationError,
    json_system_prompt,
)


class FakeAdapter:
    def __init__(self, outcomes: Iterable[Any]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.closed = 0

    def complete(self, **kwargs: Any) -> OneShotResult[str]:
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return OneShotResult(outcome, "fake", kwargs["model"])

    def close(self) -> None:
        self.closed += 1


def test_protocol_and_text_result_are_provider_neutral() -> None:
    adapter = FakeAdapter(["answer"])
    result = OneShotClient(adapter, wait=lambda _: None).text(
        system="system", user="user", model="resolved-model"
    )
    assert PROTOCOL_VERSION == "oneshot-v1"
    assert result == OneShotResult("answer", "fake", "resolved-model")
    assert adapter.calls[0]["timeout"] == 120


@pytest.mark.parametrize(
    "error",
    [
        OneShotConnectionError("connection"),
        OneShotTimeoutError("timeout"),
        OneShotEmptyResponseError("empty"),
        OneShotDecodeError("decode"),
        OneShotValidationError("validation"),
        OneShotCompletionError("transient", retryable=True),
    ],
)
def test_retryable_failures_get_a_fresh_full_attempt(error: Exception) -> None:
    waits: list[float] = []
    adapter = FakeAdapter([error, error, "fresh"])
    result = OneShotClient(adapter, wait=waits.append).text(
        system="s", user="u", model="m"
    )
    assert result.value == "fresh"
    assert len(adapter.calls) == 3
    assert waits == [1.0, 2.0]


@pytest.mark.parametrize(
    "error",
    [
        OneShotConfigurationError("config"),
        OneShotAuthenticationError("auth"),
        OneShotCompletionError("bad model"),
        RuntimeError("programmer error"),
    ],
)
def test_nonretryable_failures_are_immediate(error: Exception) -> None:
    adapter = FakeAdapter([error, "unused"])
    with pytest.raises(type(error)):
        OneShotClient(adapter, wait=lambda _: None).text(system="s", user="u", model="m")
    assert len(adapter.calls) == 1


def test_json_instruction_fence_object_and_validator_are_inside_retry() -> None:
    adapter = FakeAdapter([
        "not json",
        "```json\n[1]\n```",
        '```json\n{"answer": "ok"}\n```',
    ])
    result = OneShotClient(adapter, wait=lambda _: None).json_object(
        system="system",
        user="user",
        model="m",
        validate=lambda value: value.get("answer") == "ok",
    )
    assert result.value == {"answer": "ok"}
    assert all("[oneshot-json-v1]" in call["system"] for call in adapter.calls)
    assert adapter.calls[0]["system"] == json_system_prompt("system")


@pytest.mark.parametrize(
    "text",
    [
        'prose {"answer": "no"}',
        '```\n{"answer": "no"}\n```',
        '```json\n{"answer": "no"}\n``` trailing',
    ],
)
def test_json_does_not_heuristically_extract_or_strip_other_fences(text: str) -> None:
    adapter = FakeAdapter([text])
    with pytest.raises(OneShotDecodeError):
        OneShotClient(adapter, attempts=1).json_object(
            system="s", user="u", model="m", validate=lambda _: True
        )


def test_validator_exception_is_a_programmer_error_and_is_not_retried() -> None:
    adapter = FakeAdapter(['{"answer": "ok"}', '{"answer": "unused"}'])

    def broken(_: dict[str, Any]) -> bool:
        raise KeyError("validator bug")

    with pytest.raises(KeyError, match="validator bug"):
        OneShotClient(adapter, wait=lambda _: None).json_object(
            system="s", user="u", model="m", validate=broken
        )
    assert len(adapter.calls) == 1


def test_decode_error_preserves_json_cause() -> None:
    adapter = FakeAdapter(["no"])
    with pytest.raises(OneShotDecodeError) as raised:
        OneShotClient(adapter, attempts=1).json_object(
            system="s", user="u", model="m", validate=lambda _: True
        )
    assert raised.value.__cause__ is not None
