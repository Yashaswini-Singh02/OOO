from app.state import RoveState


def feed(state: RoveState) -> dict:
    """Turn what passed the gate into the one nudge for this run."""
    if state.get("wants"):
        return {
            "nudge": {
                "kind": "plan",
                "days": state["plan"],
                "avoid": state.get("avoid", []),
            }
        }
    top = state["verified"][0]
    return {
        "nudge": {
            "id": top["id"],
            "name": top["name"],
            "kind": top["kind"],
            "outfit": top.get("outfit"),
            "cost_inr": top.get("cost_inr", 0),
            "distance_m": top.get("distance_m"),
            "hours_verified": top.get("open_now") is True,
            "trigger": state.get("trigger"),
        }
    }
