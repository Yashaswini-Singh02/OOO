"""When to wake the graph. One function per condition, each returning a bool.
They look only at the location update and the traveller's saved spots, so checking
them costs nothing; the graph (and its LLM call) runs only when one fires.
"""
from datetime import datetime, timedelta

from app.geo import distance_m
from app.hours import minutes_since_opening

NEAR_M = 1500
JUST_OPENED_MIN = 30
JUST_OPENED_WITHIN_M = 5000

ROUTINE_DAYS = 5  # this many days in a row with nowhere new
ROUTINE_PLACES = 2  # e.g. home and office
TOUCH_GRASS_COOLDOWN = timedelta(days=7)

# How far scout may look when each trigger fires.
RADIUS_M = {
    "near_saved_spot": 3000,
    "place_just_opened": 5000,
    "rain_starting": 10000,
    "touch_grass": 10000,
}


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


def places_visited(history: list) -> dict:
    """Map each day to the places stayed at that day. A place is a ~100 m cell with at
    least two updates on the same day, so points passed through in transit do not count.
    """
    seen: dict = {}
    for h in history:
        cell = (round(h["lat"], 3), round(h["lng"], 3))
        seen.setdefault(h["at"][:10], {}).setdefault(cell, 0)
        seen[h["at"][:10]][cell] += 1
    return {day: {cell for cell, n in cells.items() if n >= 2} for day, cells in seen.items()}


def touch_grass(point: dict, history: list, nudges: list) -> bool:
    """True after ROUTINE_DAYS days in a row spent between the same one or two places,
    unless this trigger already fired within its cooldown.
    """
    now = datetime.fromisoformat(point["now"])
    for n in nudges:
        if n.get("trigger") == "touch_grass" and now - datetime.fromisoformat(n["at"]) < TOUCH_GRASS_COOLDOWN:
            return False
    by_day = places_visited(history)
    window = [(now - timedelta(days=d)).date().isoformat() for d in range(ROUTINE_DAYS)]
    if any(not by_day.get(day) for day in window):
        return False  # a day with no data, or no stay yet today: not enough to judge
    return len(set().union(*(by_day[day] for day in window))) <= ROUTINE_PLACES


def fired(
    point: dict,
    previous: dict | None,
    saved: list,
    history: list | None = None,
    nudges: list | None = None,
) -> str | None:
    """Name of the first trigger that fires for this update, or None."""
    if place_just_opened(point, saved):
        return "place_just_opened"
    if near_saved_spot(point, saved):
        return "near_saved_spot"
    if rain_starting(point, previous):
        return "rain_starting"
    if history and touch_grass(point, history, nudges or []):
        return "touch_grass"
    return None
