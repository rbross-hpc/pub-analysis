# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Managed OpenCode adapter using the native session HTTP API."""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx

from .oneshot import (
    OneShotAuthenticationError,
    OneShotCompletionError,
    OneShotConfigurationError,
    OneShotConnectionError,
    OneShotEmptyResponseError,
    OneShotResult,
    OneShotTimeoutError,
)


_URL_RE = re.compile(r"https?://[^\s]+")
_PERMISSION_DENY = [{"permission": "*", "pattern": "*", "action": "deny"}]
_MANAGED_CONFIG = {
    "default_agent": "puba",
    "agent": {
        "puba": {
            "mode": "primary",
            "prompt": "Follow the supplied system and user instructions exactly.",
            "permission": "deny",
        }
    },
    "permission": "deny",
    "plugin": [],
    "instructions": [],
}


class OpenCodeAdapter:
    """Own one lazy local OpenCode server and disposable request sessions."""

    def __init__(
        self,
        *,
        executable: str = "opencode",
        startup_timeout: float = 15,
        shutdown_timeout: float = 5,
        process_factory: Callable[..., Any] | None = None,
        http_client_factory: Callable[..., Any] | None = None,
        executable_resolver: Callable[[str], str | None] | None = None,
        tempdir_factory: Callable[..., Any] | None = None,
        password_factory: Callable[[], str] | None = None,
    ) -> None:
        self._executable = executable
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._process_factory = process_factory or subprocess.Popen
        self._http_client_factory = http_client_factory or httpx.Client
        self._executable_resolver = executable_resolver or shutil.which
        self._tempdir_factory = tempdir_factory or tempfile.TemporaryDirectory
        self._password_factory = password_factory or (lambda: secrets.token_urlsafe(32))
        self._process: Any | None = None
        self._client: Any | None = None
        self._tempdir: Any | None = None
        self._base_url: str | None = None
        self._password: str | None = None
        self._output: deque[str] = deque(maxlen=20)
        self._url_queue: queue.Queue[str] = queue.Queue(maxsize=1)
        self._lock = threading.RLock()
        self._closed = False

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        timeout: float,
    ) -> OneShotResult[str]:
        provider_id, model_id = _split_model(model)
        self.start()
        session_id: str | None = None
        try:
            session = self._request(
                "POST",
                "/session",
                json_body={"title": "puba one-shot completion", "permission": _PERMISSION_DENY},
                timeout=min(timeout, 10),
            )
            if not isinstance(session, dict) or not isinstance(session.get("id"), str):
                raise OneShotCompletionError("OpenCode session response is missing an id")
            session_id = session["id"]
            try:
                response = self._request(
                    "POST",
                    f"/session/{session_id}/message",
                    json_body={
                        "model": {"providerID": provider_id, "modelID": model_id},
                        "system": system,
                        "tools": {},
                        "parts": [{"type": "text", "text": user}],
                    },
                    timeout=timeout,
                )
                return _result(response, model)
            except OneShotTimeoutError:
                self._best_effort("POST", f"/session/{session_id}/abort")
                raise
        finally:
            if session_id is not None:
                try:
                    self._request("DELETE", f"/session/{session_id}", timeout=5)
                except Exception:
                    # Cleanup must never alter a completion result or its original failure.
                    pass

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise OneShotConnectionError("OpenCode adapter is closed")
            if self._client is not None:
                if self._process is not None and self._process.poll() is None:
                    return
                self._cleanup_managed()
            executable = self._executable_resolver(self._executable)
            if executable is None:
                raise OneShotConfigurationError(
                    f"OpenCode executable not found: {self._executable!r}"
                )
            # A failed start can leave unread output queued after cleanup.
            self._output.clear()
            self._url_queue = queue.Queue(maxsize=1)
            self._password = self._password_factory()
            self._tempdir = self._tempdir_factory(prefix="puba-opencode-")
            env = os.environ.copy()
            env.pop("OPENCODE_CONFIG", None)
            env["OPENCODE_SERVER_PASSWORD"] = self._password
            env["OPENCODE_SERVER_USERNAME"] = "opencode"
            env["OPENCODE_CONFIG_DIR"] = self._tempdir.name
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(_MANAGED_CONFIG)
            command = [
                executable, "serve", "--pure", "--hostname", "127.0.0.1", "--port", "0"
            ]
            try:
                self._process = self._process_factory(
                    command,
                    cwd=self._tempdir.name,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
                if self._process.stdout is None:
                    raise OSError("OpenCode process has no startup output stream")
                threading.Thread(
                    target=self._read_output,
                    args=(self._process.stdout,),
                    name="puba-opencode-output",
                    daemon=True,
                ).start()
                self._await_server()
            except OneShotConfigurationError:
                self._cleanup_managed()
                raise
            except Exception as exc:
                details = self._diagnostics()
                self._cleanup_managed()
                if isinstance(exc, (OneShotConnectionError, OneShotAuthenticationError)):
                    raise
                raise OneShotConnectionError(f"Could not start OpenCode{details}") from exc

    def _await_server(self) -> None:
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            try:
                base_url = self._url_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            parsed = httpx.URL(base_url)
            if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
                raise OneShotConnectionError("OpenCode announced a non-loopback URL")
            self._base_url = base_url
            self._client = self._http_client_factory(
                auth=httpx.BasicAuth("opencode", self._password or "")
            )
            self._wait_for_health(deadline)
            return
        raise OneShotConnectionError(
            f"OpenCode did not announce a URL within {self._startup_timeout:g}s{self._diagnostics()}"
        )

    def _wait_for_health(self, deadline: float) -> None:
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                health = self._request(
                    "GET", "/global/health", timeout=2, ensure_started=False
                )
                if isinstance(health, dict) and health.get("healthy") is True:
                    return
                last_error = OneShotConnectionError("invalid health response")
            except (OneShotConnectionError, OneShotTimeoutError) as exc:
                last_error = exc
            time.sleep(0.05)
        raise OneShotConnectionError(f"OpenCode health check timed out: {last_error}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float,
        ensure_started: bool = True,
    ) -> Any:
        if ensure_started:
            self.start()
        client, base_url = self._client, self._base_url
        if client is None or base_url is None:
            raise OneShotConnectionError("OpenCode server is not running")
        try:
            response = client.request(method, base_url + path, json=json_body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise OneShotTimeoutError(f"OpenCode request timed out after {timeout:g}s") from exc
        except httpx.HTTPError as exc:
            raise OneShotConnectionError("Could not communicate with OpenCode") from exc
        if response.status_code in (401, 403):
            raise OneShotAuthenticationError("OpenCode rejected its managed credentials")
        if response.is_error:
            detail = response.text[:500].replace("\n", " ")
            retryable = response.status_code == 429 or response.status_code >= 500
            raise OneShotCompletionError(
                f"OpenCode {method} {path} returned HTTP {response.status_code}: {detail}",
                retryable=retryable,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise OneShotCompletionError(
                f"OpenCode {method} {path} did not return JSON"
            ) from exc

    def _best_effort(self, method: str, path: str) -> None:
        try:
            self._request(method, path, timeout=5)
        except Exception:
            pass

    def _read_output(self, stream: Any) -> None:
        for raw_line in stream:
            line = raw_line.strip()
            if line:
                bounded = line[:500]
                self._output.append(bounded)
                match = _URL_RE.search(bounded)
                if match is not None:
                    try:
                        self._url_queue.put_nowait(match.group(0).rstrip("/.,;)"))
                    except queue.Full:
                        pass

    def _diagnostics(self) -> str:
        if not self._output:
            return ""
        password = self._password or ""
        lines = [line.replace(password, "[redacted]") if password else line for line in self._output]
        return "; output: " + " | ".join(lines)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client, self._client = self._client, None
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            self._cleanup_managed()

    def _cleanup_managed(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            try:
                self._signal_process(process, signal.SIGTERM)
                process.wait(timeout=self._shutdown_timeout)
            except subprocess.TimeoutExpired:
                self._signal_process(process, signal.SIGKILL)
                try:
                    process.wait(timeout=self._shutdown_timeout)
                except Exception:
                    pass
            except Exception:
                pass
        tempdir, self._tempdir = self._tempdir, None
        if tempdir is not None:
            try:
                tempdir.cleanup()
            except Exception:
                pass
        self._base_url = None

    @staticmethod
    def _signal_process(process: Any, sig: signal.Signals) -> None:
        pid = getattr(process, "pid", None)
        if isinstance(pid, int):
            try:
                os.killpg(pid, sig)
                return
            except ProcessLookupError:
                return
            except OSError:
                pass
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    def __enter__(self) -> OpenCodeAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _split_model(model: str) -> tuple[str, str]:
    value = model.strip()
    if value != model or value.count("/") < 1:
        raise OneShotConfigurationError(
            "OpenCode model must use explicit provider/model syntax"
        )
    provider, model_id = value.split("/", 1)
    if not provider or not model_id or provider != provider.strip() or model_id != model_id.strip():
        raise OneShotConfigurationError(f"Invalid OpenCode model identifier: {model!r}")
    return provider, model_id


def _result(response: Any, model: str) -> OneShotResult[str]:
    if not isinstance(response, dict):
        raise OneShotCompletionError("OpenCode completion response is not an object")
    info = response.get("info")
    parts = response.get("parts")
    if not isinstance(info, dict) or not isinstance(parts, list):
        raise OneShotCompletionError("OpenCode completion response is malformed")
    error = info.get("error")
    if error is not None:
        message = "OpenCode completion failed"
        retryable = False
        if isinstance(error, dict):
            name = str(error.get("name", "OpenCodeError"))
            data = error.get("data")
            detail = data.get("message") if isinstance(data, dict) else None
            status = data.get("statusCode") if isinstance(data, dict) else None
            message = f"{name}: {detail or 'completion failed'}"
            lowered = name.lower()
            if "auth" in lowered or status in (401, 403):
                raise OneShotAuthenticationError(message)
            if "timeout" in lowered or status == 408:
                raise OneShotTimeoutError(message)
            retryable = status in (409, 429) or (
                isinstance(status, int) and status >= 500
            )
        raise OneShotCompletionError(message, retryable=retryable)
    text = "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    ).strip()
    if not text:
        raise OneShotEmptyResponseError("OpenCode completed without text content")
    usage = _normalize_usage(info.get("tokens"))
    request_id = info.get("id") if isinstance(info.get("id"), str) else None
    finish = info.get("finish")
    return OneShotResult(
        value=text,
        backend="opencode",
        model=model,
        usage=usage,
        cost=float(info["cost"]) if isinstance(info.get("cost"), (int, float)) else None,
        request_id=request_id,
        finish_reason=finish if isinstance(finish, str) else None,
    )


def _normalize_usage(tokens: Any) -> dict[str, int] | None:
    if not isinstance(tokens, dict):
        return None
    usage: dict[str, int] = {}
    for source, target in (("input", "input_tokens"), ("output", "output_tokens"), ("reasoning", "reasoning_tokens")):
        value = tokens.get(source)
        if isinstance(value, int):
            usage[target] = value
    cache = tokens.get("cache")
    if isinstance(cache, dict):
        for source, target in (("read", "cache_read_tokens"), ("write", "cache_write_tokens")):
            value = cache.get(source)
            if isinstance(value, int):
                usage[target] = value
    if "input_tokens" in usage and "output_tokens" in usage:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage or None
