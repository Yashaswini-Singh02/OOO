"""The conversation layer: one incoming message in, one reply out.

Channel-agnostic and free of tracing imports, so it can be tested offline. WhatsApp
(app/whatsapp.py) is one front end; anything that can deliver {text, lat, lng, now}
could be another. Each traveller has their own SQLite file.
"""
import contextvars
import hashlib
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Literal

import openai
from pydantic import BaseModel

from app import budget_plan, messages, runtime, store, tools
from app import providers
from app.nodes.wardrobe import is_worship
from app.providers import CATEGORIES, FABRICS

USERS_DIR = Path(__file__).resolve().parent.parent / "data" / "users"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_TRIP = {"city": "", "district": "", "days_left": 2}
ASKED_RADIUS_M = 5000
TRIP_RADIUS_M = 20000
MAX_TRIP_DAYS = 7
SHORTLIST = 3  # how many places to offer when someone says what they love
NEED_PLACE = "Tell me a place, e.g. \"cafes in Udaipur\", or share your location."
ACTIVITIES = ["trek", "sunset", "beach", "walk", "market", "indoor", "cafe"]
NO_REPEAT = timedelta(hours=24)
# the words a like or dislike can be mapped onto: place kinds and the tags they carry
KINDS = sorted(set(CATEGORIES) | {t for _, tags, _, _ in CATEGORIES.values() for t in tags} | set(ACTIVITIES))
LIKE_WEIGHT, DISLIKE_WEIGHT = 0.8, -1.0

HELP = messages.WELCOME
GREETINGS = {"/start", "/help", "hi", "hello", "hey", "help", "start"}
FORGET = {"/forget", "forget me", "forget everything", "forget everything about me",
          "forget my preferences", "reset my preferences"}


class Packed(BaseModel):
    name: str  # a few words, e.g. "white linen shirt"
    colour: str = ""
    fabric: str = "other"  # one of providers.FABRICS
    formality: str = "casual"  # casual, smart, ethnic or formal
    style: list[str] = []


class Intent(BaseModel):
    intent: Literal[
        "set_trip", "set_budget", "spend", "budget_status", "plan", "shop", "rate", "save", "more", "find", "preference", "trip_plan", "about", "info", "pack", "outfits", "videos", "profile", "chat", "help"
    ]
    activities: list[str] = []  # for plan: chosen from ACTIVITIES
    amount: float | None = None  # for set_budget and spend, in rupees
    category: str | None = None  # for spend: one of budget_plan.CATEGORIES
    period: Literal["trip", "month"] | None = None  # for set_budget
    rating: int | None = None  # for rate: 1 to 5
    city: str | None = None  # a place they name: where they are, or where to look or plan
    district: str | None = None  # for set_trip
    days_left: int | None = None  # for set_trip and plan; for trip_plan, the length in days
    packed: list[Packed] = []  # for pack: the clothes they say they are taking
    place: str | None = None  # for about: the name of the place they ask about
    index: int | None = None  # for about, from a button: position in the last plan
    likes: list[str] = []  # for preference and find: chosen from KINDS
    dislikes: list[str] = []  # for preference and find: chosen from KINDS
    notes: list[str] = []  # for preference: lasting things about them that fit no word in KINDS


class Understood(BaseModel):
    steps: list[Intent]  # one per thing the message says, facts first and the request last


# Intents that only tell us something. A message can carry several of these and one request.
TELLS = ["set_trip", "set_budget", "spend", "pack", "preference"]


def understand(text: str, context: str = "") -> list[Intent]:
    """One model call that splits the message into the things it says, classifies each and
    pulls out its fields. `context` describes the last suggestion and what is already known,
    so "not this kind of place" or "something like that again" can be resolved.
    """
    client = openai.OpenAI(base_url=OPENROUTER_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    completion = client.chat.completions.parse(
        model=os.getenv("OO_MODEL", "anthropic/claude-haiku-5.5"),
        messages=[
            {
                "role": "system",
                "content": (
                    "Read a traveller's chat message for a travel companion. A message often says "
                    "several things at once, e.g. 'I'm in Udaipur for 3 days, I love sunsets and "
                    "my budget is 10000' is three steps: set_trip, preference, set_budget. Return "
                    "one step for each thing said, each with its own intent and only its own "
                    "fields. Never drop part of the message. Most messages are a single step. "
                    "Intents: trip_plan (they ask you to plan a trip or itinerary for a "
                    "named destination; city is the destination and days_left the number of days), "
                    "set_trip (they only state where they currently are and for how long, without "
                    "asking for a plan), set_budget (a "
                    "total budget for the trip or the month), spend (they spent money), "
                    "budget_status (asking what is left), plan (they name things they want to do), "
                    "shop (asking what to buy locally), rate (feedback on the last suggestion, "
                    "as a rating from 1 to 5), save (they want to keep the last suggested "
                    "place for later), more (they want another suggestion, a different one), "
                    "find (they ask for a kind of place right now, e.g. 'somewhere to eat', "
                    "'a cafe nearby', 'I want food, not shopping'; this is a one-time request, not "
                    "a lasting taste), "
                    "preference (a lasting like or dislike, stated as one: 'I don't like parks', "
                    "'I hate museums', 'I love beaches', 'never suggest markets', or a reaction to "
                    "the last suggestion such as 'not this kind of thing'), "
                    "pack (they tell you what clothes they are taking; list each in packed, with "
                    f"fabric from: {', '.join(FABRICS)}), "
                    "outfits (they ask what to wear where, or where their clothes will look good), "
                    "videos (they want more reels, videos or vlogs of the last place), "
                    "profile (they ask what you know or remember about them), "
                    "about (they ask to hear about one named place, e.g. 'tell me about City Palace'; "
                    "put its name in place), info (they want more detail on the last suggestion, "
                    "such as its hours or what people say), "
                    "chat (small talk, thanks, how they feel about the trip, or a general travel "
                    "question that needs no place looked up, e.g. 'is October a good time for "
                    "Udaipur?'), help (they ask what you can do, or nothing else fits). "
                    "For preference, anything lasting about them that those words cannot express "
                    "(diet, pace, company, crowds, mobility, interests) goes in notes, a few words "
                    "each in their own terms: 'vegetarian', 'avoids crowds', 'travelling with a "
                    "toddler', 'loves live music'. "
                    "A message about what they want at this moment is find, not preference. "
                    "Asking for more ideas of a kind they love ('more sunrise ideas', 'other sunset "
                    "spots') is preference with that kind in likes. "
                    "Whenever the message names a town, city or area to look in, put it in city. "
                    f"For find and preference, fill likes (wanted) and dislikes (not wanted) using "
                    f"only words from: {', '.join(KINDS)}. "
                    "When they refer to the last suggestion without naming a kind, use its kind "
                    "from the context. "
                    f"For plan, map what they want onto activities from: {', '.join(ACTIVITIES)}. "
                    f"For spend, choose category from: {', '.join(budget_plan.CATEGORIES)}. "
                    "Amounts are in rupees. Leave a field empty when the message does not state it."
                ),
            },
            {"role": "user", "content": f"Context:\n{context or 'none'}\n\nMessage: {text}"},
        ],
        response_format=Understood,
        extra_body=providers.THINK,
    )
    parsed = completion.choices[0].message.parsed
    return parsed.steps if parsed and parsed.steps else [Intent(intent="help")]


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
        "dislikes": store.get_kv(conn, "dislikes", []),
        "notes": store.get_kv(conn, "notes", []),
    }


def _hint(conn: sqlite3.Connection) -> str:
    """What the model needs to resolve references to earlier messages."""
    last = store.get_kv(conn, "last_nudge")
    liked = [t for t, w in store.load_taste(conn)["tags"].items() if w > 0]
    lines = []
    if last:
        lines.append(f"Last suggestion: {last['name']} (kind: {', '.join(last.get('tags') or []) or 'unknown'})")
    lines.append(f"Already likes: {', '.join(liked) or 'nothing recorded'}")
    lines.append(f"Already dislikes: {', '.join(store.get_kv(conn, 'dislikes', [])) or 'nothing recorded'}")
    lines.append(f"Already noted about them: {'; '.join(store.get_kv(conn, 'notes', [])) or 'nothing'}")
    trip = store.get_kv(conn, "trip", DEFAULT_TRIP)
    if trip.get("city"):
        lines.append(f"Their trip: {trip['city']}, {trip['days_left']} day(s)")
    asked = store.get_kv(conn, "asked")
    if asked:
        lines.append(
            f"Your last message ended with this question to them: \"{asked}\" Read a short reply (yes, sure, "
            "go ahead, the first one, or just a list of interests) as their answer to it. Agreeing to a plan is "
            "trip_plan for their trip's town and days; interests they give are a preference step before it."
        )
    return "\n".join(lines)


MAX_NOTES = 12


def _learn(conn: sqlite3.Connection, likes: list, dislikes: list, notes: list = ()) -> str:
    """Record stated likes and dislikes for good, and keep anything else they said about
    themselves in their own words; returns a short confirmation.
    """
    likes = [k for k in likes if k in KINDS]
    dislikes = [k for k in dislikes if k in KINDS and k not in likes]
    taste = store.load_taste(conn)
    for k in likes:
        taste["tags"][k] = LIKE_WEIGHT
    for k in dislikes:
        taste["tags"][k] = DISLIKE_WEIGHT
    store.save_taste(conn, taste)
    known = [d for d in store.get_kv(conn, "dislikes", []) if d not in likes]
    store.set_kv(conn, "dislikes", sorted(set(known) | set(dislikes)))
    kept = store.get_kv(conn, "notes", [])
    notes = [n.strip() for n in notes if n.strip() and n.strip().lower() not in {k.lower() for k in kept}]
    if notes:
        store.set_kv(conn, "notes", (kept + notes)[-MAX_NOTES:])
    parts = []
    if dislikes:
        parts.append(f"no more {' or '.join(messages.spoken(k) for k in dislikes)}")
    if likes:
        parts.append(f"more {' and '.join(messages.spoken(k) for k in likes)}")
    if notes:
        parts.append(f"noted that you said \"{', '.join(notes)}\"")
    return "Got it: " + ", ".join(parts) + ". I'll remember that." if parts else ""


def _run_and_reply(conn: sqlite3.Connection, context: dict, run: Callable[[dict], dict]) -> str:
    result = run(context)
    nudge = result.get("nudge")
    if not nudge:
        if context.get("wants"):
            return "I couldn't fit those into the days you have left without breaking something."
        return messages.silence_text(result)
    if nudge.get("kind") == "itinerary" and not nudge["days"]:
        return "I couldn't find enough places I could verify there to make a plan."
    if nudge.get("kind") == "itinerary":
        nudge["ask_packing"] = not store.get_kv(conn, "outfits", [])
        star = nudge.get("spotlight")
        extra = [star] if star and star["id"] not in {p["id"] for p in nudge["days"]} else []
        store.set_kv(conn, "last_plan", nudge["days"] + extra)  # so "tell me about X" can find them
    if nudge.get("kind") == "shortlist":
        if not nudge["places"]:
            return messages.silence_text(result)
        store.set_kv(conn, "shortlist", nudge["places"])  # so each can be opened, by button or by name
        _ask(conn, str(messages.nudge_text(nudge)).split("\n")[-1])
        for place in nudge["places"]:
            store.add_nudge(conn, place["id"], context["now"], context.get("trigger"))  # "more ideas" brings new ones
        return messages.nudge_text(nudge)
    if nudge.get("kind") not in ("plan", "itinerary"):
        store.add_nudge(conn, nudge["id"], context["now"], context.get("trigger"))
        _remember(conn, nudge)
        if nudge.get("city"):
            # where the traveller actually is beats whatever the trip said before
            trip = store.get_kv(conn, "trip", DEFAULT_TRIP)
            store.set_kv(conn, "trip", {**trip, "city": nudge["city"],
                                        "district": nudge.get("district") or nudge["city"]})
    return messages.nudge_text(nudge)


def _where(conn: sqlite3.Connection, intent: Intent) -> dict | None:
    """The point a request is about: a place the traveller named, else where they last
    were, else the town of their trip. None when there is nothing to go on.
    """
    trip = store.get_kv(conn, "trip", DEFAULT_TRIP)
    if intent.city:
        found = tools.geocode(intent.city)
        return {**found, "weather": "clear", "city": intent.city} if found else None
    here = store.last_location(conn)
    if here:
        return {"lat": here["lat"], "lng": here["lng"], "weather": here["weather"]}
    if trip.get("city"):
        found = tools.geocode(trip["city"])
        return {**found, "weather": "clear", "city": trip["city"]} if found else None
    return None


def _at(place: dict) -> dict:
    """The graph inputs for a point from _where()."""
    out = {"lat": place["lat"], "lng": place["lng"], "weather": place["weather"]}
    if place.get("city"):
        out.update(city=place["city"], district=place["city"])
    return out


MAX_CARDS = 20


def _remember(conn: sqlite3.Connection, nudge: dict) -> None:
    """Keep what follow-ups need about a suggested place: as the latest one, and under a short
    key that its own buttons carry, so a tap on an older card is about that card's place.
    """
    memory = {k: nudge.get(k) for k in ("id", "name", "tags", "lat", "lng", "google_id", "city", "what")}
    store.set_kv(conn, "last_nudge", memory)
    ref = hashlib.sha256(str(nudge.get("id") or nudge["name"]).encode()).hexdigest()[:8]
    cards = store.get_kv(conn, "cards", {})
    cards.pop(ref, None)
    cards[ref] = memory
    store.set_kv(conn, "cards", dict(list(cards.items())[-MAX_CARDS:]))
    nudge["ref"] = ref


def _card_of(conn: sqlite3.Connection, message: dict) -> dict | None:
    """The place a follow-up is about: the card whose button was tapped, else the latest one."""
    return store.get_kv(conn, "cards", {}).get(message.get("ref") or "") or store.get_kv(conn, "last_nudge")


def _pack(conn: sqlite3.Connection, items: list) -> list:
    """Add outfits to what this traveller has packed. Kept as words only: full detail for
    styling in the kv store, and the basics in the wardrobe table the graph reads.
    """
    outfits = store.get_kv(conn, "outfits", [])
    for item in items:
        slug = "-".join(item["name"].lower().split())[:40] or "outfit"
        taken = {o["id"] for o in outfits}
        outfits.append({**item, "id": slug if slug not in taken else f"{slug}-{len(outfits) + 1}"})
    store.set_kv(conn, "outfits", outfits)
    store.save_wardrobe(conn, [
        {"id": o["id"], "colour": o.get("colour") or "", "formality": o.get("formality") or "casual",
         "fabric": o.get("fabric") or "other"} for o in outfits
    ])
    return outfits


def _packed_reply(conn: sqlite3.Connection, added: list, notify: Callable[[str], None]) -> str:
    outfits = _pack(conn, added)
    names = " and ".join(o["name"] for o in outfits[-len(added):])
    got = f"Packed: {names}. That's {len(outfits)} outfit{'s' if len(outfits) != 1 else ''} in your suitcase."
    return got


def _plan_trip(conn: sqlite3.Connection, city: str, days: int, now: str, run: Callable[[dict], dict],
               notify: Callable[[str], None]) -> str:
    """A day-by-day plan for a named town, which also becomes the traveller's trip."""
    there = tools.geocode(city)
    if there is None:
        return f"I couldn't find {city}. Try the name of the town or city."
    days = max(1, min(days or 2, MAX_TRIP_DAYS))
    city = there["name"]  # the place's proper name, not however it was typed
    store.set_kv(conn, "trip", {"city": city, "district": city, "days_left": days})
    notify(f"On it. Putting together {days} day{'s' if days != 1 else ''} in {city}...")
    return _run_and_reply(
        conn,
        {**_context(conn, now), "lat": there["lat"], "lng": there["lng"], "weather": "clear",
         "city": city, "district": city, "days_left": days,
         "trigger": "trip", "max_radius_m": TRIP_RADIUS_M, "recent": []},
        run,
    )


BACKDROPS = 20  # how many verified places the stylist may choose between
REEL_PLACES = 2  # places whose reels are looked up for one reply: each new place costs a search
PLAN_OFFER = "Want me to plan the rest of your {days} too? Tell me what you're into, or just say yes."
MOOD = "Happy to. What are you in the mood for: something to eat, a cafe, a bit of shopping, or a place to see?"


def _backdrops(conn: sqlite3.Connection, now: str, run: Callable[[dict], dict]) -> list:
    """Verified places to style outfits against when there is no plan: found around where the
    traveller is, or across the town of their trip. Empty when neither is known.
    """
    here = _where(conn, Intent(intent="outfits"))
    if here is None:
        return []
    result = run({
        **_context(conn, now), **_at(here), "trigger": "asked", "recent": [], "shortlist": BACKDROPS,
        "purpose": "outfit", "max_radius_m": TRIP_RADIUS_M if here.get("city") else ASKED_RADIUS_M,
    })
    nudge = result.get("nudge") or {}
    return nudge.get("places", []) if nudge.get("kind") == "shortlist" else []


def _wear(conn: sqlite3.Connection, now: str, run: Callable[[dict], dict], notify: Callable[[str], None]) -> str:
    """Where to wear what is packed: each outfit matched to a verified place, with a shot
    idea and reels of that place. The places come from the plan when there is one; otherwise
    a few are found for the purpose, and the buddy offers to plan the rest.
    """
    outfits, plan = store.get_kv(conn, "outfits", []), store.get_kv(conn, "last_plan", [])
    trip = store.get_kv(conn, "trip", DEFAULT_TRIP)
    places = plan or _backdrops(conn, now, run)
    # an outfit is never styled against a temple or other place of worship
    places = [p for p in places if not is_worship(p)]
    if not places and not trip.get("city") and not store.last_location(conn):
        return "Where are you headed, and for how many days? I'll find the spots to wear it."
    pairs = tools.style_match(outfits, places)
    if not pairs:
        return "I couldn't find a place I could verify to match that with right now. Try again in a bit."
    by_id = {p["id"]: p for p in places}
    chosen = list(dict.fromkeys(x["place_id"] for x in pairs))  # in order, each place once
    store.set_kv(conn, "shortlist", [by_id[i] for i in chosen])  # so each can be opened, by button or by name
    videos = {
        i: tools.video_search(by_id[i]["name"], by_id[i].get("city") or trip.get("city", ""), by_id[i].get("what"))
        for i in chosen[:REEL_PLACES]
    }
    ask = ""
    if not plan:
        days = trip.get("days_left") or 0
        ask = PLAN_OFFER.format(days=f"{days} day{'s' if days != 1 else ''}" if days else "trip")
        _ask(conn, ask)
    return messages.pairings_text(pairs, outfits, places, videos, ask, tools.place_about(by_id[chosen[0]])["photo"])


def _ask(conn: sqlite3.Connection, question: str) -> None:
    """Remember the question the buddy just asked, so a short answer to it can be understood."""
    store.set_kv(conn, "asked", question)


def _profile(conn: sqlite3.Connection, now: datetime) -> str:
    trip = store.get_kv(conn, "trip", DEFAULT_TRIP)
    tags = store.load_taste(conn)["tags"]
    budget = store.load_budget(conn)
    left = sum(budget["alloc"].values()) - sum(budget["spent"].values())
    return messages.profile_text({
        "trip": f"{trip['city']}, {trip['days_left']} day(s)" if trip.get("city") else "",
        "likes": sorted(t for t, w in tags.items() if w > 0),
        "dislikes": store.get_kv(conn, "dislikes", []),
        "notes": store.get_kv(conn, "notes", []),
        "outfits": [o["name"] for o in store.get_kv(conn, "outfits", [])],
        "saved": [x["name"] for x in store.load_saved(conn)],
        "budget": f"₹{left:,.0f} left of ₹{sum(budget['alloc'].values()):,.0f}" if budget["alloc"] else "",
    })


def _card(conn: sqlite3.Connection, place: dict) -> str:
    """The full card for one known place: description, reviews, photo and links."""
    about = tools.place_about(place)
    videos = tools.video_search(place["name"], place.get("city") or "", place.get("what"))
    nudge = {
        **place,
        **about,
        "kind": place.get("kind") or "place",
        "hours_verified": place.get("open_now") is True,
        "videos": [{"url": v["url"], "source": v.get("source", "video")} for v in videos],
        "why": ["It's in the plan I made for you."] if place.get("day") else [],
    }
    liked = sorted(t for t, w in store.load_taste(conn)["tags"].items() if w > 0)
    nudge["say"] = tools.buddy_line(nudge, nudge["why"], store.get_kv(conn, "notes", []), likes=liked)
    _remember(conn, nudge)
    return messages.nudge_text(nudge)


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
    understand: Callable[[str, str], list[Intent] | Intent] = understand,
    notify: Callable[[str], None] = lambda text: None,
    see: Callable[[str, str], dict | None] = tools.see_outfit,
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

    if message.get("image"):
        notify("Nice, let me take a look...")
        try:
            outfit = see(message["image"], message.get("image_type", "image/jpeg"))
        except (openai.OpenAIError, ValueError):
            return "I couldn't read that photo. Try another one, or just tell me what it is."
        if outfit is None:
            return "I couldn't make out an outfit in that photo. Send one of the clothes, or tell me what you're packing."
        packed = _packed_reply(conn, [outfit], notify)
        if not message.get("text", "").strip():
            notify(packed + " Let me see where it'll look best...")
            return _wear(conn, now, run, notify)

    text = message.get("text", "")
    if text.strip().lower() in GREETINGS:
        return HELP  # no need to spend a model call on "hi"
    if text.strip().lower() in FORGET:
        store.forget(conn)
        return "Done. I've forgotten everything about you: trip, budget, tastes, packing and saved places."
    hint = _hint(conn)
    if not message.get("intent"):
        store.set_kv(conn, "asked", None)  # a question stays open for the next thing they type, not past it
    if message.get("image"):
        hint += f"\nThey attached a photo of an outfit with this message. It is already packed as: {outfit['name']}"
    steps = _steps(message.get("intent") or understand(text, hint))
    if message.get("image"):
        # the photo is packed already; what is left of the caption is facts, and where to wear it
        tells = [s for s in steps if s.intent in TELLS and s.intent != "pack"]
        for plan in (s for s in steps if s.intent == "trip_plan" and s.city):
            # "what do I do with my days in X" also says where they are
            tells.insert(0, Intent(intent="set_trip", city=plan.city, days_left=plan.days_left))
        steps = tells + [Intent(intent="outfits")]
    # everything the message tells us is taken in first, each with a one-line confirmation;
    # the last step gets the full reply
    told = [str(_act(conn, message, step, run, notify, brief=True)).split("\n")[0] for step in steps[:-1]]
    if message.get("image"):
        told.insert(0, packed)
    typed = bool(text) and not message.get("intent")  # a button press needs no reaction
    spoke = []

    def react(lines: list) -> str:
        return tools.react(text, lines, str(_profile(conn, then))) if typed else ""

    talking = []

    def say(status: str) -> None:
        """The status line before a slow step. The first one carries everything taken in so
        far, said in the buddy's own words when they can be written. It is written and sent
        alongside the slow step, not before it, so the search does not wait for the words.
        """
        if isinstance(status, messages.Reply):
            for earlier in talking:
                earlier.join()
            return notify(status)  # a finished reply sent ahead of another: passed on as it is
        first = not spoke
        lines = (told if first else []) + [status]
        memory = str(_profile(conn, then)) if typed and first else ""
        spoke.append(status)

        def speak() -> None:
            voiced = tools.react(text, lines, memory) if typed and first else ""
            # the result follows at once, so a question here would be answered by nobody
            voiced = _no_question(voiced)
            for line in [voiced] if voiced else ["\n".join(lines[:-1]), status]:
                if line:
                    notify(line)

        for earlier in talking:
            earlier.join()  # status lines go out in the order they were said
        talking.append(threading.Thread(target=contextvars.copy_context().run, args=(speak,)))
        talking[-1].start()

    try:
        reply = _act(conn, message, steps[-1], run, say)
    finally:
        for thread in talking:
            thread.join()  # the buddy's words always land before the result they introduce
    if spoke:
        return reply
    # nothing slow happened, so nothing has been said yet
    voiced = ""
    if steps[-1].intent in TELLS:
        # the reply opens with a confirmation: say that part, and the rest, as the buddy would
        voiced = react(told + [str(reply).split("\n")[0], "Nothing is being searched yet."])
    if not voiced:
        if told:
            notify("\n".join(told))
        return reply
    remaining = messages.rest(reply)
    if not str(remaining):
        return voiced
    notify(voiced)
    return remaining


def _no_question(line: str) -> str:
    """Drop a closing question from a line that introduces a result."""
    bare = re.sub(r"[^\w?.!…]+$", "", line.strip())  # a trailing emoji does not stop it being a question
    if not bare.endswith("?"):
        return line
    parts = re.split(r"(?<=[.!?…])\s+", bare)
    while len(parts) > 1 and parts[-1].endswith("?"):
        parts.pop()
    return " ".join(parts)


def _steps(understood) -> list[Intent]:
    """The steps of one message, in the order to act on them: what it tells us first
    (a stated taste last of those, so its follow-up knows the trip), then at most one request.
    """
    steps = [understood] if isinstance(understood, Intent) else list(understood or [])
    tells = sorted((s for s in steps if s.intent in TELLS), key=lambda s: s.intent == "preference")
    asks = [s for s in steps if s.intent not in TELLS]
    return tells + asks[-1:] or [Intent(intent="help")]


def _act(
    conn: sqlite3.Connection, message: dict, intent: Intent, run: Callable[[dict], dict],
    notify: Callable[[str], None], brief: bool = False,
) -> str:
    """Carry out one step. `brief` means more of the message follows, so only take the
    information in and confirm it: no search and no follow-up suggestion.
    """
    now = message["now"]
    then = datetime.fromisoformat(now)
    trip = store.get_kv(conn, "trip", DEFAULT_TRIP)
    period = store.get_kv(conn, "budget_period", "trip")

    if intent.intent == "set_trip":
        trip = {
            "city": intent.city or trip["city"],
            "district": intent.district or intent.city or trip["district"],
            "days_left": intent.days_left if intent.days_left is not None else trip["days_left"],
        }
        store.set_kv(conn, "trip", trip)
        return messages.trip_text(
            trip["district"], trip["days_left"],
            knows_taste=bool(store.load_taste(conn)["tags"]),
            knows_packing=bool(store.get_kv(conn, "outfits", [])),
        )

    if intent.intent == "trip_plan":
        city = intent.city or trip.get("city")  # "yes, plan it" means the trip they already told us about
        if not city:
            return "Where to? Tell me the place and how many days, e.g. \"plan 2 days in Udaipur\"."
        return _plan_trip(conn, city, intent.days_left or trip.get("days_left"), now, run, notify)

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

    if intent.intent == "about":
        plan = store.get_kv(conn, "last_plan", [])
        short = store.get_kv(conn, "shortlist", [])
        place = None
        if intent.index is not None and 0 <= intent.index - messages.SHORTLIST_KEY < len(short):
            place = short[intent.index - messages.SHORTLIST_KEY]
        elif intent.index is not None and 0 <= intent.index < len(plan):
            place = plan[intent.index]
        elif intent.place:
            wanted = intent.place.lower()
            place = next((p for p in short + plan if wanted in p["name"].lower() or p["name"].lower() in wanted), None)
            if place is None:
                here = _where(conn, intent)
                notify(f"On it. Looking up {intent.place}...")
                place = tools.lookup(intent.place, here["lat"], here["lng"]) if here else tools.lookup(intent.place)
        if place is None:
            return "Which place? Tell me its name, or tap one from the plan."
        if intent.index is not None:
            notify(f"On it. Pulling up {place['name']}...")
        return _card(conn, place)

    if intent.intent == "pack":
        if not intent.packed:
            return "What are you taking? Tell me the pieces, or send photos of your outfits."
        packed = _packed_reply(conn, [p.model_dump() for p in intent.packed], notify)
        if brief:
            return packed
        notify(packed + " Let me see where they'll look best...")
        return _wear(conn, now, run, notify)

    if intent.intent == "outfits":
        if not store.get_kv(conn, "outfits", []):
            return "Tell me what you're packing first, or send photos of your outfits."
        return _wear(conn, now, run, notify)

    if intent.intent == "chat":
        return tools.chat_reply(message.get("text", ""), str(_profile(conn, then))) or HELP

    if intent.intent == "profile":
        return _profile(conn, then)

    if intent.intent == "videos":
        last = _card_of(conn, message)
        if last is None:
            return "Ask me about a place first, then I can find videos of it."
        return messages.videos_text(
            last["name"], tools.video_search(last["name"], last.get("city") or "", last.get("what"))
        )

    if intent.intent == "info":
        last = _card_of(conn, message)
        if last is None:
            return "Ask me about a place first, then I can dig into it."
        return messages.details_text(last["name"], tools.place_details(last))

    if intent.intent == "find":
        here = _where(conn, intent)
        if here is None:
            return NEED_PLACE
        only = [k for k in intent.likes if k in KINDS]
        skip = [k for k in intent.dislikes if k in KINDS]
        if not only:
            _ask(conn, MOOD)  # so a short answer such as "eat" is read as the kind of place
            return MOOD
        context = _context(conn, now)
        notify(f"On it. Looking for {' or '.join(only)} near you...")
        return _run_and_reply(
            conn,
            {**context, **_at(here),
             "trigger": "asked", "max_radius_m": ASKED_RADIUS_M, "only": only,
             # for this request only: nothing here is written to the profile
             "dislikes": sorted(set(context["dislikes"]) | set(skip))},
            run,
        )

    if intent.intent == "preference":
        learned = _learn(conn, intent.likes, intent.dislikes, intent.notes)
        if not learned:
            return "Tell me what you do or don't enjoy, e.g. \"I don't like parks\" or \"I love museums\"."
        about_kinds = any(k in KINDS for k in intent.likes + intent.dislikes)
        here = _where(conn, intent) if about_kinds and not brief else None
        if here is None:
            return learned
        context = {**_context(conn, now), **_at(here), "trigger": "asked", "max_radius_m": ASKED_RADIUS_M}
        likes = [k for k in intent.likes if k in KINDS]
        if likes:
            # they named something they love, so the follow-up is one of exactly that
            notify(learned + " Let me find you a few...")
            # with only a town to go on, not a pin, look across the whole town
            radius = TRIP_RADIUS_M if here.get("city") else ASKED_RADIUS_M
            return _run_and_reply(
                conn, {**context, "only": likes, "max_radius_m": radius, "shortlist": SHORTLIST}, run
            )
        # they were reacting to a suggestion, so follow up with one that fits better
        notify(learned + " Finding you something else...")
        return _run_and_reply(conn, context, run)

    if intent.intent in ("plan", "shop", "more"):
        here = _where(conn, intent)
        if here is None:
            return NEED_PLACE
        context = {**_context(conn, now), **_at(here)}
        if intent.intent == "more":
            # everything suggested in the last day is already excluded, so this is a new one
            notify("On it. Finding you another one...")
            return _run_and_reply(conn, {**context, "trigger": "asked", "max_radius_m": ASKED_RADIUS_M}, run)
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
        last = _card_of(conn, message)
        if last is None or last.get("lat") is None:
            return "There's no suggestion to save yet."
        store.save_spots(conn, [last])
        return f"Saved {last['name']}. I'll let you know when you're near it."

    if intent.intent == "rate" and intent.rating:
        last = _card_of(conn, message)
        if last is None:
            return "I haven't suggested anything yet, so there's nothing to rate."
        store.add_rating(conn, last["name"], last.get("tags") or [], max(1, min(5, intent.rating)))
        return f"Noted {intent.rating}/5 for {last['name']}. I'll weigh that in next time."

    return HELP
