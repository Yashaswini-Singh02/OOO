from app import tools
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
    videos = top.get("videos")
    if videos is None:  # live mode: scout skipped the lookup, do it for this one venue
        videos = tools.video_search(top["name"], state.get("city", ""))
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
            "links": [v["url"] for v in videos],
            "source": top.get("source"),
        }
    }
