from app.state import RoveState


def score(candidate: dict, tag_weights: dict) -> float:
    """Sum of the user's weights over the candidate's tags. Arithmetic, not a model call."""
    return round(sum(tag_weights.get(tag, 0.0) for tag in candidate.get("tags", [])), 3)


def taste(state: RoveState) -> dict:
    tag_weights = state.get("taste", {}).get("tags", {})
    scored = [{**c, "score": score(c, tag_weights)} for c in state.get("candidates", [])]
    # sorted() is stable, so ties keep scout's order
    return {"ranked": sorted(scored, key=lambda c: c["score"], reverse=True)}
