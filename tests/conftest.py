from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from pydantic import HttpUrl

from grocery_intelligence.config import Access, Source


@pytest.fixture
def local_site():
    state = {'body': '<div data-price-item><b class="name">牛乳 1L</b><span class="inc-tax">213円</span></div>',
             'calls': [], 'status': 200, 'rules': 'User-agent: *\nDisallow: /blocked', 'etag': '"version1"'}

    class Server(BaseHTTPRequestHandler):
        def do_GET(self):
            state['calls'].append((self.path, self.headers.get('If-None-Match')))
            is_rules = self.path == '/robots.txt'
            status = 200 if is_rules else state['status']
            if not is_rules and status == 200 and self.headers.get('If-None-Match') == state['etag']:
                status = 304
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8' if is_rules else 'text/html; charset=utf-8')
            self.send_header('ETag', state['etag'])
            self.send_header('Retry-After', '180')
            self.end_headers()
            if status != 304:
                self.wfile.write((state['rules'] if is_rules else state['body']).encode())

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Server) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        yield state, origin
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture
def local_source(local_site):
    _, origin = local_site
    return Source(id='demo', chain='DEMO', store_name='テスト店', url=HttpUrl(f'{origin}/prices'),
                  robots_url=HttpUrl(f'{origin}/robots.txt'), source_type='html', access_status=Access.permitted,
                  enabled=True, parser='marked-prices', permission_reference='Owned fixture')


@pytest.fixture
def source_config():
    return Path('configs/sources.yaml').resolve()
