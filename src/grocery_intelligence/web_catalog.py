"""Read local price exports for the neighborhood shopping browser."""

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from grocery_intelligence.config import sources
from grocery_intelligence.database import database, query
from grocery_intelligence.models import JST, normalized

STORES = {
    "オーケー 千葉幸町": "ok-chiba-saiwaicho",
    "カスミ 千葉みなと": "kasumi-chiba-minato",
    "カスミ 稲毛海岸": "kasumi-inagekaigan",
    "カスミ 西千葉": "kasumi-nishichiba",
    "ワイズマート ペリエ稲毛海岸": "ysmart-inagekaigan",
    "ワイズマート ペリエ西千葉": "ysmart-nishichiba",
    "スーパーせんどう 千葉みなと": "sendo-chiba-minato",
    "マルエツ 千葉幸町": "maruetsu-chiba-saiwaicho",
    "ベルクス 美浜": "belx-mihama",
    "業務スーパー 新港": "gyomu-shinminato",
    "ヤオコー 稲毛海岸": "yaoko-inagekaigan",
    "イオン マリンピア": "aeon-marinpia",
}


OK_MATCHES = {
    "【ベーカリー】手作り アメリカンペパロニとウインナーピザ": (
        "手作り アメリカンペパロニとウインナーピザ",
        "直径約30cm 1枚",
    ),
    "手羽元あぶり焼チキン 6本+1本増量": ("手羽元あぶり焼チキン", "6本+1本"),
    "なま ぶり 切身 養殖": ("ぶり 切身 養殖", "100g"),
    "和牛(黒毛和種)A4・A5等級 肩肉 極うすぎり(1.0〜1.5mmカット)": (
        "黒毛和種 A4・A5等級 肩肉 極うすぎり",
        "100g",
    ),
    "【寿司】具沢山まぐろ太巻": ("具沢山まぐろ太巻", "ハーフ"),
}
JAN_MATCHES = {
    ("ウィルキンソン ドライジンジャーエール アップル", "500ml"): (
        "4514603484519",
        "https://www.asahiinryo.co.jp/products/list/jan/0002.html",
    ),
    ("コカ・コーラ", "700ml"): (
        "4902102137089",
        "https://paypay.ne.jp/uploads/event/pdf_retail-cocacola-20260601_jancode_01.pdf",
    ),
    ("明治 R-1 ドリンク レギュラー", "112g"): (
        "4902705096011",
        "https://www.tajimaya-cc.net/products/192382",
    ),
    ("明治 R-1 ドリンク 低糖・低カロリー", "112g"): (
        "4902705096028",
        "https://www.tajimaya-cc.net/products/192383",
    ),
}


@dataclass(frozen=True)
class Offer:
    url: str
    origin: str
    day: str
    until: str
    untaxed: Decimal | None
    taxed: Decimal | None
    member_untaxed: Decimal | None
    member_taxed: Decimal | None
    note: str

    def status(self, today: str, clock: str = ""):
        if self.origin == "搜索索引":
            return "历史样本"
        if self.day > today:
            return "尚未开始"
        if (self.until and today > self.until) or (
            self.origin == "门店页面" and self.day < today
        ):
            return "历史记录"
        if clock:
            if (
                re.search(r"(?:开店至12:00|開店〜12時|朝市 9:00–12:00)", self.note)
                and clock >= "12:00"
            ):
                return "时段已结束"
            if "朝市 9:00–12:00" in self.note and clock < "09:00":
                return "尚未开始"
            if match := re.search(r"(\d{2}:\d{2}) (起|まで)", self.note):
                hour, bound = match.groups()
                if bound == "起" and clock < hour:
                    return "尚未开始"
                if bound == "まで" and clock >= hour:
                    return "时段已结束"
        return "今日页面" if self.origin == "门店页面" else "传单期内"


@dataclass
class Item:
    id: str
    store_id: str
    name: str
    spec: str
    category: str
    jan: str
    jan_url: str
    offers: list[Offer]


@dataclass(frozen=True)
class Store:
    name: str
    distance: float | None = None
    url: str = ""


def active_promotion(offer: Offer, today: str, clock: str) -> bool:
    status = offer.status(today, clock)
    if status == "传单期内":
        return bool(offer.until)
    return status == "今日页面" and any(
        marker in offer.note for marker in ("タイムセール", "当日限り", "特売", "特価")
    )


def previous_price(item: Item, current: Offer) -> Offer | None:
    """Compare ordinary prices within one store and product specification."""
    if not item.spec or current.taxed is None:
        return None
    candidates = [
        offer
        for offer in item.offers
        if offer.taxed is not None
        and offer.origin == current.origin
        and offer.origin != "搜索索引"
        and (offer.until or offer.day) < current.day
    ]
    if not candidates:
        return None
    latest_day = max(offer.until or offer.day for offer in candidates)
    latest = [offer for offer in candidates if (offer.until or offer.day) == latest_day]
    if len({offer.taxed for offer in latest}) != 1:
        return None
    return latest[0]


def http_url(value):
    parts = urlsplit(value)
    return (
        value
        if parts.scheme in ("https", "http") and parts.hostname and not parts.username
        else ""
    )


def exact_amount(value):
    if not value:
        return None
    try:
        result = Decimal(value)
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def valid_jan(value):
    if len(value) not in (8, 13) or not value.isascii() or not value.isdecimal():
        return False
    return (
        sum(
            int(digit) * (1 if i % 2 == 0 else 3)
            for i, digit in enumerate(reversed(value))
        )
        % 10
        == 0
    )


def _iter_records(exports):
    patterns = (
        ("flyer-live-page-prices-*.csv", "门店页面"),
        ("flyer-index-price-samples-*.csv", "搜索索引"),
        ("ok-kanto-flyer-*.csv", "关东周报"),
        ("store-flyers-*.csv", ""),
    )
    for pattern, origin in patterns:
        for path in sorted(exports.glob(pattern)):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    yield row, origin or row["source_scope"]


def load_catalog(exports: Path, source_file: Path, database_path: Path | None = None):
    stores = {
        row.id: Store(row.store_name, row.scenario_distance_km, str(row.url))
        for row in sources(source_file)
        if row.store_address or row.latitude is not None or row.enabled
    }
    items: dict[str, Item] = {}
    for row, origin in _iter_records(exports):
        store = row.get("store") or "オーケー 千葉幸町"
        store_id = STORES.get(store, store)
        stores.setdefault(
            store_id, Store(store, url=http_url(row.get("store_url", "")))
        )
        name = normalized(row["product"])
        spec = normalized(row.get("spec") or "")
        if store_id == "ok-chiba-saiwaicho" and origin == "门店页面":
            name, spec = OK_MATCHES.get(name, (name, spec))
        if not name:
            continue
        key = hashlib.blake2s(
            f"{store_id}\x1f{name}\x1f{spec}".encode(), digest_size=10
        ).hexdigest()
        category = row.get("category") or "未分类"
        jan = row.get("jan") or ""
        jan_url = http_url(row.get("jan_url") or "")
        for (match_name, suffix), identification in JAN_MATCHES.items():
            if name == match_name and spec.casefold().endswith(suffix):
                jan, jan_url = identification
                break
        item = items.setdefault(
            key, Item(key, store_id, name, spec, category, "", "", [])
        )
        if category != "未分类":
            item.category = category
        if jan_url and valid_jan(jan):
            item.jan, item.jan_url = jan, jan_url
        day = (
            row.get("page_date_jst")
            or row.get("edition_start")
            or row.get("valid_from")
            or row.get("source_date")
        )
        if not day:
            continue
        date.fromisoformat(day)
        limit = row.get("per_person_limit")
        note = " · ".join(
            filter(
                None,
                (
                    row.get("offer_label"),
                    row.get("note"),
                    f"每人限 {limit} 件" if limit else "",
                ),
            )
        )
        offer = Offer(
            http_url(row.get("product_url") or row["source_url"]),
            origin,
            day,
            row.get("edition_end") or row.get("valid_until") or "",
            exact_amount(
                row.get("nonmember_or_card_before_tax_jpy")
                or row.get("price_before_tax_jpy")
            ),
            exact_amount(
                row.get("nonmember_or_card_including_tax_jpy")
                or row.get("price_including_tax_jpy")
            ),
            exact_amount(row.get("member_cash_before_tax_jpy")),
            exact_amount(row.get("member_cash_including_tax_jpy")),
            note,
        )
        if offer not in item.offers:
            item.offers.append(offer)
    if database_path is not None and database_path.exists():
        with database(database_path) as connection:
            records = query(connection)
        for record in records:
            if (
                record.sales_channel != "physical_store"
                or record.price_type != "promotion"
            ):
                continue
            stores.setdefault(record.store_id, Store(record.store_name))
            name = record.product_name_normalized
            spec = (
                f"{record.quantity.normalize():f}{record.unit}"
                if record.quantity is not None
                else ""
            )
            key = hashlib.blake2s(
                f"{record.store_id}\x1f{name}\x1f{spec}".encode(), digest_size=10
            ).hexdigest()
            jan = record.jan_code or ""
            url = http_url(record.source_url or "")
            item = items.setdefault(
                key,
                Item(
                    key,
                    record.store_id,
                    name,
                    spec,
                    record.category or "未分类",
                    jan if valid_jan(jan) else "",
                    url,
                    [],
                ),
            )
            day = (record.valid_from or record.observed_at).date().isoformat()
            end = record.valid_until.date().isoformat() if record.valid_until else day
            origin = "门店传单" if record.source_type == "flyer" else "门店页面"
            offer = Offer(
                url,
                origin,
                day,
                end,
                record.price_excluding_tax,
                record.price_including_tax,
                None,
                None,
                "特売" if origin == "门店页面" else "",
            )
            if offer not in item.offers:
                item.offers.append(offer)
    timestamp = datetime.now(JST)
    today, clock = timestamp.date().isoformat(), timestamp.strftime("%H:%M")
    for item in items.values():
        item.offers.sort(
            key=lambda offer: (
                offer.status(today, clock) in ("今日页面", "传单期内"),
                offer.until or offer.day,
                offer.origin != "搜索索引",
                offer.origin != "门店页面",
            ),
            reverse=True,
        )
    return stores, {key: item for key, item in items.items() if item.offers}
