import unittest

from app.nodes.taste import taste


class TasteTest(unittest.TestCase):
    def test_ranks_by_tag_overlap(self):
        state = {
            "taste": {"tags": {"sunset": 0.8, "trek": 0.3, "market": -0.5}, "ratings": []},
            "candidates": [
                {"name": "market", "tags": ["market", "food"]},
                {"name": "falls", "tags": ["trek", "waterfall"]},
                {"name": "fort", "tags": ["sunset", "fort"]},
            ],
        }
        out = taste(state)
        self.assertEqual(set(out), {"ranked"})
        self.assertEqual([c["name"] for c in out["ranked"]], ["fort", "falls", "market"])
        self.assertEqual([c["score"] for c in out["ranked"]], [0.8, 0.3, -0.5])

    def test_no_profile_keeps_scout_order(self):
        out = taste({"candidates": [{"name": "a", "tags": ["x"]}, {"name": "b", "tags": []}]})
        self.assertEqual([c["name"] for c in out["ranked"]], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
