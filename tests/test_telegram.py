import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from app import assistant, store
from app.assistant import Intent
from app.nodes import scout as scout_module
from app.nodes.scout import ScoutQueries
from app.pipeline import build


def load_telegram():
    """Import app.telegram without starting tracing: stub the two modules that would."""
    stubs = {"app.trace": types.ModuleType("app.trace"), "app.graph": types.ModuleType("app.graph")}
    stubs["app.graph"].run_once = lambda context: {}
    with patch.dict(sys.modules, stubs):
        sys.modules.pop("app.telegram", None)
        module = importlib.import_module("app.telegram")
    sys.modules.pop("app.telegram", None)
    return module


CHAT = {"id": 42}
PLACE = {"latitude": 15.4989, "longitude": 73.8313}


class TelegramUpdateTest(unittest.TestCase):
    tg = load_telegram()

    def test_text_pin_and_live_location_are_told_apart(self):
        text = self.tg.incoming({"message": {"chat": CHAT, "date": 1791600000, "text": "hi"}})
        self.assertEqual((text["kind"], text["text"], text["chat"]), ("text", "hi", 42))
        pin = self.tg.incoming({"message": {"chat": CHAT, "date": 1791600000, "location": PLACE}})
        self.assertEqual((pin["kind"], pin["lat"]), ("pin", 15.4989))
        first = self.tg.incoming(
            {"message": {"chat": CHAT, "date": 1791600000, "location": {**PLACE, "live_period": 3600}}}
        )
        self.assertEqual(first["kind"], "live")
        moved = self.tg.incoming(
            {"edited_message": {"chat": CHAT, "date": 1791600000, "edit_date": 1791600600, "location": PLACE}}
        )
        self.assertEqual(moved["kind"], "live")
        self.assertGreater(moved["now"], first["now"])  # the edit time, not the original send time

    def test_buttons_carry_their_intent_without_a_model_call(self):
        tap = self.tg.incoming({"callback_query": {"id": "q1", "data": "rate:5", "message": {"chat": CHAT}}})
        self.assertEqual((tap["kind"], tap["intent"].intent, tap["intent"].rating), ("button", "rate", 5))
        self.assertIsNone(self.tg.incoming({"callback_query": {"id": "q1", "data": "nope", "message": {"chat": CHAT}}}))

    def test_a_suggestion_renders_as_a_card_with_buttons(self):
        from app import messages
        card = messages.nudge_text({
            "name": "Fort <Aguada>", "kind": "place", "what": "Historical landmark", "address": "Candolim",
            "distance_m": 6211, "hours_verified": True, "rating": 4.4, "rating_count": 51234,
            "why": ["people rate it 4.4 out of 5"], "ruled_out": "2 closed right now",
            "source": "https://maps.google.com/?cid=1",
            "videos": [{"url": "https://youtu.be/a", "source": "youtube"},
                       {"url": "https://youtu.be/b", "source": "youtube"},
                       {"url": "https://instagram.com/reel/c", "source": "instagram"}],
        })
        out = self.tg.render(card)
        self.assertTrue(out["text"].startswith("<b>Fort &lt;Aguada&gt;</b>\n"))  # escaped, bold title
        self.assertIn("Historical landmark · Candolim", out["text"])
        self.assertIn("6.2 km away · open now · rated 4.4 (51,234 reviews)", out["text"])
        self.assertIn("Why this: people rate it 4.4 out of 5.", out["text"])
        self.assertIn("Ruled out on the way: 2 closed right now.", out["text"])
        self.assertNotIn("http", out["text"])  # links are buttons, not raw URLs
        links, actions = out["reply_markup"]["inline_keyboard"]
        self.assertEqual([b["text"] for b in links], ["Open in Maps", "Watch video", "See reel"])
        self.assertEqual([b["text"] for b in actions], ["Loved it", "Not for me", "Save for later"])

    def test_welcome_offers_a_share_location_button(self):
        from app import messages
        keyboard = self.tg.render(messages.WELCOME)["reply_markup"]["keyboard"]
        self.assertTrue(keyboard[0][0]["request_location"])

    def test_other_updates_are_ignored(self):
        self.assertIsNone(self.tg.incoming({"message": {"chat": CHAT, "date": 1, "photo": []}}))
        self.assertIsNone(self.tg.incoming({"poll": {}}))

    def test_allow_list(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_CHATS": "42, 7"}):
            self.assertTrue(self.tg.allowed(42))
            self.assertFalse(self.tg.allowed(99))
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_CHATS": ""}):
            self.assertTrue(self.tg.allowed(99))


class LiveLocationTest(unittest.TestCase):
    def test_live_updates_nudge_only_when_a_trigger_fires_and_saved_spots_work(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"OO_SCENARIO": "goa", "OO_FIXTURES": "1"}), \
                patch.object(scout_module, "plan_queries",
                             return_value=ScoutQueries(place_query="nearby", radius_m=60000)):
            conn = store.connect(os.path.join(tmp, "user.db"))
            run = build().invoke
            say = lambda intent, now: assistant.handle(
                conn, {"text": "", "lat": None, "lng": None, "now": now}, run, understand=lambda _: intent)

            # Friday: a pin near Fontainhas, then "save that place"
            pin = assistant.handle(conn, {"text": "", "lat": 15.4989, "lng": 73.8313,
                                          "now": "2026-10-09T10:00:00+05:30"}, run)
            self.assertIn("Fontainhas Latin Quarter", pin)
            self.assertIn("Saved Fontainhas Latin Quarter", say(Intent(intent="save"), "2026-10-09T10:01:00+05:30"))
            say(Intent(intent="set_trip", city="Pune", days_left=0), "2026-10-09T10:02:00+05:30")

            # Sunday, sharing live location: far from anything saved -> silence
            quiet = assistant.on_live_location(conn, 15.453, 73.804, "2026-10-11T10:00:00+05:30", run)
            self.assertIsNone(quiet)
            # walking back near the saved spot -> the bot speaks up on its own
            near = assistant.on_live_location(conn, 15.4995, 73.832, "2026-10-11T11:00:00+05:30", run)
            self.assertIsNotNone(near)
            # still there a few minutes later -> throttled
            again = assistant.on_live_location(conn, 15.4996, 73.8321, "2026-10-11T11:05:00+05:30", run)
            self.assertIsNone(again)
            conn.close()


if __name__ == "__main__":
    unittest.main()
