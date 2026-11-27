"""Reproduce the Chiba city statistics sample in a separate evidence database."""

import csv
import hashlib
import io
import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from protego import Protego

from grocery_intelligence.fetching import http_get
from grocery_intelligence.models import now

ORIGIN = 'https://www.e-stat.go.jp'
URL = ORIGIN + '/stat-search/file-download?statInfId=000040527019&fileKind=0'
TERMS = ORIGIN + '/terms-of-use'
AGENT = 'GroceryIntelligenceResearch/0.1'
ATTRIBUTION = '出典：政府統計の総合窓口(e-Stat)「小売物価統計調査（動向編）」を加工'


def extract(body):
    workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    try:
        rows = workbook['B001-1'].values
        for _ in range(9):
            next(rows)
        city_column = next(rows).index('12100')
        header = next(rows)
        if header[city_column] != '千葉市' or header[9:14] != ('銘柄符号', '品目', '単位', '銘柄改正年月', '表章単位'):
            raise ValueError('Unexpected worksheet layout')
        for row in rows:
            code, name, unit, _, currency = row[9:14]
            value = row[city_column]
            if (row[8] == '2026年10月' and isinstance(code, int) and 1000 <= code < 1400 and currency == '円'
                    and isinstance(value, (int, float)) and not isinstance(value, bool)):
                yield {'city': '千葉市', 'month': '2026-10', 'item_code': code, 'item': name,
                       'unit': unit, 'price_jpy': str(Decimal(str(value)))}
    finally:
        workbook.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    local = Path('data/raw/estat-chiba-2026-10.xlsx')
    if args.download:
        rules = http_get(ORIGIN + '/robots.txt', AGENT)
        if rules.status != 200:
            raise SystemExit(f'robots.txt HTTP {rules.status}')
        robots = Protego.parse(rules.body.decode('utf-8-sig'))
        if not robots.can_fetch(URL, AGENT):
            raise SystemExit('robots.txt disallows the selected file')
        time.sleep(max(3, robots.crawl_delay(AGENT) or 0))
        result = http_get(URL, AGENT)
        if result.status != 200 or not result.body.startswith(b'PK'):
            raise SystemExit(f'Download stopped at HTTP {result.status}')
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(result.body)
    body = local.read_bytes()
    evidence = {'source_url': URL, 'terms_url': TERMS, 'attribution': ATTRIBUTION,
                'raw_data_hash': hashlib.sha256(body).hexdigest(), 'processed_at': now().isoformat()}
    records = [row | evidence for row in extract(body)]
    if not records:
        raise SystemExit('No matching statistics rows')
    output = Path('data/exports')
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect('data/estat-baseline.db') as db:
        db.execute('CREATE TABLE IF NOT EXISTS baseline (month TEXT, city TEXT, item_code INTEGER, record TEXT, PRIMARY KEY (month,city,item_code))')
        db.executemany('INSERT OR IGNORE INTO baseline VALUES (?, ?, ?, ?)', [
            (row['month'], row['city'], row['item_code'], json.dumps(row, ensure_ascii=False)) for row in records
        ])
    (output / 'estat-chiba-2026-10.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    with (output / 'estat-chiba-2026-10.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(f'{len(records)} city-level records, SHA-256 {evidence["raw_data_hash"]}')


if __name__ == '__main__':
    main()
