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

    def test_a_temple_stays_in_the_list_but_gets_no_outfit_advice(self):
        temple = {"name": "Jagdish Temple", "what": "Hindu Temple", "activity": "walk"}
        ghat = {"name": "Ambrai Ghat", "what": "Tourist attraction", "activity": "walk"}
        out = wardrobe({"wardrobe": [LINEN], "ranked": [temple, ghat]})
        self.assertEqual([(c["name"], c["outfit"]) for c in out["ranked"]],
                         [("Jagdish Temple", None), ("Ambrai Ghat", "linen-shirt")])

    def test_an_outfit_is_shown_by_a_readable_name_not_its_id(self):
        from app.messages import outfit_label
        self.assertEqual(outfit_label("ombre-tiered-chiffon-halter-maxi-dress-w"), "ombre tiered chiffon halter maxi dress")
        self.assertEqual(outfit_label("ombre-halter-maxi-dress-with-asymmetric--2"), "ombre halter maxi dress")
        self.assertEqual(outfit_label("trek-tee"), "trek tee")


if __name__ == "__main__":
    unittest.main()
