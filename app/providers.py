"""Real providers, used when OO_FIXTURES=0.

Places and shops: Google Places (GOOGLE_API_KEY), with OpenStreetMap as the no-key fallback.
Videos: YouTube (same Google key) plus Tavily restricted to instagram.com for reel links.
Events: Tavily search, read by the model, then each venue looked up in Google Places.
Weather: Open-Meteo, no key.
Paid calls go through app.quota so a daily cap can never be exceeded.
"""
import os
import re
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
        "primaryTypeDisplayName", "shortFormattedAddress", "editorialSummary", "addressComponents",
    )
)
GOOGLE_DETAILS_URL = "https://places.googleapis.com/v1/places/{id}"
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
    "viewpoint": (["tourism=viewpoint"], ["viewpoint", "sunset", "sunrise", "photography"], "sunset", False),
    "beach": (["natural=beach"], ["beach", "swim"], "beach", False),
    "waterfall": (["waterway=waterfall"], ["waterfall", "nature", "trek"], "trek", False),
    "fort": (["historic=fort", "historic=castle"], ["fort", "heritage", "photography"], "walk", False),
    "museum": (["tourism=museum", "tourism=gallery"], ["museum", "art", "indoor"], "indoor", True),
    "market": (["amenity=marketplace"], ["market", "shopping", "food"], "market", False),
    "cafe": (["amenity=cafe"], ["cafe", "food", "indoor"], "cafe", True),
    "restaurant": (["amenity=restaurant"], ["restaurant", "food", "indoor"], "food", True),
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
    "restaurant": ["restaurant"],
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


def _component(p: dict, *kinds: str) -> str | None:
    for kind in kinds:
        for part in p.get("addressComponents", []):
            if kind in part.get("types", []):
                return part.get("longText")
    return None


def _from_google(p: dict, category: str, kind: str = "place") -> dict:
    _, tags, activity, indoor = CATEGORIES[category]
    status = p.get("businessStatus", "OPERATIONAL")
    return {
        "id": f"g-{p['id']}",
        "google_id": p["id"],
        "types": p.get("types", []),
        "city": _component(p, "locality", "postal_town"),
        "district": _component(p, "administrative_area_level_3", "administrative_area_level_2"),
        "region": _component(p, "administrative_area_level_1"),
        "name": p["displayName"]["text"],
        "kind": kind,
        "lat": p["location"]["latitude"],
        "lng": p["location"]["longitude"],
        "tags": sun_tags(tags, p["displayName"]["text"]),
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


# Google has no place type for a viewpoint, so one is recognised by its name. A plain
# tourist attraction is never tagged as a sunset spot just because it was searched for.
VIEWPOINT_NAME = re.compile(r"sunset|sunrise|view ?point|lookout|\bpoint\b", re.I)
VIEWPOINT_QUERY = "sunset viewpoint"
SUNRISE_QUERY = "sunrise viewpoint"


def sun_tags(tags: list, name: str, wanted: str = "") -> list:
    """A viewpoint is tagged for sunrise or for sunset, not both, when there is anything to
    tell them apart: its own name first, else which of the two it was searched for.
    """
    low = name.lower()
    named = [t for t in ("sunrise", "sunset") if t in low]
    keep = named[0] if len(named) == 1 else wanted if wanted in ("sunrise", "sunset") else ""
    return [t for t in tags if not keep or t not in ("sunrise", "sunset") or t == keep]


def _category(p: dict, wanted: list) -> str:
    own = set(p.get("types", [])) | {p.get("primaryType")}
    if "viewpoint" in wanted and VIEWPOINT_NAME.search(p["displayName"]["text"]):
        return "viewpoint"
    return next((c for c in wanted if c != "viewpoint" and own & set(GOOGLE_TYPES[c])), "attraction")


def google_places(categories: list, lat: float, lng: float, radius_m: int, hint: str = "") -> list:
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
    places = {f"g-{p['id']}": _from_google(p, _category(p, wanted)) for p in found}
    if "viewpoint" in wanted:
        # a search by type cannot ask for sunset spots, so ask for them in words as well
        try:
            words = SUNRISE_QUERY if "sunrise" in hint.lower() else VIEWPOINT_QUERY
            spots = google_find(words, lat, lng, category="viewpoint")
        except (RuntimeError, requests.RequestException):
            spots = []  # the places found by type are still worth having
        sun = "sunrise" if words == SUNRISE_QUERY else "sunset"
        places.update({
            s["id"]: {**s, "tags": sun_tags(s["tags"], s["name"], sun)} for s in spots if "lodging" not in s["types"]
        })
    return list(places.values())


def google_find(
    text: str, lat: float | None = None, lng: float | None = None,
    category: str = "attraction", kind: str = "place",
) -> list:
    """Free-text lookup, e.g. a named venue, 'cashew feni shop' or a city. Results lean
    towards the given point when there is one.
    """
    body = {"textQuery": text, "maxResultCount": 3}
    if lat is not None:
        body["locationBias"] = {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": float(GOOGLE_MAX_RADIUS_M)}
        }
    return [_from_google(p, category, kind) for p in _google_post(GOOGLE_TEXT_URL, body)]


# ---------- videos: YouTube + Instagram reel links ----------

GENERIC_WORDS = {
    "the", "and", "of", "at", "in", "cafe", "restaurant", "hotel", "park", "temple", "church",
    "beach", "fort", "museum", "market", "room", "rooms", "centre", "center", "point", "view",
}


def core_name(place: str) -> str:
    """The venue's own name, without a trailing branch or area: 'X – HSR Layout' -> 'X'."""
    return re.split(r"\s+[–—|-]\s+|,|\(", place)[0].strip()


def is_about(place: str, text: str) -> bool:
    """True when the text clearly names the venue. A link that merely shares a city or a
    generic word with it is not about it, and is better left out than shown.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", core_name(place).lower()) if len(w) > 2]
    distinctive = [w for w in words if w not in GENERIC_WORDS] or words
    haystack = re.sub(r"[^a-z0-9]+", " ", text.lower())
    squashed = haystack.replace(" ", "")
    return bool(distinctive) and all(w in haystack or w in squashed for w in distinctive)


def youtube_videos(place: str, city: str) -> list:
    if not quota.allow("youtube"):
        return []
    r = requests.get(
        YOUTUBE_URL,
        params={"part": "snippet", "type": "video", "maxResults": 5,
                "q": f'"{core_name(place)}" {city}'.strip(), "key": _google_key()},
        timeout=TIMEOUT_S,
    )
    if not r.ok:
        raise RuntimeError(f"YouTube {r.status_code}: {r.text[:300]}")
    return [
        {
            "place": place,
            "title": item["snippet"]["title"],
            "text": item["snippet"].get("description", ""),
            "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
            "source": "youtube",
        }
        for item in r.json().get("items", [])
        if is_about(place, item["snippet"]["title"] + " " + item["snippet"].get("description", ""))
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
    key = f"reels2:{place}:{city}"
    hit = quota.cached(key)
    if hit is None:
        results = tavily(
            f'"{core_name(place)}" {city}'.strip(), max_results=5, include_domains=["instagram.com"]
        )
        hit = [
            {"place": place, "title": r.get("title", ""), "text": r.get("content", "")[:600],
             "url": r["url"], "source": "instagram"}
            for r in results
            if is_about(place, f"{r.get('title', '')} {r.get('content', '')}")
        ]
        hit.sort(key=lambda v: "/reel/" not in v["url"])  # actual reels before profile pages
        quota.remember(key, hit)
    return hit


# ---------- places Instagram names for one kind of thing in one town ----------

MAX_REEL_SPOTS = 6


class ReelSpot(BaseModel):
    name: str
    source_url: str


class ReelSpots(BaseModel):
    spots: list[ReelSpot]


def same_place(a: str, b: str, ignore: tuple = ()) -> bool:
    """True when two names are the same venue: 'Monsoon Palace' and 'Sajjangarh Monsoon
    Palace'. Generic words and the kind searched for ('sunset') do not count as a match.
    """
    def words(name: str) -> set:
        found = re.findall(r"[a-z0-9]+", core_name(name).lower())
        return {w for w in found if len(w) > 2 and w not in GENERIC_WORDS and w not in ignore}

    x, y = words(a), words(b)
    if not x or not y:
        return core_name(a).lower() == core_name(b).lower()
    return x <= y or y <= x


def reel_spots(kind: str, city: str) -> list:
    """The places public Instagram posts name for one kind of thing in one town, e.g. sunset
    spots in Udaipur: [{"name", "url"}]. One Tavily credit and one small model call per kind
    and town, then cached. A name is kept only when the post it is credited to contains it.
    """
    key = f"reelspots:{kind}:{city.lower()}"
    hit = quota.cached(key)
    if hit is not None:
        return hit
    search_key = f"reelsearch:{kind}:{city.lower()}"
    results = quota.cached(search_key)
    if results is None:
        results = tavily(f"best {kind} spots in {city}", max_results=8, include_domains=["instagram.com"])
        quota.remember(search_key, results)  # so a failed model call never costs a second credit
    posts = {r["url"]: f"{r.get('title', '')} {r.get('content', '')}" for r in results}
    if not posts:
        return []
    pages = "\n\n".join(f"URL: {url}\n{text[:800]}" for url, text in posts.items())
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "These are Instagram posts found by a search. List the specific, named places "
                    "in the given town that a post recommends for the given kind of outing. Use "
                    "only names written in the posts, spelled as written, each with the URL of "
                    "the post that names it. Leave out hotels, the town itself, and anything "
                    "vague such as 'the lake' or 'old city'. Return an empty list if none."
                ),
            },
            {"role": "user", "content": f"Town: {city}\nKind of outing: {kind}\n\nPosts:\n{pages}"},
        ],
        response_format=ReelSpots,
    )
    parsed = completion.choices[0].message.parsed
    hit, seen = [], set()
    for s in (parsed.spots if parsed else []):
        if s.source_url in posts and is_about(s.name, posts[s.source_url]) and s.name.lower() not in seen:
            seen.add(s.name.lower())
            hit.append({"name": s.name, "url": s.source_url})
    hit = hit[:MAX_REEL_SPOTS]
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


# ---------- what a place is ----------

def google_about(google_id: str) -> dict:
    """Google's own description of one place, plus a few visitor reviews to describe it from."""
    if not quota.allow("google"):
        return {}
    r = requests.get(
        GOOGLE_DETAILS_URL.format(id=google_id),
        headers={"X-Goog-Api-Key": _google_key(),
                 "X-Goog-FieldMask": "editorialSummary,generativeSummary,reviews,photos"},
        timeout=TIMEOUT_S,
    )
    if not r.ok:
        raise RuntimeError(f"Google Places {r.status_code}: {r.text[:300]}")
    body = r.json()
    summary = body.get("editorialSummary", {}).get("text") or (
        body.get("generativeSummary", {}).get("overview", {}).get("text")
    )
    reviews = [x.get("text", {}).get("text", "") for x in body.get("reviews", [])]
    photos = body.get("photos", [])
    return {
        "summary": summary,
        "reviews": [x[:500] for x in reviews if x][:4],
        "photo_name": photos[0]["name"] if photos else None,
        "photo_by": (photos[0].get("authorAttributions") or [{}])[0].get("displayName") if photos else None,
    }


def google_photo(photo_name: str, width: int = 1000) -> str | None:
    """A direct image address for one place photo. The address Google returns carries no
    API key, so it is safe to hand to a chat service.
    """
    if not quota.allow("google"):
        return None
    r = requests.get(
        f"https://places.googleapis.com/v1/{photo_name}/media",
        params={"maxWidthPx": width, "skipHttpRedirect": "true"},
        headers={"X-Goog-Api-Key": _google_key()},
        timeout=TIMEOUT_S,
    )
    if not r.ok:
        raise RuntimeError(f"Google Places photo {r.status_code}: {r.text[:300]}")
    return r.json().get("photoUri")


class Described(BaseModel):
    about: str  # what the place is
    reviews_say: str  # what visitors report, kept apart so it is never read as fact


def describe(name: str, what: str | None, details: dict) -> dict:
    """What a place is, and separately what its reviewers say. Written only from Google's
    description and reviews; either part is empty when there is nothing to base it on.
    """
    empty = {"about": "", "reviews_say": ""}
    if not details.get("summary") and not details.get("reviews"):
        return empty
    facts = f"Name: {name}\nType: {what or 'unknown'}\nDescription: {details.get('summary') or 'none'}\n"
    facts += "Visitor reviews:\n" + ("\n".join(f"- {x}" for x in details.get("reviews", [])) or "none")
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "Describe a place for a traveller using only the details given. "
                    "about: one short sentence saying what the place is, from its type and "
                    "description. Leave about empty when all you could say is its name and type "
                    "('X is a park'), since both are already shown. reviews_say: at most 18 words "
                    "on what the reviews add that about does not already say: practical things "
                    "such as effort, crowds, timing, what to bring, or a complaint. Never repeat "
                    "about in other words; if the reviews add nothing new, leave it empty. It is "
                    "shown after the words 'Reviews say:', so do not start it with 'Reviewers' or "
                    "'Visitors'. Leave reviews_say empty if there are no reviews. Never add facts, prices, hours or "
                    "history that are not in the details. Plain text, no superlatives."
                ),
            },
            {"role": "user", "content": facts},
        ],
        response_format=Described,
    )
    parsed = completion.choices[0].message.parsed
    return parsed.model_dump() if parsed else empty


# ---------- more about one place ----------

def google_more(google_id: str) -> dict:
    """The practical details of one place: weekly hours, website, phone and review excerpts."""
    if not quota.allow("google"):
        return {}
    r = requests.get(
        GOOGLE_DETAILS_URL.format(id=google_id),
        headers={"X-Goog-Api-Key": _google_key(),
                 "X-Goog-FieldMask": "regularOpeningHours.weekdayDescriptions,websiteUri,"
                                     "nationalPhoneNumber,reviews"},
        timeout=TIMEOUT_S,
    )
    if not r.ok:
        raise RuntimeError(f"Google Places {r.status_code}: {r.text[:300]}")
    body = r.json()
    quotes = [
        {"text": x.get("text", {}).get("text", "")[:280], "rating": x.get("rating"),
         "when": x.get("relativePublishTimeDescription")}
        for x in body.get("reviews", []) if x.get("text", {}).get("text")
    ]
    return {
        "hours": body.get("regularOpeningHours", {}).get("weekdayDescriptions", []),
        "website": body.get("websiteUri"),
        "phone": body.get("nationalPhoneNumber"),
        "quotes": quotes[:2],
    }


# ---------- the words around a trip plan ----------

class DayTheme(BaseModel):
    day: int
    theme: str


class PlaceHook(BaseModel):
    id: str
    hook: str


class TripStory(BaseModel):
    intro: str
    themes: list[DayTheme]
    hooks: list[PlaceHook]
    spotlight_line: str


def trip_story(city: str, places: list, spotlight: dict | None) -> dict:
    """The friendly wording around an already-verified plan: an opening line, a theme per
    day, a short hook per place and a line for the highlighted place. The model chooses
    words only; which places are in the plan was decided before it is called.
    """
    lines = [
        f"- id={p['id']} | day {p['day']} | {p['name']} | {p.get('what') or 'place'} | "
        f"{p.get('summary') or 'no description'}"
        for p in places
    ]
    highlight = (
        f"{spotlight['name']} | {spotlight.get('what') or 'place'} | {spotlight.get('summary') or 'no description'} "
        f"| chosen as: {spotlight['label']}" if spotlight else "none"
    )
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Outofoffice, a travel buddy who plans trips for a friend: warm, a "
                    "little playful, never salesy. You are given a finished, verified plan. Write "
                    "only the words around it. intro: one sentence that makes them look forward "
                    "to the trip. themes: for each day, three to five words capturing that day's "
                    "feel from its places. hooks: for each place id, at most ten words on why to "
                    "go, taken from its type and description. spotlight_line: one sentence on the "
                    "highlighted place, or empty if there is none. Use only the details given: no "
                    "prices, hours, history, dishes or claims that are not in them. If a place has "
                    "no description, base its hook on its type alone. No emoji."
                ),
            },
            {"role": "user", "content": f"Trip: {city}\nPlan:\n" + "\n".join(lines) + f"\nHighlight: {highlight}"},
        ],
        response_format=TripStory,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        return {}
    known = {p["id"] for p in places}
    return {
        "intro": parsed.intro,
        "themes": {t.day: t.theme for t in parsed.themes},
        "hooks": {h.id: h.hook for h in parsed.hooks if h.id in known},
        "spotlight_line": parsed.spotlight_line,
    }


# ---------- is a link really about the place? ----------

class Verdicts(BaseModel):
    about_place: list[bool]


def subject_check(place: str, what: str | None, links: list) -> list:
    """Keep only links whose subject is the place itself. Naming the place is not enough:
    a post about a villa or cafe "near" it mentions it too, and would mislead.
    """
    if not links:
        return []
    listing = "\n".join(f"{i + 1}. TITLE: {v.get('title', '')}\n   TEXT: {v.get('text', '')[:500]}" for i, v in enumerate(links))
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "For each numbered post, decide whether its main subject is the named place "
                    "itself, so that someone watching it would see that place. Answer false when "
                    "the place is only mentioned in passing, used as a landmark for something "
                    "else nearby (a villa, hotel, village, cafe, road), listed among several "
                    "places, or when you cannot tell. Return one boolean per post, in order."
                ),
            },
            {"role": "user", "content": f"Place: {place} ({what or 'place'})\n\nPosts:\n{listing}"},
        ],
        response_format=Verdicts,
    )
    parsed = completion.choices[0].message.parsed
    verdicts = parsed.about_place if parsed else []
    if len(verdicts) != len(links):
        return []  # an answer that does not line up cannot be trusted for any of them
    return [v for v, ok in zip(links, verdicts) if ok]


# ---------- the buddy's own words: always built on checked facts, never beyond them ----------

# How hard the model thinks. Deciding (what a message means, which place suits someone) gets
# high effort. Wording gets low effort, because it runs on every reply and a written line is
# checked against the facts afterwards anyway. Both can be set: OO_EFFORT, OO_VOICE_EFFORT.
THINK = {"reasoning": {"effort": os.getenv("OO_EFFORT", "high")}}
VOICE_THINK = {"reasoning": {"effort": os.getenv("OO_VOICE_EFFORT", "low")}}


class Said(BaseModel):
    line: str


def _say(system: str, user: str) -> str:
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=Said,
        extra_body=VOICE_THINK,
    )
    parsed = completion.choices[0].message.parsed
    return parsed.line.strip() if parsed else ""


BUDDY_MAX_CHARS = 280
REACT_MAX_CHARS = 420
VOICE = (
    "Your voice: an excited friend who knows travel, texting. Pick up on what they said and play "
    "it back with energy, the way a friend would ('Udaipur for three more days and you LOVE "
    "sunsets?'). Contractions, short punchy sentences, one or two emojis that fit, the odd word "
    "in capitals for emphasis. Have an opinion and a plan. Never sound like a form being "
    "confirmed: no 'Got it', 'Noted', 'I'll remember that', 'On it', and no brochure words such "
    "as hidden gem, must-visit, nestled, vibrant."
)


def grounded(line: str, facts: str, limit: int = BUDDY_MAX_CHARS) -> bool:
    """A cheap guard on a model-written line: short enough, and every number in it is one
    that the facts contain. A line that fails is dropped in favour of the plain reasons.
    """
    numbers = re.findall(r"\d+(?:\.\d+)?", line)
    return bool(line) and len(line) <= limit and all(n in facts for n in numbers)


def buddy_line(place: dict, reasons: list, notes: list, now: str, likes: list = ()) -> str:
    """One or two sentences, in a friend's voice, on why this place for this traveller.
    Written by a model from the checked facts only; "" when it strays or cannot be written.
    """
    facts = "\n".join(x for x in [
        f"Place: {place['name']} ({place.get('what') or 'place'})",
        f"Description: {place.get('about')}" if place.get("about") else "",
        f"What reviews say: {place.get('reviews_say')}" if place.get("reviews_say") else "",
        f"Distance: {place['distance_m'] / 1000:.1f} km" if place.get("distance_m") is not None else "",
        "Open right now: yes" if place.get("hours_verified") else "Open right now: not known",
        f"Local time now: {now[11:16]}" if now else "",
        f"Why I picked it: {' '.join(reasons)}" if reasons else "",
        f"What they love: {', '.join(likes)}" if likes else "",
        f"What they told me about themselves: {'; '.join(notes)}" if notes else "",
    ] if x)
    line = _say(
        "You are a travel buddy texting a friend about one place you picked for them. Write one "
        "or two short sentences, at most 30 words, speaking to them as 'you'. " + VOICE + " "
        "The description and the reviews are printed right under your message, so do not retell "
        "them: give the personal angle, and at most one tip taken from them. Only credit them "
        "with tastes listed under 'What they love'; a sunrise lover is not a sunset lover. If "
        "the place does not really match what they love, say so honestly in a few words. "
        "Say why they would enjoy it, or give one practical tip, drawing on why you picked it. "
        "Use only the facts given. Do not add any detail that is not in them: no views, dishes, "
        "history, prices, opening times or crowds unless the facts say so. Do not say it is open "
        "unless the facts say yes. Mention what they told you about themselves only when the "
        "facts support the link. Do not repeat the place's name, its rating or its distance, "
        "which are shown beside your message. No hashtags.",
        facts,
    )
    return line if grounded(line, facts) else ""


CAN_DO = (
    "find one verified place near them (they share a location, or say e.g. 'somewhere to eat' "
    "or 'a cafe in Udaipur'), plan a trip ('plan 2 days in Udaipur'), say what a town is known "
    "for buying, track a trip budget and spending, remember likes, dislikes and what they packed, "
    "match outfits to places, and speak up on its own when they share live location"
)


def chat_reply(text: str, memory: str) -> str:
    """A short conversational reply to small talk or a general travel question. It may not
    name venues, prices or times: those only ever come from the checked pipeline.
    """
    return _say(
        "You are Outofoffice, a travel buddy in a chat app. Reply in one to three short sentences, "
        "specific to what they said. " + VOICE + " You can chat, react, and share general, "
        "widely known travel advice (weather by season, what to carry, etiquette, how long "
        "things take). Never name a specific venue, restaurant, shop, hotel, price, opening time "
        "or event, because you only recommend places you have checked: when they need one, tell "
        "them in passing what to send you to get it. Promise only that a place you find will be "
        "real and checked, never that it will match their diet or other wishes. Use what you remember about them when it "
        f"is relevant. What you can do: {CAN_DO}. No lists.",
        f"What I remember about them:\n{memory or 'nothing yet'}\n\nTheir message: {text}",
    )


def react(text: str, understood: list, memory: str) -> str:
    """The buddy's reaction to a message, in place of a row of confirmations. It says what
    was taken in and what happens next, in a friend's voice. "" when it strays, so the plain
    confirmations are sent instead.
    """
    facts = f"{text}\n" + "\n".join(understood) + f"\n{memory}"
    line = _say(
        "You are Outofoffice, a travel buddy in a chat app. The traveller just sent a message. "
        "Below it is what you understood and what you are doing about it. " + VOICE + " "
        "How much to say depends on the message. "
        "If they only asked for something (somewhere to eat, another suggestion, a plan): one "
        "lively line, at most 20 words, that says you are on it. No plan, no list. "
        "If they told you about themselves or the trip (where, how long, what they love or "
        "avoid, a budget): two or three short sentences, at most 45 words. React to it, then say "
        "how you would shape their time in terms of kinds of experience that follow from what "
        "THEY said (for a sunset lover with three evenings that might be one classic lake view, "
        "one panoramic hilltop, one offbeat spot in nature; build yours from their message, do "
        "not reuse this one), then lead into what you are doing next. "
        "Everything you understood must come across, including any amount of money exactly as "
        "given. Rules that always hold: never name a specific venue, restaurant, shop, dish, "
        "price or opening time, only kinds of place, because the places come from a checked "
        "search. Never promise a place will suit their diet or other wishes: say you will keep "
        "it in mind. Do not invent facts about the town. When you are about to find places, do "
        "not ask a question: the places come next and the question after them. If nothing is "
        "being searched yet, end by asking what they want first, and do not mention searching.",
        f"Their message: {text}\n\nWhat I understood and am doing:\n" + "\n".join(f"- {u}" for u in understood)
        + f"\n\nWhat I remember about them:\n{memory or 'nothing yet'}",
    )
    return line if grounded(line, facts, REACT_MAX_CHARS) else ""


class ShortLine(BaseModel):
    place_id: str
    line: str


class ShortStory(BaseModel):
    lines: list[ShortLine]
    ask: str


def _place_facts(p: dict) -> str:
    return " | ".join(x for x in [
        f"id={p['id']}", p["name"], p.get("what") or "place",
        f"description: {p['summary']}" if p.get("summary") else "no description",
        f"rated {p['rating']} by {p['rating_count']} people" if p.get("rating") and p.get("rating_count") else "",
        f"{p['distance_m'] / 1000:.1f} km away" if p.get("distance_m") is not None else "",
        "also named in Instagram posts for this" if p.get("reel") else "",
    ] if x)


def shortlist_story(places: list, kinds: list, notes: list, city: str) -> dict:
    """The wording around a few already-verified places: one line each on why, and a closing
    question. The model chooses words only; a line that strays from its place's facts is dropped.
    """
    facts = {p["id"]: _place_facts(p) for p in places}
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {"role": "system", "content": (
                "You are a travel buddy who has just found a few places for a friend. For each "
                "place write one line, at most 16 words, on why it suits what they love, using "
                "only that place's own facts below: its type, description, rating and distance. "
                "With no description, keep to what its name and type make certain. Never guess "
                "how busy, quiet or empty a place will be. Do not add "
                "views, history, timings, prices or crowds that the facts do not state. Do not "
                "repeat the place's name, its rating or its distance: those are shown beside your "
                "line, so say what kind of morning or evening it would be instead. Then write "
                "`ask`: only a question, at most 18 words, offering two or three next steps from: "
                "food, a full day-by-day plan, more ideas of this kind, what to pack. Do not "
                "greet them or react again in `ask`; that was already said. " + VOICE
            )},
            {"role": "user", "content": (
                f"Town: {city or 'unknown'}\nThey love: {', '.join(kinds)}\n"
                f"About them: {'; '.join(notes) or 'nothing else'}\n\nPlaces:\n" + "\n".join(facts.values())
            )},
        ],
        response_format=ShortStory,
        extra_body=VOICE_THINK,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        return {}
    lines = {x.place_id: x.line.strip() for x in parsed.lines
             if x.place_id in facts and grounded(x.line.strip(), facts[x.place_id], 160)}
    ask = parsed.ask.strip()
    return {"lines": lines, "ask": ask if ask and len(ask) <= 200 and not re.search(r"\d", ask) else ""}


class Fit(BaseModel):
    place_id: str


def best_fit(places: list, notes: list) -> str | None:
    """Of a few places that all passed the checks, the id of the one that best suits what
    the traveller said about themselves. A choice between verified options, not a claim.
    """
    listing = "\n".join(
        f"- id={p['id']} | {p['name']} | {p.get('what') or 'place'} | {p.get('summary') or 'no description'}"
        for p in places
    )
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {"role": "system", "content": (
                "Choose the one place from the list that best suits this traveller, given what "
                "they said about themselves. The list is in order of preference already, so keep "
                "the first one unless another clearly suits them better. Answer with its id exactly."
            )},
            {"role": "user", "content": f"About the traveller: {'; '.join(notes)}\n\nPlaces:\n{listing}"},
        ],
        response_format=Fit,
        extra_body=THINK,
    )
    parsed = completion.choices[0].message.parsed
    return parsed.place_id if parsed and parsed.place_id in {p["id"] for p in places} else None


# ---------- wardrobe: what is packed, and where it will photograph well ----------

FABRICS = ["linen", "cotton", "silk", "wool", "quick-dry", "synthetic", "denim", "other"]


class Outfit(BaseModel):
    is_clothing: bool  # false when the photo does not show an outfit or garment
    name: str  # a few words, e.g. "flowy printed maxi dress"
    colour: str
    fabric: str  # one of FABRICS; "other" when it cannot be told
    formality: str  # casual, smart, ethnic or formal
    style: list[str]  # up to four words, e.g. ["flowy", "printed", "full-length"]


def see_outfit(image_b64: str, media_type: str = "image/jpeg") -> Outfit:
    """Describe the outfit in one photo. Done once, when the traveller packs: the result is
    stored as words and the photo itself is not kept.
    """
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "Describe the main outfit or garment in the photo for a packing list. Describe "
                    "only the clothing: nothing about the person wearing it. "
                    f"fabric must be one of: {', '.join(FABRICS)}; use 'other' if you cannot tell. "
                    "formality is one of: casual, smart, ethnic, formal. Set is_clothing to false "
                    "if the photo does not show clothing."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What outfit is this?"},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                ],
            },
        ],
        response_format=Outfit,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("the photo could not be described")
    if parsed.fabric not in FABRICS:
        parsed.fabric = "other"
    return parsed


class Pairing(BaseModel):
    outfit_id: str
    place_id: str
    why: str
    pose: str = ""  # one shot idea for that outfit in that place


class Pairings(BaseModel):
    pairings: list[Pairing]


def style_match(outfits: list, places: list) -> list:
    """Pair each packed outfit with the place in the plan where it would photograph best.
    This is styling advice, an opinion: it is presented as a suggestion, never as fact.
    """
    wardrobe = "\n".join(
        f"- id={o['id']} | {o['name']} | {o.get('colour')} | {o.get('fabric')} | {o.get('formality')} | "
        f"{', '.join(o.get('style') or [])}" for o in outfits
    )
    spots = "\n".join(
        f"- id={p['id']} | {p['name']} | {p.get('what') or 'place'} | {p.get('summary') or 'no description'}"
        for p in places
    )
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a travel buddy with an eye for photos. For each outfit, choose the one "
                    "place from the list where it would look best in pictures, and say why in at "
                    "most fourteen words, in terms of the setting and the outfit (colour against "
                    "the backdrop, movement of the fabric, the mood of the place). Also respect "
                    "practicality: nothing delicate or full-length for a trek, waterfall or beach, and "
                    "never a temple or other place of worship. "
                    "Also give `pose`: one shot idea for that outfit there, at most fourteen words, such "
                    "as where to stand or how to move, built only on the place's type and description. "
                    "Use only the places given and only what their type and description say about "
                    "them. One pairing per outfit, using the ids exactly as given."
                ),
            },
            {"role": "user", "content": f"Outfits:\n{wardrobe}\n\nPlaces:\n{spots}"},
        ],
        response_format=Pairings,
    )
    parsed = completion.choices[0].message.parsed
    outfit_ids, place_ids = {o["id"] for o in outfits}, {p["id"] for p in places}
    return [
        x.model_dump() for x in (parsed.pairings if parsed else [])
        if x.outfit_id in outfit_ids and x.place_id in place_ids
    ]
