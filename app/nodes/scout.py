import os
from datetime import datetime, timedelta

import openai
from pydantic import BaseModel, ValidationError

from app import tools
from app.state import RoveState

OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Haiku while testing; set OO_MODEL to another OpenRouter model id to change it.
MODEL = os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5")
DEFAULT_QUERY = "things to do nearby"
DEFAULT_RADIUS_M = 3000

SYSTEM = (
    "You plan searches for a travel companion that suggests one nearby thing to do. "
    "Write a short place-search query (a few words, no city name) suited to the "
    "context, and a search radius in metres between 1000 and 60000. If venues were "
    "already rejected, search for something different from them."
)


class ScoutQueries(BaseModel):
    place_query: str
    radius_m: int


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


def scout(state: RoveState) -> dict:
    warnings = list(state.get("warnings", []))
    # Loop guard: scout already ran if candidates exist, so this entry is a replan.
    replans = state.get("replans", 0) + (1 if "candidates" in state else 0)

    try:
        queries = plan_queries(state)
    except (openai.OpenAIError, ValidationError, ValueError) as e:
        warnings.append(f"scout used the default query: {type(e).__name__}: {e}")
        queries = ScoutQueries(place_query=DEFAULT_QUERY, radius_m=DEFAULT_RADIUS_M)

    places = tools.place_search(
        queries.place_query, state.get("lat", 0.0), state.get("lng", 0.0), queries.radius_m
    )
    places = [{**p, "videos": tools.video_search(p["name"])} for p in places]
    events = tools.event_search(state.get("district", ""), *event_window(state))

    return {"candidates": places + events, "replans": replans, "warnings": warnings}
