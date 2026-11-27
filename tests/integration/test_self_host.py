import json
from pathlib import Path

from typer.testing import CliRunner

from grocery_intelligence.cli import app
from grocery_intelligence.nearby import parse_candidates
from grocery_intelligence.web_catalog import load_catalog


def test_configured_html_prices_appear_in_local_catalog(
    tmp_path, local_site, monkeypatch
):
    state, origin = local_site
    state["body"] = (
        '<article><h2>牛乳</h2><span class="size">500mL</span><span class="price">180</span></article>'
    )
    config = tmp_path / "sources.yaml"
    config.write_text(
        json.dumps(
            [
                {
                    "id": "local",
                    "chain": "Local",
                    "store_name": "Local",
                    "url": origin + "/prices",
                    "robots_url": origin + "/robots.txt",
                    "latitude": 35.68,
                    "longitude": 139.76,
                    "source_type": "html",
                    "access_status": "permitted",
                    "enabled": True,
                    "access_review": "Local fixture",
                    "parser": "css",
                    "selectors": {
                        "row": "article",
                        "name": "h2",
                        "including": ".price",
                        "spec": ".size",
                        "channel": "physical_store",
                        "price_type": "promotion",
                    },
                }
            ]
        )
    )
    db = tmp_path / "prices.db"
    monkeypatch.setenv("GROCERY_SOURCE_CONFIG", str(config))
    monkeypatch.setenv("GROCERY_DATABASE", str(db))
    result = CliRunner().invoke(app, ["crawl", "--store", "local"])
    assert result.exit_code == 0, result.output
    stores, items = load_catalog(Path(tmp_path / "exports"), config, db)
    assert "local" in stores
    item = next(iter(items.values()))
    assert item.spec == "500ml" and item.offers[0].taxed == 180


def test_osm_candidates_are_disabled():
    candidates = parse_candidates(
        {
            "elements": [
                {
                    "id": 100,
                    "type": "way",
                    "center": {"lat": 35.681, "lon": 139.767},
                    "tags": {"name": "Example", "website": "https://example.com/"},
                }
            ]
        }
    )
    assert candidates[0]["id"] == "osm-way-100"
    assert candidates[0]["enabled"] is False
    assert candidates[0]["access_status"] == "unverified"
