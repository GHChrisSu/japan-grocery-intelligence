from decimal import Decimal

import pytest

from grocery_intelligence.vision import VisionCandidate, reconcile_card


def test_reconcile_agreement_and_reject_differences(local_source):
    ocr = VisionCandidate(name='国産若凌もも肉 100g', amount=Decimal(166), tax_basis='税抜')
    vision = VisionCandidate(name='国産若鶏もも肉 100g', amount=Decimal(166), tax_basis='税抜')
    candidate = reconcile_card(local_source, ocr, vision)
    assert candidate.product_name_normalized == '国産若鶏もも肉 100g'
    assert candidate.price_excluding_tax == Decimal(166)
    assert candidate.confidence is None
    for delta in ({'amount': Decimal(167)}, {'tax_basis': '税込'}, {'name': '国産若鶏もも肉 1kg'}):
        with pytest.raises(ValueError):
            reconcile_card(local_source, ocr, vision.model_copy(update=delta))
