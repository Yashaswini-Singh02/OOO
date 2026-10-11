"""When to wake the graph. One function per condition, each returning a bool.
They look only at the location update and the traveller's saved spots, so checking
them costs nothing; the graph (and its LLM call) runs only when one fires.
"""
from datetime import datetime

from app.geo import distance_m
from app.hours import minutes_since_opening

NEAR_M = 1500
JUST_OPENED_MIN = 30
JUST_OPENED_WITHIN_M = 5000

# How far scout may look when each trigger fires.
RADIUS_M = {"near_saved_spot": 3000, "place_just_opened": 5000, "rain_starting": 10000}


def _within(point: dict, spot: dict, metres: float) -> bool:
    return distance_m(point["lat"], point["lng"], spot["lat"], spot["lng"]) <= metres


def near_saved_spot(point: dict, saved: list) -> bool:
    return any(_within(point, s, NEAR_M) for s in saved)


def place_just_opened(point: dict, saved: list) -> bool:
    now = datetime.fromisoformat(point["now"])
    for s in saved:
        since = minutes_since_opening(s.get("hours"), now)
        if since is not None and since <= JUST_OPENED_MIN and _within(point, s, JUST_OPENED_WITHIN_M):
            return True
    return False


def rain_starting(point: dict, previous: dict | None) -> bool:
    return previous is not None and point["weather"] == "rain" and previous["weather"] != "rain"


def fired(point: dict, previous: dict | None, saved: list) -> str | None:
    """Name of the first trigger that fires for this update, or None."""
    if place_just_opened(point, saved):
        return "place_just_opened"
    if near_saved_spot(point, saved):
        return "near_saved_spot"
    if rain_starting(point, previous):
        return "rain_starting"
    return None
