import os
import unittest
from unittest.mock import patch

from app.nodes import scout as scout_module
from app.nodes.scout import ScoutQueries
from app.pipeline import build
from app.replay import load_route, replay

QUERIES = ScoutQueries(place_query="something nearby", radius_m=60000)


class RouteReplayTest(unittest.TestCase):
    def test_one_walk_gives_a_handful_of_nudges_not_a_wall(self):
        graph = build()
        with (
            patch.dict(os.environ, {"OO_SCENARIO": "goa", "OO_FIXTURES": "1"}),
            patch.object(scout_module, "plan_queries", return_value=QUERIES) as plan,
        ):
            nudges = replay(load_route(), graph.invoke, show=False)

        self.assertEqual(
            [(n["at"][11:16], n["name"], n["trigger"]) for n in nudges],
            [
                ("09:00", "Fontainhas Latin Quarter", "near_saved_spot"),
                ("13:10", "Museum of Goa", "rain_starting"),
                ("17:10", "Chapora Fort", "near_saved_spot"),
                ("18:10", "Saturday Night Market, Arpora", "place_just_opened"),
            ],
        )
        # 15 location updates, but the graph (and its LLM call) ran only when a trigger fired
        self.assertEqual(plan.call_count, 4)
        # never the same venue twice
        self.assertEqual(len({n["id"] for n in nudges}), len(nudges))


if __name__ == "__main__":
    unittest.main()
