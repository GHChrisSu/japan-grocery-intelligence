# Self-host Japan Grocery Intelligence

Run this project on macOS or Linux with Python 3.12 and [uv](https://docs.astral.sh/uv/). Each installation keeps its own store configuration, map origin, SQLite database, and API keys.

```sh
uv sync --locked
cp .env.example .env
mkdir -p data
cp configs/sources.example.yaml data/sources.yaml
# In .env, set GROCERY_SOURCE_CONFIG=data/sources.yaml
uv run alembic upgrade head
uv run python -m grocery_intelligence.web --database
```

Open <http://127.0.0.1:8787>. Put a JSON `[latitude, longitude]` pair in `data/map-origin.json` to set the map origin. You can use a public landmark. The map requests background tiles from Japan's Geospatial Information Authority.

## Find stores and collect prices

Run `uv run grocery nearby LATITUDE LONGITUDE --radius 1500 --output data/candidates.yaml` or add stores to `data/sources.yaml` manually. The optional command sends a rounded coordinate and radius to OpenStreetMap's Overpass service once. It returns disabled store candidates, including their known websites or OSM listing links. Copy the candidates you want into `data/sources.yaml` and configure each store's price source.

For a public HTML price page, set the actual prices URL and review the publisher's rules. Configure the store's selectors as shown in `configs/sources.example.yaml`. Set `access_review` to your notes, `access_status: permitted`, and `enabled: true` once reviewed. For physical-store offers, set `channel: physical_store` and `price_type: promotion` when the source is a promotion page. Price selectors should target plain amounts with an explicit tax basis. Date attributes are optional. When present, put ISO 8601 timestamps with an offset in the card's `data-valid-from` / `data-valid-until` attributes.

```sh
uv run grocery check-sources
uv run grocery crawl --store your-store
uv run grocery crawl --all
uv run grocery scheduler
```

The scheduler runs attached to your terminal. Scrapling reads configured public HTML pages and checks `robots.txt` before collection. Every publisher's page structure differs. Image and dynamic pages may need a separate adapter or a local flyer import.

## Bring your own image model

Set `GROCERY_VISION_BASE_URL` to an OpenAI-compatible base URL ending in `/v1`, `GROCERY_VISION_MODEL` to a model with image input, and `GROCERY_VISION_API_KEY` if your service requires one. You can use a local or remote service. The image is sent to that service when you explicitly run `analyze`. Use a JPEG or PNG up to 10 MB.

```sh
uv run grocery analyze flyer.jpg --store your-store --output data/review.json
# Check the names, prices, tax basis, package size and dates against the image.
uv run grocery import-review data/review.json --store your-store
```

For today's comparison the app needs a verified tax-inclusive ordinary price. Enter the actual dates where known. An undated imported price appears for its observation day. Use the existing `import-flyer` command for local OCR. All collection runs and reviewed imports are stored locally, and the web server's `--database` option displays physical-store promotions.

## Circle comparison

Open **地图**, set a radius, click the map to move the center, and choose **比价**. The comparison shows the lowest verified ordinary tax-inclusive price from each in-range store for comparable items. Product rules distinguish cuts and preparations. Unweighed bags have their own unit. Same-JAN offers are compared by package. Coverage follows the offers that your collection or imports have gathered.

Source code is MIT licensed. Leaflet has its own BSD notice under `src/grocery_intelligence/assets/LICENSE`. Optional store candidates come from [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). Public map tiles retain the provider's attribution.
