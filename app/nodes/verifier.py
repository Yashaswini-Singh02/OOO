from math import asin, cos, radians, sin, sqrt

from neatlogs import span

from app.state import RoveState

# A reel that places a venue further than this from where it really is gets refused.
MAX_REEL_MISMATCH_M = 50_000


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(a))


def refusal(c: dict) -> str | None:
    """Why this candidate must not reach the feed, or None if it may."""
    if not c.get("exists"):
        return "venue not found"
    if not c.get("open_now"):
        return "closed right now"
    for video in c.get("videos", []):
        off_by = distance_m(c["lat"], c["lng"], video["claimed_lat"], video["claimed_lng"])
        if off_by > MAX_REEL_MISMATCH_M:
            return "not where the reel claims"
    return None


@span(kind="GUARDRAIL", name="verifier")
def verifier(state: RoveState) -> dict:
    ok, bad = [], []
    for c in state.get("ranked", []):
        reason = refusal(c)
        if reason:
            bad.append({**c, "reason": reason})
        else:
            ok.append(c)
    return {"verified": ok, "rejected": state.get("rejected", []) + bad}


def route(state: RoveState) -> str:
    if state.get("verified"):
        return "feed"
    if state.get("replans", 0) < 2:
        return "replan"
    return "give_up"
