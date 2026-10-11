from app.state import RoveState

SAVED_BONUS = 1.0  # the traveller saved this spot themselves
INDOOR_IN_RAIN = 1.0
OUTDOOR_IN_RAIN = -3.0
OUTDOOR_ACTIVITIES = {"trek", "beach", "sunset", "market", "walk"}


def score(candidate: dict, tag_weights: dict) -> float:
    """Sum of the user's weights over the candidate's tags. Arithmetic, not a model call."""
    return round(sum(tag_weights.get(tag, 0.0) for tag in candidate.get("tags", [])), 3)


def adjust(candidate: dict, state: RoveState) -> float:
    bonus = SAVED_BONUS if candidate.get("id") in state.get("saved", []) else 0.0
    if state.get("weather") == "rain":
        if candidate.get("indoor"):
            bonus += INDOOR_IN_RAIN
        elif candidate.get("activity") in OUTDOOR_ACTIVITIES:
            bonus += OUTDOOR_IN_RAIN
    return bonus


def taste(state: RoveState) -> dict:
    tag_weights = state.get("taste", {}).get("tags", {})
    scored = [
        {**c, "score": round(score(c, tag_weights) + adjust(c, state), 3)}
        for c in state.get("candidates", [])
    ]
    # sorted() is stable, so ties keep scout's order
    return {"ranked": sorted(scored, key=lambda c: c["score"], reverse=True)}
