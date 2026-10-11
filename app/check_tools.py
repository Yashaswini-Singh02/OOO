"""Section 3 check: call all four tools under one WORKFLOW span.

    python -m app.check_tools

The trace should show four TOOL spans as siblings, each with input and output.
"""
import app.trace  # noqa: F401  MUST be the first import in every entry point

import neatlogs
from neatlogs import span

from app import tools


@span(kind="WORKFLOW", name="check_tools")
def check_tools() -> dict:
    places = tools.place_search("sunset viewpoint", lat=15.4989, lng=73.8313)
    return {
        "places": len(places),
        "videos": len(tools.video_search(places[0]["name"])),
        "events": len(tools.event_search("North Goa", "2026-10-10", "2026-10-12")),
        "shops": len(tools.shop_search("Cashew feni", "North Goa")),
    }


if __name__ == "__main__":
    print(check_tools())
    neatlogs.flush()
    neatlogs.shutdown()
