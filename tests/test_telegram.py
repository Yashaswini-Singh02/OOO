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
            "why": ["people rate it 4.4 out of 5"], "about": "A 17th-century fort on the coast.",
            "reviews_say": "the lighthouse view is worth the climb.",
            "source": "https://maps.google.com/?cid=1",
            "videos": [{"url": "https://youtu.be/a", "source": "youtube"},
                       {"url": "https://youtu.be/b", "source": "youtube"},
                       {"url": "https://instagram.com/reel/c", "source": "instagram"}],
        })
        out = self.tg.render(card)
        self.assertTrue(out["text"].startswith("<b>Fort &lt;Aguada&gt;</b>\n<i>Historical landmark</i>\n"))
        self.assertIn("A 17th-century fort on the coast.", out["text"])
        self.assertIn("\n📍 6.2 km away\n🟢 Open now\n⭐ 4.4 · 51,234 reviews\n", out["text"])  # one fact per line
        self.assertIn("💡 <b>Why this:</b> people rate it 4.4 out of 5.", out["text"])
        self.assertNotIn("Candolim", out["text"])  # no address: the Maps button covers it
        self.assertNotIn("Ruled out", out["text"])
        self.assertNotIn("http", out["text"])  # links are buttons, not raw URLs
        links, feedback, more, info = out["reply_markup"]["inline_keyboard"]
        self.assertEqual([b["text"] for b in info], ["ℹ️ Tell me more"])
        self.assertIn("Want to know more about this place?", out["text"])
        self.assertEqual([b["text"] for b in links], ["🗺 Maps", "▶️ Video", "🎬 Reel"])
        both = messages.nudge_text({"name": "X", "kind": "place", "hours_verified": True, "source": "https://m",
                                    "videos": [{"url": "https://instagram.com/reel/a", "source": "instagram"},
                                               {"url": "https://instagram.com/acct", "source": "instagram"}]})
        self.assertEqual([label for label, _ in both.links], ["🗺 Maps", "🎬 Reel"])  # one Instagram button
        self.assertEqual([b["text"] for b in feedback], ["❤️ Loved it", "👎 Not for me"])
        self.assertEqual([b["text"] for b in more], ["🔖 Save for later", "🔄 Suggest another"])
        # the plain-text form, for channels without formatting, carries the same facts
        self.assertIn("6.2 km away · open now · rated 4.4 (51,234 reviews)", str(card))

    def test_a_picture_goes_out_with_the_card_and_never_blocks_it(self):
        from app import messages
        nudge = {"name": "Fort Aguada", "kind": "place", "hours_verified": True, "why": [],
                 "photo": "https://lh3.googleusercontent.com/x", "photo_by": "A. Visitor", "videos": []}
        card = messages.nudge_text(nudge)
        self.assertIn("📷 A. Visitor · Google", card.html)

        sent = []
        with patch.object(self.tg, "call", side_effect=lambda method, **kw: sent.append((method, kw))):
            self.tg.deliver(42, card)
        self.assertEqual([m for m, _ in sent], ["sendPhoto"])  # one message: photo with caption and buttons
        self.assertEqual(sent[0][1]["photo"], "https://lh3.googleusercontent.com/x")
        self.assertIn("<b>Fort Aguada</b>", sent[0][1]["caption"])
        self.assertIn("inline_keyboard", sent[0][1]["reply_markup"])

        long_card = messages.nudge_text({**nudge, "about": "x" * 1100})
        sent.clear()
        with patch.object(self.tg, "call", side_effect=lambda method, **kw: sent.append((method, kw))):
            self.tg.deliver(42, long_card)
        self.assertEqual([m for m, _ in sent], ["sendPhoto", "sendMessage"])  # too long for a caption

        def failing(method, **kw):
            sent.append((method, kw))
            if method == "sendPhoto":
                raise RuntimeError("telegram sendPhoto failed: wrong file")
        sent.clear()
        with patch.object(self.tg, "call", side_effect=failing):
            self.tg.deliver(42, card)
        self.assertEqual([m for m, _ in sent], ["sendPhoto", "sendMessage"])  # the text still arrives

    def test_a_trip_plan_reads_like_a_friend_wrote_it_and_offers_each_place(self):
        from app import messages
        place = lambda i, day, name: {"id": i, "day": day, "name": name, "what": "Castle", "rating": 4.5,
                                      "rating_count": 9000, "source": f"https://maps/{i}"}
        plan = messages.nudge_text({
            "kind": "itinerary", "city": "Udaipur",
            "days": [place("a", 1, "City Palace"), place("b", 2, "Monsoon Palace")],
            "spotlight": {"id": "c", "name": "Ambrai Ghat", "label": "Hidden gem", "rating": 4.7, "rating_count": 800,
                          "source": "https://maps/c", "videos": [{"url": "https://instagram.com/reel/x", "source": "instagram"}]},
            "story": {"intro": "Two days of lakes and palaces.", "themes": {1: "Old city on foot"},
                      "hooks": {"a": "The palace the city grew around."}, "spotlight_line": "Go at dusk."},
        })
        out = self.tg.render(plan)
        self.assertIn("Two days of lakes and palaces.", out["text"])
        self.assertIn("🗓 <b>Day 1 · Old city on foot</b>", out["text"])
        self.assertIn("<i>The palace the city grew around.</i>", out["text"])
        self.assertIn("✨ <b>Hidden gem:</b>", out["text"])
        self.assertIn("Want the full story on any of these?", out["text"])
        rows = out["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows[0][0]["text"], "🎬 Reel: Ambrai Ghat")
        taps = [(b["text"], b["callback_data"]) for row in rows[1:] for b in row]
        self.assertEqual(taps, [("City Palace", "about:0"), ("Monsoon Palace", "about:1"), ("Ambrai Ghat", "about:2")])
        tap = self.tg.incoming({"callback_query": {"id": "q", "data": "about:2", "message": {"chat": CHAT}}})
        self.assertEqual((tap["intent"].intent, tap["intent"].index), ("about", 2))

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
                conn, {"text": "", "lat": None, "lng": None, "now": now}, run, understand=lambda *_: intent)

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
