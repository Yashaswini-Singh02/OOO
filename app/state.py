from typing import Optional, TypedDict


class RoveState(TypedDict, total=False):
    # --- context, set by the trigger ---
    lat: float
    lng: float
    now: str  # iso8601, from the simulator
    city: str
    district: str
    days_left: int
    weather: str
    wants: list  # activities the traveller asked for, e.g. ["trek", "sunset"]
    trigger: str  # why this run started: near_saved_spot, place_just_opened, rain_starting
    max_radius_m: int  # how far the trigger lets scout look
    saved: list  # ids of spots the traveller saved
    recent: list  # ids nudged in the last 24 h, never repeated
    dislikes: list  # kinds of place the traveller said they do not want, e.g. ["park"]
    notes: list  # lasting things the traveller said about themselves, in their words, e.g. ["vegetarian"]
    shortlist: int  # when set, answer with this many verified places to choose from, not one
    purpose: str  # "outfit": the places are wanted as backdrops for what the traveller packed
    only: list  # kinds asked for in this one request, e.g. ["food"]; not a lasting taste

    # --- profile, loaded from sqlite ---
    taste: dict  # {tags: {...}, ratings: [...]}
    budget: dict  # {alloc: {...}, spent: {...}}
    wardrobe: list  # [{id, colour, formality, fabric}]

    # --- working set ---
    candidates: list  # everything scout found
    ranked: list  # after taste + budget
    verified: list  # passed the gate
    rejected: list  # [{candidate, reason}]  <- failure evidence
    warnings: list  # plain strings: overspend, nothing packed suits an activity
    plan: list  # [{day, id, name, activity, outfit, at}] when wants is set
    conflicts: list  # [{a, b, day, reason}] pairs that cannot share a day
    plan_ok: bool  # the verifier's verdict on the current plan
    avoid: list  # plain strings: what not to book, and why
    nudge: Optional[dict]
    replans: int  # loop guard, max 2
