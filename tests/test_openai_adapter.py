from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from puba.llm.oneshot import (
    OneShotAuthenticationError,
    OneShotConnectionError,
    OneShotEmptyResponseError,
    OneShotTimeoutError,
)
from puba.llm.openai import OpenAIAdapter, _map_error


def _chunk(
    content: str | None = None,
    *,
    request_id: str | None = None,
    finish: str | None = None,
    usage: object | None = None,
) -> SimpleNamespace:
    choice = SimpleNamespace(
        delta=SimpleNamespace(content=content), finish_reason=finish
    )
    return SimpleNamespace(id=request_id, choices=[choice], usage=usage)


def test_stream_is_fully_assembled_and_metadata_normalized() -> None:
    client = MagicMock()
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
    client.chat.completions.create.return_value = [
        _chunk("  hello", request_id="req_1"),
        _chunk(None),
        _chunk(" world  ", finish="stop", usage=usage),
    ]
    result = OpenAIAdapter(client=client).complete(
        system="system", user="user", model="Argo Model", timeout=9
    )
    assert result.value == "hello world"
    assert result.backend == "openai"
    assert result.model == "Argo Model"
    assert result.request_id == "req_1"
    assert result.finish_reason == "stop"
    assert result.usage == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    client.chat.completions.create.assert_called_once_with(
        model="Argo Model",
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        temperature=0,
        timeout=9,
        stream=True,
    )


def test_midstream_connection_failure_is_mapped_with_cause() -> None:
    client = MagicMock()

    def stream():
        yield _chunk("partial")
        raise ConnectionError("disconnected")

    client.chat.completions.create.return_value = stream()
    with pytest.raises(OneShotConnectionError) as raised:
        OpenAIAdapter(client=client).complete(system="s", user="u", model="m", timeout=1)
    assert isinstance(raised.value.__cause__, ConnectionError)


@pytest.mark.parametrize(
    ("native", "mapped"),
    [
        (ConnectionError("down"), OneShotConnectionError),
        (TimeoutError("slow"), OneShotTimeoutError),
    ],
)
def test_builtin_transport_errors_are_mapped(native: Exception, mapped: type[Exception]) -> None:
    assert isinstance(_map_error(native), mapped)


def test_sdk_authentication_error_is_mapped() -> None:
    import httpx
    import openai

    response = httpx.Response(401, request=httpx.Request("GET", "https://example.test"))
    native = openai.AuthenticationError("bad key", response=response, body=None)
    assert isinstance(_map_error(native), OneShotAuthenticationError)


def test_empty_stream_is_rejected() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = []
    with pytest.raises(OneShotEmptyResponseError):
        OpenAIAdapter(client=client).complete(system="s", user="u", model="m", timeout=1)


def test_lazy_client_and_close_are_idempotent() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = [_chunk("ok")]
    factory = MagicMock(return_value=client)
    adapter = OpenAIAdapter(client_factory=factory)
    adapter.complete(system="s", user="u", model="m", timeout=1)
    adapter.complete(system="s", user="u", model="m", timeout=1)
    adapter.close()
    adapter.close()
    factory.assert_called_once_with()
    client.close.assert_called_once_with()
