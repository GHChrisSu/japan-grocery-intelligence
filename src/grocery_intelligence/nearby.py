"""Look up nearby supermarkets in OpenStreetMap without fetching store sites."""

import json
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import HttpUrl, ValidationError

from grocery_intelligence.config import Source


def nearby_sources(latitude, longitude, radius, user_agent):
    if not (
        -85 <= latitude <= 85 and -180 <= longitude <= 180 and 100 <= radius <= 5000
    ):
        raise ValueError("Invalid coordinates or radius (100–5000 metres)")
    query = f"[out:json][timeout:15];nwr[shop=supermarket](around:{radius},{latitude:.3f},{longitude:.3f});out center 100;"
    request = Request(
        "https://overpass-api.de/api/interpreter",
        data=urlencode({"data": query}).encode(),
        headers={"User-Agent": user_agent},
    )
    with urlopen(request, timeout=25) as response:
        try:
            result = json.loads(response.read(2_000_000))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Overpass did not return a valid response") from exc
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("elements"), list)
        or result.get("remark")
    ):
        raise ValueError("Overpass returned an incomplete result")
    return parse_candidates(result)


def parse_candidates(result):
    candidates = []
    for row in result.get("elements", []):
        tags = row.get("tags", {})
        point = row.get("center", row)
        kind, osm_id = row.get("type"), row.get("id")
        if kind not in ("node", "way", "relation") or not isinstance(osm_id, int):
            continue
        name = tags.get("name") or tags.get("brand")
        if not name or "lat" not in point or "lon" not in point:
            continue
        osm_url = f"https://www.openstreetmap.org/{kind}/{osm_id}"
        try:
            url = HttpUrl(tags.get("website") or tags.get("contact:website") or osm_url)
            if url.username or url.password:
                url = HttpUrl(osm_url)
            value = urlsplit(str(url))
            candidate = Source.model_validate(
                {
                    "id": f"osm-{kind}-{osm_id}",
                    "chain": tags.get("brand") or name,
                    "store_name": name,
                    "url": str(url),
                    "robots_url": f"{value.scheme}://{value.netloc}/robots.txt",
                    "source_type": "html",
                    "access_status": "unverified",
                    "latitude": point["lat"],
                    "longitude": point["lon"],
                    "notes": f"© OpenStreetMap contributors · {osm_url}",
                }
            )
        except ValidationError:
            continue
        candidates.append(candidate.model_dump(mode="json", exclude_none=True))
    return candidates
