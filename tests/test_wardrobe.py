import unittest

from app.nodes.wardrobe import wardrobe

LINEN = {"id": "linen-shirt", "colour": "white", "formality": "smart", "fabric": "linen"}
QUICK_DRY = {"id": "trek-tee", "colour": "black", "formality": "casual", "fabric": "quick-dry"}

FALLS = {"name": "Dudhsagar Falls", "activity": "trek"}
FORT = {"name": "Chapora Fort", "activity": "sunset"}


class WardrobeTest(unittest.TestCase):
    def test_linen_only_drops_the_trek(self):
        out = wardrobe({"wardrobe": [LINEN], "ranked": [FALLS, FORT]})
        self.assertEqual(set(out), {"ranked", "warnings"})
        self.assertEqual([c["name"] for c in out["ranked"]], ["Chapora Fort"])
        self.assertEqual(out["warnings"], ["nothing packed suits Dudhsagar Falls (trek)"])

    def test_assigns_the_right_outfit_to_each_activity(self):
        out = wardrobe({"wardrobe": [LINEN, QUICK_DRY], "ranked": [FALLS, FORT]})
        outfits = {c["name"]: c["outfit"] for c in out["ranked"]}
        self.assertEqual(outfits, {"Dudhsagar Falls": "trek-tee", "Chapora Fort": "linen-shirt"})
        self.assertEqual(out["warnings"], [])

    def test_empty_wardrobe_applies_no_constraint(self):
        out = wardrobe({"wardrobe": [], "ranked": [FALLS, FORT]})
        self.assertEqual(out["ranked"], [FALLS, FORT])


if __name__ == "__main__":
    unittest.main()
