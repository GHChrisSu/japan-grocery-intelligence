"""Comparable unit prices for grocery offers in a selected map area."""

import re
from dataclasses import dataclass
from decimal import Decimal

from grocery_intelligence.models import normalized
from grocery_intelligence.web_catalog import Item, Offer
from grocery_intelligence.web_i18n import chinese_product

# Cuts and preparations with materially different prices stay in separate groups.
GROUPS = {
    "牛奶": r"(?:牛乳|牛奶)",
    "鸡蛋": r"(?:鸡蛋|たまご|タマゴ|鶏卵)",
    "鸡胸肉": r"(?:鸡胸肉|鶏むね肉|どりむね肉|鶏ムネ肉)",
    "鸡腿肉": r"(?:鸡腿肉|若どりもも肉|若鶏もも肉)",
    "猪五花肉": r"(?:猪五花肉|豚(?:肉)?(?:バラ|ばら))",
    "猪里脊": r"(?:猪里脊|豚(?:肉)?ロース)",
    "生鲜西兰花": r"(?:西兰花|ブロッコリー)",
    "菠菜": r"(?:菠菜|ほうれん草)",
    "白萝卜": r"(?:白萝卜|大根|だいこん)",
    "洋葱": r"(?:洋葱|玉ねぎ|たまねぎ)",
    "土豆": r"(?:土豆|じゃがいも)",
    "番茄": r"(?:番茄|トマト)",
    "金针菇": r"(?:金针菇|えのき)",
    "蟹味菇": r"(?:蟹味菇|ぶなしめじ)",
    "香蕉": r"(?:香蕉|バナナ)",
    "蜜柑": r"(?:蜜柑|みかん)",
    "绢豆腐": r"(?:绢豆腐|きぬ|絹豆腐)",
    "老豆腐": r"(?:老豆腐|もめん|木綿)",
    "养殖鰤鱼切身": r"(?:ぶり.*養殖.*切身|ぶり.*切身.*養殖|养殖鰤鱼切身)",
    "银鲑切身": r"(?:銀鮭.*切身|银鲑切身)",
    "剑鱼切身": r"(?:めかじき.*切身|剑鱼切片|剑鱼切身)",
    "生米": r"(?:越光米|こしひかり|コシヒカリ|まっしぐら)",
}
EXCLUDE = re.compile(
    r"味付|調味|已调味|腌|便当|弁当|唐揚|炸|串|丸|ソース|酱|サラダ|布丁|プリン|面包|パン|スープ|汤底|绞肉|挽肉|切り餅|即食|米飯|ごはん|ハーフ|半份|アイス|冰淇淋|咖啡|コーヒー|低糖|低脂|ミルクティー|红茶"
)
METRIC = re.compile(
    r"(?<![\d./])(\d+(?:\.\d+)?)\s*(kg|ml|g|l)(?![a-z])(?:\s*[×x]\s*(\d+))?",
    re.IGNORECASE,
)
COUNT = re.compile(r"(?<![\d./])(\d+)\s*(個|个|コ|本|株|袋|パック|盒|枚)(?:入)?$")


@dataclass(frozen=True)
class Comparison:
    item: Item
    offer: Offer
    category: str
    unit: str
    unit_price: Decimal


def comparable(item: Item, offer: Offer) -> Comparison | None:
    if offer.taxed is None:
        return None
    if item.jan and item.jan_url:
        return Comparison(item, offer, item.name, "同条码每份", offer.taxed)
    if not item.spec:
        return None
    name = f"{item.name} {chinese_product(item.name)}"
    if EXCLUDE.search(name):
        return None
    category = next(
        (key for key, pattern in GROUPS.items() if re.search(pattern, name)), None
    )
    if category is None:
        return None
    # Frozen, frozen-thawed and plain produce/cuts are distinct groups.
    if re.search(r"解凍|解冻", name + item.spec):
        category += "（解冻）"
    elif re.search(r"冷凍|冷冻", name + item.spec):
        category += "（冷冻）"
    spec = normalized(item.spec).replace(",", "")
    if re.search(r"\d/\d|[~〜]|\d\s*\+", spec):
        return None
    measures = list(METRIC.finditer(spec))
    if len(measures) == 1:
        quantity, unit, count = measures[0].groups()
        amount = Decimal(quantity) * int(count or 1)
        unit = unit.lower()
        if unit in ("kg", "l"):
            amount *= 1000
        if amount <= 0:
            return None
        if unit in ("l", "ml"):
            return Comparison(
                item, offer, category, "每升", offer.taxed * 1000 / amount
            )
        return Comparison(item, offer, category, "每100克", offer.taxed * 100 / amount)
    count = COUNT.search(spec) if not measures else None
    if count and int(count[1]) > 0:
        if count[2] == "袋" and category in (
            "菠菜",
            "金针菇",
            "蟹味菇",
            "香蕉",
            "蜜柑",
            "土豆",
            "洋葱",
        ):
            return Comparison(
                item, offer, category, "每袋（重量未标）", offer.taxed / int(count[1])
            )
        if count[2] in ("個", "个", "コ", "本", "株", "枚"):
            return Comparison(
                item, offer, category, "每件", offer.taxed / int(count[1])
            )
    return None


def compare_deals(deals, store_ids):
    groups: dict[tuple[str, str, str], dict[str, Comparison]] = {}
    covered = 0
    for item, offer in deals:
        store_id = item.store_id
        if store_id not in store_ids:
            continue
        covered += 1
        result = comparable(item, offer)
        if result is None:
            continue
        identity = item.jan if result.unit == "同条码每份" else ""
        category = item.jan if identity else result.category
        matches = groups.setdefault((category, result.unit, identity), {})
        existing = matches.get(store_id)
        if existing is None or result.unit_price < existing.unit_price:
            matches[store_id] = result
    return covered, {
        (
            next(iter(values.values())).category if identity else category,
            unit,
            identity,
        ): sorted(
            values.values(), key=lambda value: (value.unit_price, value.item.store_id)
        )
        for (category, unit, identity), values in sorted(groups.items())
        if len(values) >= 2
    }
