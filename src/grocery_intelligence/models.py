import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

JST = ZoneInfo('Asia/Tokyo')


def now():
    return datetime.now(JST)


def normalized(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', text).split())


class PriceObservation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    chain_id: str
    store_id: str
    store_name: str
    product_name_raw: str = Field(min_length=1, max_length=120)
    product_name_normalized: str = Field(min_length=1, max_length=120)
    category: str | None = None
    brand: str | None = None
    jan_code: str | None = Field(default=None, pattern=r'^(\d{8}|\d{13})$')
    origin_country: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    price_excluding_tax: Decimal | None = Field(default=None, ge=0)
    price_including_tax: Decimal | None = Field(default=None, ge=0)
    currency: Literal['JPY'] = 'JPY'
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    regular_price: Decimal | None = Field(default=None, ge=0)
    discount_rate: Decimal | None = Field(default=None, ge=0, le=1)
    discount_amount: Decimal | None = Field(default=None, ge=0)
    price_type: Literal['regular', 'promotion', 'clearance', 'receipt', 'member_only'] | None = None
    sales_channel: Literal['physical_store', 'online_store', 'unknown'] = 'unknown'
    membership_required: bool | None = None
    purchase_limit: int | None = Field(default=None, gt=0)
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    purchased_at: AwareDatetime | None = None
    observed_at: AwareDatetime = Field(default_factory=now)
    source_published_at: AwareDatetime | None = None
    source_type: str
    source_url: str | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    evidence_id: str | None = None
    raw_data_hash: str | None = None

    @field_validator('valid_from', 'valid_until', 'observed_at', 'source_published_at', 'purchased_at')
    @classmethod
    def local_time(cls, value):
        return value.astimezone(JST) if value else None

    @model_validator(mode='after')
    def integrity(self):
        untaxed, taxed = self.price_excluding_tax, self.price_including_tax
        if untaxed is None and taxed is None:
            raise ValueError('price and explicit tax basis required')
        if taxed is not None and untaxed is not None and taxed < untaxed:
            raise ValueError('taxed price is lower than untaxed price')
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError('invalid date interval')
        if (self.quantity is None) != (self.unit is None):
            raise ValueError('quantity and unit must be specified together')
        return self


def extract_size(text: str):
    size_pattern = r'(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(kg|ml|g|l|個|本)(?![a-zA-Z])'
    matches = re.findall(size_pattern, normalized(text), re.IGNORECASE)
    if len(matches) != 1 or re.search(r'[×✕*]', text):
        return None, None
    amount, unit = matches[0]
    unit = unit.lower()
    scale, base = {'kg': (1000, 'g'), 'l': (1000, 'ml')}.get(unit, (1, unit))
    return Decimal(amount.replace(',', '')) * scale, base


def unit_price(record: PriceObservation, basis: str = 'including'):
    amount = record.price_including_tax if basis == 'including' else record.price_excluding_tax
    if record.quantity is None or amount is None:
        return None
    scale = 100 if record.unit in ('g', 'ml') else 1
    return amount * scale / record.quantity, f'{scale}{record.unit}'
