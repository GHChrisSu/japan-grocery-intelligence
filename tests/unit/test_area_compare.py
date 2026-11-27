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
