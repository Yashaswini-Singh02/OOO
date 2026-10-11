from app.state import RoveState


def feed(state: RoveState) -> dict:
    """Turn the best verified candidate into the one nudge for this run."""
    top = state["verified"][0]
    return {
        "nudge": {
            "id": top["id"],
            "name": top["name"],
            "kind": top["kind"],
            "outfit": top.get("outfit"),
            "cost_inr": top.get("cost_inr", 0),
        }
    }
