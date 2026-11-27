"""Local, read-only grocery browser."""

import json
from argparse import ArgumentParser
from collections import Counter
from datetime import datetime
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from grocery_intelligence.area_compare import compare_deals
from grocery_intelligence.config import Settings, sources
from grocery_intelligence.models import JST, normalized
from grocery_intelligence.web_catalog import (
    active_promotion,
    load_catalog,
    previous_price,
)
from grocery_intelligence.web_i18n import (
    LANGUAGE,
    PAGE_URL,
    chinese_product,
    chinese_spec,
    localized_note,
    localized_spec,
    product_name,
    store_name,
    tr,
)

ACTIVE = {"今日页面", "传单期内"}
PAGE_SIZE = 36
SEARCH_ALIASES = {
    "牛奶": ("牛乳", "ミルク"),
    "鸡肉": ("鶏", "とり", "チキン"),
    "猪肉": ("豚", "ポーク"),
    "鸡蛋": ("卵", "たまご", "タマゴ"),
    "米饭": ("米飯", "ごはん", "こしひかり"),
    "洋葱": ("たまねぎ", "玉ねぎ", "オニオン"),
}


def layout(title, body, extra_head=""):
    japanese = LANGUAGE.get() == "ja"
    urls = urlsplit(PAGE_URL.get())
    params = parse_qs(urls.query)
    params.pop("lang", None)
    switches = []
    for code, label in (("zh", "简体中文"), ("ja", "日本語")):
        target = urls.path + "?" + urlencode(params | {"lang": code}, doseq=True)
        current = ' aria-current="true"' if code == LANGUAGE.get() else ""
        switches.append(
            f'<a href="{escape(target)}" lang="{code}"{current}>{label}</a>'
        )
    return f"""<!doctype html><html lang="{"ja" if japanese else "zh-CN"}"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{escape(title)} · {tr("幸町购物清单")}</title><link rel="stylesheet" href="/styles.css">{extra_head}
    </head><body><header><div class="wrap mast"><a class="logo" href="/">{tr("幸町购物清单")}</a>
    <nav aria-label="{tr("主导航")}"><a href="/">{tr("当期优惠")}</a><a href="/map">{tr("地图")}</a></nav>
    <div class="languages">{"".join(switches)}</div></div></header>
    <main class="wrap">{body}</main></body></html>"""


def money(amount):
    if amount is None:
        return "—"
    return (
        f"¥{amount:,.0f}" if amount == amount.to_integral_value() else f"¥{amount:,.2f}"
    )


def link(url, label):
    return (
        f'<a href="{escape(url)}" rel="noreferrer">{escape(label)}</a>' if url else ""
    )


def option(value, label, selected):
    flag = " selected" if value == selected else ""
    return f'<option value="{escape(value)}"{flag}>{escape(label)}</option>'


def item_card(item, store, today, clock=""):
    offer = item.offers[0]
    badge = offer.status(today, clock)
    return f"""<article class="card"><div class="meta"><span>{escape(store_name(store.name))}</span>
    <span class="tag">{tr(badge)}</span></div><h2><a href="/products/{item.id}">{escape(product_name(item.name))}</a></h2>
    <p class="spec">{escape(localized_spec(item.spec) or tr("规格待核实"))}</p><div class="price">
    {money(offer.taxed)} <small>{tr("含税")}</small></div>
    <div class="foot"><span>{tr(offer.origin)}</span><span>{escape(offer.until or offer.day)}</span></div></article>"""


def listing(stores, items, params, today, store_id="", clock=""):
    query = params.get("q", [""])[0]
    store_id = store_id or params.get("store", [""])[0]
    category = params.get("category", [""])[0]
    only_active = params.get("active", [""])[0] == "1"
    counts = Counter(item.store_id for item in items.values())
    categories = sorted({item.category for item in items.values()})
    tokens = normalized(query).casefold().split()
    results = []
    for item in items.values():
        shop = stores[item.store_id].name
        haystack = normalized(
            f"{item.name} {chinese_product(item.name)} {item.spec} {chinese_spec(item.spec)} "
            f"{item.jan} {item.category} {tr(item.category)} {shop} {store_name(shop)}"
        ).casefold()
        if (
            (not store_id or item.store_id == store_id)
            and (not category or item.category == category)
            and all(
                any(
                    term in haystack for term in (token, *SEARCH_ALIASES.get(token, ()))
                )
                for token in tokens
            )
            and (not only_active or item.offers[0].status(today, clock) in ACTIVE)
        ):
            results.append(item)
    results.sort(
        key=lambda item: (
            item.offers[0].status(today, clock) not in ACTIVE,
            stores[item.store_id].distance is None,
            stores[item.store_id].distance or 0,
            item.name,
            item.spec,
        )
    )
    store = stores.get(store_id)
    title = store_name(store.name) if store else tr("附近超市的商品价格")
    subtitle = tr("{count} 项商品规格", count=len(results))
    if store and store.distance is not None:
        subtitle = (
            tr("约 {distance:.2f} 公里", distance=store.distance) + " · " + subtitle
        )
    choices = option("", tr("全部门店"), store_id) + "".join(
        option(key, f"{store_name(value.name)}（{counts[key]}）", store_id)
        for key, value in stores.items()
    )
    groups = option("", tr("全部类别"), category) + "".join(
        option(group, tr(group), category) for group in categories
    )
    checked = " checked" if only_active else ""
    total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
    requested_page = params.get("page", ["1"])[0]
    page_number = 1
    if (
        requested_page.isascii()
        and requested_page.isdecimal()
        and len(requested_page) <= 6
    ):
        page_number = min(total_pages, max(1, int(requested_page)))
    offset = (page_number - 1) * PAGE_SIZE
    cards = "".join(
        item_card(item, stores[item.store_id], today, clock)
        for item in results[offset : offset + PAGE_SIZE]
    )
    pagination = ""
    if total_pages > 1:
        filters = {
            "q": query,
            "store": store_id,
            "category": category,
            "active": "1" if only_active else "",
        }
        previous = (
            link("/?" + urlencode(filters | {"page": page_number - 1}), tr("上一页"))
            if page_number > 1
            else ""
        )
        following = (
            link("/?" + urlencode(filters | {"page": page_number + 1}), tr("下一页"))
            if page_number < total_pages
            else ""
        )
        pagination = (
            f'<nav class="pages" aria-label="{tr("分页")}">{previous}'
            f"<span>{tr('第 {page} / {total} 页', page=page_number, total=total_pages)}</span>{following}</nav>"
        )
    if not cards:
        cards = (
            f'<p class="empty">{tr("没有符合条件的商品。可以清空搜索或切换门店。")}</p>'
        )
    return layout(
        title,
        f'''<section class="hero"><p class="kicker">{tr("千叶 · 美浜")}</p><h1>{escape(title)}</h1>
    <p>{escape(subtitle)} · {escape(today)} JST</p>{link(store.url, tr("查看门店来源")) if store else ""}</section>
    <form class="filters" action="/" method="get"><input type="hidden" name="view" value="products"><label>{tr("搜索商品或 JAN")}
    <input type="search" name="q" value="{escape(query)}" placeholder="{tr("例如 豆腐、鶏肉、500ml")}"></label>
    <label>{tr("门店")}<select name="store">{choices}</select></label><label>{tr("类别")}<select name="category">{groups}</select></label>
    <button type="submit">{tr("搜索")}</button><label class="check"><input type="checkbox" name="active" value="1"{checked}> {tr("仅显示今日页面及传单期内")}</label>
    <a class="reset" href="/?view=products">{tr("清空筛选")}</a></form><section class="grid" aria-label="{tr("商品")}">{cards}</section>{pagination}''',
    )


def current_deals(items, today, clock):
    for item in items:
        for offer in item.offers:
            if offer.taxed is not None and active_promotion(offer, today, clock):
                yield item, offer
                break


def promotions(stores, items, today, clock, store_id=""):
    by_store = {key: [] for key in stores}
    for item, offer in current_deals(items.values(), today, clock):
        by_store[item.store_id].append((item, offer))
    if store_id not in stores:
        eligible = [key for key in stores if by_store[key]]
        store_id = min(
            eligible,
            key=lambda key: (
                stores[key].distance is None,
                stores[key].distance or 0,
            ),
            default=next(iter(stores), ""),
        )
    choices = "".join(
        option(key, f"{store_name(shop.name)} · {len(by_store[key])}", store_id)
        for key, shop in stores.items()
    )
    rows = []
    for item, offer in by_store.get(store_id, []):
        prior = previous_price(item, offer)
        if prior is None:
            history = f"<small>{tr('之前价格未知')}</small>"
        else:
            evidence = link(prior.url, money(prior.taxed)) or money(prior.taxed)
            history = f"<small>{tr('之前价')} {evidence} · {escape(prior.until or prior.day)}</small>"
        until = offer.until or offer.day
        benefit = escape(localized_note(offer.note))
        rows.append(f"""<li class="deal-row"><div>
          {link(f"/products/{item.id}", product_name(item.name))}
          <small>{escape(localized_spec(item.spec) or tr("规格待核实"))}</small>
          <small>{tr("有效至")} {escape(until)}{f" · {benefit}" if benefit else ""}</small>
        </div><div class="deal-prices"><strong>{money(offer.taxed)}</strong>
          <small>{tr("当前含税价")}</small>{history}</div></li>""")
    body = "".join(rows)
    if not rows:
        body = f'<li class="empty">{tr("这家店暂无已收录的当期优惠。")}</li>'
    return layout(
        tr("当期优惠"),
        f'''<section class="hero"><h1>{tr("当期优惠")}</h1>
      <p>{escape(today)} JST · {tr("{count} 项当期商品", count=len(rows))}</p>
      <p>{tr("价格来自当期传单及门店特卖信息。")}</p></section>
      <form class="deal-filter" action="/" method="get"><label>{tr("门店")}
        <select name="store">{choices}</select></label><button type="submit">{tr("查看优惠")}</button></form>
      <ul class="deal-list" aria-label="{tr("当期优惠")}">{body}</ul>''',
    )


def store_list(stores, items):
    counts = Counter(item.store_id for item in items.values())
    ranked = sorted(
        stores.items(),
        key=lambda pair: (pair[1].distance is None, pair[1].distance or 0),
    )
    cards = []
    for key, store in ranked:
        distance = (
            tr("约 {distance:.2f} 公里", distance=store.distance)
            if store.distance is not None
            else tr("距离待核实")
        )
        cards.append(f"""<article class="card"><h2><a href="/store?{escape(urlencode({"id": key}))}">
        {escape(store_name(store.name))}</a></h2><p>{distance} · {tr("{count} 项商品规格", count=counts[key])}</p></article>""")
    return layout(
        tr("附近门店"),
        f'<section class="hero"><h1>{tr("附近门店")}</h1></section><div class="grid">{"".join(cards)}</div>',
    )


def detail(item, store, today, clock=""):
    store_link = "/store?" + urlencode({"id": item.store_id})
    jan = link(item.jan_url, item.jan) if item.jan else tr("待核实")
    compare = (
        link("/?" + urlencode({"q": item.jan}), tr("查看同条码商品"))
        if item.jan
        else ""
    )
    title = product_name(item.name)
    original = (
        f'<p lang="ja">{tr("日文原名")} · {escape(item.name)}</p>'
        if title != item.name
        else ""
    )
    offers = []
    for offer in item.offers:
        dates = offer.day + (
            f"{tr(' 至 ')}{offer.until}"
            if offer.until and offer.until != offer.day
            else ""
        )
        member = ""
        if offer.member_taxed is not None:
            member = f"<p>{tr('会员现金价')} <strong>{money(offer.member_taxed)}</strong> {tr('含税')} · {money(offer.member_untaxed)} {tr('未税')}</p>"
        offers.append(f"""<article class="card offer"><div class="meta"><span>{tr(offer.origin)}</span>
        <span class="tag">{tr(offer.status(today, clock))}</span></div><p>{escape(dates)}</p>
        <p>{tr("普通价")} <strong>{money(offer.taxed)}</strong> {tr("含税")} · {money(offer.untaxed)} {tr("未税")}</p>{member}
        <p>{escape(localized_note(offer.note))}</p>{link(offer.url, tr("查看原始价格来源"))}</article>""")
    return layout(
        title,
        f'''<section class="hero"><a href="{escape(store_link)}">‹ {escape(store_name(store.name))}</a>
    <h1>{escape(title)}</h1>{original}<p>{escape(localized_spec(item.spec) or tr("规格待核实"))} · {escape(tr(item.category))}</p>
    <p>{tr("JAN 条码")} {jan} {compare}</p></section><h2>{tr("价格记录")}</h2><div class="offers">{"".join(offers)}</div>''',
    )


def distance_km(origin, position):
    lat, lng, other_lat, other_lng = map(radians, (*origin, *position))
    arc = (
        sin((other_lat - lat) / 2) ** 2
        + cos(lat) * cos(other_lat) * sin((other_lng - lng) / 2) ** 2
    )
    return 12742 * asin(sqrt(min(1, arc)))


def area_page(stores, items, today, clock, positions, params):
    try:
        lat, lng, radius = (float(params[key][0]) for key in ("lat", "lng", "km"))
        if not (-85 <= lat <= 85 and -180 <= lng <= 180 and 0.25 <= radius <= 10):
            raise ValueError
    except (KeyError, ValueError):
        return 400, layout(
            tr("圈内比价"),
            f"<p>{tr('请从地图选择有效的范围。')}</p>{link('/map', tr('返回地图'))}",
        )
    nearby = sorted(
        (distance_km([lat, lng], point), key)
        for key, point in positions.items()
        if key in stores
    )
    nearby = [(distance, key) for distance, key in nearby if distance <= radius]
    covered, groups = compare_deals(
        current_deals(items.values(), today, clock), {key for _, key in nearby}
    )
    stores_list = " · ".join(
        link("/?" + urlencode({"store": key}), store_name(stores[key].name))
        for _, key in nearby
    )
    back = "/map?" + urlencode({"lat": lat, "lng": lng, "km": radius})
    cards = []
    for (category, unit, jan), results in groups.items():
        base, suffix = category.split("（", 1) if "（" in category else (category, "")
        label = (
            product_name(category)
            if jan
            else tr(base) + (f"（{tr(suffix[:-1])}）" if suffix else "")
        )
        cheapest = results[0].unit_price
        rows = []
        for result in results:
            item, offer = result.item, result.offer
            name = link(f"/products/{item.id}", product_name(item.name))
            tag = (
                f"<small>{tr('本组最低')}</small>"
                if result.unit_price == cheapest
                else ""
            )
            rows.append(f"""<li class="deal-row"><div><strong>{escape(store_name(stores[item.store_id].name))}</strong>
              <div>{name}</div><small>{escape(localized_spec(item.spec))} · {money(offer.taxed)} {tr("含税")}</small>
            </div><div class="deal-prices"><strong>{money(result.unit_price)}</strong><small>{tr(unit)}</small>{tag}</div></li>""")
        cards.append(
            f'<section class="compare-group"><h2>{escape(label)}</h2><ul class="deal-list">{"".join(rows)}</ul></section>'
        )
    results_html = (
        "".join(cards)
        or f'<p class="empty">{tr("圈内暂时没有两家以上门店可比的当期商品。")}</p>'
    )
    summary = tr(
        "{radius:g} 公里 · {stores} 家门店 · {offers} 项当期优惠 · {groups} 组可比",
        radius=radius,
        stores=len(nearby),
        offers=covered,
        groups=len(groups),
    )
    return 200, layout(
        tr("圈内比价"),
        f"""<section class="hero">{link(back, tr("返回地图"))}<h1>{tr("圈内比价")}</h1>
        <p>{summary}</p><p>{tr("按同类商品的含税单价比较，价格来自已收录的当期优惠。")}</p>
        <p>{stores_list}</p></section><div class="compare-results">{results_html}</div>""",
    )


def map_page(stores, items, today, positions, origin_path, clock="", params=None):
    try:
        origin = json.loads(origin_path.read_text())
        if (
            not isinstance(origin, list)
            or len(origin) != 2
            or not all(
                type(n) in (int, float) and abs(n) <= limit
                for n, limit in zip(origin, (85, 180), strict=True)
            )
        ):
            origin = None
    except (OSError, ValueError):
        origin = None
    search_center, radius = origin, 1.0
    if params:
        try:
            lat, lng, value = (float(params[key][0]) for key in ("lat", "lng", "km"))
            if -85 <= lat <= 85 and -180 <= lng <= 180 and 0.25 <= value <= 10:
                search_center, radius = [lat, lng], value
        except (KeyError, ValueError):
            pass
    pins = []
    deals = list(current_deals(items.values(), today, clock))
    for key, position in positions.items():
        store = stores.get(key)
        if not store:
            continue
        active = [(item, offer) for item, offer in deals if item.store_id == key]
        pins.append(
            {
                "name": store_name(store.name),
                "point": position,
                "distance": round(distance_km(origin, position), 2) if origin else None,
                "url": "/?" + urlencode({"store": key}),
                "history": "/store?" + urlencode({"id": key}),
                "count": len(active),
                "preview": [
                    {
                        "name": product_name(item.name),
                        "spec": localized_spec(item.spec),
                        "price": money(offer.taxed),
                        "url": f"/products/{item.id}",
                    }
                    for item, offer in active[:5]
                ],
            }
        )
    pins.sort(
        key=lambda pin: pin["distance"] if pin["distance"] is not None else float("inf")
    )
    messages = {
        "distance": tr("直线 {distance} 公里 · ", distance="{distance}"),
        "count": tr("{count} 项当期商品", count="{count}"),
        "active": tr("查看当期商品和价格"),
        "empty": tr("目前没有收录这家门店的当期价格。"),
        "history": tr("门店页面与历史价格"),
        "origin": tr("你的街区起点"),
        "tiles": tr("底图暂时载入失败，门店标记仍可点击。"),
        "within": tr("圈内 {count} 家门店", count="{count}"),
        "center": tr("比价圆心"),
    }
    data = escape(
        json.dumps(
            {
                "origin": origin,
                "pins": pins,
                "messages": messages,
                "searchCenter": search_center,
                "radius": radius,
            },
            ensure_ascii=False,
        )
    )
    return layout(
        tr("附近超市地图"),
        f'''<section class="hero"><h1>{tr("附近超市地图")}</h1>
    <p>{tr("点击门店查看目前收录的商品与含税价")} · {escape(today)} JST</p></section>
    <section class="map-layout" id="shopping-map" data-map="{data}" data-error="{tr("地图数据无法读取，请使用下方门店列表。")}">
    <div class="map-stage"><div class="map-toolbar"><div id="map-stores" aria-label="{tr("选择门店")}" hidden></div>
      <button type="button" id="map-overview">{tr("查看全图")}</button></div>
      <div id="map" aria-label="{tr("附近门店地图")}"></div>
      <div class="compare-controls"><label>{tr("比价半径")} <input id="compare-radius" type="number" min="0.25" max="10" step="any" value="{radius:g}"> km</label>
      <span id="compare-count" aria-live="polite"></span><a id="compare-link" class="map-action" href="/map" hidden>{tr("比价")}</a></div>
      <small class="map-note">{tr("点击地图移动圆心。")}</small></div>
      <aside class="card" id="map-panel" aria-live="polite"><p>{tr("选择一个门店。")}</p></aside>
    </section><p class="map-note">{tr("蓝点为你的街区起点。距离为直线距离，商品由公开页面和当期传单提供。")}</p>
    <details class="map-note"><summary>{tr("查看门店列表")}</summary>{" · ".join(link(pin["url"], pin["name"]) for pin in pins)}</details>''',
        '<link rel="stylesheet" href="/leaflet.css"><script defer src="/leaflet.js"></script><script defer src="/map.js"></script>',
    )


def create_server(port, exports, source_file, database_path=None):
    package = Path(__file__).resolve().parent
    assets = package / "assets"
    try:
        positions = json.loads(assets.joinpath("stores-map.json").read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("Could not load the bundled store coordinates") from exc
    static_assets = {
        "/leaflet.js": ("leaflet.js", "text/javascript"),
        "/leaflet.css": ("leaflet.css", "text/css"),
        "/map.js": ("map.js", "text/javascript"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = urlsplit(self.path)
            if route.path == "/styles.css":
                css = (package / "web_styles.css").read_bytes()
                self.send_content(200, css, "text/css; charset=utf-8")
                return
            if route.path in static_assets:
                filename, kind = static_assets[route.path]
                self.send_content(
                    200,
                    assets.joinpath(filename).read_bytes(),
                    f"{kind}; charset=utf-8",
                )
                return
            stores, items = load_catalog(exports, source_file, database_path)
            timestamp = datetime.now(JST)
            today, clock = timestamp.date().isoformat(), timestamp.strftime("%H:%M")
            params = parse_qs(route.query)
            languages = SimpleCookie()
            try:
                languages.load(self.headers.get("Cookie", ""))
            except CookieError:
                languages.clear()
            stored = languages.get("grocery_lang")
            self.selected_language = params.pop("lang", [""])[0]
            language = self.selected_language or (stored.value if stored else "zh")
            language_token = LANGUAGE.set("ja" if language == "ja" else "zh")
            url_token = PAGE_URL.set(self.path)
            try:
                self.render_page(route.path, stores, items, today, clock, params)
            finally:
                PAGE_URL.reset(url_token)
                LANGUAGE.reset(language_token)

        def render_page(self, path, stores, items, today, clock, params):
            store_id = params.get("id", [""])[0]
            item = (
                items.get(path.removeprefix("/products/"))
                if path.startswith("/products/")
                else None
            )
            status = 200
            if path in ("/map", "/compare"):
                configured_positions = {
                    source.id: [source.latitude, source.longitude]
                    for source in sources(source_file)
                    if source.latitude is not None and source.longitude is not None
                }
                all_positions = positions | configured_positions
                if path == "/compare":
                    status, page = area_page(
                        stores, items, today, clock, all_positions, params
                    )
                else:
                    page = map_page(
                        stores,
                        items,
                        today,
                        all_positions,
                        exports.parent / "map-origin.json",
                        clock,
                        params,
                    )
            elif path == "/" and set(params) <= {"store"}:
                page = promotions(
                    stores, items, today, clock, params.get("store", [""])[0]
                )
            elif path == "/":
                page = listing(stores, items, params, today, clock=clock)
            elif path == "/stores":
                page = store_list(stores, items)
            elif path == "/store" and store_id in stores:
                page = listing(stores, items, params, today, store_id, clock)
            elif item:
                page = detail(item, stores[item.store_id], today, clock)
            else:
                status, page = (
                    404,
                    layout(
                        tr("页面未找到"),
                        f"<h1>{tr('页面未找到')}</h1><p>{link('/?view=products', tr('返回商品列表'))}</p>",
                    ),
                )
            self.send_content(status, page.encode(), "text/html; charset=utf-8")

        def send_content(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; img-src https://cyberjapandata.gsi.go.jp; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            selected = getattr(self, "selected_language", "")
            if selected in ("zh", "ja"):
                self.send_header(
                    "Set-Cookie",
                    f"grocery_lang={selected}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax",
                )
            if content_type.startswith("text/html"):
                self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--exports", type=Path, default=Path("data/exports"))
    parser.add_argument(
        "--database", action="store_true", help="Read local SQLite promotions"
    )
    args = parser.parse_args()
    settings = Settings()
    database_path = settings.database if args.database else None
    with create_server(
        args.port, args.exports, settings.source_config, database_path
    ) as server:
        print(f"http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
