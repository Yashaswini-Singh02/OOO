import app.trace  # noqa: F401  MUST be the first import in every entry point

import sys

import neatlogs
from neatlogs import span

from app import scenarios
from app.pipeline import build
from app.state import RoveState

graph = build()


@span(kind="WORKFLOW", name="nudge")
def run_once(context: RoveState) -> dict:
    return graph.invoke(context)


def report(result: dict) -> None:
    nudge = result.get("nudge")
    if nudge and nudge.get("kind") == "plan":
        for p in nudge["days"]:
            print(f"day {p['day']}: {p['name']} at {p['at']}, wear {p['outfit']}")
        for a in nudge["avoid"]:
            print("avoid:", a)
    else:
        print("nudge:", nudge)
    print("replans:", result.get("replans"))
    for c in result.get("conflicts", []):
        print(f"conflict on day {c['day']}: {c['reason']}")
    for r in result.get("rejected", []):
        print(f"rejected: {r['name']} ({r['reason']})")
    print("warnings:", result.get("warnings", []))


if __name__ == "__main__":
    # python -m app.graph            -> the default Goa context
    # python -m app.graph goa_2day   -> fixtures/goa_2day.json
    report(run_once(scenarios.load(sys.argv[1] if len(sys.argv) > 1 else None)))
    neatlogs.flush()
    neatlogs.shutdown()
