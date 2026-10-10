import app.trace  # noqa: F401  MUST be the first import in every entry point

from typing import TypedDict

import neatlogs
from langgraph.graph import END, START, StateGraph
from neatlogs import span


class RoveState(TypedDict, total=False):
    city: str
    candidates: list
    replans: int


def trigger(state: RoveState) -> dict:
    return {"replans": 0}


def scout(state: RoveState) -> dict:
    return {"candidates": [{"name": "placeholder"}]}


g = StateGraph(RoveState)
g.add_node("trigger", trigger)
g.add_node("scout", scout)
g.add_edge(START, "trigger")
g.add_edge("trigger", "scout")
g.add_edge("scout", END)
graph = g.compile()


@span(kind="WORKFLOW", name="nudge")
def run_once(city: str):
    return graph.invoke({"city": city})


if __name__ == "__main__":
    print(run_once("Goa"))
    neatlogs.flush()
    neatlogs.shutdown()
