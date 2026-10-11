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
        self.assertIn("Share your location first", self.say(intent=wants))
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
        self.assertIn("3 day(s) left", self.say(intent=Intent(intent="set_trip", city="Goa", days_left=3)))
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
        self.assertIn("Share your location first", self.say(intent=Intent(intent="more")))
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

    def test_anything_else_gets_help(self):
        self.assertIn("I'm Outofoffice", self.say("hello"))


if __name__ == "__main__":
    unittest.main()
