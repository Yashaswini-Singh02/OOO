from collections import Counter

from app import tools
from app.state import RoveState

TRIGGER_REASON = {
    "near_saved_spot": "you saved this and you're close to it",
    "place_just_opened": "it has just opened",
    "rain_starting": "it has started raining",
    "touch_grass": "you've been between the same couple of places for days",
}


def reasons(top: dict, state: RoveState) -> list:
    """Why this candidate, in plain words. Every reason is read off the data, never written by a model."""
    why = []
    trigger = state.get("trigger")
    if trigger == "speciality" and top.get("craft"):
        why.append(f"{top['craft']} is what this region is known for")
    elif trigger == "rain_starting" and top.get("indoor"):
        why.append("it has started raining and this is indoors")
    elif trigger in TRIGGER_REASON:
        why.append(TRIGGER_REASON[trigger])

    liked = state.get("taste", {}).get("tags", {})
    matches = sorted((t for t in top.get("tags", []) if liked.get(t, 0) > 0), key=lambda t: -liked[t])[:2]
    if matches:
        why.append(f"it matches your taste for {' and '.join(matches)}")

    verified = state.get("verified", [])
    if top.get("rating") and top["rating"] >= 4:
        why.append(f"people rate it {top['rating']} out of 5")
    if len(verified) > 1 and top.get("distance_m") == min(c.get("distance_m", 0) for c in verified):
        why.append("it is the closest option that passed my checks")
    if not why:
        why.append("it is the best match I could verify near you")
    return why


def ruled_out(state: RoveState) -> str:
    """One line on what the gate refused this run, e.g. '2 closed right now, 1 venue not found'."""
    counts = Counter(r["reason"] for r in state.get("rejected", []))
    return ", ".join(f"{n} {reason}" for reason, n in counts.most_common())


def feed(state: RoveState) -> dict:
    """Turn what passed the gate into the one nudge for this run."""
    if state.get("wants"):
        return {
            "nudge": {
                "kind": "plan",
                "days": state["plan"],
                "avoid": state.get("avoid", []),
            }
        }
    top = state["verified"][0]
    videos = top.get("videos")
    if videos is None:  # live mode: scout skipped the lookup, do it for this one venue
        videos = tools.video_search(top["name"], state.get("city", ""))
    return {
        "nudge": {
            "id": top["id"],
            "name": top["name"],
            "kind": top["kind"],
            "what": top.get("what"),
            "address": top.get("address"),
            "summary": top.get("summary"),
            "rating": top.get("rating"),
            "rating_count": top.get("rating_count"),
            "why": reasons(top, state),
            "ruled_out": ruled_out(state),
            "outfit": top.get("outfit"),
            "cost_inr": top.get("cost_inr", 0),
            "distance_m": top.get("distance_m"),
            "hours_verified": top.get("open_now") is True,
            "trigger": state.get("trigger"),
            "videos": [{"url": v["url"], "source": v.get("source", "video")} for v in videos],
            "links": [v["url"] for v in videos],
            "source": top.get("source"),
            "tags": top.get("tags", []),
            "lat": top.get("lat"),
            "lng": top.get("lng"),
            "craft": top.get("craft"),
            "gi_registered": top.get("gi_registered"),
        }
    }
