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
        return providers.google_places(categories or [], lat, lng, radius_m, hint=query)
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
    key = f"place3:{place['google_id']}"
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


@span(kind="TOOL", name="buddy_line")
def buddy_line(place: dict, reasons: list, notes: list, now: str = "", likes: list = ()) -> str:
    """Why this place, in a friend's voice, from the checked facts only. "" in fixture mode
    or when the model call fails or strays, in which case the plain reasons are shown.
    """
    if use_fixtures():
        return ""
    try:
        return providers.buddy_line(place, reasons, notes, now, list(likes))
    except openai.OpenAIError:
        return ""


@span(kind="TOOL", name="react")
def react(text: str, understood: list, memory: str) -> str:
    """The buddy's reaction to a message, replacing the plain confirmations; "" in fixture
    mode or when it cannot be written, in which case the confirmations are sent as they are.
    """
    if use_fixtures() or not text:
        return ""
    try:
        return providers.react(text, understood, memory)
    except openai.OpenAIError:
        return ""


@span(kind="TOOL", name="shortlist_story")
def shortlist_story(places: list, kinds: list, notes: list, city: str = "") -> dict:
    """{"lines": {place id: why}, "ask": closing question} for a few verified places. Empty in
    fixture mode or if the model call fails, in which case the places are listed plainly.
    """
    if use_fixtures() or not places:
        return {}
    try:
        return providers.shortlist_story(places, kinds, notes, city)
    except openai.OpenAIError:
        return {}


@span(kind="TOOL", name="chat_reply")
def chat_reply(text: str, memory: str) -> str:
    """A conversational reply to small talk or a general question; "" if it cannot be written."""
    if use_fixtures():
        return ""
    try:
        return providers.chat_reply(text, memory)
    except openai.OpenAIError:
        return ""


@span(kind="TOOL", name="best_fit")
def best_fit(places: list, notes: list) -> str | None:
    """Which of a few verified places suits what the traveller said about themselves."""
    if use_fixtures() or not notes or len(places) < 2:
        return None
    try:
        return providers.best_fit(places, notes)
    except openai.OpenAIError:
        return None


@span(kind="TOOL", name="see_outfit", capture_input=False)
def see_outfit(image_b64: str, media_type: str = "image/jpeg") -> dict | None:
    """Describe one outfit photo as packing-list words, or None if it shows no clothing.
    The image is not recorded in the trace and is not stored anywhere.
    """
    outfit = providers.see_outfit(image_b64, media_type)
    return outfit.model_dump(exclude={"is_clothing"}) if outfit.is_clothing else None


@span(kind="TOOL", name="style_match")
def style_match(outfits: list, places: list) -> list:
    """[{outfit_id, place_id, why}]: where each outfit would photograph best. Fixture mode
    pairs them in order with no reason, so tests stay offline.
    """
    if not outfits or not places:
        return []
    if use_fixtures():
        return [{"outfit_id": o["id"], "place_id": places[i % len(places)]["id"], "why": ""}
                for i, o in enumerate(outfits)]
    try:
        return providers.style_match(outfits, places)
    except openai.OpenAIError:
        return []


@span(kind="TOOL", name="reel_spots")
def reel_spots(kind: str, city: str) -> list:
    """[{name, url}]: places Instagram posts name for a kind of outing in a town. Live mode:
    one Tavily credit and one small model call per kind and town, then cached. Empty when
    it cannot be checked, so the suggestion falls back to what Google found.
    """
    if use_fixtures() or not city:
        return []
    try:
        return providers.reel_spots(kind, city)
    except (RuntimeError, requests.RequestException, openai.OpenAIError):
        return []


@span(kind="TOOL", name="lookup")
def lookup(
    name: str, lat: float | None = None, lng: float | None = None, category: str = "attraction"
) -> dict | None:
    """Find one named place, near a point when given. None if it cannot be found."""
    if use_fixtures():
        return next((p for p in _fixture("places") if name.lower() in p["name"].lower()), None)
    try:
        found = providers.google_find(name, lat, lng, category=category)
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
