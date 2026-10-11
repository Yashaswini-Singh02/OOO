"""The scout tools. Fixture-backed by default (OO_FIXTURES=1) so the graph can be
built and tested without keys, quota or network. With OO_FIXTURES=0 places and
weather come from live providers (see app/providers.py).
"""
import json
import os
from datetime import datetime
from pathlib import Path

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
    return providers.overpass_places(categories or [], lat, lng, radius_m, datetime.now().astimezone())


@span(kind="TOOL", name="video_search")
def video_search(place: str) -> list:
    if use_fixtures():
        return [v for v in _fixture("videos") if v["place"] == place]
    return []  # no live reel provider yet; nothing is invented in its place


@span(kind="TOOL", name="event_search")
def event_search(district: str, from_date: str, to_date: str) -> list:
    if use_fixtures():
        return _fixture("events")
    return []  # no live event provider yet; nothing is invented in its place


@span(kind="TOOL", name="shop_search")
def shop_search(craft: str, district: str) -> list:
    if use_fixtures():
        return [s for s in _fixture("shops") if s["craft"] == craft]
    return []  # no live shop provider yet


@span(kind="TOOL", name="weather")
def weather(lat: float, lng: float) -> str:
    """'rain' or 'clear' at this location right now."""
    if use_fixtures():
        return "clear"
    return providers.open_meteo_weather(lat, lng)
