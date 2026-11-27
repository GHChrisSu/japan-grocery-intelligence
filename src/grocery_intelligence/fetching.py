from scrapling import Selector
from scrapling.fetchers import Fetcher


def http_get(url, user_agent, headers=None):
    return Fetcher.get(
        str(url), headers={'User-Agent': user_agent, **(headers or {})},
        impersonate=None, stealthy_headers=False, follow_redirects=False,
        timeout=15, retries=1,
    )


def render_local(url, ready):
    """Run the JS capability probe without loading external resources."""
    from urllib.parse import urlsplit

    from playwright.sync_api import sync_playwright

    endpoint = urlsplit(url)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1':
        raise ValueError('Browser adapter currently requires a local fixture')
    with sync_playwright() as engine:
        browser = engine.chromium.launch()
        context = browser.new_context(service_workers='block')
        context.route('**/*', lambda route: route.continue_() if route.request.url == url else route.abort())
        page = context.new_page()
        response = page.goto(url, timeout=5000)
        if response is None or not response.ok:
            raise ValueError('Render request failed')
        page.locator(ready).first.wait_for(timeout=5000)
        return Selector(page.content())
