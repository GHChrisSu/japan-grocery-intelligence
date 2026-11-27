from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Access(StrEnum):
    unverified = "unverified"
    permitted = "permitted"
    restricted = "restricted"
    manual_only = "manual_only"
    unavailable = "unavailable"


class HtmlSelectors(BaseModel):
    row: str = Field(min_length=1)
    name: str = Field(min_length=1)
    excluding: str = ""
    including: str = ""
    spec: str = ""
    jan: str = ""
    channel: Literal["physical_store", "online_store", "unknown"] = "unknown"
    price_type: Literal["regular", "promotion"] | None = None

    @model_validator(mode="after")
    def check_prices(self):
        if not (self.excluding or self.including):
            raise ValueError("Configure at least one price selector")
        return self


class Source(BaseModel):
    id: str
    chain: str
    store_name: str
    url: HttpUrl
    source_type: str
    access_status: Access
    enabled: bool = False
    crawl_interval_hours: float = 12
    concurrency: int = 1
    parser: str | None = None
    selectors: HtmlSelectors | None = None
    latitude: float | None = Field(default=None, ge=-85, le=85)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    terms_url: HttpUrl | None = None
    robots_url: HttpUrl | None = None
    permission_reference: str | None = None
    access_review: str | None = Field(default=None, min_length=1)
    store_address: str | None = None
    scenario_distance_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    notes: str = ""

    @model_validator(mode="after")
    def check(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both store coordinates")
        if self.parser == "css" and self.selectors is None:
            raise ValueError("The css parser requires selectors")
        if self.concurrency != 1 or self.crawl_interval_hours < 12:
            raise ValueError("Require concurrency=1 and at least 12 hours between runs")
        if self.enabled and (
            self.access_status != Access.permitted
            or not (self.access_review or self.permission_reference)
            or not self.robots_url
            or not self.parser
        ):
            raise ValueError(
                "Enabled source requires a documented access review, robots URL and parser"
            )
        if self.enabled and self.robots_url != HttpUrl(
            f"{self.url.scheme}://{self.url.host}"
            + (f":{self.url.port}" if self.url.port not in (80, 443, None) else "")
            + "/robots.txt"
        ):
            raise ValueError("robots URL must be the canonical URL of the same origin")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GROCERY_", env_file=".env", extra="ignore"
    )
    database: Path = Path("data/grocery.db")
    source_config: Path = Path("configs/sources.yaml")
    user_agent: str = "GroceryIntelligenceResearch/0.1"
    request_gap: float = 3
    vision_base_url: HttpUrl | None = None
    vision_model: str | None = None
    vision_api_key: str | None = Field(default=None, repr=False)


def sources(path: Path) -> list[Source]:
    result = [Source.model_validate(item) for item in yaml.safe_load(path.read_text())]
    if len({item.id for item in result}) != len(result):
        raise ValueError("Duplicate source IDs")
    return result
