from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from puba import llm
from puba.llm.oneshot import OneShotResult


def test_process_client_is_reused_and_replaced_when_backend_changes(monkeypatch):
    clients = [MagicMock(), MagicMock()]
    backends = iter(["opencode", "opencode", "openai"])
    monkeypatch.setattr(llm.config, "llm_backend", lambda: next(backends))
    monkeypatch.setattr(llm, "_new_client", lambda _: clients.pop(0))
    llm.close_client()

    first = llm._client()
    assert llm._client() is first
    second = llm._client()

    first.close.assert_called_once_with()
    assert second is not first
    llm.close_client()
    second.close.assert_called_once_with()


def test_provider_neutral_functions_pass_resolved_model(monkeypatch):
    client = MagicMock()
    client.text.return_value = OneShotResult("answer", "opencode", "provider/model")
    client.json_object.return_value = OneShotResult({"ok": True}, "opencode", "provider/model")
    monkeypatch.setattr(llm, "_client", lambda: client)
    validator = lambda value: value.get("ok") is True

    assert llm.text(system="s", user="u", model="provider/model").value == "answer"
    assert llm.json_object(
        system="s", user="u", model="provider/model", validate=validator,
    ).value == {"ok": True}
    client.text.assert_called_once_with(system="s", user="u", model="provider/model")
    client.json_object.assert_called_once_with(
        system="s", user="u", model="provider/model", validate=validator,
    )


def test_close_waits_for_active_call(monkeypatch):
    client = MagicMock()
    entered = threading.Event()
    release = threading.Event()

    def text(**_):
        entered.set()
        release.wait(timeout=2)
        return OneShotResult("answer", "opencode", "provider/model")

    client.text.side_effect = text
    monkeypatch.setattr(llm, "_new_client", lambda _: client)
    monkeypatch.setattr(llm.config, "llm_backend", lambda: "opencode")
    llm.close_client()

    with ThreadPoolExecutor(max_workers=2) as executor:
        call = executor.submit(
            llm.text, system="s", user="u", model="provider/model",
        )
        assert entered.wait(timeout=1)
        closing = executor.submit(llm.close_client)
        assert not closing.done()
        client.close.assert_not_called()
        release.set()
        assert call.result(timeout=2).value == "answer"
        closing.result(timeout=2)

    client.close.assert_called_once_with()
