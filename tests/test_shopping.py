import os
import unittest
from unittest.mock import patch

from app import gi, scenarios, triggers
from app.nodes import scout as scout_module
from app.nodes.verifier import refusal
from app.pipeline import build


def run(scenario: str) -> tuple[dict, int]:
    with (
        patch.dict(os.environ, {"OO_SCENARIO": "goa", "OO_FIXTURES": "1"}),
        patch.object(scout_module, "plan_queries") as plan,
    ):
        return build().invoke(scenarios.load(scenario)), plan.call_count


class GiTableTest(unittest.TestCase):
    def test_district_resolves_to_its_region(self):
        self.assertEqual(gi.crafts_for("North Goa")[0]["craft"], "Cashew feni")
        self.assertEqual(gi.crafts_for("", "Goa")[0]["craft"], "Cashew feni")
        self.assertEqual(gi.crafts_for("Pune", "Pune"), [])


class ShoppingTest(unittest.TestCase):
    def test_goa_gets_a_verified_shop_for_a_local_craft(self):
        state, llm_calls = run("shopping_goa")
        self.assertEqual(llm_calls, 0)  # a lookup, not a model call
        nudge = state["nudge"]
        self.assertEqual((nudge["kind"], nudge["craft"]), ("shop", "Cashew feni"))
        self.assertTrue(nudge["gi_registered"])
        self.assertEqual(state["replans"], 0)

    def test_region_without_an_entry_stays_silent(self):
        state, llm_calls = run("shopping_nowhere")
        self.assertEqual(llm_calls, 0)
        self.assertNotIn("nudge", state)
        self.assertEqual(state["candidates"], [])

    def test_verifier_refuses_a_craft_from_somewhere_else(self):
        shop = {"kind": "shop", "craft": "Kanchipuram silk", "exists": True, "open_now": True}
        self.assertEqual(refusal(shop, {"district": "North Goa", "city": "Goa"}),
                         "craft is not tied to this region")
        self.assertIsNone(refusal({**shop, "craft": "Cashew feni"}, {"district": "North Goa", "city": "Goa"}))

    def test_trigger_is_for_trips_in_regions_with_a_craft_and_fires_once(self):
        point = {"now": "2026-10-10T11:00:00+05:30"}
        goa = {"city": "Goa", "district": "North Goa", "days_left": 2}
        self.assertTrue(triggers.speciality(point, goa, []))
        self.assertFalse(triggers.speciality(point, {**goa, "days_left": 0}, []))
        self.assertFalse(triggers.speciality(point, {"city": "Pune", "district": "Pune", "days_left": 2}, []))
        sent = [{"venue_id": "x", "at": "2026-10-09T11:00:00+05:30", "trigger": "speciality"}]
        self.assertFalse(triggers.speciality(point, goa, sent))


if __name__ == "__main__":
    unittest.main()
