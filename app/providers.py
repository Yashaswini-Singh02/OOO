"""Real providers, used when OO_FIXTURES=0.

Places and shops: Google Places (GOOGLE_API_KEY), with OpenStreetMap as the no-key fallback.
Videos: YouTube (same Google key) plus Tavily restricted to instagram.com for reel links.
Events: Tavily search, read by the model, then each venue looked up in Google Places.
Weather: Open-Meteo, no key.
Paid calls go through app.quota so a daily cap can never be exceeded.
"""
import os
from datetime import datetime

import openai
import requests
from pydantic import BaseModel

from app import quota
from app.hours import is_open

GOOGLE_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
GOOGLE_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
YOUTUBE_URL = "https://www.googleapis.com/youtube/v3/search"
TAVILY_URL = "https://api.tavily.com/search"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
GOOGLE_FIELDS = ",".join(
    f"places.{f}"
    for f in (
        "id", "displayName", "location", "primaryType", "types", "businessStatus",
        "currentOpeningHours.openNow", "googleMapsUri", "rating", "userRatingCount",
        "primaryTypeDisplayName", "shortFormattedAddress", "editorialSummary",
    )
)
GOOGLE_MAX_RADIUS_M = 50_000
MAX_EVENTS = 3

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
# category -> Google Places (New) types
GOOGLE_TYPES = {
    "viewpoint": ["tourist_attraction"],
    "beach": ["beach"],
    "waterfall": ["national_park", "tourist_attraction"],
    "fort": ["historical_landmark"],
    "museum": ["museum", "art_gallery"],
    "market": ["market"],
    "cafe": ["cafe"],
    "park": ["park"],
    "attraction": ["tourist_attraction"],
}
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


# ---------- Google Places ----------

def _google_key() -> str:
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    return key


def _google_post(url: str, body: dict) -> list:
    if not quota.allow("google"):
        raise RuntimeError("Google daily cap reached (OO_GOOGLE_DAILY_CAP)")
    r = requests.post(
        url,
        json=body,
        headers={"X-Goog-Api-Key": _google_key(), "X-Goog-FieldMask": GOOGLE_FIELDS},
        timeout=TIMEOUT_S,
    )
    if not r.ok:
        raise RuntimeError(f"Google Places {r.status_code}: {r.text[:300]}")
    return r.json().get("places", [])


def _from_google(p: dict, category: str, kind: str = "place") -> dict:
    _, tags, activity, indoor = CATEGORIES[category]
    status = p.get("businessStatus", "OPERATIONAL")
    return {
        "id": f"g-{p['id']}",
        "name": p["displayName"]["text"],
        "kind": kind,
        "lat": p["location"]["latitude"],
        "lng": p["location"]["longitude"],
        "tags": tags,
        "activity": activity,
        "indoor": indoor,
        "cost_inr": 0,
        "cost_known": False,
        "rating": p.get("rating"),
        "rating_count": p.get("userRatingCount"),
        "what": p.get("primaryTypeDisplayName", {}).get("text"),
        "address": p.get("shortFormattedAddress"),
        "summary": p.get("editorialSummary", {}).get("text"),
        "exists": status != "CLOSED_PERMANENTLY",
        # temporarily closed is a real place that is shut, not a missing one
        "open_now": False if status == "CLOSED_TEMPORARILY" else p.get("currentOpeningHours", {}).get("openNow"),
        "source": p.get("googleMapsUri"),
    }


def google_places(categories: list, lat: float, lng: float, radius_m: int) -> list:
    wanted = [c for c in categories if c in GOOGLE_TYPES] or DEFAULT_CATEGORIES
    types = sorted({t for c in wanted for t in GOOGLE_TYPES[c]})
    found = _google_post(
        GOOGLE_NEARBY_URL,
        {
            "includedTypes": types,
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(min(radius_m, GOOGLE_MAX_RADIUS_M)),
                }
            },
        },
    )
    places = []
    for p in found:
        own = set(p.get("types", [])) | {p.get("primaryType")}
        category = next((c for c in wanted if own & set(GOOGLE_TYPES[c])), wanted[0])
        places.append(_from_google(p, category))
    return places


def google_find(text: str, lat: float, lng: float, category: str = "attraction", kind: str = "place") -> list:
    """Free-text lookup near a point, e.g. a named venue or 'cashew feni shop'."""
    found = _google_post(
        GOOGLE_TEXT_URL,
        {
            "textQuery": text,
            "maxResultCount": 3,
            "locationBias": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": float(GOOGLE_MAX_RADIUS_M)}
            },
        },
    )
    return [_from_google(p, category, kind) for p in found]


# ---------- videos: YouTube + Instagram reel links ----------

def youtube_videos(place: str, city: str) -> list:
    if not quota.allow("youtube"):
        return []
    r = requests.get(
        YOUTUBE_URL,
        params={"part": "snippet", "type": "video", "maxResults": 2, "q": f"{place} {city}", "key": _google_key()},
        timeout=TIMEOUT_S,
    )
    if not r.ok:
        raise RuntimeError(f"YouTube {r.status_code}: {r.text[:300]}")
    return [
        {
            "place": place,
            "title": item["snippet"]["title"],
            "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
            "source": "youtube",
        }
        for item in r.json().get("items", [])
    ]


def tavily(query: str, max_results: int = 5, include_domains: list | None = None) -> list:
    """One basic Tavily search (1 credit). Returns [] when the daily cap is reached."""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    if not quota.allow("tavily"):
        return []
    body = {"query": query, "search_depth": "basic", "max_results": max_results}
    if include_domains:
        body["include_domains"] = include_domains
    r = requests.post(TAVILY_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT_S)
    if not r.ok:
        raise RuntimeError(f"Tavily {r.status_code}: {r.text[:300]}")
    return r.json().get("results", [])


def reel_links(place: str, city: str) -> list:
    """Public Instagram links found by search. Links only: nothing is scraped or re-hosted."""
    key = f"reels:{place}:{city}"
    hit = quota.cached(key)
    if hit is None:
        results = tavily(f"{place} {city} reel", max_results=3, include_domains=["instagram.com"])
        hit = [
            {"place": place, "title": r.get("title", ""), "url": r["url"], "source": "instagram"}
            for r in results
        ]
        quota.remember(key, hit)
    return hit


# ---------- events: search, read, then look each venue up ----------

class FoundEvent(BaseModel):
    name: str
    venue: str
    date: str
    start_time: str
    source_url: str


class FoundEvents(BaseModel):
    events: list[FoundEvent]


def read_events(results: list, district: str, from_date: str, to_date: str) -> list[FoundEvent]:
    """Have the model pull events out of search results. It may only use what is on the page."""
    pages = "\n\n".join(f"URL: {r['url']}\nTITLE: {r.get('title')}\n{r.get('content', '')}" for r in results)
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    f"From the search results, list events in {district} happening between "
                    f"{from_date} and {to_date}. Include an event only if the results state its "
                    "name, its venue and its date; use HH:MM for start_time or an empty string "
                    "if it is not stated. source_url must be the URL of the result it came from. "
                    "Never add an event the results do not mention. An empty list is a valid answer."
                ),
            },
            {"role": "user", "content": pages},
        ],
        response_format=FoundEvents,
    )
    parsed = completion.choices[0].message.parsed
    return parsed.events[:MAX_EVENTS] if parsed else []


def live_events(district: str, from_date: str, to_date: str, lat: float, lng: float) -> list:
    key = f"events:{district}:{from_date}:{to_date}"
    hit = quota.cached(key)
    if hit is not None:
        return hit
    results = tavily(f"events in {district} from {from_date} to {to_date}")
    events = []
    for e in read_events(results, district, from_date, to_date) if results else []:
        venues = google_find(f"{e.venue} {district}", lat, lng)
        if not venues:
            continue  # the venue could not be found, so the event is not offered at all
        v = venues[0]
        events.append(
            {
                **v,
                "id": f"event-{v['id']}-{e.date}",
                "name": f"{e.name} at {v['name']}",
                "kind": "event",
                "tags": ["event"],
                "activity": "event",
                "starts": f"{e.date}T{e.start_time}" if e.start_time else e.date,
                "source": e.source_url,
            }
        )
    quota.remember(key, events)
    return events
