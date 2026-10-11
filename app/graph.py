import app.trace  # noqa: F401  MUST be the first import in every entry point

import neatlogs
from langgraph.graph import END, START, StateGraph
from neatlogs import span

from app import store
from app.nodes.budget import budget
from app.nodes.scout import scout
from app.nodes.taste import taste
from app.nodes.wardrobe import wardrobe
from app.state import RoveState


def trigger(state: RoveState) -> dict:
    """Start a run: reset the loop guard and load any profile keys the caller did not pass."""
    conn = store.connect()
    profile = store.load_profile(conn)
    conn.close()
    missing = {k: v for k, v in profile.items() if k not in state}
    return {"replans": 0, **missing}


g = StateGraph(RoveState)
g.add_node("trigger", trigger)
g.add_node("scout", scout)
g.add_node("taste", taste)
g.add_node("budget", budget)
g.add_node("wardrobe", wardrobe)
g.add_edge(START, "trigger")
g.add_edge("trigger", "scout")
g.add_edge("scout", "taste")
g.add_edge("taste", "budget")
g.add_edge("budget", "wardrobe")
g.add_edge("wardrobe", END)
graph = g.compile()


@span(kind="WORKFLOW", name="nudge")
def run_once(context: RoveState) -> dict:
    return graph.invoke(context)


if __name__ == "__main__":
    result = run_once(
        {
            "city": "Goa",
            "district": "North Goa",
            "lat": 15.4989,
            "lng": 73.8313,
            "now": "2026-10-10T09:00:00+05:30",
            "days_left": 2,
            "weather": "clear",
        }
    )
    for c in result["ranked"]:
        print(f"{c['score']:>5}  {c['name']}  outfit={c.get('outfit')}")
    print("warnings:", result.get("warnings", []))
    neatlogs.flush()
    neatlogs.shutdown()
