from __future__ import annotations

import io
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from puba.llm.oneshot import (
    OneShotAuthenticationError,
    OneShotCompletionError,
    OneShotConfigurationError,
    OneShotTimeoutError,
)
from puba.llm.opencode import OpenCodeAdapter, _result, _split_model


class FakeProcess:
    def __init__(self, output: str = "listening http://127.0.0.1:43210\n") -> None:
        self.stdout = io.StringIO(output)
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return 0 if self.terminated or self.killed else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class FakeTempdir:
    name = "/isolated/puba-opencode"

    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True


def _adapter(handler: Any, **overrides: Any) -> tuple[OpenCodeAdapter, FakeProcess, FakeTempdir, list[Any]]:
    process = overrides.pop("process", FakeProcess())
    startup_timeout = overrides.pop("startup_timeout", 1)
    tempdir = FakeTempdir()
    starts: list[Any] = []

    def process_factory(command: list[str], **kwargs: Any) -> FakeProcess:
        starts.append((command, kwargs))
        return process

    def client_factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    selected_process_factory = overrides.pop("process_factory", process_factory)
    adapter = OpenCodeAdapter(
        process_factory=selected_process_factory,
        http_client_factory=client_factory,
        executable_resolver=lambda _: "/bin/opencode",
        tempdir_factory=lambda **_: tempdir,
        password_factory=lambda: "random-secret",
        startup_timeout=startup_timeout,
        **overrides,
    )
    return adapter, process, tempdir, starts


def test_managed_startup_auth_isolation_health_reuse_and_cleanup(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_CONFIG", "/ambient/config.json")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", "/ambient/config")
    monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "ambient-user")
    requests: list[httpx.Request] = []
    next_session = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal next_session
        requests.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/session" and request.method == "POST":
            next_session += 1
            body = json.loads(request.content)
            assert body["permission"] == [{"permission": "*", "pattern": "*", "action": "deny"}]
            return httpx.Response(200, json={"id": f"ses_{next_session}"})
        if request.url.path.endswith("/message"):
            body = json.loads(request.content)
            assert body == {
                "model": {"providerID": "openai", "modelID": "gpt/model"},
                "system": "system",
                "tools": {},
                "parts": [{"type": "text", "text": "user"}],
            }
            return httpx.Response(200, json={
                "info": {"id": "msg_1", "finish": "stop", "cost": 0.25,
                         "tokens": {"input": 4, "output": 2, "cache": {"read": 1}}},
                "parts": [{"type": "text", "text": " hello "},
                          {"type": "reasoning", "text": "hidden"},
                          {"type": "text", "text": "world "}],
            })
        if request.method == "DELETE":
            return httpx.Response(200, json=True)
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    adapter, process, tempdir, starts = _adapter(handler)
    first = adapter.complete(system="system", user="user", model="openai/gpt/model", timeout=5)
    second = adapter.complete(system="system", user="user", model="openai/gpt/model", timeout=5)
    assert first.value == "hello world"
    assert first.usage == {"input_tokens": 4, "output_tokens": 2, "cache_read_tokens": 1, "total_tokens": 6}
    assert (first.request_id, first.finish_reason, first.cost) == ("msg_1", "stop", 0.25)
    assert second.value == "hello world"
    assert len(starts) == 1
    command, options = starts[0]
    assert command == ["/bin/opencode", "serve", "--pure", "--hostname", "127.0.0.1", "--port", "0"]
    assert options["cwd"] == tempdir.name
    assert options["start_new_session"] is True
    assert options["env"]["OPENCODE_SERVER_PASSWORD"] == "random-secret"
    assert options["env"]["OPENCODE_SERVER_USERNAME"] == "opencode"
    assert options["env"]["OPENCODE_CONFIG_DIR"] == tempdir.name
    assert "OPENCODE_CONFIG" not in options["env"]
    config = json.loads(options["env"]["OPENCODE_CONFIG_CONTENT"])
    assert config["plugin"] == [] and config["instructions"] == []
    assert config["permission"] == "deny"
    assert sum(request.url.path == "/global/health" for request in requests) == 1
    assert sum(request.method == "DELETE" for request in requests) == 2
    adapter.close()
    adapter.close()
    assert process.terminated and tempdir.cleaned


@pytest.mark.parametrize("model", ["gpt-5", "/gpt-5", "openai/", " openai/gpt "])
def test_model_identifier_requires_strict_provider_and_model(model: str) -> None:
    with pytest.raises(OneShotConfigurationError):
        _split_model(model)


def test_timeout_aborts_and_deletes_without_masking_original() -> None:
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_timeout"})
        if request.url.path.endswith("/message"):
            raise httpx.ReadTimeout("slow", request=request)
        if request.method == "DELETE":
            return httpx.Response(500, text="cleanup failed")
        return httpx.Response(200, json=True)

    adapter, _, _, _ = _adapter(handler)
    with pytest.raises(OneShotTimeoutError) as raised:
        adapter.complete(system="s", user="u", model="openai/gpt", timeout=1)
    assert isinstance(raised.value.__cause__, httpx.ReadTimeout)
    assert ("POST", "/session/ses_timeout/abort") in paths
    assert ("DELETE", "/session/ses_timeout") in paths
    adapter.close()


def test_delete_failure_does_not_mask_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_1"})
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json={"info": {}, "parts": [{"type": "text", "text": "ok"}]})
        return httpx.Response(500, text="delete failed")

    adapter, _, _, _ = _adapter(handler)
    assert adapter.complete(system="s", user="u", model="openai/gpt", timeout=1).value == "ok"
    adapter.close()


def test_provider_http_error_is_classified_by_retryability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_1"})
        if request.url.path.endswith("/message"):
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=True)

    adapter, _, _, _ = _adapter(handler)
    with pytest.raises(OneShotCompletionError) as raised:
        adapter.complete(system="s", user="u", model="openai/gpt", timeout=1)
    assert raised.value.retryable is True
    adapter.close()


def test_provider_timeout_aborts_before_delete() -> None:
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_timeout"})
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json={
                "info": {"error": {"name": "ProviderTimeoutError", "data": {"message": "slow"}}},
                "parts": [],
            })
        return httpx.Response(200, json=True)

    adapter, _, _, _ = _adapter(handler)
    with pytest.raises(OneShotTimeoutError):
        adapter.complete(system="s", user="u", model="openai/gpt", timeout=1)
    assert paths[-2:] == [
        ("POST", "/session/ses_timeout/abort"),
        ("DELETE", "/session/ses_timeout"),
    ]
    adapter.close()


def test_dead_managed_process_is_restarted() -> None:
    processes = [FakeProcess(), FakeProcess()]
    starts: list[FakeProcess] = []

    def process_factory(*_: Any, **__: Any) -> FakeProcess:
        process = processes.pop(0)
        starts.append(process)
        return process

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"healthy": True})

    adapter, _, _, _ = _adapter(handler, process_factory=process_factory)
    adapter.start()
    starts[0].terminated = True
    adapter.start()
    assert len(starts) == 2
    adapter.close()


@pytest.mark.parametrize(
    ("error", "expected", "retryable"),
    [
        ({"name": "ProviderAuthError", "data": {"message": "login"}}, OneShotAuthenticationError, None),
        ({"name": "ProviderTimeoutError", "data": {"message": "slow"}}, OneShotTimeoutError, None),
        ({"name": "ProviderError", "data": {"message": "busy", "statusCode": 503}}, OneShotCompletionError, True),
        ({"name": "ModelNotFoundError", "data": {"message": "bad model", "statusCode": 404}}, OneShotCompletionError, False),
    ],
)
def test_native_provider_errors_are_classified(
    error: dict[str, Any], expected: type[Exception], retryable: bool | None
) -> None:
    with pytest.raises(expected) as raised:
        _result({"info": {"error": error}, "parts": []}, "openai/gpt")
    if retryable is not None:
        assert isinstance(raised.value, OneShotCompletionError)
        assert raised.value.retryable is retryable


def test_concurrent_completions_share_startup_and_use_independent_sessions() -> None:
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    session_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/session":
            with lock:
                session_count += 1
                session_id = f"ses_{session_count}"
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/message"):
            barrier.wait(timeout=2)
            session_id = request.url.path.split("/")[2]
            return httpx.Response(200, json={
                "info": {}, "parts": [{"type": "text", "text": session_id}]
            })
        return httpx.Response(200, json=True)

    adapter, _, _, starts = _adapter(handler)

    def complete(_: int) -> str:
        return adapter.complete(
            system="s", user="u", model="openai/gpt", timeout=2
        ).value

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = set(executor.map(complete, range(2)))
    assert results == {"ses_1", "ses_2"}
    assert len(starts) == 1
    adapter.close()


def test_startup_diagnostics_are_bounded_and_sanitize_password() -> None:
    output = "".join(f"line-{index} random-secret\n" for index in range(30))
    process = FakeProcess(output)
    adapter, _, tempdir, _ = _adapter(
        lambda _: httpx.Response(500), process=process, startup_timeout=0.02
    )
    with pytest.raises(Exception) as raised:
        adapter.start()
    message = str(raised.value)
    assert "random-secret" not in message
    assert "[redacted]" in message
    assert "line-0" not in message
    assert "line-29" in message
    assert tempdir.cleaned


def test_startup_retry_discards_stale_announced_url() -> None:
    adapter = OpenCodeAdapter(
        executable_resolver=lambda _: "/bin/opencode",
        tempdir_factory=lambda **_: FakeTempdir(),
        password_factory=lambda: "secret",
    )
    adapter._url_queue.put("http://127.0.0.1:1")
    adapter._output.append("stale output")
    adapter._process_factory = lambda *_, **__: (_ for _ in ()).throw(OSError("failed"))

    with pytest.raises(Exception):
        adapter.start()

    assert adapter._url_queue.empty()
    assert "stale output" not in adapter._output


def test_close_kills_process_after_terminate_timeout() -> None:
    process = FakeProcess()

    def wait(timeout: float | None = None) -> int:
        if not process.killed:
            raise subprocess.TimeoutExpired("opencode", timeout)
        return 0

    process.wait = wait  # type: ignore[method-assign]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"healthy": True})

    adapter, _, _, _ = _adapter(handler, process=process)
    adapter.start()
    adapter.close()
    assert process.terminated and process.killed
