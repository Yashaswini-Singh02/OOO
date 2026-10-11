import unittest
from unittest.mock import patch

import openai

from app.nodes import scout as scout_module
from app.nodes.scout import ScoutQueries, scout

CONTEXT = {
    "city": "Goa",
    "district": "North Goa",
    "lat": 15.4989,
    "lng": 73.8313,
    "now": "2026-10-10T09:00:00+05:30",
    "days_left": 2,
}
QUERIES = ScoutQueries(place_query="sunset viewpoint", radius_m=30000)


class ScoutTest(unittest.TestCase):
    def test_writes_candidates_from_places_and_events(self):
        with patch.object(scout_module, "plan_queries", return_value=QUERIES):
            out = scout({**CONTEXT, "replans": 0})
        self.assertEqual(set(out), {"candidates", "replans", "warnings"})
        kinds = [c["kind"] for c in out["candidates"]]
        self.assertEqual(kinds.count("place"), 5)
        self.assertEqual(kinds.count("event"), 2)
        self.assertEqual(out["replans"], 0)
        self.assertEqual(out["warnings"], [])
        chapora = next(c for c in out["candidates"] if c["id"] == "chapora-fort")
        self.assertEqual(len(chapora["videos"]), 1)

    def test_reentry_increments_replans(self):
        with patch.object(scout_module, "plan_queries", return_value=QUERIES):
            out = scout({**CONTEXT, "replans": 1, "candidates": []})
        self.assertEqual(out["replans"], 2)

    def test_llm_failure_falls_back_and_says_so(self):
        with patch.object(scout_module, "plan_queries", side_effect=openai.OpenAIError("no key")):
            out = scout({**CONTEXT, "replans": 0})
        self.assertEqual(len(out["candidates"]), 7)
        self.assertEqual(out["warnings"], ["scout used the default query: OpenAIError: no key"])


if __name__ == "__main__":
    unittest.main()
