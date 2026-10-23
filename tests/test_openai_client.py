# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for streamed OpenAI-compatible LLM completions."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from tenacity import wait_none

from puba.llm import openai_client


def _chunk(content: str | None = None, *, choices: bool = True) -> MagicMock:
    chunk = MagicMock()
    chunk.choices = [MagicMock()] if choices else []
    if choices:
        chunk.choices[0].delta.content = content
    return chunk


def _stream(*chunks: MagicMock):
    return list(chunks)


def _without_retry(func):
    return getattr(func, "retry_with")(stop=lambda _: True, wait=wait_none())


def test_chat_text_concatenates_content_and_ignores_empty_chunks(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = _stream(
        _chunk("  Hello"), _chunk(choices=False), _chunk(None), _chunk(" world  "),
    )
    monkeypatch.setattr(openai_client, "_client", lambda: client)

    assert openai_client.chat_text("system", "user") == "Hello world"
    client.chat.completions.create.assert_called_once_with(
        model=openai_client._model("distill"),
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
        timeout=120,
        stream=True,
    )


def test_chat_json_parses_fenced_response_after_full_stream(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = _stream(
        _chunk("```json\n{\"answer\": "), _chunk("\"complete\"}\n```"),
    )
    monkeypatch.setattr(openai_client, "_client", lambda: client)

    assert openai_client.chat_json("system", "user", validate=lambda data: data["answer"] == "complete") == {
        "answer": "complete",
    }
    assert client.chat.completions.create.call_args.kwargs["response_format"] == {"type": "json_object"}


def test_direct_openai_backend_preserves_temperature_and_omits_json_mode(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = _stream(_chunk('{"answer": "complete"}'))
    monkeypatch.setattr(openai_client, "_client", lambda: client)
    monkeypatch.setattr(openai_client.config, "llm_backend", lambda: "openai")

    assert openai_client.chat_json("system", "user", temperature=0.25) == {"answer": "complete"}
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.25
    assert "response_format" not in kwargs


def test_bridge_backend_rejects_nonzero_temperature(monkeypatch):
    monkeypatch.setattr(openai_client, "_client", lambda: MagicMock())
    with pytest.raises(ValueError, match="does not support temperature"):
        _without_retry(openai_client.chat_text)("system", "user", temperature=0.25)


def test_client_is_reused_and_closed_when_backend_changes(monkeypatch):
    clients = [MagicMock(), MagicMock()]
    backends = iter(["bridge", "bridge", "openai"])
    monkeypatch.setattr(openai_client.config, "llm_backend", lambda: next(backends))
    monkeypatch.setattr(openai_client, "_new_client", lambda _: clients.pop(0))
    openai_client.close_client()

    first = openai_client._client()
    assert openai_client._client() is first
    second = openai_client._client()

    first.close.assert_called_once_with()
    assert second is not first
    openai_client.close_client()
    second.close.assert_called_once_with()


def test_chat_text_retries_after_mid_stream_failure(monkeypatch):
    client = MagicMock()

    def failed_stream():
        yield _chunk("partial")
        raise ConnectionError("stream disconnected")

    client.chat.completions.create.side_effect = [failed_stream(), _stream(_chunk("fresh response"))]
    monkeypatch.setattr(openai_client, "_client", lambda: client)

    retry_without_delay = getattr(openai_client.chat_text, "retry_with")(wait=wait_none())
    assert retry_without_delay("system", "user") == "fresh response"
    assert client.chat.completions.create.call_count == 2


def test_chat_json_retries_parse_and_validation_failures(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _stream(_chunk(json.dumps({"answer": 1}))),
        _stream(_chunk(json.dumps({"answer": "valid"}))),
    ]
    monkeypatch.setattr(openai_client, "_client", lambda: client)

    retry_without_delay = getattr(openai_client.chat_json, "retry_with")(wait=wait_none())
    assert retry_without_delay("system", "user", validate=lambda data: isinstance(data["answer"], str)) == {
        "answer": "valid",
    }
    assert client.chat.completions.create.call_count == 2


@pytest.mark.parametrize("stream", [_stream(), _stream(_chunk(choices=False), _chunk(None))])
def test_chat_text_rejects_empty_stream(monkeypatch, stream):
    client = MagicMock()
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr(openai_client, "_client", lambda: client)

    with pytest.raises(ValueError, match="without response content"):
        _without_retry(openai_client.chat_text)("system", "user")


def test_exhausted_stream_retries_reraise_underlying_exception(monkeypatch):
    client = MagicMock()

    def failed_stream():
        yield _chunk("partial")
        raise ConnectionError("stream disconnected")

    client.chat.completions.create.side_effect = [failed_stream(), failed_stream(), failed_stream()]
    monkeypatch.setattr(openai_client, "_client", lambda: client)

    retry_without_delay = getattr(openai_client.chat_text, "retry_with")(wait=wait_none())
    with pytest.raises(ConnectionError, match="stream disconnected"):
        retry_without_delay("system", "user")
    assert client.chat.completions.create.call_count == 3
