import os
import unittest
from unittest.mock import patch

from app import triggers
from app.nodes import scout as scout_module
from app.nodes.scout import ScoutQueries
from app.pipeline import build
from app.replay import load_route, replay

HOME, OFFICE, CAFE = (15.4827, 73.8073), (15.496, 73.837), (15.5005, 73.829)


def stay(day: int, time: str, pos: tuple) -> dict:
    return {"at": f"2026-10-{day:02d}T{time}:00+05:30", "lat": pos[0], "lng": pos[1], "weather": "clear"}


def routine(days: range) -> list:
    out = []
    for d in days:
        out += [stay(d, "09:30", OFFICE), stay(d, "13:00", OFFICE), stay(d, "19:30", HOME), stay(d, "22:00", HOME)]
    return out


def point(day: int, time: str) -> dict:
    return {"now": f"2026-10-{day:02d}T{time}:00+05:30", "lat": OFFICE[0], "lng": OFFICE[1], "weather": "clear"}


class TouchGrassTest(unittest.TestCase):
    def test_fires_after_five_days_between_two_places(self):
        self.assertTrue(triggers.touch_grass(point(9, "13:00"), routine(range(5, 10)), []))

    def test_needs_the_full_five_days(self):
        self.assertFalse(triggers.touch_grass(point(8, "13:00"), routine(range(5, 9)), []))

    def test_a_new_place_in_the_window_keeps_it_quiet(self):
        history = routine(range(5, 10)) + [stay(9, "15:00", CAFE), stay(9, "16:00", CAFE)]
        self.assertFalse(triggers.touch_grass(point(9, "17:00"), history, []))

    def test_passing_through_somewhere_once_is_not_a_visit(self):
        history = routine(range(5, 10)) + [stay(9, "18:30", CAFE)]
        self.assertTrue(triggers.touch_grass(point(9, "22:00"), history, []))

    def test_does_not_fire_again_inside_the_cooldown(self):
        earlier = [{"venue_id": "x", "at": "2026-10-09T13:00:00+05:30", "trigger": "touch_grass"}]
        self.assertFalse(triggers.touch_grass(point(10, "13:00"), routine(range(5, 11)), earlier))

    def test_routine_route_nudges_exactly_once(self):
        with (
            patch.dict(os.environ, {"OO_SCENARIO": "goa", "OO_FIXTURES": "1"}),
            patch.object(scout_module, "plan_queries",
                         return_value=ScoutQueries(place_query="nearby", radius_m=60000)),
        ):
            nudges = replay(load_route("route_routine"), build().invoke, show=False)
        self.assertEqual([(n["at"][:16], n["trigger"]) for n in nudges],
                         [("2026-10-09T13:00", "touch_grass")])


if __name__ == "__main__":
    unittest.main()
