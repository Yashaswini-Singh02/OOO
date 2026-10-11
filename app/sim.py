"""Location simulator: replay a recorded route through the runtime.

    python -m app.sim                 # the Goa walk, fixtures/route_goa.json
    python -m app.sim route_routine   # five days of home and office

The demo runs online, so location is replayed, not sensed. Each point goes through
the same on_location() a live client would call, and each nudge is its own trace.
"""
import app.trace  # noqa: F401  MUST be the first import in every entry point

import sys

import neatlogs

from app.graph import run_once
from app.replay import load_route, replay

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "route_goa"
    nudges = replay(load_route(name), run_once)
    print(f"\n{len(nudges)} nudges from {name}")
    neatlogs.flush()
    neatlogs.shutdown()
