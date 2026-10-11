"""The scout tools. Fixture-backed by default (OO_FIXTURES=1) so the graph can be
built and tested without keys, quota or network. With OO_FIXTURES=0 places and
weather come from live providers (see app/providers.py).
"""
import json
import os
from datetime import datetime
from pathlib import Path

import openai
import requests
from neatlogs import span

from app import providers, quota

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def use_fixtures() -> bool:
    return os.getenv("OO_FIXTURES", "1") == "1"


def _fixture(kind: str) -> list:
    """Load fixtures/<kind>_<scenario>.json; OO_SCENARIO picks the scenario (default goa)."""
    scenario = os.getenv("OO_SCENARIO", "goa")
    with open(FIXTURES / f"{kind}_{scenario}.json") as f:
        return json.load(f)


@span(kind="TOOL", name="place_search")
def place_search(
    query: str, lat: float, lng: float, radius_m: int = 3000, categories: list | None = None
) -> list:
    if use_fixtures():
        return _fixture("places")
    if os.getenv("GOOGLE_API_KEY"):
        return providers.google_places(categories or [], lat, lng, radius_m)
    return providers.overpass_places(categories or [], lat, lng, radius_m, datetime.now().astimezone())


@span(kind="TOOL", name="video_search")
def video_search(place: str, city: str = "", what: str | None = None) -> list:
    """Links to videos that show a place. Live mode: one YouTube search, one Tavily credit
    and one small model call per new place, then cached.

    A link passes two checks: it must name the place, and a model must judge the place to
    be its subject. If the second check cannot run, only links with the place in their own
    title are kept. No link is better than a misleading one.
    """
    if use_fixtures():
        return [v for v in _fixture("videos") if v["place"] == place]
    key = f"videos:{place}:{city}"
    hit = quota.cached(key)
    if hit is not None:
        return hit
    links = []
    for source in (providers.youtube_videos, providers.reel_links):
        try:
            links += source(place, city)
        except (RuntimeError, requests.RequestException):
            continue  # one source being down must not cost the nudge its other links
    try:
        links = providers.subject_check(place, what, links)
    except openai.OpenAIError:
        links = [v for v in links if providers.is_about(place, v.get("title", ""))]
    quota.remember(key, links)
    return links


@span(kind="TOOL", name="event_search")
def event_search(
    district: str, from_date: str, to_date: str, lat: float = 0.0, lng: float = 0.0
) -> list:
    """Live mode costs one Tavily credit per district per day; results are cached."""
    if use_fixtures():
        return _fixture("events")
    return providers.live_events(district, from_date, to_date, lat, lng)


@span(kind="TOOL", name="shop_search")
def shop_search(craft: str, district: str, lat: float = 0.0, lng: float = 0.0) -> list:
    if use_fixtures():
        return [s for s in _fixture("shops") if s["craft"] == craft]
    return providers.google_find(f"{craft} shop in {district}", lat, lng, category="market", kind="shop")


@span(kind="TOOL", name="place_about")
def place_about(place: dict) -> dict:
    """What one place is: {"about", "reviews_say", "photo", "photo_by"}.
    Live mode: two Google calls and one small model call per new place, then cached.
    """
    plain = {"about": place.get("summary") or "", "reviews_say": "", "photo": None, "photo_by": None}
    if use_fixtures() or not place.get("google_id"):
        return plain
    key = f"place2:{place['google_id']}"
    hit = quota.cached(key)
    if hit is None:
        try:
            details = providers.google_about(place["google_id"])
            words = providers.describe(place["name"], place.get("what"), details)
            hit = {
                "about": words["about"] or plain["about"],
                "reviews_say": words["reviews_say"],
                "photo": providers.google_photo(details["photo_name"]) if details.get("photo_name") else None,
                "photo_by": details.get("photo_by"),
            }
        except (RuntimeError, requests.RequestException, openai.OpenAIError):
            return plain  # not cached, so it is tried again next time
        quota.remember(key, hit)
    return hit


@span(kind="TOOL", name="place_details")
def place_details(place: dict) -> dict:
    """Practical details of one place: {"hours", "website", "phone", "quotes"}. One Google call, cached."""
    if use_fixtures() or not place.get("google_id"):
        return {}
    key = f"more:{place['google_id']}"
    hit = quota.cached(key)
    if hit is None:
        try:
            hit = providers.google_more(place["google_id"])
        except (RuntimeError, requests.RequestException):
            return {}
        quota.remember(key, hit)
    return hit


@span(kind="TOOL", name="trip_story")
def trip_story(city: str, places: list, spotlight: dict | None) -> dict:
    """The wording around a verified trip plan. Empty in fixture mode or if the model call fails,
    in which case the plan is shown plainly.
    """
    if use_fixtures():
        return {}
    try:
        return providers.trip_story(city, places, spotlight)
    except openai.OpenAIError:
        return {}


@span(kind="TOOL", name="lookup")
def lookup(name: str, lat: float | None = None, lng: float | None = None) -> dict | None:
    """Find one named place, near a point when given. None if it cannot be found."""
    if use_fixtures():
        return next((p for p in _fixture("places") if name.lower() in p["name"].lower()), None)
    try:
        found = providers.google_find(name, lat, lng)
    except (RuntimeError, requests.RequestException):
        return None
    return found[0] if found else None


# Fixture-mode stand-ins for a place lookup, so named-place requests can be tested offline.
FIXTURE_PLACES = {"goa": (15.4989, 73.8313), "panaji": (15.4989, 73.8313), "north goa": (15.4989, 73.8313)}


@span(kind="TOOL", name="geocode")
def geocode(place: str) -> dict | None:
    """Where a named place is: {"name", "lat", "lng"}, or None if it cannot be found."""
    if use_fixtures():
        at = FIXTURE_PLACES.get(place.strip().lower())
        return {"name": place, "lat": at[0], "lng": at[1]} if at else None
    key = f"geocode:{place.strip().lower()}"
    hit = quota.cached(key)
    if hit is None:
        try:
            found = providers.google_find(place)
        except (RuntimeError, requests.RequestException):
            return None
        hit = {"name": found[0]["name"], "lat": found[0]["lat"], "lng": found[0]["lng"]} if found else {}
        quota.remember(key, hit)
    return hit or None


@span(kind="TOOL", name="weather")
def weather(lat: float, lng: float) -> str:
    """'rain' or 'clear' at this location right now."""
    if use_fixtures():
        return "clear"
    return providers.open_meteo_weather(lat, lng)
