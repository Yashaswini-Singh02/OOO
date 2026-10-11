from app import tools
from app.messages import spoken
from app.nodes.planner import place_fields
from app.state import RoveState

TRIGGER_REASON = {
    "near_saved_spot": "You saved this one and you're right by it.",
    "place_just_opened": "It's just opened for the day.",
    "rain_starting": "It's started raining.",
    "touch_grass": "You've been between the same couple of places for days.",
}
FIT_POOL = 5  # how many verified places the traveller's own notes may choose between
MAX_REASONS = 2  # a friend gives one or two reasons, not a list


def reasons(top: dict, state: RoveState) -> list:
    """Why this candidate, the way a friend would say it. Every reason is read off the
    data, never written by a model; the most personal ones come first.
    """
    why = []
    trigger = state.get("trigger")
    if trigger == "speciality" and top.get("craft"):
        why.append(f"{top['craft']} is what this area is known for.")
    elif trigger == "rain_starting" and top.get("indoor"):
        why.append("It's started raining and this one's indoors.")
    elif trigger in TRIGGER_REASON:
        why.append(TRIGGER_REASON[trigger])

    only = state.get("only", [])
    if only:
        why.append("It's what you asked for.")
        if top.get("reel"):
            where = f" in {state['city']}" if state.get("city") else ""
            why.append(f"It comes up on Instagram for {spoken(only[0])}{where} too.")

    liked = state.get("taste", {}).get("tags", {})
    matches = sorted(
        (t for t in top.get("tags", []) if liked.get(t, 0) > 0 and t not in only), key=lambda t: -liked[t]
    )[:2]
    if matches:
        why.append(f"You're into {' and '.join(spoken(t) for t in matches)}, so I thought of this.")

    verified = state.get("verified", [])
    rated = [c["rating"] for c in verified if c.get("rating")]
    if top.get("rating") and len(rated) > 1 and top["rating"] == max(rated):
        why.append("It's the best-rated spot I found near you.")
    elif top.get("rating") and top["rating"] >= 4:
        why.append("People rate it really well.")
    if len(verified) > 1 and top.get("distance_m") == min(c.get("distance_m", 0) for c in verified):
        why.append("It's the closest one too.")
    if not why:
        why.append("It's the best thing I could confirm near you.")
    return why[:MAX_REASONS]


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


def shortlist(state: RoveState) -> dict:
    """A few verified places to choose between, best first, for someone who has just said
    what they love. Each can be opened as a full card.
    """
    picks = [
        {**place_fields(c), "distance_m": c.get("distance_m"), "reel": c.get("reel")}
        for c in state["verified"][: state["shortlist"]]
    ]
    return {
        "nudge": {
            "kind": "shortlist",
            "city": state.get("city", ""),
            "kinds": state.get("only", []),
            "places": picks,
            # backdrops for an outfit are worded by the stylist, once it has chosen among them
            "story": {} if state.get("purpose") == "outfit" else tools.shortlist_story(
                picks, state.get("only", []), state.get("notes", []), state.get("city", "")
            ),
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
    if state.get("shortlist"):
        return shortlist(state)
    top = state["verified"][0]
    if state.get("trigger") == "asked" and state.get("notes"):
        # several places passed; let what the traveller said about themselves choose among them
        pick = tools.best_fit(state["verified"][:FIT_POOL], state["notes"])
        top = next((c for c in state["verified"] if c["id"] == pick), top)
    videos = top.get("videos")
    if videos is None:  # live mode: scout skipped the lookup, do it for this one venue
        # search with the venue's own town, not the trip's, so the links are about this place
        videos = tools.video_search(top["name"], top.get("city") or state.get("city", ""), top.get("what"))
    about = tools.place_about(top)
    nudge = {
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
    liked = state.get("taste", {}).get("tags", {})
    nudge["say"] = tools.buddy_line(
        nudge, nudge["why"], state.get("notes", []), state.get("now", ""), sorted(t for t, w in liked.items() if w > 0)
    )
    return {"nudge": nudge}
