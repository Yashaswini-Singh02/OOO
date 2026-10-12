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
        self.assertIn("Day 1: Dudhsagar Falls at 07:00, wear your trek tee.", reply)
        self.assertIn("Day 2:", reply)
        self.assertIn("Avoid:", reply)

    def test_rating_the_last_nudge_moves_the_taste_profile(self):
        self.assertIn("nothing to rate", self.say(intent=Intent(intent="rate", rating=5)))
        self.say(**FONTAINHAS)
        self.assertIn("Noted 5/5 for Fontainhas Latin Quarter", self.say(intent=Intent(intent="rate", rating=5)))
        self.assertEqual(store.load_taste(self.conn)["tags"]["heritage"], 0.2)

    def test_trip_and_shop(self):
        reply = self.say(intent=Intent(intent="set_trip", city="Goa", days_left=3))
        self.assertIn("you're in Goa for 3 more days", reply)
        # it offers the next steps, including the ones nothing is known about yet
        self.assertIn('"plan 3 days in Goa"', reply)
        self.assertIn("tell me your taste", reply)
        self.assertTrue(reply.ask_location)
        self.say(intent=Intent(intent="preference", likes=["sunset"]))
        self.assertNotIn("tell me your taste", self.say(intent=Intent(intent="set_trip", city="Goa", days_left=3)))
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
        self.assertEqual(sent, ["Got it: no more museums. I'll remember that. Finding you something else..."])
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
        self.assertIn("It's what you asked for.", reply)
        # nothing was written to the profile
        self.assertEqual(store.get_kv(self.conn, "dislikes", []), [])
        self.assertEqual(store.load_taste(self.conn)["tags"], {})

    def test_one_message_can_say_several_things(self):
        sent = []
        reply = assistant.handle(
            self.conn, {"text": "I'm in Goa for 3 more days, budget 10000, I love sunsets", "lat": None, "lng": None, "now": NOW},
            self.graph.invoke, notify=sent.append,
            understand=lambda *_: [
                Intent(intent="preference", likes=["sunset"]),
                Intent(intent="set_trip", city="Goa", days_left=3),
                Intent(intent="set_budget", amount=10000),
            ],
        )
        # the facts are confirmed in one short message, then the taste is acted on in that town
        self.assertEqual(sent[0], "Got it: you're in Goa for 3 more days.\nBudget set: ₹10,000 for the trip.")
        self.assertEqual(sent[1], "Got it: more sunsets. I'll remember that. Let me find you a few...")
        # a few verified sunset places to choose between, each one openable, then a question
        self.assertTrue(1 <= len(reply.choices) <= 3)
        self.assertTrue(reply.startswith("1. "))
        self.assertIn("more sunsets?", reply.split("\n")[-1])
        name, key = reply.choices[0]
        self.assertEqual(self.say(intent=Intent(intent="about", index=key)).title, name)
        self.assertEqual(self.say(intent=Intent(intent="about", place=name.lower())).title, name)
        # what was offered is not offered again when they ask for more
        again = assistant.handle(self.conn, {"text": "more sunsets", "lat": None, "lng": None, "now": NOW},
                                 self.graph.invoke, understand=lambda *_: Intent(intent="preference", likes=["sunset"]))
        self.assertFalse({n for n, _ in reply.choices} & {n for n, _ in again.choices})
        self.assertEqual(store.get_kv(self.conn, "trip")["days_left"], 3)
        self.assertEqual(store.load_taste(self.conn)["tags"], {"sunset": 0.8})
        self.assertEqual(sum(store.load_budget(self.conn)["alloc"].values()), 10000)

    def test_things_outside_the_fixed_words_are_remembered_in_their_own_words(self):
        reply = self.say(intent=Intent(intent="preference", dislikes=["museum"], notes=["vegetarian", "avoids crowds"]))
        self.assertEqual(reply.split(". I'll")[0], 'Got it: no more museums, noted that you said "vegetarian, avoids crowds"')
        self.say(intent=Intent(intent="preference", notes=["Vegetarian"]))  # said again: not kept twice
        self.assertEqual(store.get_kv(self.conn, "notes"), ["vegetarian", "avoids crowds"])
        self.assertIn("You told me: vegetarian; avoids crowds", self.say(intent=Intent(intent="profile")))
        # they reach the graph, and the model is reminded of them on the next message
        self.assertEqual(assistant._context(self.conn, NOW)["notes"], ["vegetarian", "avoids crowds"])
        self.assertIn("Already noted about them: vegetarian; avoids crowds", assistant._hint(self.conn))
        self.say("/forget")
        self.assertEqual(store.get_kv(self.conn, "notes", []), [])

    def test_small_talk_gets_a_reply_in_the_buddys_own_words(self):
        from unittest.mock import patch
        with patch.object(assistant.tools, "chat_reply", return_value="October is lovely there.") as chat:
            reply = self.say(text="is october a good time?", intent=Intent(intent="chat"))
        self.assertEqual(reply, "October is lovely there.")
        self.assertEqual(chat.call_args.args[0], "is october a good time?")
        # with no model to hand, it falls back to saying what it can do
        self.assertEqual(self.say(intent=Intent(intent="chat")), assistant.HELP)

    def test_confirmations_are_said_in_the_buddys_voice_when_it_can_be_written(self):
        from unittest.mock import patch
        steps = [Intent(intent="set_trip", city="Goa", days_left=3), Intent(intent="preference", likes=["sunset"])]
        text = "I'm in Goa for 3 more days, I love sunsets"
        sent = []
        with patch.object(assistant.tools, "react", return_value="Goa for three days and you LOVE sunsets?") as react:
            reply = assistant.handle(self.conn, {"text": text, "lat": None, "lng": None, "now": NOW},
                                     self.graph.invoke, notify=sent.append, understand=lambda *_: steps)
        # one message in its own words instead of two confirmations, then the card
        self.assertEqual(sent, ["Goa for three days and you LOVE sunsets?"])
        self.assertEqual(react.call_args.args[1], [
            "Got it: you're in Goa for 3 more days.",
            "Got it: more sunsets. I'll remember that. Let me find you a few...",
        ])
        self.assertTrue(reply.choices)

        # a message that only tells us something: its options stay, the "Got it" line goes
        sent = []
        with patch.object(assistant.tools, "react", return_value="Three more days in Goa, nice."):
            reply = assistant.handle(self.conn, {"text": "in goa for 3 days", "lat": None, "lng": None, "now": NOW},
                                     self.graph.invoke, notify=sent.append, understand=lambda *_: steps[:1])
        self.assertEqual(sent, ["Three more days in Goa, nice."])
        self.assertTrue(reply.startswith("What next?"))
        self.assertNotIn("Got it", reply)
        self.assertTrue(reply.ask_location)

        # a button press is never reacted to
        with patch.object(assistant.tools, "react", return_value="x") as react:
            self.say(intent=Intent(intent="more"))
        react.assert_not_called()

    def test_a_button_on_an_older_card_is_about_that_cards_place(self):
        first = self.say(**FONTAINHAS)
        second = self.say(intent=Intent(intent="more"))
        self.assertNotEqual(first.title, second.title)
        self.assertTrue(first.ref and second.ref and first.ref != second.ref)
        saved = assistant.handle(
            self.conn, {"text": "", "lat": None, "lng": None, "now": NOW, "intent": Intent(intent="save"), "ref": first.ref},
            self.graph.invoke,
        )
        self.assertIn(first.title, saved)
        # with no card named, a follow-up is about the latest one
        self.assertIn(second.title, self.say(intent=Intent(intent="rate", rating=5)))

    def test_forget_clears_what_was_learned(self):
        self.say(intent=Intent(intent="preference", likes=["sunset"], dislikes=["park"]))
        self.say(intent=Intent(intent="set_trip", city="Goa", days_left=2))
        self.say(intent=Intent(intent="set_budget", amount=10000))
        self.assertIn("forgotten", self.say("/forget"))
        self.assertEqual(store.get_kv(self.conn, "dislikes", []), [])
        self.assertEqual(store.load_taste(self.conn)["tags"], {})
        # nothing at all is left, so the memory summary is empty too
        self.assertIn("Nothing yet", self.say(intent=Intent(intent="profile")))

    def test_likes_are_learned_and_unknown_words_are_ignored(self):
        reply = self.say(intent=Intent(intent="preference", likes=["sunset", "discos"]))
        self.assertEqual(reply, "Got it: more sunsets. I'll remember that.")
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

    def test_packing_by_text_and_photo_then_matching_outfits_to_the_plan(self):
        from app.assistant import Packed
        MAXI = {"name": "flowy printed maxi dress", "colour": "red", "fabric": "cotton",
                "formality": "ethnic", "style": ["flowy", "printed", "full-length"]}
        # before any plan: it is stored, and the buddy says what to do next
        reply = self.say(intent=Intent(intent="pack", packed=[Packed(name="white linen shirt", fabric="linen")]))
        self.assertIn("Where are you headed", reply)  # no trip known yet, so that is the one thing to ask
        self.assertEqual([w["fabric"] for w in store.load_wardrobe(self.conn)], ["linen"])

        plan = self.say(intent=Intent(intent="trip_plan", city="Goa", days_left=2))
        self.assertNotIn("What are you packing?", plan)  # already packed, so no need to ask

        # a photo: described once by the model, stored as words only
        sent = []
        reply = assistant.handle(
            self.conn, {"text": "", "lat": None, "lng": None, "now": NOW, "image": "BASE64"},
            self.graph.invoke, notify=sent.append, see=lambda image, kind: MAXI,
        )
        self.assertIn("Packed: flowy printed maxi dress. That's 2 outfits", sent[-1])
        self.assertEqual(reply.title, "Where each outfit will shine")
        self.assertIn("flowy printed maxi dress at ", reply)
        self.assertIn("white linen shirt at ", reply)
        stored = store.get_kv(self.conn, "outfits")
        self.assertEqual([o["id"] for o in stored], ["white-linen-shirt", "flowy-printed-maxi-dress"])
        self.assertNotIn("BASE64", str(stored))

        self.assertEqual(self.say(intent=Intent(intent="outfits")).title, "Where each outfit will shine")
        memory = self.say(intent=Intent(intent="profile"))
        self.assertIn("Packed: white linen shirt, flowy printed maxi dress", memory)
        self.assertIn("Trip: Goa, 2 day(s)", memory)

    def test_a_photo_with_a_caption_gets_places_for_the_outfit_then_an_offer_to_plan(self):
        DRESS = {"name": "ombre maxi dress", "colour": "pink", "fabric": "synthetic", "formality": "casual", "style": []}
        sent, hints = [], []

        def understand(text, hint):
            hints.append(hint)
            # the caption says where they are and asks what to do; the model also repeats the dress
            return [Intent(intent="pack", packed=[assistant.Packed(name="this dress")]),
                    Intent(intent="trip_plan", city="Goa", days_left=2)]

        reply = assistant.handle(
            self.conn, {"text": "I am two days in Goa and I have this dress, what do I do", "lat": None, "lng": None,
                        "now": NOW, "image": "BASE64"},
            self.graph.invoke, understand=understand, notify=sent.append, see=lambda image, kind: DRESS,
        )
        self.assertIn("already packed as: ombre maxi dress", hints[0])
        # packed once, from the photo; the caption's own mention of the dress adds nothing
        self.assertEqual([o["name"] for o in store.get_kv(self.conn, "outfits")], ["ombre maxi dress"])
        self.assertEqual(store.get_kv(self.conn, "trip"), {"city": "Goa", "district": "Goa", "days_left": 2})
        # first: where the dress will shine. No itinerary yet, only the offer of one.
        self.assertEqual(reply.title, "Where each outfit will shine")
        self.assertIn("ombre maxi dress at ", reply)
        self.assertEqual(len(reply.choices), 1)
        self.assertTrue(reply.endswith("Want me to plan the rest of your 2 days too? Tell me what you're into, or just say yes."))
        self.assertFalse(any(getattr(m, "choices", None) for m in sent))
        self.assertIsNone(store.get_kv(self.conn, "last_plan"))
        # tapping the place to look at it first does not lose the question
        opened = assistant.handle(self.conn, {"text": "", "lat": None, "lng": None, "now": NOW,
                                              "intent": Intent(intent="about", index=reply.choices[0][1])}, self.graph.invoke)
        self.assertEqual(opened.title, reply.choices[0][0])

        # "yes" is read as the answer to that question, and the plan is for the trip already known
        hints.clear()
        plan = assistant.handle(
            self.conn, {"text": "yes", "lat": None, "lng": None, "now": NOW}, self.graph.invoke,
            understand=lambda text, hint: hints.append(hint) or Intent(intent="trip_plan"),
        )
        self.assertIn("Want me to plan the rest of your 2 days too?", hints[0])
        self.assertIn("Their trip: Goa, 2 day(s)", hints[0])
        self.assertIn("Want the full story on any of these?", plan)
        self.assertNotIn("What are you packing?", plan)
        # the question is only open for one message
        self.assertNotIn("ended with this question", assistant._hint(self.conn))

    def test_a_line_that_introduces_a_result_does_not_end_on_a_question(self):
        said = "Udaipur with THAT dress? Love it! 🌅 I'm lining up spots. Want to start with day one or the sunset? 🌇"
        self.assertEqual(assistant._no_question(said), "Udaipur with THAT dress? Love it! 🌅 I'm lining up spots.")
        self.assertEqual(assistant._no_question("On it!"), "On it!")
        self.assertEqual(assistant._no_question("Shall I?"), "Shall I?")  # never left with nothing

    def test_a_plan_asks_what_is_packed_when_nothing_is(self):
        self.assertIn("What are you packing?", self.say(intent=Intent(intent="trip_plan", city="Goa", days_left=2)))

    def test_a_photo_that_is_not_clothing_is_declined(self):
        reply = assistant.handle(
            self.conn, {"text": "", "lat": None, "lng": None, "now": NOW, "image": "BASE64"},
            self.graph.invoke, see=lambda image, kind: None,
        )
        self.assertIn("couldn't make out an outfit", reply)
        self.assertEqual(store.get_kv(self.conn, "outfits", []), [])

    def test_more_videos_lists_every_link_for_the_last_place(self):
        self.assertIn("Ask me about a place first", self.say(intent=Intent(intent="videos")))
        self.say(intent=Intent(intent="about", place="chapora"))
        videos = self.say(intent=Intent(intent="videos"))
        self.assertEqual(videos.title, "Watch Chapora Fort")
        self.assertIn("Sunset from the Chapora ramparts", videos.html)

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
