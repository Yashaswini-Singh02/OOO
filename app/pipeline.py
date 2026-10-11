"""The LangGraph state graph. No tracing import here, so tests can build it offline;
entry points import app.trace first and then this module.
"""
from langgraph.graph import END, START, StateGraph

from app import store
from app.nodes.budget import budget
from app.nodes.feed import feed
from app.nodes.scout import scout
from app.nodes.taste import taste
from app.nodes.verifier import route, verifier
from app.nodes.wardrobe import wardrobe
from app.state import RoveState

PROFILE_KEYS = ("taste", "budget", "wardrobe")


def trigger(state: RoveState) -> dict:
    """Start a run: reset the loop guard and load any profile keys the caller did not pass."""
    if all(k in state for k in PROFILE_KEYS):
        return {"replans": 0}
    conn = store.connect()
    profile = store.load_profile(conn)
    conn.close()
    return {"replans": 0, **{k: v for k, v in profile.items() if k not in state}}


def build():
    g = StateGraph(RoveState)
    g.add_node("trigger", trigger)
    g.add_node("scout", scout)
    g.add_node("taste", taste)
    g.add_node("budget", budget)
    g.add_node("wardrobe", wardrobe)
    g.add_node("verifier", verifier)
    g.add_node("feed", feed)
    g.add_edge(START, "trigger")
    g.add_edge("trigger", "scout")
    g.add_edge("scout", "taste")
    g.add_edge("taste", "budget")
    g.add_edge("budget", "wardrobe")
    g.add_edge("wardrobe", "verifier")
    # The cycle: a failed gate sends the run back to scout, at most twice.
    g.add_conditional_edges(
        "verifier", route, {"feed": "feed", "replan": "scout", "give_up": END}
    )
    g.add_edge("feed", END)
    return g.compile()
