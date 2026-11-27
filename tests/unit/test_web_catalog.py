from dataclasses import replace
from decimal import Decimal

from grocery_intelligence.web import detail, listing, promotions
from grocery_intelligence.web_catalog import (
    Item,
    Offer,
    Store,
    previous_price,
    valid_jan,
)


def test_web_price_page_and_active_filter():
    current = Offer(
        "https://example.com/flyer",
        "门店传单",
        "2026-11-27",
        "2026-11-29",
        Decimal(398),
        Decimal("429.84"),
        Decimal(387),
        Decimal("417.96"),
        "每人限 2 件",
    )
    item = Item("fish", "store", "ぶり 切身 <見本>", "100g", "鲜鱼", "", "", [current])
    stores = {"store": Store("附近超市", 0.8)}
    page = detail(item, stores["store"], "2026-11-27")
    assert "¥429.84" in page and "¥417.96" in page
    assert "<見本>" not in page and "&lt;見本&gt;" in page
    params = {"q": ["ぶり 100g"], "active": ["1"]}
    assert "/products/fish" in listing(stores, {"fish": item}, params, "2026-11-29")
    assert "/products/fish" not in listing(stores, {"fish": item}, params, "2026-11-30")


def test_morning_offer_stops_at_noon():
    offer = Offer(
        "https://example.com/flyer",
        "门店传单",
        "2026-11-27",
        "2026-11-27",
        Decimal(148),
        Decimal(159),
        None,
        None,
        "开店至12:00",
    )
    assert offer.status("2026-11-27", "11:59") == "传单期内"
    assert offer.status("2026-11-27", "12:00") == "时段已结束"


def test_deals_show_only_active_promotions_and_comparable_previous_prices():
    current = Offer(
        "https://example.com/new",
        "门店传单",
        "2026-11-27",
        "2026-11-29",
        None,
        Decimal(180),
        None,
        None,
        "",
    )
    prior = replace(
        current,
        url="https://example.com/old",
        day="2026-11-24",
        until="2026-11-26",
        taxed=Decimal(200),
    )
    item = Item(
        "apple", "shop", "サンふじりんご", "1個", "果物", "", "", [current, prior]
    )
    other = replace(
        item, id="milk", name="明治 おいしい牛乳", spec="900mL", offers=[current]
    )
    expired = replace(item, id="expired", offers=[prior])
    generic = replace(
        item, id="regular", offers=[replace(current, origin="门店页面", until="")]
    )
    page = promotions(
        {"shop": Store("附近超市")},
        {x.id: x for x in (item, other, expired, generic)},
        "2026-11-27",
        "18:00",
    )
    assert page.count('class="deal-row"') == 2
    assert "之前价格未知" in page
    assert "¥180" in page and 'href="https://example.com/old"' in page
    assert previous_price(replace(item, spec=""), current) is None
    assert (
        previous_price(
            replace(item, offers=[current, replace(prior, origin="搜索索引")]), current
        )
        is None
    )
    assert (
        previous_price(
            replace(item, offers=[current, prior, replace(prior, taxed=Decimal(220))]),
            current,
        )
        is None
    )


def test_jan_check_digit():
    assert valid_jan("4902705096011")
    assert not valid_jan("4902705096012")
