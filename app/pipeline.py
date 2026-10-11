"""The LangGraph state graph. No tracing setup here, so tests can build it offline;
entry points import app.trace first and then this module.
"""
from langgraph.graph import END, START, StateGraph
from neatlogs import span

from app import store
from app.nodes.budget import budget
from app.nodes.feed import feed
from app.nodes.planner import planner
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
    # each node is one step in the run's trace, with its tools and model calls beneath it;
    # the verifier is already traced as a GUARDRAIL
    steps = {"trigger": trigger, "scout": scout, "taste": taste, "budget": budget,
             "wardrobe": wardrobe, "planner": planner, "feed": feed}
    for name, node in steps.items():
        g.add_node(name, span(kind="CHAIN", name=name)(node))
    g.add_node("verifier", verifier)
    g.add_edge(START, "trigger")
    g.add_edge("trigger", "scout")
    g.add_edge("scout", "taste")
    g.add_edge("taste", "budget")
    g.add_edge("budget", "wardrobe")
    g.add_edge("wardrobe", "planner")
    g.add_edge("planner", "verifier")
    # The cycle: a failed gate sends the run back to scout, at most twice.
    g.add_conditional_edges(
        "verifier", route, {"feed": "feed", "replan": "scout", "give_up": END}
    )
    g.add_edge("feed", END)
    return g.compile()
