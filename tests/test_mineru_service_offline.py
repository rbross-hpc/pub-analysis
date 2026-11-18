# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Offline contract tests for the MinerU v1 service."""
import io
import json
import zipfile
from unittest.mock import patch

import httpx
import pytest

from puba.pdf.mineru import _run_mineru_service, _safe_extract_zip


def archive(*, structured=None, unsafe=False):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        if unsafe:
            z.writestr('../escape', 'bad')
        z.writestr('markdown.md', '# Paper\n\nBody')
        z.writestr('middle_json.json', '{}')
        z.writestr('structured_content.json', json.dumps(structured if structured is not None else {
            'pages': [{'page_idx': 0, 'blocks': [{'type': 'image', 'image_source': 'images/a.jpg', 'captions': [{'content': 'Figure 1'}]}]}]}))
        z.writestr('images/a.jpg', b'jpeg')
    return out.getvalue()


def run_service(tmp_path, *, created=None, status=None, result=None, handler_override=None):
    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF-1.4\n')
    calls = []
    def response_for(request):
        calls.append(request)
        if handler_override:
            response = handler_override(request)
            if response is not None:
                return response
        if request.url.path == '/v1/health':
            return httpx.Response(200, json={'status': 'ok', 'version': 'test'})
        if request.url.path == '/v1/tasks':
            return httpx.Response(200, json=created if created is not None else {
                'task_id': 'task-1', 'files': [{'file_id': 'f1', 'upload': {'url': '/upload'}}]})
        if request.url.path == '/upload':
            assert request.content == b'%PDF-1.4\n'
            return httpx.Response(200)
        if request.url.path == '/v1/tasks/task-1':
            return httpx.Response(200, json=status if status is not None else {
                'status': 'completed', 'files': [{'file_id': 'f1', 'status': 'completed', 'result_url': '/result'}]})
        if request.url.path == '/result':
            return httpx.Response(200, content=result if result is not None else archive())
        raise AssertionError(str(request.url))
    def handler(request):
        response = response_for(request)
        if response.is_stream_consumed:
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=httpx.ByteStream(response.content))
        return response
    real_client = httpx.AsyncClient
    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    with patch('puba.pdf.mineru.httpx.AsyncClient', side_effect=client):
        value = _run_mineru_service(pdf, tmp_path / 'paper.puba', {
            'base_url': 'http://mineru.test', 'timeout_s': 2, 'poll_interval_s': 0})
    return value, calls


def test_service_contract_and_persistence(tmp_path):
    (markdown, blocks), calls = run_service(tmp_path)
    assert markdown.startswith('# Paper')
    assert blocks[0]['image_caption'] == ['Figure 1']
    assert blocks[0]['img_path'] == 'images/a.jpg'
    assert (tmp_path / 'paper.puba/mineru/images/a.jpg').read_bytes() == b'jpeg'
    assert [(r.method, r.url.path) for r in calls] == [
        ('GET', '/v1/health'), ('POST', '/v1/tasks'), ('PUT', '/upload'),
        ('GET', '/v1/tasks/task-1'), ('GET', '/result')]
    assert json.loads(calls[1].content)['tier'] == 'basic'


@pytest.mark.parametrize('created', [[], {}, {'task_id': 'x', 'files': []},
    {'task_id': 'x', 'files': [{'file_id': 'f', 'upload': {'url': 'http://evil.test/upload'}}]}])
def test_rejects_bad_task_creation(tmp_path, created):
    with pytest.raises(RuntimeError):
        run_service(tmp_path, created=created)


@pytest.mark.parametrize('status', [[], {}, {'status': 'completed', 'files': []},
    {'status': 'completed', 'files': [{'file_id': 'wrong', 'status': 'completed'}]},
    {'status': 'surprise', 'files': [{'file_id': 'f1', 'status': 'running'}]},
    {'status': 'failed', 'files': [{'file_id': 'f1', 'status': 'failed', 'error': 'failure'}]}])
def test_rejects_bad_status(tmp_path, status):
    with pytest.raises(RuntimeError):
        run_service(tmp_path, status=status)


@pytest.mark.parametrize('payload', [b'not zip', archive(unsafe=True), archive(structured={'pages': 'invalid'}),
    archive(structured={'pages': [{'page_idx': 0, 'blocks': ['invalid']}]})])
def test_bad_result_preserves_existing_artifacts(tmp_path, payload):
    old = tmp_path / 'paper.puba/mineru/old.txt'
    old.parent.mkdir(parents=True)
    old.write_text('old')
    with pytest.raises(RuntimeError):
        run_service(tmp_path, result=payload)
    assert old.read_text() == 'old'
    assert not (tmp_path / 'escape').exists()


@pytest.mark.parametrize('kind', ['image', 'chart', 'table'])
@pytest.mark.parametrize('assets', ['absent', 'unrelated', 'directory'])
def test_missing_referenced_image_preserves_existing_artifacts(tmp_path, kind, assets):
    target = tmp_path / 'paper.puba/mineru'
    (target / 'images').mkdir(parents=True)
    (target / 'images/old.jpg').write_bytes(b'\x00old crop\xff')
    (target / 'service.json').write_bytes(b'old metadata\x00')
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}

    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('markdown.md', 'new body')
        z.writestr('middle_json.json', '{}')
        z.writestr('structured_content.json', json.dumps({'pages': [{'page_idx': 0, 'blocks': [
            {'type': kind, 'image_source': 'images/a.jpg'}]}]}))
        if assets == 'unrelated':
            z.writestr('images/other.jpg', b'unrelated')
        elif assets == 'directory':
            z.writestr('images/a.jpg/', b'')

    with pytest.raises(RuntimeError, match='image.*images/a.jpg'):
        run_service(tmp_path, result=out.getvalue())
    after = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    assert after == before


def test_source_less_visual_blocks_are_valid(tmp_path):
    structured = {'pages': [{'page_idx': 0, 'blocks': [
        {'type': 'image'}, {'type': 'chart', 'image_source': None}, {'type': 'table'}]}]}
    (_, blocks), _ = run_service(tmp_path, result=archive(structured=structured))
    assert all('img_path' not in block for block in blocks)


def test_deadline_cancels_stalled_headers(tmp_path, monkeypatch):
    import asyncio
    import time
    from puba.pdf import mineru

    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF')
    cancelled = []

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(mineru.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='timed out'):
        _run_mineru_service(pdf, tmp_path / 'analysis', {
            'base_url': 'http://mineru.test', 'timeout_s': 0.1})
    assert time.monotonic() - start < 2
    assert cancelled == [True]
    assert not (tmp_path / 'analysis' / 'mineru').exists()


def test_timeout_preserves_existing_artifacts(tmp_path):
    old = tmp_path / 'paper.puba/mineru/old.txt'
    old.parent.mkdir(parents=True)
    old.write_text('old')
    def timeout(request):
        raise httpx.ReadTimeout('timeout', request=request)
    with pytest.raises(RuntimeError, match='timed out'):
        run_service(tmp_path, handler_override=timeout)
    assert old.read_text() == 'old'


def test_replace_directory_rolls_back_on_install_failure(tmp_path, monkeypatch):
    from puba.pdf import mineru
    target = tmp_path / 'mineru'
    target.mkdir()
    (target / 'old').write_text('keep')
    staged = tmp_path / 'staged'
    staged.mkdir()
    original_replace = mineru.os.replace
    def fail_install(src, dst):
        if src == staged:
            raise OSError('install failed')
        original_replace(src, dst)
    monkeypatch.setattr(mineru.os, 'replace', fail_install)
    with pytest.raises(OSError, match='install failed'):
        mineru._replace_directory(staged, target)
    assert (target / 'old').read_text() == 'keep'


def test_duplicate_archive_paths_rejected(tmp_path):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('same', 'one')
        with pytest.warns(UserWarning):
            z.writestr('same', 'two')
    path = tmp_path / 'archive.zip'
    path.write_bytes(out.getvalue())
    with pytest.raises(RuntimeError, match='duplicate'):
        _safe_extract_zip(path, tmp_path)
