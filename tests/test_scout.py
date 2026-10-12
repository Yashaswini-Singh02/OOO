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
QUERIES = ScoutQueries(place_query="sunset viewpoint", radius_m=70000)


class ScoutTest(unittest.TestCase):
    def test_writes_candidates_from_places_and_events(self):
        with patch.object(scout_module, "plan_queries", return_value=QUERIES):
            out = scout({**CONTEXT, "replans": 0})
        self.assertEqual(set(out), {"candidates", "replans", "warnings", "search"})
        kinds = [c["kind"] for c in out["candidates"]]
        self.assertEqual(kinds.count("place"), 7)
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
        self.assertEqual(len(out["candidates"]), 3)  # default radius is 3 km
        self.assertEqual(out["warnings"], ["scout used the default query: OpenAIError: no key"])

    def test_what_the_traveller_likes_is_always_searched_for(self):
        seen = []

        def search(query, lat, lng, radius, categories):
            seen.append(categories)
            return []

        picked = ScoutQueries(place_query="things to do", radius_m=3000, categories=["attraction"])
        with patch.object(scout_module, "plan_queries", return_value=picked), \
                patch.object(scout_module.tools, "place_search", side_effect=search):
            scout({**CONTEXT, "replans": 0, "taste": {"tags": {"sunset": 0.8, "museum": -1.0}}})
        self.assertEqual(seen, [["viewpoint", "attraction"]])

    def test_what_the_traveller_asked_to_do_is_searched_for_even_if_the_model_looks_elsewhere(self):
        seen = []
        picked = ScoutQueries(place_query="seafood dinner", radius_m=10000, categories=["restaurant", "market"])
        with patch.object(scout_module, "plan_queries", return_value=picked), \
                patch.object(scout_module.tools, "place_search",
                             side_effect=lambda q, lat, lng, r, categories: seen.append(categories) or []):
            scout({**CONTEXT, "replans": 0, "wants": ["trek", "sunset"]})
        # a trek needs the waterfall search and a sunset the viewpoint search, ahead of the model's own picks
        self.assertEqual(seen[0][:2], ["viewpoint", "waterfall"])
        self.assertIn("restaurant", seen[0])

    def test_backdrops_for_an_outfit_are_not_judged_by_whether_they_are_open_this_minute(self):
        with patch.object(scout_module, "plan_queries", return_value=QUERIES):
            now = scout({**CONTEXT, "replans": 0})
            outfit = scout({**CONTEXT, "replans": 0, "purpose": "outfit"})
        # the fixture has places closed at 09:00; for a nudge that is a refusal, for a backdrop it is unknown
        self.assertIn(False, [c["open_now"] for c in now["candidates"]])
        self.assertEqual({c["open_now"] for c in outfit["candidates"]}, {None})

    def test_a_plain_attraction_is_not_tagged_as_a_sunset_spot(self):
        from app import providers
        temple = {"displayName": {"text": "Jagdish Temple"}, "types": ["hindu_temple", "tourist_attraction"]}
        point = {"displayName": {"text": "Sunset Point"}, "types": ["tourist_attraction"]}
        self.assertEqual(providers._category(temple, ["viewpoint", "attraction"]), "attraction")
        self.assertEqual(providers._category(temple, ["viewpoint"]), "attraction")
        self.assertEqual(providers._category(point, ["attraction", "viewpoint"]), "viewpoint")
        self.assertNotIn("sunset", providers.CATEGORIES["attraction"][1])

    def test_a_request_is_cross_checked_against_instagram(self):
        google = [{"id": "g-1", "name": "Sajjangarh Monsoon Palace"}, {"id": "g-2", "name": "Sunset Terrace"}]
        spots = [{"name": "Monsoon Palace", "url": "https://instagram.com/reel/a"},
                 {"name": "Sunset Point", "url": "https://instagram.com/reel/b"},
                 {"name": "Nowhere Ghat", "url": "https://instagram.com/reel/c"}]
        looked_up = []

        def lookup(name, lat, lng, category):
            looked_up.append((name, category))
            return {"id": "g-3", "name": "Sunset Point"} if name.startswith("Sunset Point") else None

        with patch.object(scout_module.tools, "reel_spots", return_value=spots), \
                patch.object(scout_module.tools, "lookup", side_effect=lookup):
            out = scout_module.with_reels(google, ["sunset"], {"city": "Udaipur", "lat": 24.58, "lng": 73.68})
        by_name = {p["name"]: p.get("reel") for p in out}
        self.assertEqual(by_name, {
            "Sajjangarh Monsoon Palace": "https://instagram.com/reel/a",  # the same place under a longer name
            "Sunset Terrace": None,  # sharing the word "sunset" is not a match
            "Sunset Point": "https://instagram.com/reel/b",  # missed by the search, so looked up
        })
        self.assertEqual(looked_up, [("Sunset Point Udaipur", "viewpoint"), ("Nowhere Ghat Udaipur", "viewpoint")])

    def test_a_place_instagram_names_is_ranked_higher_and_said_so(self):
        from app.nodes.feed import reasons
        from app.nodes.taste import taste
        plain = {"id": "a", "tags": ["sunset"], "rating": 4.8, "rating_count": 9000}
        named = {"id": "b", "tags": ["sunset"], "rating": 4.5, "rating_count": 300, "reel": "https://instagram.com/reel/b"}
        ranked = taste({"candidates": [plain, named], "taste": {"tags": {"sunset": 0.8}}})["ranked"]
        self.assertEqual(ranked[0]["id"], "b")
        state = {"trigger": "asked", "only": ["sunset"], "city": "Udaipur", "verified": ranked}
        self.assertEqual(reasons(ranked[0], state), [
            "It's what you asked for.", "It comes up on Instagram for sunsets in Udaipur too.",
        ])

    def test_a_written_line_must_stay_inside_the_facts(self):
        from app import providers
        facts = "Place: Sunset Point\nDistance: 1.2 km\nWhat reviews say: calm at dusk"
        self.assertTrue(providers.grounded("It's calm at dusk, an easy 1.2 km from you.", facts))
        self.assertFalse(providers.grounded("Entry is 50 rupees and it's calm at dusk.", facts))  # an invented number
        self.assertFalse(providers.grounded("x" * 400, facts))
        self.assertFalse(providers.grounded("", facts))

    def test_the_buddys_line_replaces_the_plain_reasons_on_a_card(self):
        from app import messages
        nudge = {"name": "Sunset Point", "kind": "place", "why": ["It's what you asked for."]}
        self.assertIn("It's what you asked for.", messages.nudge_text(nudge))
        card = messages.nudge_text({**nudge, "say": "You wanted a sunset, and this one is close."})
        self.assertIn("You wanted a sunset, and this one is close.", card)
        self.assertNotIn("It's what you asked for.", card)

    def test_notes_choose_between_verified_places_only_when_asked(self):
        from app.nodes import feed as feed_module
        a = {"id": "a", "name": "A", "kind": "place", "videos": []}
        b = {"id": "b", "name": "B", "kind": "place", "videos": []}
        state = {"trigger": "asked", "verified": [a, b], "notes": ["avoids crowds"]}
        with patch.object(feed_module.tools, "best_fit", return_value="b"):
            self.assertEqual(feed_module.feed(state)["nudge"]["id"], "b")
            self.assertEqual(feed_module.feed({**state, "trigger": "rain_starting"})["nudge"]["id"], "a")
        with patch.object(feed_module.tools, "best_fit", return_value=None):
            self.assertEqual(feed_module.feed(state)["nudge"]["id"], "a")

    def test_sunrise_and_sunset_places_are_told_apart(self):
        from app import providers
        both = ["viewpoint", "sunset", "sunrise", "photography"]
        self.assertNotIn("sunrise", providers.sun_tags(both, "Sunset Point Park Udaipur"))
        self.assertNotIn("sunset", providers.sun_tags(both, "Bahubali Hills Sunrise Point"))
        # the name says nothing, so what it was searched for decides
        self.assertNotIn("sunset", providers.sun_tags(both, "Karni Mata Viewpoint", "sunrise"))
        # the name wins over the search: a sunset point found by a sunrise search stays a sunset point
        self.assertNotIn("sunrise", providers.sun_tags(both, "Sunset Point", "sunrise"))
        self.assertEqual(providers.sun_tags(both, "Lake View Point"), both)

    def test_a_review_line_that_repeats_the_description_is_left_out(self):
        from app import messages
        nudge = {"name": "Sunset Point Park", "kind": "place", "why": [],
                 "about": "A peaceful garden with scenic lake views and sunset vistas."}
        same = messages.nudge_text({**nudge, "reviews_say": "People enjoy the scenic sunset views over the lake from this peaceful garden."})
        self.assertNotIn("Reviews say", same)
        new = messages.nudge_text({**nudge, "reviews_say": "It is a steep climb, so bring water and go before the crowds."})
        self.assertIn("Reviews say: It is a steep climb", new)
        self.assertEqual(messages.short("Bahuballi Hills Sunrise Point Udaipur", 30), "Bahuballi Hills Sunrise Point…")
        self.assertEqual(messages.short("Fateh Sagar Lake"), "Fateh Sagar Lake")

    def test_reasons_sound_like_a_friend_and_stay_short(self):
        from app.nodes.feed import reasons
        top = {"tags": ["viewpoint", "sunset"], "rating": 4.6, "distance_m": 900}
        other = {"tags": ["attraction"], "rating": 4.2, "distance_m": 1500}
        state = {"trigger": "asked", "taste": {"tags": {"sunset": 0.8}}, "verified": [top, other]}
        self.assertEqual(reasons(top, state), [
            "You're into sunsets, so I thought of this.", "It's the best-rated spot I found near you.",
        ])
        # a temple that only ranks well is never said to match a taste for sunsets
        self.assertEqual(reasons(other, state), ["People rate it really well."])


if __name__ == "__main__":
    unittest.main()
