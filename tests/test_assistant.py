import os
import tempfile
import unittest
from unittest.mock import patch

from app import assistant, store
from app.assistant import Intent
from app.nodes import scout as scout_module
from app.nodes.scout import ScoutQueries
from app.pipeline import build

NOW = "2026-10-10T10:00:00+05:30"
FONTAINHAS = {"lat": 15.4989, "lng": 73.8313}


class AssistantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "user.db"))
        self.graph = build()
        patches = [
            patch.dict(os.environ, {"OO_SCENARIO": "goa", "OO_FIXTURES": "1"}),
            patch.object(scout_module, "plan_queries",
                         return_value=ScoutQueries(place_query="nearby", radius_m=60000)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def say(self, text: str = "", intent: Intent | None = None, **extra) -> str:
        return assistant.handle(
            self.conn, {"text": text, "lat": None, "lng": None, "now": NOW, **extra},
            self.graph.invoke, understand=lambda *_: intent or Intent(intent="help"),
        )

    def test_shared_location_gets_one_verified_nudge_and_is_not_repeated(self):
        first = self.say(**FONTAINHAS)
        self.assertIn("Fontainhas Latin Quarter", first)
        self.assertIn("open now", first)
        second = self.say(**FONTAINHAS)
        self.assertNotIn("Fontainhas Latin Quarter", second)

    def test_budget_is_set_spent_and_reported(self):
        reply = self.say(intent=Intent(intent="set_budget", amount=10000, period="trip"))
        self.assertIn("₹10,000 left this trip", reply)
        self.assertIn("activities: ₹0 of ₹3500", reply)
        reply = self.say(intent=Intent(intent="spend", amount=850, category="food"))
        self.assertIn("Logged ₹850 on food", reply)
        self.assertIn("food: ₹850 of ₹3000", reply)
        self.assertIn("₹9,150 left", self.say(intent=Intent(intent="budget_status")))

    def test_plan_needs_a_location_then_splits_the_goa_conflict(self):
        wants = Intent(intent="plan", activities=["trek", "sunset"])
        self.assertIn("or share your location", self.say(intent=wants))
        store.save_wardrobe(self.conn, [
            {"id": "linen-shirt", "colour": "white", "formality": "smart", "fabric": "linen"},
            {"id": "trek-tee", "colour": "black", "formality": "casual", "fabric": "quick-dry"},
        ])
        store.save_taste(self.conn, {"tags": {"sunset": 0.8, "photography": 0.6, "trek": 0.5}, "ratings": []})
        self.say(**FONTAINHAS)
        reply = self.say(intent=wants)
        self.assertIn("Day 1: Dudhsagar Falls at 07:00, wear your trek-tee.", reply)
        self.assertIn("Day 2:", reply)
        self.assertIn("Avoid:", reply)

    def test_rating_the_last_nudge_moves_the_taste_profile(self):
        self.assertIn("nothing to rate", self.say(intent=Intent(intent="rate", rating=5)))
        self.say(**FONTAINHAS)
        self.assertIn("Noted 5/5 for Fontainhas Latin Quarter", self.say(intent=Intent(intent="rate", rating=5)))
        self.assertEqual(store.load_taste(self.conn)["tags"]["heritage"], 0.2)

    def test_trip_and_shop(self):
        self.assertIn("you're in Goa for 3 more days", self.say(intent=Intent(intent="set_trip", city="Goa", days_left=3)))
        self.say(**FONTAINHAS)
        self.assertIn("Cashew feni (GI-registered)", self.say(intent=Intent(intent="shop")))

    def test_slow_steps_send_a_status_first_and_quick_ones_do_not(self):
        sent = []
        ask = lambda intent=None, **extra: assistant.handle(
            self.conn, {"text": "", "lat": None, "lng": None, "now": NOW, **extra},
            self.graph.invoke, understand=lambda *_: intent or Intent(intent="help"), notify=sent.append,
        )
        ask(**FONTAINHAS)
        self.assertEqual(sent, ["On it. Checking what's open near you..."])
        ask(Intent(intent="shop"))
        self.assertEqual(len(sent), 2)
        ask(Intent(intent="budget_status"))
        ask(Intent(intent="spend", amount=100, category="food"))
        self.assertEqual(len(sent), 2)  # instant answers need no "on it"

    def test_suggest_another_gives_a_different_place_each_time(self):
        self.assertIn("or share your location", self.say(intent=Intent(intent="more")))
        first = self.say(**FONTAINHAS)
        second = self.say(intent=Intent(intent="more"))
        third = self.say(intent=Intent(intent="more"))
        titles = [r.title for r in (first, second, third)]
        self.assertEqual(len(set(titles)), 3, titles)

    def test_a_stated_dislike_is_remembered_and_applied_at_once(self):
        first = self.say(**FONTAINHAS)
        self.assertEqual(first.title, "Fontainhas Latin Quarter")
        sent = []
        reply = assistant.handle(
            self.conn, {"text": "i dont like museums", "lat": None, "lng": None, "now": NOW},
            self.graph.invoke, understand=lambda *_: Intent(intent="preference", dislikes=["museum"]),
            notify=sent.append,
        )
        self.assertEqual(sent, ["Got it: no more museum suggestions. I'll remember that. Finding you something else..."])
        self.assertNotIn("Museum", reply.title)
        self.assertEqual(store.get_kv(self.conn, "dislikes"), ["museum"])
        self.assertEqual(store.load_taste(self.conn)["tags"]["museum"], -1.0)
        # it stays excluded on later requests too
        titles = [self.say(intent=Intent(intent="more")).title for _ in range(3)]
        self.assertFalse(any("Museum" in t for t in titles), titles)

    def test_asking_for_a_kind_of_place_is_a_one_time_request_not_a_taste(self):
        self.say(**FONTAINHAS)
        sent = []
        reply = assistant.handle(
            self.conn, {"text": "somewhere to eat, not buy or roam around", "lat": None, "lng": None, "now": NOW},
            self.graph.invoke, notify=sent.append,
            understand=lambda *_: Intent(intent="find", likes=["food"], dislikes=["shopping", "walk"]),
        )
        self.assertEqual(sent, ["On it. Looking for food near you..."])
        self.assertEqual(reply.title, "Fixture Fish Thali House")
        self.assertIn("it is what you asked for", reply)
        # nothing was written to the profile
        self.assertEqual(store.get_kv(self.conn, "dislikes", []), [])
        self.assertEqual(store.load_taste(self.conn)["tags"], {})

    def test_forget_clears_what_was_learned(self):
        self.say(intent=Intent(intent="preference", likes=["sunset"], dislikes=["park"]))
        self.assertIn("forgotten", self.say("/forget"))
        self.assertEqual(store.get_kv(self.conn, "dislikes"), [])
        self.assertEqual(store.load_taste(self.conn)["tags"], {})

    def test_likes_are_learned_and_unknown_words_are_ignored(self):
        reply = self.say(intent=Intent(intent="preference", likes=["sunset", "discos"]))
        self.assertEqual(reply, "Got it: more sunset. I'll remember that.")
        self.assertEqual(store.load_taste(self.conn)["tags"], {"sunset": 0.8})

    def test_the_model_is_told_about_the_last_suggestion(self):
        self.say(**FONTAINHAS)
        seen = []
        assistant.handle(
            self.conn, {"text": "not this kind of place", "lat": None, "lng": None, "now": NOW},
            self.graph.invoke, understand=lambda text, context: seen.append(context) or Intent(intent="help"),
        )
        self.assertIn("Last suggestion: Fontainhas Latin Quarter (kind: heritage, walk, photography, cafe)", seen[0])

    def test_planning_a_trip_to_a_named_place_needs_no_location(self):
        sent = []
        reply = assistant.handle(
            self.conn, {"text": "plan 2 days in goa", "lat": None, "lng": None, "now": NOW},
            self.graph.invoke, notify=sent.append,
            understand=lambda *_: Intent(intent="trip_plan", city="Goa", days_left=2),
        )
        self.assertEqual(sent, ["On it. Putting together 2 days in Goa..."])
        self.assertEqual(reply.title, "Goa in 2 days")
        self.assertIn("Day 1:", reply)
        self.assertIn("Day 2:", reply)
        self.assertIn("🗓 <b>Day 2</b>", reply.html)
        # a place that is shut this minute still belongs in a plan for another day
        self.assertIn("Saturday Night Market, Arpora", reply)
        self.assertEqual(store.get_kv(self.conn, "trip"), {"city": "Goa", "district": "Goa", "days_left": 2})
        self.assertEqual(store.load_nudges(self.conn), [])  # a plan is not a nudge

    def test_a_place_in_the_plan_can_be_opened_and_then_asked_about(self):
        plan = self.say(intent=Intent(intent="trip_plan", city="Goa", days_left=2))
        self.assertTrue(plan.choices)
        self.assertIn("Want the full story on any of these?", plan)
        first = plan.choices[0][0]
        card = self.say(intent=Intent(intent="about", index=0))
        self.assertEqual(card.title, first)
        self.assertIn("Want to know more about this place?", card)
        by_name = self.say(intent=Intent(intent="about", place="dudhsagar"))
        self.assertEqual(by_name.title, "Dudhsagar Falls")
        # the follow-up is about whichever place was opened last
        self.assertIn("everything I could find on Dudhsagar Falls", self.say(intent=Intent(intent="info")))
        self.assertIn("Which place?", self.say(intent=Intent(intent="about", index=99)))

    def test_trip_plan_asks_when_the_place_is_missing_or_unknown(self):
        self.assertIn("Where to?", self.say(intent=Intent(intent="trip_plan")))
        self.assertIn("I couldn't find Atlantis", self.say(intent=Intent(intent="trip_plan", city="Atlantis")))

    def test_a_request_can_name_a_place_instead_of_sharing_a_location(self):
        reply = self.say(intent=Intent(intent="find", likes=["food"], city="Panaji"))
        self.assertEqual(reply.title, "Fixture Fish Thali House")

    def test_anything_else_gets_help(self):
        self.assertIn("I'm Outofoffice", self.say("hello"))


if __name__ == "__main__":
    unittest.main()
