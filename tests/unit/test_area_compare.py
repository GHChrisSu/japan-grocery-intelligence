from dataclasses import replace
from decimal import Decimal

from grocery_intelligence.area_compare import comparable, compare_deals
from grocery_intelligence.web_catalog import Item, Offer


def test_area_comparison_normalizes_package_size_and_excludes_other_stores():
    offer = Offer(
        "", "门店传单", "2026-11-27", "2026-11-29", None, Decimal(240), None, None, ""
    )
    large = Item("large", "a", "明治 おいしい牛乳", "900mL", "乳制品", "", "", [offer])
    small = replace(large, id="small", store_id="b", spec="500mL")
    cheaper_package = replace(offer, taxed=Decimal(150))
    outside = replace(small, store_id="outside")
    covered, groups = compare_deals(
        [(large, offer), (small, cheaper_package), (outside, offer)], {"a", "b"}
    )
    assert covered == 2
    matches = groups[("牛奶", "每升", "")]
    assert [match.item.store_id for match in matches] == ["a", "b"]
    assert matches[1].unit_price == 300
    assert comparable(replace(large, name="明治 牛乳プリン"), offer) is None
    assert comparable(replace(large, spec="1/2袋"), offer) is None


def test_different_names_resolve_to_same_food_with_its_own_unit():
    offer = Offer(
        "", "门店传单", "2026-11-27", "2026-11-29", None, Decimal(170), None, None, ""
    )
    examples = (
        ("はす", "れんこん", "100g", ("莲藕", "每100克", "")),
        ("塩銀鮭切身 甘口", "塩銀鮭 甘塩味", "2切", ("盐渍银鲑切身", "每片", "")),
        (
            "チャルメラ しょうゆラーメン",
            "サッポロ一番 しょうゆ味",
            "各5食",
            ("酱油味袋装拉面", "每份", ""),
        ),
        (
            "とろけるシチュークリーム",
            "ハウス シチューミクス クリーム",
            "140g",
            ("奶油炖菜块", "每100克", ""),
        ),
    )
    template = Item("left", "a", "", "140g", "未分类", "", "", [offer])
    for first, second, spec, group in examples:
        left = replace(template, name=first, spec=spec)
        right = replace(left, id="right", store_id="b", name=second)
        _, groups = compare_deals([(left, offer), (right, offer)], {"a", "b"})
        assert group in groups
    assert comparable(replace(template, name="牛乳を使ったパン"), offer) is None
    assert comparable(replace(template, name="鶏もも唐揚げ弁当"), offer) is None
    chips = comparable(replace(template, name="玉ねぎ味 ポテトチップス"), offer)
    assert chips is not None and chips.category == "薯片"
