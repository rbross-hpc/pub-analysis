# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline tests for the asynchronous MinerU service adapter."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from puba.pdf.mineru import _run_mineru_service


def _archive(stem: str = "paper", *, unsafe: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if unsafe:
            archive.writestr("../escape.txt", "bad")
        archive.writestr(f"{stem}/auto/{stem}.md", "# Paper\n\nBody text.\n")
        archive.writestr(
            f"{stem}/auto/{stem}_content_list.json",
            json.dumps([{"type": "image", "page_idx": 0, "img_path": "images/a.jpg"}]),
        )
        archive.writestr(f"{stem}/auto/{stem}_middle.json", "{}")
        archive.writestr(f"{stem}/auto/{stem}_content_list_v2.json", "[]")
        archive.writestr(f"{stem}/auto/images/a.jpg", b"jpeg")
    return output.getvalue()


def _response(status: int, *, json_data=None, content: bytes = b"") -> httpx.Response:
    request = httpx.Request("GET", "http://mineru.test")
    if json_data is not None:
        return httpx.Response(status, json=json_data, request=request)
    return httpx.Response(status, content=content, request=request)


class _FakeClient:
    def __init__(self, responses, calls):
        self.responses = iter(responses)
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return next(self.responses)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return next(self.responses)

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = next(self.responses)

        class _Stream:
            def __enter__(self):
                return response

            def __exit__(self, *args):
                return None

        return _Stream()


def _run(fake_pdf: Path, analysis_dir: Path, responses):
    calls = []
    client = _FakeClient(responses, calls)
    with patch("puba.pdf.mineru.httpx.Client", return_value=client):
        result = _run_mineru_service(
            fake_pdf,
            analysis_dir,
            {"base_url": "http://mineru.test/", "timeout_s": 30, "poll_interval_s": 0.01},
        )
    return result, calls


def test_service_downloads_and_normalizes_archive(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    analysis_dir = tmp_path / "paper.puba"
    stale = analysis_dir / "mineru" / "images" / "stale.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")
    responses = [
        _response(200, json_data={"status": "healthy", "version": "3.4.5", "protocol_version": 2}),
        _response(202, json_data={"task_id": "task-1"}),
        _response(200, json_data={"status": "processing"}),
        _response(200, json_data={"status": "completed"}),
        _response(200, content=_archive()),
    ]

    (markdown, content_list), calls = _run(pdf, analysis_dir, responses)

    assert markdown.startswith("# Paper")
    assert content_list[0]["img_path"] == "images/a.jpg"
    mineru = analysis_dir / "mineru"
    assert (mineru / "paper.md").exists()
    assert (mineru / "paper_content_list.json").exists()
    assert (mineru / "paper_content_list_v2.json").exists()
    assert (mineru / "paper_middle.json").exists()
    assert (mineru / "images" / "a.jpg").read_bytes() == b"jpeg"
    assert json.loads((mineru / "service.json").read_text(encoding="utf-8")) == {
        "base_url": "http://mineru.test",
        "task_id": "task-1",
        "version": "3.4.5",
        "protocol_version": 2,
    }
    assert not stale.exists()
    assert [call[:2] for call in calls] == [
        ("GET", "http://mineru.test/health"),
        ("POST", "http://mineru.test/tasks"),
        ("GET", "http://mineru.test/tasks/task-1"),
        ("GET", "http://mineru.test/tasks/task-1"),
        ("GET", "http://mineru.test/tasks/task-1/result"),
    ]
    submitted = calls[1][2]
    assert submitted["files"]["files"][0] == "paper.pdf"
    assert submitted["data"]["backend"] == "pipeline"
    assert submitted["data"]["formula_enable"] == "false"
    assert submitted["data"]["response_format_zip"] == "true"


def test_service_failure_preserves_existing_artifacts(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    existing = tmp_path / "paper.puba" / "mineru" / "paper.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("old", encoding="utf-8")
    responses = [
        _response(200, json_data={"status": "healthy"}),
        _response(202, json_data={"task_id": "task-2"}),
        _response(200, json_data={"status": "failed", "error": "GPU unavailable"}),
    ]

    with pytest.raises(RuntimeError, match="GPU unavailable"):
        _run(pdf, existing.parent.parent, responses)
    assert existing.read_text(encoding="utf-8") == "old"


def test_service_rejects_unsafe_zip(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    responses = [
        _response(200, json_data={"status": "healthy"}),
        _response(202, json_data={"task_id": "task-3"}),
        _response(200, json_data={"status": "completed"}),
        _response(200, content=_archive(unsafe=True)),
    ]

    with pytest.raises(RuntimeError, match="unsafe path"):
        _run(pdf, tmp_path / "paper.puba", responses)


def test_service_requires_list_content_list(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("paper/auto/paper.md", "# Paper")
        archive.writestr("paper/auto/paper_content_list.json", "{}")
    responses = [
        _response(200, json_data={"status": "healthy"}),
        _response(202, json_data={"task_id": "task-4"}),
        _response(200, json_data={"status": "completed"}),
        _response(200, content=output.getvalue()),
    ]

    with pytest.raises(RuntimeError, match="must be a JSON list"):
        _run(pdf, tmp_path / "paper.puba", responses)


def test_service_requires_object_content_list_entries(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("paper/auto/paper.md", "# Paper")
        archive.writestr("paper/auto/paper_content_list.json", '["bad"]')
    responses = [
        _response(200, json_data={"status": "healthy"}),
        _response(202, json_data={"task_id": "task-5"}),
        _response(200, json_data={"status": "completed"}),
        _response(200, content=output.getvalue()),
    ]

    with pytest.raises(RuntimeError, match="entries must be JSON objects"):
        _run(pdf, tmp_path / "paper.puba", responses)
