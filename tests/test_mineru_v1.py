import json

import pytest

from puba.pdf.mineru import _read_outputs, _same_origin_url


def test_rejects_cross_origin_upload_and_result_urls():
    with pytest.raises(RuntimeError, match='origin'):
        _same_origin_url('http://mineru:8000', 'http://evil.test/steal')
    assert _same_origin_url('http://mineru:8000', '/v1/files/123/content') == 'http://mineru:8000/v1/files/123/content'


def test_v1_normalizes_page_blocks(tmp_path):
    (tmp_path / 'markdown.md').write_text('# Title\n\nText', encoding='utf-8')
    (tmp_path / 'structured_content.json').write_text(json.dumps({'pages': [
        {'page_idx': 0, 'blocks': [{'type': 'text', 'content': 'Text'}]},
        {'page_idx': 1, 'blocks': [{'type': 'paragraph_title', 'content': 'Heading'}]},
    ]}), encoding='utf-8')
    text, blocks = _read_outputs(tmp_path, 'paper')
    assert text.startswith('# Title')
    assert [(b['page_idx'], b['text']) for b in blocks] == [(0, 'Text'), (1, 'Heading')]


def test_v1_normalizes_figures(tmp_path):
    (tmp_path / 'markdown.md').write_text('Figure', encoding='utf-8')
    (tmp_path / 'structured_content.json').write_text(json.dumps({'pages': [{
        'page_idx': 5, 'blocks': [{'type': 'chart', 'content': '',
        'image_source': 'images/page_5_chart_1.jpg',
        'captions': [{'content': 'Fig. 1'}], 'bbox': [0.1, 0.2, 0.5, 0.6]}]}],
        'extensions': {'docvortex_layout': {'pages': [{'page_idx': 5, 'width_pt': 600, 'height_pt': 800}]}}}), encoding='utf-8')
    _, blocks = _read_outputs(tmp_path, 'paper')
    assert blocks[0]['type'] == 'chart'
    assert blocks[0]['img_path'] == 'images/page_5_chart_1.jpg'
    assert blocks[0]['chart_caption'] == ['Fig. 1']
    assert blocks[0]['bbox'] == [60, 160, 300, 480]


def test_v1_rejects_invalid_structured_content(tmp_path):
    (tmp_path / 'markdown.md').write_text('text', encoding='utf-8')
    (tmp_path / 'structured_content.json').write_text('{"pages": "bad"}', encoding='utf-8')
    with pytest.raises(RuntimeError, match='pages'):
        _read_outputs(tmp_path, 'paper')
