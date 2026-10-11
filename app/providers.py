"""Real providers, used when OO_FIXTURES=0. Both are free and need no key:
OpenStreetMap's Overpass API for places and Open-Meteo for weather.
"""
from datetime import datetime

import requests

from app.hours import is_open

# Public Overpass servers are often busy, so try each in turn.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 25
USER_AGENT = "outofoffice-hackathon/0.1"

# category -> (OSM filters, our tags, activity, indoor)
CATEGORIES = {
    "viewpoint": (["tourism=viewpoint"], ["viewpoint", "sunset", "photography"], "sunset", False),
    "beach": (["natural=beach"], ["beach", "swim"], "beach", False),
    "waterfall": (["waterway=waterfall"], ["waterfall", "nature", "trek"], "trek", False),
    "fort": (["historic=fort", "historic=castle"], ["fort", "heritage", "photography"], "walk", False),
    "museum": (["tourism=museum", "tourism=gallery"], ["museum", "art", "indoor"], "indoor", True),
    "market": (["amenity=marketplace"], ["market", "shopping", "food"], "market", False),
    "cafe": (["amenity=cafe"], ["cafe", "food", "indoor"], "cafe", True),
    "park": (["leisure=park"], ["park", "nature", "walk"], "walk", False),
    "attraction": (["tourism=attraction"], ["attraction"], "walk", False),
}
DEFAULT_CATEGORIES = ["attraction", "viewpoint", "beach"]
# WMO weather codes that mean rain, drizzle, showers or thunderstorm
RAIN_CODES = set(range(51, 68)) | set(range(80, 83)) | set(range(95, 100))


def overpass_places(categories: list, lat: float, lng: float, radius_m: int, now: datetime) -> list:
    wanted = [c for c in categories if c in CATEGORIES] or DEFAULT_CATEGORIES
    clauses = "".join(
        f'nwr[{f.replace("=", "=\"", 1)}"]["name"](around:{int(radius_m)},{lat},{lng});'
        for c in wanted
        for f in CATEGORIES[c][0]
    )
    query = f"[out:json][timeout:20];({clauses});out center tags 40;"
    elements, last_error = None, None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(
                url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S
            )
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except (requests.RequestException, ValueError) as e:
            last_error = e
    if elements is None:
        raise RuntimeError(f"every Overpass server failed; last error: {last_error}")

    places = []
    for el in elements:
        osm = el.get("tags", {})
        category = next(
            (c for c in wanted if any(osm.get(f.split("=")[0]) == f.split("=")[1] for f in CATEGORIES[c][0])),
            None,
        )
        point = el if "lat" in el else el.get("center")
        if category is None or point is None:
            continue
        _, tags, activity, indoor = CATEGORIES[category]
        places.append(
            {
                "id": f"osm-{el['type']}-{el['id']}",
                "name": osm.get("name:en") or osm["name"],
                "kind": "place",
                "lat": point["lat"],
                "lng": point["lon"],
                "tags": tags,
                "activity": activity,
                "indoor": indoor,
                "cost_inr": 0,
                "cost_known": False,
                "exists": True,  # it is a mapped, named feature in OpenStreetMap
                "hours": osm.get("opening_hours"),
                "open_now": is_open(osm.get("opening_hours"), now),
                "source": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            }
        )
    return places


def open_meteo_weather(lat: float, lng: float) -> str:
    r = requests.get(
        OPEN_METEO_URL,
        params={"latitude": lat, "longitude": lng, "current": "precipitation,weather_code"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    current = r.json()["current"]
    raining = current.get("precipitation", 0) > 0 or current.get("weather_code") in RAIN_CODES
    return "rain" if raining else "clear"
