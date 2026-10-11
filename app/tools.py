"""The scout tools. Fixture-backed by default (OO_FIXTURES=1) so the graph can be
built and tested without keys, quota or network. With OO_FIXTURES=0 places and
weather come from live providers (see app/providers.py).
"""
import json
import os
from datetime import datetime
from pathlib import Path

import requests
from neatlogs import span

from app import providers

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
def video_search(place: str, city: str = "") -> list:
    """Links to videos about a place. Live mode costs one Tavily credit per new place."""
    if use_fixtures():
        return [v for v in _fixture("videos") if v["place"] == place]
    links = []
    for source in (providers.youtube_videos, providers.reel_links):
        try:
            links += source(place, city)
        except (RuntimeError, requests.RequestException):
            continue  # one source being down must not cost the nudge its other links
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


@span(kind="TOOL", name="weather")
def weather(lat: float, lng: float) -> str:
    """'rain' or 'clear' at this location right now."""
    if use_fixtures():
        return "clear"
    return providers.open_meteo_weather(lat, lng)
