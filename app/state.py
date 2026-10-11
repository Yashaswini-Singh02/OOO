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
