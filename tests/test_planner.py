import unittest

from app.nodes.planner import day_conflict, planner

FALLS = {
    "id": "dudhsagar-falls", "name": "Dudhsagar Falls", "activity": "trek",
    "lat": 15.3144, "lng": 74.3143, "duration_h": 9, "start_by": "07:00", "outfit": "trek-tee",
}
FORT = {
    "id": "chapora-fort", "name": "Chapora Fort", "activity": "sunset",
    "lat": 15.606, "lng": 73.7364, "duration_h": 2, "arrive_by": "17:30", "outfit": "linen-shirt",
}
WALK = {
    "id": "fontainhas", "name": "Fontainhas Latin Quarter", "activity": "walk",
    "lat": 15.4989, "lng": 73.8313, "duration_h": 2,
}


class DayConflictTest(unittest.TestCase):
    def test_trek_then_west_coast_sunset_cannot_share_a_day(self):
        conflict = day_conflict([FORT, FALLS])
        self.assertEqual((conflict["a"], conflict["b"]), ("dudhsagar-falls", "chapora-fort"))
        self.assertIn("ends 16:00", conflict["reason"])
        self.assertIn("needed by 17:30", conflict["reason"])

    def test_single_item_and_nearby_untimed_item_are_fine(self):
        self.assertIsNone(day_conflict([FALLS]))
        self.assertIsNone(day_conflict([FORT, WALK]))


class PlannerTest(unittest.TestCase):
    STATE = {"wants": ["trek", "sunset"], "days_left": 2, "ranked": [FORT, FALLS, WALK]}

    def test_no_wants_means_no_plan(self):
        self.assertEqual(planner({"ranked": [FORT]}), {"plan": [], "avoid": []})

    def test_first_pass_puts_everything_on_day_one(self):
        out = planner(self.STATE)
        self.assertEqual(set(out), {"plan", "avoid", "warnings"})
        self.assertEqual([(p["day"], p["id"]) for p in out["plan"]],
                         [(1, "dudhsagar-falls"), (1, "chapora-fort")])
        self.assertEqual(out["avoid"], [])

    def test_known_conflict_splits_the_days_and_names_what_to_avoid(self):
        conflict = {**day_conflict([FALLS, FORT]), "day": 1}
        out = planner({**self.STATE, "conflicts": [conflict]})
        self.assertEqual(
            [(p["day"], p["id"], p["outfit"]) for p in out["plan"]],
            [(1, "dudhsagar-falls", "trek-tee"), (2, "chapora-fort", "linen-shirt")],
        )
        self.assertEqual(len(out["avoid"]), 1)
        self.assertTrue(out["avoid"][0].startswith("Day 1: no sunset booking."))

    def test_one_day_left_cannot_fit_both(self):
        conflict = {**day_conflict([FALLS, FORT]), "day": 1}
        out = planner({**self.STATE, "days_left": 1, "conflicts": [conflict]})
        self.assertEqual([p["id"] for p in out["plan"]], ["dudhsagar-falls"])
        self.assertEqual(out["warnings"], ["no day left for Chapora Fort"])


if __name__ == "__main__":
    unittest.main()
