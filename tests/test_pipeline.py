import os
import unittest
from unittest.mock import patch

from app.nodes import scout as scout_module
from app.nodes.scout import ScoutQueries
from app import scenarios
from app.pipeline import build

CONTEXT = {
    "city": "Goa",
    "district": "North Goa",
    "lat": 15.4989,
    "lng": 73.8313,
    "now": "2026-10-10T09:00:00+05:30",
    "days_left": 2,
    # profile passed in, so trigger does not open the real store
    "taste": {"tags": {"sunset": 0.8}, "ratings": []},
    "budget": {"alloc": {}, "spent": {}},
    "wardrobe": [],
}
QUERIES = ScoutQueries(place_query="sunset viewpoint", radius_m=30000)


def run(scenario: str, context: dict = CONTEXT) -> tuple[dict, int]:
    """Invoke the graph offline; returns the final state and how many times scout planned."""
    with (
        patch.dict(os.environ, {"OO_SCENARIO": scenario}),
        patch.object(scout_module, "plan_queries", return_value=QUERIES) as plan,
    ):
        return build().invoke(context), plan.call_count


class PipelineTest(unittest.TestCase):
    def test_verified_candidate_reaches_the_feed(self):
        state, scout_runs = run("goa")
        self.assertEqual(scout_runs, 1)
        self.assertEqual(state["replans"], 0)
        self.assertEqual(state["nudge"]["name"], "Chapora Fort")
        # the one closed event was refused on the way
        self.assertEqual([r["id"] for r in state["rejected"]], ["arpora-night-market"])

    def test_all_refused_replans_twice_then_stays_silent(self):
        state, scout_runs = run("closed")
        self.assertEqual(scout_runs, 3)  # first pass + 2 replans
        self.assertEqual(state["replans"], 2)
        self.assertNotIn("nudge", state)
        self.assertEqual(state["verified"], [])
        self.assertEqual(
            {r["id"]: r["reason"] for r in state["rejected"]},
            {
                "chapora-fort": "closed right now",
                "hidden-lagoon-viewpoint": "venue not found",
                "cabo-de-rama": "not where the reel claims",
                "arpora-night-market": "closed right now",
            },
        )

    def test_goa_conflict_is_caught_and_split_across_two_days(self):
        state, scout_runs = run("goa", scenarios.load("goa_2day"))
        self.assertEqual(scout_runs, 2)  # first pass + the replan that resolves the conflict
        self.assertEqual(state["replans"], 1)
        self.assertEqual(len(state["conflicts"]), 1)
        self.assertEqual(
            [(p["day"], p["name"], p["outfit"]) for p in state["nudge"]["days"]],
            [(1, "Dudhsagar Falls", "trek-tee"), (2, "Chapora Fort", "linen-shirt")],
        )
        self.assertEqual(len(state["nudge"]["avoid"]), 1)


if __name__ == "__main__":
    unittest.main()
