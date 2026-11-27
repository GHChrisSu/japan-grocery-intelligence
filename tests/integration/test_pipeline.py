from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
from pydantic import HttpUrl, ValidationError
from scrapling import Selector
from sqlalchemy import select

from grocery_intelligence import crawl, documents
from grocery_intelligence.config import Settings, Source
from grocery_intelligence.database import (
    database,
    errors,
    persist,
    query,
    register,
    sources,
)
from grocery_intelligence.fetching import render_local
from grocery_intelligence.models import extract_size, now, unit_price
from grocery_intelligence.parsing import amount, parse_html, tokyo_date


def test_validation(local_source):
    data = local_source.model_dump(mode='json')
    for delta in ({'concurrency': 2}, {'access_status': 'manual_only'}, {'permission_reference': None},
                  {'robots_url': 'https://example.com/robots.txt'}, {'crawl_interval_hours': 1}):
        with pytest.raises(ValidationError):
            Source.model_validate(data | delta)
    for invalid in ('1,20', '-20', '99〜109'):
        with pytest.raises(ValueError):
            amount(invalid)
    assert amount('￥１，２３４．５０') == Decimal('1234.50')
    assert extract_size('豚肉 １．５ＫＧ') == (Decimal(1500), 'g')
    assert extract_size('豆腐 150g×3') == (None, None)
    parsed = tokyo_date('2026年11月26日 10:33')
    assert parsed is not None and parsed.isoformat().endswith('+09:00')
    assert tokyo_date('no date') is None


def test_html_tax_and_dates(local_source, local_site):
    state, _ = local_site
    row = parse_html(state['body'], local_source)[0]
    assert row.price_excluding_tax is None
    assert row.price_including_tax == Decimal(213)
    assert unit_price(row) == (Decimal('21.3'), '100ml')
    assert unit_price(row, 'excluding') is None
    for markup in ('<p>empty</p>', state['body'].replace('inc-tax', 'missing'),
                   state['body'].replace('</div>', '<span class="inc-tax">100</span></div>'),
                   state['body'].replace('data-price-item', 'data-price-item data-valid-from="2026-01-03T00:00:00+09:00" data-valid-until="2026-01-01T00:00:00+09:00"')):
        with pytest.raises(ValueError):
            parse_html(markup, local_source)


def test_adaptive_candidate(tmp_path):
    options = {'adaptive': True, 'url': 'https://fixture.invalid/',
               'storage_args': {'storage_file': str(tmp_path / 'adaptive.db')}}
    old = Selector('<main><div id="old" class="name">牛乳</div></main>', **options)
    new = Selector('<main><div id="new" class="name">牛乳</div></main>', **options)
    assert old.css('#old', auto_save=True)
    assert not new.css('#old')
    assert new.css('#old', adaptive=True)[0].text == '牛乳'


def test_dynamic(local_site, local_source):
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip('Install Chromium with uv run playwright install chromium')
    state, origin = local_site
    state['body'] = '''<html><main></main><script>setTimeout(() => {
      document.querySelector('main').innerHTML = '<div data-price-item><b class="name">豆腐</b><b class="ex-tax">98</b></div>';
    }, 20)</script></html>'''
    with pytest.raises(ValueError):
        render_local('https://example.com', 'main')
    try:
        page = render_local(f'{origin}/prices', '[data-price-item]')
    except Error as exc:
        if 'Executable doesn' in str(exc):
            pytest.skip('Install Chromium headless shell with Playwright')
        raise
    assert parse_html(page, local_source)[0].price_excluding_tax == Decimal(98)


def test_crawl_and_history(tmp_path, local_site, local_source, monkeypatch):
    state, _ = local_site
    settings = Settings(database=tmp_path / 'db.sqlite')
    initial = now()
    monkeypatch.setattr(crawl.time, 'sleep', lambda _: None)
    monkeypatch.setattr(crawl, 'now', lambda: initial)
    assert crawl.collect(local_source, settings) == ('success', 1)
    assert crawl.collect(local_source, settings) == ('interval', 0)
    monkeypatch.setattr(crawl, 'now', lambda: initial + timedelta(days=1))
    assert crawl.collect(local_source, settings) == ('unchanged', 0)
    assert state['calls'][-1] == ('/prices', '"version1"')
    state['body'] = state['body'].replace('213', '223')
    state['etag'] = '"version2"'
    monkeypatch.setattr(crawl, 'now', lambda: initial + timedelta(days=2))
    assert crawl.collect(local_source, settings) == ('success', 1)
    with database(settings.database) as conn:
        assert {x.price_including_tax for x in query(conn)} == {Decimal(213), Decimal(223)}
    state['etag'] = '"version3"'
    monkeypatch.setattr(crawl, 'now', lambda: initial + timedelta(days=3))
    assert crawl.collect(local_source, settings) == ('unchanged', 0)


@pytest.mark.parametrize(('status','reason'), [(403,'HTTP 403'), (429,'HTTP 429'), (302,'Unexpected HTTP 302 (redirects disabled)')])
def test_fail_closed(tmp_path, local_site, local_source, monkeypatch, status, reason):
    state, _ = local_site
    state['status'] = status
    settings = Settings(database=tmp_path / 'db.sqlite')
    monkeypatch.setattr(crawl.time, 'sleep', lambda _: None)
    assert crawl.collect(local_source.model_copy(update={'enabled': False}), settings) == ('disabled', 0)
    assert not state['calls']
    assert crawl.collect(local_source, settings) == ('error', 0)
    with database(settings.database) as conn:
        assert conn.execute(select(errors.c.reason)).scalar_one() == reason
        assert bool(conn.execute(select(sources.c.pause_until)).scalar_one()) == (status != 302)
    assert len(state['calls']) == 2


def test_disallowed_url(tmp_path, local_site, local_source, monkeypatch):
    state, origin = local_site
    settings = Settings(database=tmp_path / 'db.sqlite')
    monkeypatch.setattr(crawl.time, 'sleep', lambda _: None)
    blocked = local_source.model_copy(update={'url': HttpUrl(f'{origin}/blocked')})
    assert crawl.collect(blocked, settings) == ('error', 0)
    assert crawl.collect(local_source, settings) == ('paused', 0)
    assert state['calls'] == [('/robots.txt', None)]


def test_documents_and_dedup(tmp_path, local_source):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), '豆腐 300g 税込 198円', fontname='japan')
        pdf_data = pdf.tobytes()
    text = documents.document_text(Path('fixture.pdf'), pdf_data)
    rows = documents.parse_document(text, local_source, 'flyer')
    assert rows[0].price_including_tax == Decimal(198)
    with database(tmp_path / 'db.sqlite') as conn:
        source_id = register(conn, local_source, 'flyer')
        assert persist(conn, source_id, 'test-hash', rows) == 1
        assert persist(conn, source_id, 'test-hash', rows) == 0
        assert query(conn)[0].raw_data_hash == 'test-hash'


def test_image_and_scanned_pdf_dispatch(local_source, monkeypatch):
    import io

    from PIL import Image

    raw = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(raw, format='PNG')
    monkeypatch.setattr(documents, 'ocr', lambda _: '牛乳 1L 税込 213円')
    image_text = documents.document_text(Path('image.png'), raw.getvalue())
    assert documents.parse_document(image_text, local_source, 'flyer')[0].price_including_tax == Decimal(213)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=100, height=100)
        page.insert_image(page.rect, stream=raw.getvalue())
        scanned_text = documents.document_text(Path('scanned.pdf'), pdf.tobytes())
    assert scanned_text == image_text


def test_receipt_privacy(local_source):
    text = '2026/11/26 12:32\n会員番号 ¥00000000\nクレジットカード ¥900\n牛乳 1L 税込 ¥198\n合計 ¥198\n'
    rows = documents.parse_document(text, local_source, 'receipt')
    assert [row.product_name_raw for row in rows] == ['牛乳 1L']
    purchased = rows[0].purchased_at
    assert purchased is not None and purchased.isoformat() == '2026-11-26T12:32:00+09:00'
    with pytest.raises(ValueError):
        documents.parse_document('牛乳 ¥198', local_source, 'receipt')
