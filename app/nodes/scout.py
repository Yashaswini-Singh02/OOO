import os
from datetime import datetime, timedelta

import openai
from pydantic import BaseModel, ValidationError

from app import gi, tools
from app.geo import distance_m
from app.hours import is_open
from app.providers import CATEGORIES, DEFAULT_CATEGORIES, same_place, sun_tags
from app.state import RoveState

OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Haiku while testing; set OO_MODEL to another OpenRouter model id to change it.
MODEL = os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5")
DEFAULT_QUERY = "things to do nearby"
DEFAULT_RADIUS_M = 3000

SYSTEM = (
    "You plan searches for a travel companion that suggests one nearby thing to do. "
    "Write a short place-search query (a few words, no city name) suited to the "
    "context, a search radius in metres between 1000 and 60000, and one to four place "
    f"categories chosen only from: {', '.join(CATEGORIES)}. In rain prefer indoor "
    "categories. Suit the local time: the suggestion is for right now, so do not look for "
    "sunset spots or night markets in the morning. "
    "If venues were already rejected, search for something different from them."
)


class ScoutQueries(BaseModel):
    place_query: str
    radius_m: int
    categories: list[str] = DEFAULT_CATEGORIES


def plan_queries(state: RoveState) -> ScoutQueries:
    """The one LLM call in the graph: turn the traveller's context into a place search."""
    tags = state.get("taste", {}).get("tags", {})
    liked = sorted((t for t, w in tags.items() if w > 0), key=lambda t: -tags[t])[:5]
    context = (
        f"City: {state.get('city', 'unknown')}\n"
        f"District: {state.get('district', 'unknown')}\n"
        f"Weather: {state.get('weather', 'unknown')}\n"
        f"Local time: {(state.get('now') or '')[11:16] or 'unknown'}\n"
        + (f"The traveller asked to fit these into their remaining days: {', '.join(state['wants'])}. "
           "Search for those, whatever the time is now.\n" if state.get("wants") else "")
        + f"Days left on the trip: {state.get('days_left', 'unknown')}\n"
        f"Tags the traveller likes most: {', '.join(liked) or 'none recorded'}\n"
        f"What the traveller said about themselves: {'; '.join(state.get('notes', [])) or 'nothing'}\n"
        + ("Purpose: an itinerary for a visit of several days, so choose four varied categories "
           "and the largest sensible radius.\n" if state.get("trigger") == "trip" else "")
        + ("Purpose: places where an outfit would photograph well, so choose four varied categories "
           "that make good backdrops, mixing sights with somewhere to sit such as a cafe or "
           "restaurant, and the largest sensible radius.\n" if state.get("purpose") == "outfit" else "")
        + f"Kinds of place the traveller does not want (never choose these): "
        f"{', '.join(state.get('dislikes', [])) or 'none'}\n"
        f"Venues already rejected this run: "
        f"{', '.join(r['name'] for r in state.get('rejected', [])) or 'none'}"
    )
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": context},
        ],
        response_format=ScoutQueries,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError(f"no structured output (finish_reason={completion.choices[0].finish_reason})")
    return parsed


def event_window(state: RoveState) -> tuple[str, str]:
    start = datetime.fromisoformat(state["now"]) if state.get("now") else datetime.now()
    end = start + timedelta(days=state.get("days_left", 1))
    return start.date().isoformat(), end.date().isoformat()


def localise(c: dict, state: RoveState) -> dict:
    """Recompute what depends on where and when the traveller is: distance and open-now."""
    out = dict(c)
    if "lat" in state and "lat" in c:
        out["distance_m"] = round(distance_m(state["lat"], state["lng"], c["lat"], c["lng"]))
    if c.get("hours") and state.get("now"):
        out["open_now"] = is_open(c["hours"], datetime.fromisoformat(state["now"]))
    return out


MAX_CRAFTS = 2  # one shop search per craft, so this bounds the cost of a shopping run


def scout_shops(state: RoveState) -> list:
    """Speciality shopping: a table lookup and a shop search. No model call."""
    lat, lng = state.get("lat", 0.0), state.get("lng", 0.0)
    district = state.get("district", "")
    shops = []
    for entry in gi.crafts_for(district, state.get("city", ""))[:MAX_CRAFTS]:
        for shop in tools.shop_search(entry["craft"], district, lat, lng):
            shops.append({**shop, "craft": entry["craft"], "gi_registered": entry["gi_registered"]})
    skip = {r["id"] for r in state.get("rejected", [])} | set(state.get("recent", []))
    radius = state.get("max_radius_m", DEFAULT_RADIUS_M)
    shops = [localise(c, state) for c in shops if c["id"] not in skip]
    return [c for c in shops if c.get("distance_m", 0) <= radius]


def unwanted(c: dict, dislikes: list) -> bool:
    """True when the candidate is a kind of place the traveller said they do not want.
    Checked against our tags, the provider's own types and its type label.
    """
    if not dislikes:
        return False
    return any(d.lower() in kinds_of(c) for d in dislikes)


def kinds_of(c: dict) -> set:
    kinds = {t.lower() for t in c.get("tags", [])} | {t.lower() for t in c.get("types", [])}
    return kinds | set((c.get("what") or "").lower().replace("_", " ").split())


def categories_for(kinds: list) -> list:
    """Search categories that serve a request such as ["food"]: the category itself, or any
    category whose tags include the word.
    """
    return [c for c, (_, tags, _, _) in CATEGORIES.items() if c in kinds or set(tags) & set(kinds)]


MAX_REEL_LOOKUPS = 3  # one Google call each, for places Instagram names that Google's search missed


def with_reels(places: list, only: list, state: RoveState) -> list:
    """Cross-check a request for one kind of place against Instagram. A place that posts
    name is marked with the post; one they name that the search missed is looked up and
    added. Every place still comes from Google and still has to pass the gate.
    """
    city = state.get("city", "")
    out, missing = [dict(p) for p in places], []
    for spot in tools.reel_spots(only[0], city):
        match = next((p for p in out if same_place(p["name"], spot["name"], tuple(only))), None)
        if match:
            match["reel"] = spot["url"]
        else:
            missing.append(spot)
    category = (categories_for(only) or ["attraction"])[0]
    for spot in missing[:MAX_REEL_LOOKUPS]:
        found = tools.lookup(f"{spot['name']} {city}", state.get("lat"), state.get("lng"), category)
        if found and found["id"] not in {p["id"] for p in out}:
            out.append({**found, "tags": sun_tags(found.get("tags", []), found["name"], only[0]), "reel": spot["url"]})
    return out


LIKED_TAGS = 2  # how many of the traveller's favourite tags steer the search


def liked_categories(state: RoveState) -> list:
    """Search categories for the tags this traveller likes most."""
    tags = state.get("taste", {}).get("tags", {})
    top = sorted((t for t, w in tags.items() if w > 0), key=lambda t: -tags[t])[:LIKED_TAGS]
    return categories_for(top)


MAX_RADIUS_M = 60_000  # the furthest a search may reach when the trigger sets no limit
MAX_REPLANS = 2  # kept in step with the verifier's route()
FRESH_CATEGORIES = 3  # how many untried categories a replan adds


def widen(prior: dict, state: RoveState, replan: int) -> ScoutQueries | None:
    """The search for a replan, worked out without a model call. Asking the model again
    gave the same query and the same radius, so the same refused venues came back and the
    replan found nothing. A replan now reaches further out (halfway to the limit, then all
    the way) and adds categories not searched yet. None when there is nothing left to
    change, which ends the run instead of repeating a search that cannot succeed.
    """
    cap = max(state.get("max_radius_m", MAX_RADIUS_M), prior["radius_m"])
    left = max(MAX_REPLANS - replan + 1, 1)
    radius = round(prior["radius_m"] + (cap - prior["radius_m"]) / left)
    grew = radius > prior["radius_m"]
    fresh = [] if state.get("only") else [
        c for c in CATEGORIES if c not in prior["tried"] and c not in state.get("dislikes", [])
    ][:FRESH_CATEGORIES]
    if not grew and not fresh:
        return None
    return ScoutQueries(
        place_query=prior["query"], radius_m=radius, categories=(prior["categories"] if grew else []) + fresh
    )


def scout(state: RoveState) -> dict:
    warnings = list(state.get("warnings", []))
    # Loop guard: scout already ran if candidates exist, so this entry is a replan.
    replans = state.get("replans", 0) + (1 if "candidates" in state else 0)

    replanning = "candidates" in state
    if state.get("trigger") == "speciality":
        if replanning:  # the craft table and the shop search would give the same answer again
            return {"candidates": [], "replans": replans, "warnings": warnings, "exhausted": True}
        return {"candidates": scout_shops(state), "replans": replans, "warnings": warnings}

    only = state.get("only", [])
    prior = state.get("search") if replanning else None
    if prior and state.get("wants"):
        # a plan is replanned to fix its schedule, so the same places are fetched again
        queries = ScoutQueries(place_query=prior["query"], radius_m=prior["radius_m"], categories=prior["categories"])
    elif prior:
        queries = widen(prior, state, replans)
        if queries is None:
            return {"candidates": [], "replans": replans, "warnings": warnings, "exhausted": True}
    elif only and categories_for(only):
        # the traveller named the kind of place, so there is nothing for the model to decide
        queries = ScoutQueries(
            place_query=" ".join(only),
            radius_m=state.get("max_radius_m", DEFAULT_RADIUS_M),
            categories=categories_for(only),
        )
    else:
        try:
            queries = plan_queries(state)
        except (openai.OpenAIError, ValidationError, ValueError) as e:
            warnings.append(f"scout used the default query: {type(e).__name__}: {e}")
            queries = ScoutQueries(place_query=DEFAULT_QUERY, radius_m=DEFAULT_RADIUS_M)

    dislikes = state.get("dislikes", [])
    # what the traveller asked for, then what they like, is always searched for, whatever the model chose
    must = list(dict.fromkeys(categories_for(state.get("wants", [])) + liked_categories(state)))
    queries.categories = must + [c for c in queries.categories if c not in must]
    queries.categories = [c for c in queries.categories if c not in dislikes] or [
        c for c in DEFAULT_CATEGORIES if c not in dislikes
    ]
    radius = min(queries.radius_m, state.get("max_radius_m", queries.radius_m))
    search = {
        "query": queries.place_query, "radius_m": radius, "categories": queries.categories,
        "tried": sorted(set((prior or {}).get("tried", [])) | set(queries.categories)),
    }
    places = tools.place_search(
        queries.place_query, state.get("lat", 0.0), state.get("lng", 0.0), radius, queries.categories
    )
    if only:
        places = with_reels(places, only, state)
    if tools.use_fixtures():
        # live video lookups cost credits, so feed does one for the venue it nudges
        places = [{**p, "videos": tools.video_search(p["name"])} for p in places]
    if state.get("trigger") == "trip" and not tools.use_fixtures():
        events = []  # the dates of a future visit are unknown, so today's events do not belong in it
    else:
        events = tools.event_search(
            state.get("district", ""), *event_window(state), state.get("lat", 0.0), state.get("lng", 0.0)
        )
    candidates = [localise(c, state) for c in places + events]
    if state.get("trigger") == "trip":
        # the visit is on another day, so whether a place is open this minute says nothing
        candidates = [{**c, "open_now": None} for c in candidates]

    # A replan must not hand the gate something it already refused, and a nudge must
    # not repeat a venue the traveller was told about recently.
    skip = {r["id"] for r in state.get("rejected", [])} | set(state.get("recent", []))
    candidates = [c for c in candidates if c["id"] not in skip]
    if not state.get("wants"):
        # things the traveller asked for by name are worth a long trip; the rest must be near
        candidates = [c for c in candidates if c.get("distance_m", 0) <= radius]
        saved = set(state.get("saved", []))
        candidates = [c for c in candidates if c["id"] in saved or not unwanted(c, dislikes)]
        if only:
            candidates = [c for c in candidates if kinds_of(c) & {k.lower() for k in only}]
    return {"candidates": candidates, "replans": replans, "warnings": warnings, "search": search}
