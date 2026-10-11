"""Location simulator: replay a recorded route through the runtime.

    python -m app.sim

The demo runs online, so location is replayed, not sensed. Each point goes through
the same on_location() a live client would call, and each nudge is its own trace.
"""
import app.trace  # noqa: F401  MUST be the first import in every entry point

import neatlogs

from app.graph import run_once
from app.replay import load_route, replay

if __name__ == "__main__":
    nudges = replay(load_route(), run_once)
    print(f"\n{len(nudges)} nudges from one simulated walk")
    neatlogs.flush()
    neatlogs.shutdown()
