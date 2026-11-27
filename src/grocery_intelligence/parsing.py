import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scrapling import Selector

from grocery_intelligence.models import JST, PriceObservation, extract_size, normalized


@dataclass(frozen=True)
class Layout:
    row: str
    name: str
    excluding: str
    including: str
    channel: str = "unknown"


LAYOUTS = {"marked-prices": Layout("[data-price-item]", ".name", ".ex-tax", ".inc-tax")}


def amount(value):
    if value is None:
        return None
    candidate = normalized(value).strip("¥円 ")
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", candidate):
        raise ValueError("Invalid or ambiguous amount")
    return Decimal(candidate.replace(",", ""))


def observation(source, name, untaxed=None, taxed=None, **metadata):
    display_name = normalized(name)
    quantity, unit = extract_size(display_name)
    return PriceObservation(
        chain_id=source.chain.casefold(),
        store_id=source.id,
        store_name=source.store_name,
        product_name_raw=name.strip(),
        product_name_normalized=display_name,
        quantity=quantity,
        unit=unit,
        price_excluding_tax=amount(untaxed),
        price_including_tax=amount(taxed),
        **metadata,
    )


def one(row, selector, required=False):
    if not selector:
        return None
    matches = row.css(selector)
    if len(matches) > 1 or (required and not matches):
        raise ValueError("Ambiguous product card or missing required label")
    return " ".join(matches[0].xpath(".//text()").getall()).strip() if matches else None


def has_layout(source):
    return source.parser in LAYOUTS or (
        source.parser == "css" and source.selectors is not None
    )


def parse_html(body, source):
    layout = source.selectors if source.parser == "css" else LAYOUTS[source.parser]
    if layout is None:
        raise ValueError("Missing configured layout")
    page = Selector(body) if isinstance(body, (str, bytes)) else body
    result = []
    for row in page.css(layout.row):
        name = one(row, layout.name, required=True)
        spec = one(row, getattr(layout, "spec", ""))
        label = f"{name} {spec}" if spec else name
        result.append(
            observation(
                source,
                label,
                one(row, layout.excluding),
                one(row, layout.including),
                source_type="html",
                source_url=str(source.url),
                sales_channel=layout.channel,
                jan_code=one(row, getattr(layout, "jan", "")),
                price_type=getattr(layout, "price_type", None),
                valid_from=row.attrib.get("data-valid-from"),
                valid_until=row.attrib.get("data-valid-until"),
            )
        )
    if not result:
        raise ValueError("No product cards matched the approved layout")
    return result


def tokyo_date(value):
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y年%m月%d日 %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=JST)
        except ValueError:
            continue
    return None
