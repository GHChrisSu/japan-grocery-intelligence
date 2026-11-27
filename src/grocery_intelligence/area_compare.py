"""Comparable unit prices for grocery offers in a selected map area."""

import re
from dataclasses import dataclass
from decimal import Decimal

from grocery_intelligence.models import normalized
from grocery_intelligence.web_catalog import Item, Offer
from grocery_intelligence.web_i18n import chinese_product

# Japanese names and Chinese display names resolve to the same product family.
# Each family has its own exclusions so a modifier meaningful for one family
# (e.g. sauce for a meat cut) does not suppress a different family (e.g. curry roux).
FAMILIES = (
    (
        "牛奶",
        r"牛乳|牛奶",
        r"布丁|プリン|面包|パン|スープ|汤|冰淇淋|アイス|咖啡|コーヒー|红茶|紅茶|奶茶|ミルクティー|低脂",
    ),
    (
        "鸡蛋",
        r"鸡蛋|たまご|タマゴ|鶏卵",
        r"布丁|プリン|面包|パン|吐司|三明治|サンド|蛋糕|ケーキ|卵焼|玉子焼",
    ),
    ("鸡胸肉", r"鸡胸(?:肉)?|鶏むね肉|どりむね肉|鶏ムネ肉", r"挽肉|绞肉|ひき肉|ミンチ"),
    (
        "鸡腿肉",
        r"鸡腿肉|若どりもも肉|若鶏もも肉|若鶏モモ切込",
        r"挽肉|绞肉|ひき肉|ミンチ",
    ),
    ("猪五花肉", r"猪五花肉|豚(?:肉)?(?:バラ|ばら)", r"ベーコン|培根"),
    ("猪里脊", r"猪里脊|豚(?:肉)?ロース", r"ハム|火腿|かたロース|肩里脊"),
    ("生鲜西兰花", r"西兰花|ブロッコリー", ""),
    ("菠菜", r"菠菜|ほうれん草", ""),
    ("白萝卜", r"白萝卜|大根|だいこん", ""),
    ("莲藕", r"莲藕|れんこん|蓮根|^はす(?:\s|$)", ""),
    ("洋葱", r"洋葱|玉ねぎ|たまねぎ", r"チップス|薯片|オニオンコンフィ|焦糖洋葱"),
    ("土豆", r"土豆|じゃがいも", r"チップス|薯片|薯条"),
    ("番茄", r"番茄|トマト", r"ジュース|番茄汁|果汁|ピザ|披萨|ケチャップ|番茄酱"),
    ("金针菇", r"金针菇|えのき", ""),
    ("蟹味菇", r"蟹味菇|ぶなしめじ", ""),
    ("香蕉", r"香蕉|バナナ", ""),
    ("蜜柑", r"蜜柑|みかん", r"ジュース|果汁|ゼリー|果冻"),
    ("绢豆腐", r"绢豆腐|きぬ|絹豆腐", r"厚揚げ|炸|唐揚"),
    ("老豆腐", r"老豆腐|もめん|木綿", r"厚揚げ|炸|唐揚"),
    ("养殖鰤鱼切身", r"ぶり.*養殖.*切身|ぶり.*切身.*養殖|养殖鰤鱼切身", ""),
    ("盐渍银鲑切身", r"塩銀鮭|淡盐银鲑|盐麴淡盐银鲑", ""),
    ("银鲑切身", r"銀鮭.*切身|银鲑切身", ""),
    ("剑鱼切身", r"めかじき.*切身|剑鱼切片|剑鱼切身", ""),
    ("生米", r"越光米|こしひかり|コシヒカリ|まっしぐら", r"切り餅|即食|米飯|ごはん"),
    (
        "酱油味袋装拉面",
        r"(?:酱油|しょうゆ|醤油).*(?:拉面|ラーメン)|ラーメン.*(?:しょうゆ|醤油)|札幌一番.*しょうゆ味",
        r"カップ|杯面|豚骨",
    ),
    ("豚骨味袋装拉面", r"豚骨.*(?:拉面|ラーメン|麺)|とんこつらーめん", r"カップ|杯面"),
    ("奶油炖菜块", r"奶油炖菜块|とろけるシチュークリーム|シチューミクス.*クリーム", ""),
    ("可乐饼", r"コロッケ|可乐饼", ""),
    ("鱼肉香肠", r"魚肉ソーセージ|鱼肉香肠", ""),
    (
        "肉香肠",
        r"ウインナー|ウィンナー|シャウエッセン|アルトバイエルン|香肠",
        r"ピザ|披萨|パン|面包|魚肉|鱼肉",
    ),
    ("薯片", r"ポテトチップス|薯片", ""),
    ("双层卷筒卫生纸", r"(?:トイレット|トイレロール).*ダブル|双层.*卫生纸", ""),
    ("单层卷筒卫生纸", r"(?:トイレット|トイレロール).*シングル|单层.*卫生纸", ""),
)
FRESH_FAMILIES = frozenset(label for label, _, _ in FAMILIES[:24]) - {"牛奶", "鸡蛋"}
PREPARED = re.compile(
    r"味付|調味|已调味|腌|便当|弁当|唐揚|炸|串|ソース|沙拉|サラダ|スープ|汤底"
)
METRIC = re.compile(
    r"(?<![\d./])(\d+(?:\.\d+)?)\s*(kg|ml|g|l)(?![a-z])(?:\s*[×x]\s*(\d+))?",
    re.IGNORECASE,
)
COUNT = re.compile(r"(?<![\d./])(\d+)\s*(個|个|コ|本|株|袋|ロール|食|切|枚)(?:入)?\)?$")


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
    category = next(
        (
            label
            for label, aliases, excluded in FAMILIES
            if re.search(aliases, name)
            and (not excluded or not re.search(excluded, name))
        ),
        None,
    )
    if category is None or (category in FRESH_FAMILIES and PREPARED.search(name)):
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
            "薯片",
        ):
            return Comparison(
                item, offer, category, "每袋（重量未标）", offer.taxed / int(count[1])
            )
        unit = {
            "個": "每件",
            "个": "每件",
            "コ": "每件",
            "本": "每根",
            "株": "每株",
            "枚": "每片",
            "切": "每片",
            "食": "每份",
            "ロール": "每卷",
        }.get(count[2])
        if unit:
            return Comparison(item, offer, category, unit, offer.taxed / int(count[1]))
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
