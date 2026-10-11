import unittest

from app.providers import core_name, is_about

PLACE = "Mysteryverse Escape Room – HSR Layout Bangalore"


class RelevanceTest(unittest.TestCase):
    def test_core_name_drops_the_branch(self):
        self.assertEqual(core_name(PLACE), "Mysteryverse Escape Room")
        self.assertEqual(core_name("Fort Aguada"), "Fort Aguada")

    def test_only_links_that_name_the_venue_are_kept(self):
        self.assertTrue(is_about(PLACE, "We tried MysteryVerse escape room in HSR!"))
        self.assertTrue(is_about(PLACE, "Mystery Verse | best escape rooms Bangalore"))
        self.assertFalse(is_about(PLACE, "Top 10 escape rooms in Bangalore you must try"))
        self.assertFalse(is_about(PLACE, "HSR Layout food walk, Bangalore vlog"))

    def test_a_name_made_of_common_words_needs_all_of_them(self):
        self.assertTrue(is_about("Big Banyan Tree", "Visiting the Big Banyan Tree near Bengaluru"))
        self.assertFalse(is_about("Big Banyan Tree", "Big breakfast in Bengaluru"))
        self.assertTrue(is_about("Chapora Fort", "Sunset at Chapora, Goa"))


if __name__ == "__main__":
    unittest.main()
