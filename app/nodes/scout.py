import os
from datetime import datetime, timedelta

import openai
from pydantic import BaseModel, ValidationError

from app import gi, tools
from app.geo import distance_m
from app.hours import is_open
from app.providers import CATEGORIES, DEFAULT_CATEGORIES
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
    "categories. If venues were already rejected, search for something different from them."
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
        f"Days left on the trip: {state.get('days_left', 'unknown')}\n"
        f"Tags the traveller likes most: {', '.join(liked) or 'none recorded'}\n"
        f"Kinds of place the traveller does not want (never choose these): "
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
    kinds = {t.lower() for t in c.get("tags", [])} | {t.lower() for t in c.get("types", [])}
    kinds |= set((c.get("what") or "").lower().replace("_", " ").split())
    return any(d.lower() in kinds for d in dislikes)


def scout(state: RoveState) -> dict:
    warnings = list(state.get("warnings", []))
    # Loop guard: scout already ran if candidates exist, so this entry is a replan.
    replans = state.get("replans", 0) + (1 if "candidates" in state else 0)

    if state.get("trigger") == "speciality":
        return {"candidates": scout_shops(state), "replans": replans, "warnings": warnings}

    try:
        queries = plan_queries(state)
    except (openai.OpenAIError, ValidationError, ValueError) as e:
        warnings.append(f"scout used the default query: {type(e).__name__}: {e}")
        queries = ScoutQueries(place_query=DEFAULT_QUERY, radius_m=DEFAULT_RADIUS_M)

    dislikes = state.get("dislikes", [])
    queries.categories = [c for c in queries.categories if c not in dislikes] or [
        c for c in DEFAULT_CATEGORIES if c not in dislikes
    ]
    radius = min(queries.radius_m, state.get("max_radius_m", queries.radius_m))
    places = tools.place_search(
        queries.place_query, state.get("lat", 0.0), state.get("lng", 0.0), radius, queries.categories
    )
    if tools.use_fixtures():
        # live video lookups cost credits, so feed does one for the venue it nudges
        places = [{**p, "videos": tools.video_search(p["name"])} for p in places]
    events = tools.event_search(
        state.get("district", ""), *event_window(state), state.get("lat", 0.0), state.get("lng", 0.0)
    )
    candidates = [localise(c, state) for c in places + events]

    # A replan must not hand the gate something it already refused, and a nudge must
    # not repeat a venue the traveller was told about recently.
    skip = {r["id"] for r in state.get("rejected", [])} | set(state.get("recent", []))
    candidates = [c for c in candidates if c["id"] not in skip]
    if not state.get("wants"):
        # things the traveller asked for by name are worth a long trip; the rest must be near
        candidates = [c for c in candidates if c.get("distance_m", 0) <= radius]
        saved = set(state.get("saved", []))
        candidates = [c for c in candidates if c["id"] in saved or not unwanted(c, dislikes)]
    return {"candidates": candidates, "replans": replans, "warnings": warnings}
