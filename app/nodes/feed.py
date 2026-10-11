from app import tools
from app.nodes.planner import place_fields
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

    if state.get("only"):
        why.append("it is what you asked for")

    liked = state.get("taste", {}).get("tags", {})
    matches = sorted((t for t in top.get("tags", []) if liked.get(t, 0) > 0), key=lambda t: -liked[t])[:2]
    if matches:
        why.append(f"it matches your taste for {' and '.join(matches)}")

    verified = state.get("verified", [])
    rated = [c["rating"] for c in verified if c.get("rating")]
    if top.get("rating") and len(rated) > 1 and top["rating"] == max(rated):
        why.append("it is the best-rated place near you that passed my checks")
    elif top.get("rating") and top["rating"] >= 4:
        why.append("visitors rate it highly")
    if len(verified) > 1 and top.get("distance_m") == min(c.get("distance_m", 0) for c in verified):
        why.append("it is the closest option that passed my checks")
    if not why:
        why.append("it is the best match I could verify near you")
    return why


GEM_MIN_RATING, GEM_MIN_REVIEWS, GEM_MAX_REVIEWS = 4.5, 30, 1500


def spotlight(verified: list) -> dict | None:
    """One place worth calling out, chosen from the numbers and nothing else: a hidden gem
    (rated highly by relatively few people) if there is one, otherwise the crowd favourite
    (the place most people have reviewed).
    """
    rated = [c for c in verified if c.get("rating") and c.get("rating_count")]
    if not rated:
        return None
    gems = [c for c in rated
            if c["rating"] >= GEM_MIN_RATING and GEM_MIN_REVIEWS <= c["rating_count"] <= GEM_MAX_REVIEWS]
    if gems:
        pick, label = max(gems, key=lambda c: (c["rating"], c["rating_count"])), "Hidden gem"
    else:
        pick, label = max(rated, key=lambda c: c["rating_count"]), "Crowd favourite"
    return {**place_fields(pick), "label": label}


def trip(state: RoveState) -> dict:
    ok = {c["id"] for c in state["verified"]}  # only places that passed the gate stay in the plan
    days = [p for p in state.get("plan", []) if p["id"] in ok]
    star = spotlight(state["verified"]) if days else None
    if star:
        star["videos"] = [
            {"url": v["url"], "source": v.get("source", "video")}
            for v in tools.video_search(star["name"], star.get("city") or state.get("city", ""), star.get("what"))
        ]
    return {
        "nudge": {
            "kind": "itinerary",
            "city": state.get("city", ""),
            "days": days,
            "spotlight": star,
            "story": tools.trip_story(state.get("city", ""), days, star) if days else {},
        }
    }


def feed(state: RoveState) -> dict:
    """Turn what passed the gate into the one nudge for this run."""
    if state.get("trigger") == "trip":
        return trip(state)
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
        # search with the venue's own town, not the trip's, so the links are about this place
        videos = tools.video_search(top["name"], top.get("city") or state.get("city", ""), top.get("what"))
    about = tools.place_about(top)
    return {
        "nudge": {
            "id": top["id"],
            "name": top["name"],
            "kind": top["kind"],
            "google_id": top.get("google_id"),
            "what": top.get("what"),
            "address": top.get("address"),
            "about": about["about"],
            "reviews_say": about["reviews_say"],
            "photo": about["photo"],
            "photo_by": about["photo_by"],
            "city": top.get("city"),
            "district": top.get("district"),
            "rating": top.get("rating"),
            "rating_count": top.get("rating_count"),
            "why": reasons(top, state),
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
