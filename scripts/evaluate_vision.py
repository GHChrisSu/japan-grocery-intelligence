"""Verify paired OCR and vision transcriptions of the synthetic two-column flyer."""

import json
import re
from pathlib import Path

from grocery_intelligence.config import Settings, sources
from grocery_intelligence.vision import VisionCandidate, reconcile_card


def main():
    root = Path('data')
    source = sources(Settings().source_config)[0]
    try:
        readings = json.loads((root / 'processed/vision-fixture.json').read_text())
        truth = json.loads((root / 'raw/fixture-flyer-two-columns.truth.json').read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Invalid or missing evaluation input: {exc}') from None
    accepted = []
    for ocr, (name, size, basis, price) in zip(readings['ocr'], readings['vision'], strict=True):
        match = re.fullmatch(r'(?P<name>.+?)\s*(?P<tax_basis>税込|税抜)\s*(?P<amount>\d+)円', ocr)
        if not match:
            raise ValueError('No explicit price and tax label in OCR card')
        candidate = VisionCandidate.model_validate({'name': f'{name} {size}', 'amount': price, 'tax_basis': basis})
        accepted.append(reconcile_card(source, VisionCandidate.model_validate(match.groupdict()), candidate))
    expected = [f'{row[0]} {row[1]}' for row in truth]
    correct = sum(row.product_name_normalized == label and
                  (row.price_including_tax if target[2] == '税込' else row.price_excluding_tax) == target[3]
                  for row, label, target in zip(accepted, expected, truth, strict=True))
    print(f'{correct}/{len(truth)} exact paired rows from a synthetic flyer')


if __name__ == '__main__':
    main()
