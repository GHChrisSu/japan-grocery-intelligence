import io
import re
import shutil
import subprocess
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from grocery_intelligence.models import normalized
from grocery_intelligence.parsing import observation, tokyo_date

MAX_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_PAGES = 12

PRIVATE = re.compile(
    r'会員|カード|電話|TEL|レジ|担当|合計|小計|預り|お預|釣銭|取引|承認|端末|'
    r'クレジット|ポイント|支払|住所|現金|割引|値引|領収|取扱|No\.|\d{7,}|(?:\d[ -]*){10,}', re.IGNORECASE
)
DATE = re.compile(r'(\d{4}(?:[/\-年])\d{1,2}(?:[/\-月])\d{1,2}日? \d{1,2}:\d{2})')
PRICE = r'[0-9][0-9,]*(?:\.[0-9]+)?'
FLYER_ROW = re.compile(rf'(?P<name>.+?)\s+(?P<basis>税抜|税込)\s*[¥]?\s*(?P<amount>{PRICE})\s*円')
RECEIPT_ROW = re.compile(rf'(?P<name>[^¥]+?)\s+¥\s*(?P<amount>{PRICE})\s*円?')


def ocr(picture: Image.Image) -> str:
    if picture.width * picture.height > MAX_PIXELS:
        raise ValueError('Image exceeds pixel limit')
    picture = ImageOps.autocontrast(ImageOps.grayscale(ImageOps.exif_transpose(picture)))
    output = io.BytesIO()
    picture.save(output, format='PNG')
    binary = shutil.which('tesseract')
    if not binary:
        raise ValueError('Install tesseract and the jpn language model')
    result = subprocess.run([binary, 'stdin', 'stdout', '-l', 'jpn', '--psm', '6'],
                            input=output.getvalue(), capture_output=True, timeout=40, check=False)
    if result.returncode:
        raise ValueError('Japanese OCR failed. Check the tesseract installation and its jpn language model')
    return result.stdout.decode('utf-8')


def document_text(path: Path, body: bytes):
    if len(body) > MAX_BYTES:
        raise ValueError('File exceeds size limit')
    if path.suffix.lower() == '.txt':
        return body.decode('utf-8')
    if path.suffix.lower() == '.pdf':
        texts = []
        with pymupdf.open(stream=body, filetype='pdf') as pdf:
            if pdf.needs_pass or pdf.page_count > MAX_PAGES:
                raise ValueError('PDF is encrypted or exceeds the page limit')
            for page in pdf:
                text = page.get_text('text', sort=True)
                if not isinstance(text, str):
                    raise TypeError('PDF did not return text')
                if not text.strip():
                    if page.rect.width * page.rect.height * 4 > MAX_PIXELS:
                        raise ValueError('PDF page exceeds pixel limit')
                    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csGRAY)
                    text = ocr(Image.frombytes('L', (pixmap.width, pixmap.height), pixmap.samples))
                texts.append(text)
        return '\n'.join(texts)
    with Image.open(io.BytesIO(body)) as img:
        return ocr(img)


def parse_document(text, source, kind):
    """Accept high-precision rows only. Review drafts before persisting."""
    is_receipt = kind == 'receipt'
    date_match = DATE.search(normalized(text)) if is_receipt else None
    purchased_at = tokyo_date(date_match[0]) if date_match else None
    records = []
    for raw in text.splitlines():
        line = normalized(raw).replace('税 抜', '税抜').replace('税 込', '税込')
        if PRIVATE.search(line) or DATE.search(line):
            continue
        match = (RECEIPT_ROW if is_receipt else FLYER_ROW).fullmatch(line)
        if not match:
            continue
        name = match['name']
        if not re.search(r'[ぁ-んァ-ヶ一-龯]', name) or PRIVATE.search(name):
            continue
        value = match['amount']
        basis = match['basis'] if not is_receipt else None
        # A receipt's item tax basis is often unspecified. Require an explicit
        # annotation on the reviewed transcription, e.g. 牛乳 税込 ¥198.
        if is_receipt:
            if not name.endswith(('税込', '税抜')):
                continue
            name, basis = name[:-2].strip(), name[-2:]
        records.append(observation(source, name, value if basis == '税抜' else None,
                                   value if basis == '税込' else None, source_type=kind,
                                   purchased_at=purchased_at, price_type='receipt' if is_receipt else None,
                                   sales_channel='physical_store' if is_receipt else 'unknown'))
    if not records:
        raise ValueError('No unambiguous item rows. Review OCR and import a labeled transcription')
    return records
