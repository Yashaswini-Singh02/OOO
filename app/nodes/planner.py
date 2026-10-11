"""Day planning for runs where the traveller named things they want (state["wants"]).

The first pass puts everything on day 1. The verifier checks each day with
day_conflict(); when a day cannot work it records the conflict and the run replans,
and this node then keeps the conflicting pair on separate days.
"""
from app.geo import distance_m
from app.state import RoveState

ROAD_KMH = 30  # rough average road speed, used to turn distance into travel time
DEFAULT_START_H = 9.0


def hours(hhmm: str | None) -> float | None:
    if hhmm is None:
        return None
    h, m = hhmm.split(":")
    return int(h) + int(m) / 60


def clock(h: float) -> str:
    return f"{int(h):02d}:{round((h - int(h)) * 60):02d}"


def travel_h(a: dict, b: dict) -> float:
    return distance_m(a["lat"], a["lng"], b["lat"], b["lng"]) / 1000 / ROAD_KMH


def fixed_time(c: dict) -> float:
    """When the candidate has to happen; untimed ones sort last."""
    return hours(c.get("start_by")) or hours(c.get("arrive_by")) or 24.0


def day_conflict(items: list) -> dict | None:
    """Walk one day's items in time order; return the first pair that cannot both happen."""
    prev, free_at = None, DEFAULT_START_H
    for c in sorted(items, key=fixed_time):
        deadline = hours(c.get("start_by")) or hours(c.get("arrive_by"))
        if prev:
            arrive = free_at + travel_h(prev, c)
        else:
            arrive = deadline if deadline is not None else free_at
        if deadline is not None and arrive > deadline:
            return {
                "a": prev["id"],
                "b": c["id"],
                "reason": (
                    f"{prev['name']} ends {clock(free_at)} and {c['name']} is "
                    f"{travel_h(prev, c):.1f} h away: arrival {clock(arrive)}, "
                    f"needed by {clock(deadline)}"
                ),
            }
        free_at = max(arrive, deadline or arrive) + c.get("duration_h", 1)
        prev = c
    return None


PER_DAY = 3
PLACE_FIELDS = ("id", "name", "what", "summary", "rating", "rating_count", "source", "activity", "tags",
                "google_id", "lat", "lng", "city", "district", "kind", "open_now", "outfit")


def place_fields(c: dict) -> dict:
    """What later steps need to describe a place, or to open its full card."""
    return {k: c.get(k) for k in PLACE_FIELDS}


def itinerary(ranked: list, days: int) -> list:
    """Spread the best-ranked places over the days, keeping each day's places close together:
    the best remaining place starts a day and its nearest neighbours fill it.
    """
    pool = list(ranked[: days * PER_DAY])
    plan = []
    for day in range(1, days + 1):
        if not pool:
            break
        seed = pool.pop(0)
        pool.sort(key=lambda c: distance_m(seed["lat"], seed["lng"], c["lat"], c["lng"]))
        for c in [seed] + [pool.pop(0) for _ in range(min(PER_DAY - 1, len(pool)))]:
            plan.append({"day": day, **place_fields(c)})
    return plan


def planner(state: RoveState) -> dict:
    if state.get("trigger") == "trip":
        return {"plan": itinerary(state.get("ranked", []), max(state.get("days_left", 1), 1)), "avoid": []}
    wants = state.get("wants", [])
    if not wants:
        return {"plan": [], "avoid": []}

    warnings = list(state.get("warnings", []))
    ranked = state.get("ranked", [])
    picks = []
    for activity in wants:
        match = next((c for c in ranked if c.get("activity") == activity), None)
        if match is None:
            warnings.append(f"nothing available for the {activity} you wanted")
        else:
            picks.append(match)

    conflicts = state.get("conflicts", [])
    clashes = {frozenset((c["a"], c["b"])) for c in conflicts}
    days: list[list] = [[] for _ in range(max(state.get("days_left", 1), 1))]
    # Longest first: the full-day item takes the earliest day and leaves a buffer day.
    for c in sorted(picks, key=lambda c: -c.get("duration_h", 1)):
        day = next(
            (d for d in days if not any(frozenset((c["id"], o["id"])) in clashes for o in d)),
            None,
        )
        if day is None:
            warnings.append(f"no day left for {c['name']}")
        else:
            day.append(c)

    plan = [
        {
            "day": n,
            "id": c["id"],
            "name": c["name"],
            "activity": c["activity"],
            "outfit": c.get("outfit"),
            "at": c.get("start_by") or c.get("arrive_by"),
        }
        for n, day in enumerate(days, start=1)
        for c in day
    ]

    day_of = {p["id"]: p["day"] for p in plan}
    names = {c["id"]: c for c in picks}
    avoid = [
        f"Day {day_of[c['a']]}: no {names[c['b']]['activity']} booking. {c['reason']}."
        for c in conflicts
        if c["a"] in day_of and c["b"] in names
    ]
    return {"plan": plan, "avoid": avoid, "warnings": warnings}
