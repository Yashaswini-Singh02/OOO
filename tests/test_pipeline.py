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
QUERIES = ScoutQueries(place_query="sunset viewpoint", radius_m=70000)


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
        self.assertEqual(scout_runs, 1)  # the model plans once; replans are worked out without it
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

    def test_a_replan_reaches_further_out_instead_of_repeating_the_search(self):
        near = ScoutQueries(place_query="sunset spot", radius_m=15000, categories=["beach", "viewpoint"])
        searched = []
        real = scout_module.tools.place_search

        def search(query, lat, lng, radius, categories):
            searched.append((radius, categories))
            return real(query, lat, lng, radius, categories)

        with patch.dict(os.environ, {"OO_SCENARIO": "closed"}), \
                patch.object(scout_module, "plan_queries", return_value=near) as plan, \
                patch.object(scout_module.tools, "place_search", side_effect=search):
            state = build().invoke(CONTEXT)
        self.assertEqual(plan.call_count, 1)
        self.assertEqual([r for r, _ in searched], [15000, 37500, 60000])
        self.assertEqual(searched[1][1], ["viewpoint", "beach", "waterfall", "fort", "museum"])  # what was tried, plus new ones
        # the first pass could only see two venues; the wider replans found and checked the other two
        self.assertEqual([r["id"] for r in state["rejected"]],
                         ["hidden-lagoon-viewpoint", "arpora-night-market", "chapora-fort", "cabo-de-rama"])
        self.assertEqual(state["replans"], 2)
        self.assertNotIn("nudge", state)

    def test_a_replan_recovers_when_a_good_place_is_just_out_of_range(self):
        near = ScoutQueries(place_query="heritage walk", radius_m=300, categories=["attraction"])
        context = {**CONTEXT, "taste": {"tags": {}, "ratings": []}, "max_radius_m": 1000,
                   "recent": ["fontainhas", "fontainhas-heritage-walk"]}
        with patch.dict(os.environ, {"OO_SCENARIO": "goa"}), patch.object(scout_module, "plan_queries", return_value=near):
            state = build().invoke(context)
        # nothing within 300 m; the replan looks to 650 m and finds the thali place at 436 m
        self.assertEqual(state["replans"], 1)
        self.assertEqual(state["nudge"]["id"], "panaji-fish-thali")

    def test_a_replan_with_nothing_left_to_change_ends_the_run_at_once(self):
        context = {**CONTEXT, "only": ["sunset"], "max_radius_m": 5000}
        searched = []
        real = scout_module.tools.place_search
        with patch.dict(os.environ, {"OO_SCENARIO": "closed"}), \
                patch.object(scout_module.tools, "place_search",
                             side_effect=lambda *a: searched.append(a) or real(*a)):
            state = build().invoke(context)
        # the kind is fixed by the request and the radius is at its limit: one search, then stop
        self.assertEqual(len(searched), 1)
        self.assertTrue(state["exhausted"])
        self.assertEqual(state["replans"], 1)
        self.assertNotIn("nudge", state)

    def test_goa_conflict_is_caught_and_split_across_two_days(self):
        state, scout_runs = run("goa", scenarios.load("goa_2day"))
        self.assertEqual(scout_runs, 1)  # the replan that resolves the conflict reuses the search
        self.assertEqual(state["replans"], 1)
        self.assertEqual(len(state["conflicts"]), 1)
        self.assertEqual(
            [(p["day"], p["name"], p["outfit"]) for p in state["nudge"]["days"]],
            [(1, "Dudhsagar Falls", "trek-tee"), (2, "Chapora Fort", "linen-shirt")],
        )
        self.assertEqual(len(state["nudge"]["avoid"]), 1)


if __name__ == "__main__":
    unittest.main()
