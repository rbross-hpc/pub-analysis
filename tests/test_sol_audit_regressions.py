import asyncio
import functools
import json

import httpx
import pytest

from puba.pdf import mineru


def async_test(func):
    @functools.wraps(func)
    def run(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))
    return run


@pytest.mark.parametrize('block', [
    {'type': 'text', 'content': []},
    {'type': 'image', 'image_source': 'images/a.jpg', 'bbox': ['bad', 0, 1, 1]},
    {'type': 'image', 'bbox': [0.8, 0.1, 0.2, 0.9]},
    {'type': 'image', 'bbox': [0.1, 0.8, 0.9, 0.2]},
    {'type': 'image', 'image_source': 123},
    {'type': 'image', 'captions': ['bad']},
])
def test_invalid_block_preserves_artifacts(tmp_path, block):
    root = tmp_path / 'result'
    root.mkdir()
    (root / 'markdown.md').write_text('body')
    (root / 'structured_content.json').write_text(json.dumps({'pages': [{'page_idx': 0, 'blocks': [block]}]}))
    (root / 'middle_json.json').write_text('{}')
    old = tmp_path / 'analysis' / 'mineru' / 'old'
    old.parent.mkdir(parents=True)
    old.write_text('keep')
    with pytest.raises(RuntimeError):
        mineru._persist_outputs(root, tmp_path / 'paper.pdf', old.parent.parent)
    assert old.read_text() == 'keep'


@async_test
async def test_control_response_is_bounded():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(b'x' * (2 * 1024 * 1024))))) as client:
        with pytest.raises(RuntimeError, match='large'):
            await mineru._get_with_retries(client, 'http://mineru.test/health', mineru.time.monotonic() + 10)


@async_test
async def test_stream_deadline_checkpoints(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(mineru.time, 'monotonic', lambda: clock[0])

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'a'
            clock[0] = 11.0
            yield b'b'

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowBody()))) as client:
        with pytest.raises(httpx.TimeoutException):
            await mineru._control_request(client, 'GET', 'http://mineru.test/health', 10)
    clock[0] = 0.0
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowBody()))) as client:
        with pytest.raises(httpx.TimeoutException):
            await mineru._download_result(client, 'http://mineru.test/result', tmp_path / 'out', 10, 'download')


@async_test
async def test_upload_deadline_checkpoint(monkeypatch):
    import io
    clock = [0.0]
    monkeypatch.setattr(mineru.time, 'monotonic', lambda: clock[0])
    chunks = mineru._upload_chunks(io.BytesIO(b'x' * 70000), 10)
    assert len(await anext(chunks)) == 65536
    clock[0] = 11.0
    with pytest.raises(httpx.TimeoutException):
        await anext(chunks)


@async_test
async def test_failed_download_body_not_read(tmp_path):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError('failed response body read')
            yield b''
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403, stream=Body()))) as client:
        with pytest.raises(RuntimeError, match='HTTP 403'):
            await mineru._download_result(client, 'http://mineru.test/result', tmp_path / 'out', mineru.time.monotonic() + 10, 'download')


@pytest.mark.parametrize('download', [False, True])
@async_test
async def test_encoded_response_rejected_before_body_read(tmp_path, download):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError('encoded body was read')
            yield b''

    def handler(request):
        assert request.headers['accept-encoding'] == 'identity'
        return httpx.Response(200, headers={'Content-Encoding': 'gzip'}, stream=Body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match='encoding'):
            if download:
                await mineru._download_result(client, 'http://mineru.test/result', tmp_path / 'out', mineru.time.monotonic() + 10, 'download')
            else:
                await mineru._control_request(client, 'GET', 'http://mineru.test/health', mineru.time.monotonic() + 10)


@pytest.mark.parametrize('download', [False, True])
@async_test
async def test_raw_stream_size_limit(tmp_path, monkeypatch, download):
    monkeypatch.setattr(mineru, '_MAX_CONTROL_BYTES', 3)
    monkeypatch.setattr(mineru, '_MAX_ARCHIVE_DOWNLOAD_BYTES', 3)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'ab'
            yield b'cd'

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))) as client:
        with pytest.raises(RuntimeError, match='too large'):
            if download:
                await mineru._download_result(client, 'http://mineru.test/result', tmp_path / 'out', mineru.time.monotonic() + 10, 'download')
            else:
                await mineru._control_request(client, 'GET', 'http://mineru.test/health', mineru.time.monotonic() + 10)
