"""Measure static and rendered HTML latency on a local server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from statistics import median
from threading import Thread
from time import perf_counter

from grocery_intelligence.fetching import http_get, render_local


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'<!doctype html><main><p id="row">213</p></main>')

    def log_message(self, format, *args):
        pass


def benchmark(operation, repetitions):
    durations = []
    for _ in range(repetitions):
        started = perf_counter()
        if operation().css('#row::text').get() != '213':
            raise RuntimeError('Benchmark fixture was not parsed correctly')
        durations.append((perf_counter() - started) * 1000)
    return round(median(durations), 2)


def main():
    with ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/prices'
            print('HTTP 10 runs, median ms', benchmark(lambda: http_get(url, 'GroceryIntelligenceResearch/0.1'), 10))
            print('Browser 3 runs, median ms', benchmark(lambda: render_local(url, '#row'), 3))
        finally:
            server.shutdown()
            thread.join(timeout=2)


if __name__ == '__main__':
    main()
