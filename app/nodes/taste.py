from math import log10

from app.state import RoveState

SAVED_BONUS = 1.0  # the traveller saved this spot themselves
REEL_BONUS = 0.5  # Instagram posts name it for what the traveller asked for
INDOOR_IN_RAIN = 1.0
OUTDOOR_IN_RAIN = -3.0
OUTDOOR_ACTIVITIES = {"trek", "beach", "sunset", "market", "walk"}


def score(candidate: dict, tag_weights: dict) -> float:
    """Sum of the user's weights over the candidate's tags. Arithmetic, not a model call."""
    return round(sum(tag_weights.get(tag, 0.0) for tag in candidate.get("tags", [])), 3)


def adjust(candidate: dict, state: RoveState) -> float:
    bonus = SAVED_BONUS if candidate.get("id") in state.get("saved", []) else 0.0
    if candidate.get("reel"):
        bonus += REEL_BONUS
    if state.get("weather") == "rain":
        if candidate.get("indoor"):
            bonus += INDOOR_IN_RAIN
        elif candidate.get("activity") in OUTDOOR_ACTIVITIES:
            bonus += OUTDOOR_IN_RAIN
    return bonus


def quality(candidate: dict) -> float:
    """A rating counts for more the more people gave it: 4.6 from 11,000 beats 4.9 from 12."""
    rating = candidate.get("rating") or 0
    return rating * log10((candidate.get("rating_count") or 0) + 10)


def taste(state: RoveState) -> dict:
    tag_weights = state.get("taste", {}).get("tags", {})
    scored = [
        {**c, "score": round(score(c, tag_weights) + adjust(c, state), 3)}
        for c in state.get("candidates", [])
    ]
    # ties go to the better-rated place; sorted() is stable, so remaining ties keep scout's order
    return {"ranked": sorted(scored, key=lambda c: (c["score"], quality(c)), reverse=True)}
