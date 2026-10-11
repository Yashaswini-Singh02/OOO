"""The runtime: one location update in, at most one nudge out.

on_location() is the single entry point for every front end. The simulator feeds it
replayed points, and a live client (a phone sharing its location) feeds it real ones;
nothing here knows the difference. Time always comes from the update itself.
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Callable

from app import store, tools, triggers

THROTTLE = timedelta(minutes=30)  # at most one nudge per half hour
NO_REPEAT = timedelta(hours=24)  # never the same venue twice in a day


def recent_ids(nudges: list, now: datetime) -> list:
    return [n["venue_id"] for n in nudges if now - datetime.fromisoformat(n["at"]) < NO_REPEAT]


def throttled(nudges: list, now: datetime) -> bool:
    return bool(nudges) and now - datetime.fromisoformat(nudges[-1]["at"]) < THROTTLE


def on_location(
    conn: sqlite3.Connection,
    point: dict,
    trip: dict,
    run: Callable[[dict], dict],
) -> dict | None:
    """Handle one update {lat, lng, now, weather?}. `trip` carries city, district,
    days_left and optionally the profile; `run` invokes the graph. Returns the nudge or None.
    """
    now = datetime.fromisoformat(point["now"])
    weather = point.get("weather") or tools.weather(point["lat"], point["lng"])
    point = {**point, "weather": weather}

    previous = store.last_location(conn)
    store.add_location(conn, point["now"], point["lat"], point["lng"], weather)

    nudges = store.load_nudges(conn)
    recent = recent_ids(nudges, now)
    # a saved spot the traveller was already nudged about should not wake the graph again
    saved = [s for s in store.load_saved(conn) if s["id"] not in recent]

    history = store.load_locations(conn)  # includes the update just stored
    trigger = triggers.fired(point, previous, saved, history, nudges, trip)
    if trigger is None or throttled(nudges, now):
        return None

    result = run(
        {
            **trip,
            "lat": point["lat"],
            "lng": point["lng"],
            "now": point["now"],
            "weather": weather,
            "trigger": trigger,
            "max_radius_m": triggers.RADIUS_M[trigger],
            "saved": [s["id"] for s in saved],
            "recent": recent,
        }
    )
    nudge = result.get("nudge")
    if nudge:
        store.add_nudge(conn, nudge["id"], point["now"], trigger)
    return nudge
