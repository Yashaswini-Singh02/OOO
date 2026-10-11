"""The four scout tools. Fixture-backed by default (OO_FIXTURES=1) so the graph
can be built and tested without keys, quota or network; swapping in a real
provider means changing one function body.
"""
import json
import os
from pathlib import Path

from neatlogs import span

USE_FIXTURES = os.getenv("OO_FIXTURES", "1") == "1"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _fixture(kind: str) -> list:
    """Load fixtures/<kind>_<scenario>.json; OO_SCENARIO picks the scenario (default goa)."""
    scenario = os.getenv("OO_SCENARIO", "goa")
    with open(FIXTURES / f"{kind}_{scenario}.json") as f:
        return json.load(f)


@span(kind="TOOL", name="place_search")
def place_search(query: str, lat: float, lng: float, radius_m: int = 3000) -> list:
    if USE_FIXTURES:
        return _fixture("places")
    raise NotImplementedError("place_search has no real provider yet")


@span(kind="TOOL", name="video_search")
def video_search(place: str) -> list:
    if USE_FIXTURES:
        return [v for v in _fixture("videos") if v["place"] == place]
    raise NotImplementedError("video_search has no real provider yet")


@span(kind="TOOL", name="event_search")
def event_search(district: str, from_date: str, to_date: str) -> list:
    if USE_FIXTURES:
        return _fixture("events")
    raise NotImplementedError("event_search has no real provider yet")


@span(kind="TOOL", name="shop_search")
def shop_search(craft: str, district: str) -> list:
    if USE_FIXTURES:
        return [s for s in _fixture("shops") if s["craft"] == craft]
    raise NotImplementedError("shop_search has no real provider yet")
