import app.trace  # noqa: F401  MUST be the first import in every entry point

import neatlogs
from neatlogs import span

from app.pipeline import build
from app.state import RoveState

graph = build()


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
    print("nudge:", result.get("nudge"))
    print("replans:", result.get("replans"))
    for r in result.get("rejected", []):
        print(f"rejected: {r['name']} ({r['reason']})")
    print("warnings:", result.get("warnings", []))
    neatlogs.flush()
    neatlogs.shutdown()
