"""Replay a recorded route through the runtime. No tracing import, so tests can use it."""
import json
import os
import tempfile
from typing import Callable

from app import runtime, scenarios, store


def load_route(name: str = "route_goa") -> dict:
    with open(scenarios.FIXTURES / f"{name}.json") as f:
        route = json.load(f)
    profile = scenarios.load(route["profile"])
    route["trip"] = {**route["trip"], **{k: profile[k] for k in ("taste", "budget", "wardrobe")}}
    return route


def replay(route: dict, run: Callable[[dict], dict], show: bool = True) -> list:
    """Step through the route with a fresh database; returns the nudges that were sent."""
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = store.connect(os.path.join(tmp, "sim.db"))
        store.save_spots(conn, route["saved"])
        for point in route["points"]:
            nudge = runtime.on_location(conn, point, route["trip"], run)
            if show:
                mark = f"NUDGE  {nudge['name']}  ({nudge['trigger']})" if nudge else "-"
                print(f"{point['now'][11:16]}  {point['label']:<22} {point['weather']:<6} {mark}")
            if nudge:
                sent.append({**nudge, "at": point["now"]})
        conn.close()
    return sent
