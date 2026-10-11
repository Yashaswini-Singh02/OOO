"""The conversation layer: one incoming message in, one reply out.

Channel-agnostic and free of tracing imports, so it can be tested offline. WhatsApp
(app/whatsapp.py) is one front end; anything that can deliver {text, lat, lng, now}
could be another. Each traveller has their own SQLite file.
"""
import hashlib
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Literal

import openai
from pydantic import BaseModel

from app import budget_plan, messages, runtime, store, tools

USERS_DIR = Path(__file__).resolve().parent.parent / "data" / "users"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_TRIP = {"city": "Goa", "district": "North Goa", "days_left": 2}
ASKED_RADIUS_M = 5000
ACTIVITIES = ["trek", "sunset", "beach", "walk", "market", "indoor", "cafe"]
NO_REPEAT = timedelta(hours=24)

HELP = messages.WELCOME
GREETINGS = {"/start", "/help", "hi", "hello", "hey", "help", "start"}


class Intent(BaseModel):
    intent: Literal[
        "set_trip", "set_budget", "spend", "budget_status", "plan", "shop", "rate", "save", "help"
    ]
    activities: list[str] = []  # for plan: chosen from ACTIVITIES
    amount: float | None = None  # for set_budget and spend, in rupees
    category: str | None = None  # for spend: one of budget_plan.CATEGORIES
    period: Literal["trip", "month"] | None = None  # for set_budget
    rating: int | None = None  # for rate: 1 to 5
    city: str | None = None  # for set_trip
    district: str | None = None  # for set_trip
    days_left: int | None = None  # for set_trip and plan


def understand(text: str) -> Intent:
    """One model call that classifies the message and pulls out its numbers."""
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify a traveller's chat message for a travel companion and extract its "
                    "fields. Intents: set_trip (where they are and for how long), set_budget (a "
                    "total budget for the trip or the month), spend (they spent money), "
                    "budget_status (asking what is left), plan (they name things they want to do), "
                    "shop (asking what to buy locally), rate (feedback on the last suggestion, "
                    "as a rating from 1 to 5), save (they want to keep the last suggested "
                    "place for later), help (anything else). "
                    f"For plan, map what they want onto activities from: {', '.join(ACTIVITIES)}. "
                    f"For spend, choose category from: {', '.join(budget_plan.CATEGORIES)}. "
                    "Amounts are in rupees. Leave a field empty when the message does not state it."
                ),
            },
            {"role": "user", "content": text},
        ],
        response_format=Intent,
    )
    parsed = completion.choices[0].message.parsed
    return parsed or Intent(intent="help")


def user_db(user: str) -> sqlite3.Connection:
    """Each traveller's own database, named by a hash so no phone number touches the disk."""
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    return store.connect(str(USERS_DIR / f"{hashlib.sha256(user.encode()).hexdigest()[:16]}.db"))


def _context(conn: sqlite3.Connection, now: str) -> dict:
    """Trip, profile and memory for this traveller, ready to merge into a graph run."""
    then = datetime.fromisoformat(now)
    recent = [
        n["venue_id"]
        for n in store.load_nudges(conn)
        if then - datetime.fromisoformat(n["at"]) < NO_REPEAT
    ]
    return {
        **store.get_kv(conn, "trip", DEFAULT_TRIP),
        **store.load_profile(conn),
        "now": now,
        "recent": recent,
        "saved": [s["id"] for s in store.load_saved(conn)],
    }


def _run_and_reply(conn: sqlite3.Connection, context: dict, run: Callable[[dict], dict]) -> str:
    result = run(context)
    nudge = result.get("nudge")
    if not nudge:
        if context.get("wants"):
            return "I couldn't fit those into the days you have left without breaking something."
        return messages.silence_text(result)
    if nudge.get("kind") != "plan":
        store.add_nudge(conn, nudge["id"], context["now"], context.get("trigger"))
        _remember(conn, nudge)
    return messages.nudge_text(nudge)


def _remember(conn: sqlite3.Connection, nudge: dict) -> None:
    store.set_kv(conn, "last_nudge", {k: nudge.get(k) for k in ("id", "name", "tags", "lat", "lng")})


def on_live_location(
    conn: sqlite3.Connection, lat: float, lng: float, now: str, run: Callable[[dict], dict]
) -> str | None:
    """A live-location update: goes through the runtime's triggers and throttle, so the
    traveller hears something only when it is worth it. Returns the nudge text or None.
    """
    trip = {**store.get_kv(conn, "trip", DEFAULT_TRIP), **store.load_profile(conn)}
    nudge = runtime.on_location(conn, {"lat": lat, "lng": lng, "now": now}, trip, run)
    if not nudge:
        return None
    _remember(conn, nudge)
    return messages.nudge_text(nudge)


def handle(
    conn: sqlite3.Connection,
    message: dict,
    run: Callable[[dict], dict],
    understand: Callable[[str], Intent] = understand,
    notify: Callable[[str], None] = lambda text: None,
) -> str:
    """Handle {text, lat, lng, now}; lat/lng are set when the traveller shared a location.
    `notify` is called with a short status line just before any step that takes seconds,
    so the front end can tell the traveller their message was received.
    """
    now = message["now"]
    then = datetime.fromisoformat(now)

    if message.get("lat") is not None:
        lat, lng = message["lat"], message["lng"]
        notify("On it. Checking what's open near you...")
        weather = tools.weather(lat, lng)
        store.add_location(conn, now, lat, lng, weather)
        return _run_and_reply(
            conn,
            {**_context(conn, now), "lat": lat, "lng": lng, "weather": weather,
             "trigger": "asked", "max_radius_m": ASKED_RADIUS_M},
            run,
        )

    text = message.get("text", "")
    if text.strip().lower() in GREETINGS:
        return HELP  # no need to spend a model call on "hi"
    intent = message.get("intent") or understand(text)
    trip = store.get_kv(conn, "trip", DEFAULT_TRIP)
    period = store.get_kv(conn, "budget_period", "trip")

    if intent.intent == "set_trip":
        trip = {
            "city": intent.city or trip["city"],
            "district": intent.district or intent.city or trip["district"],
            "days_left": intent.days_left if intent.days_left is not None else trip["days_left"],
        }
        store.set_kv(conn, "trip", trip)
        return f"Got it: {trip['district']}, {trip['days_left']} day(s) left. Share your location when you want a suggestion."

    if intent.intent == "set_budget" and intent.amount:
        period = intent.period or "trip"
        store.set_alloc(conn, budget_plan.split(intent.amount))
        store.set_kv(conn, "budget_period", period)
        return f"Budget set: ₹{intent.amount:,.0f} for the {period}.\n" + budget_plan.status(
            store.load_budget(conn), period, trip["days_left"], then
        )

    if intent.intent == "spend" and intent.amount:
        category = intent.category if intent.category in budget_plan.CATEGORIES else "other"
        store.add_spend(conn, category, intent.amount, message.get("text", ""))
        return f"Logged ₹{intent.amount:,.0f} on {category}.\n" + budget_plan.status(
            store.load_budget(conn), period, trip["days_left"], then
        )

    if intent.intent == "budget_status":
        return budget_plan.status(store.load_budget(conn), period, trip["days_left"], then)

    if intent.intent in ("plan", "shop"):
        here = store.last_location(conn)
        if here is None:
            return "Share your location first, so I know where you're starting from."
        context = {**_context(conn, now), "lat": here["lat"], "lng": here["lng"], "weather": here["weather"]}
        if intent.intent == "shop":
            notify("On it. Looking for what this place is known for...")
            return _run_and_reply(conn, {**context, "trigger": "speciality", "max_radius_m": ASKED_RADIUS_M}, run)
        wants = [a for a in intent.activities if a in ACTIVITIES]
        if not wants:
            return "Tell me what you want to do, e.g. a trek, a sunset, a beach or a market."
        if intent.days_left is not None:
            context["days_left"] = intent.days_left
        notify("On it. Working out how to fit that into your days...")
        return _run_and_reply(conn, {**context, "wants": wants}, run)

    if intent.intent == "save":
        last = store.get_kv(conn, "last_nudge")
        if last is None or last.get("lat") is None:
            return "There's no suggestion to save yet."
        store.save_spots(conn, [last])
        return f"Saved {last['name']}. I'll let you know when you're near it."

    if intent.intent == "rate" and intent.rating:
        last = store.get_kv(conn, "last_nudge")
        if last is None:
            return "I haven't suggested anything yet, so there's nothing to rate."
        store.add_rating(conn, last["name"], last.get("tags") or [], max(1, min(5, intent.rating)))
        return f"Noted {intent.rating}/5 for {last['name']}. I'll weigh that in next time."

    return HELP
