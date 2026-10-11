import unittest

from app.nodes.verifier import route, verifier

GOOD = {"id": "a", "name": "A", "exists": True, "open_now": True, "lat": 15.5, "lng": 73.8}


class VerifierTest(unittest.TestCase):
    def test_splits_ranked_into_verified_and_rejected_with_reasons(self):
        far_reel = {"claimed_lat": 15.0, "claimed_lng": 74.3}
        state = {
            "ranked": [
                GOOD,
                {**GOOD, "id": "b", "name": "B", "exists": False},
                {**GOOD, "id": "c", "name": "C", "open_now": False},
                {**GOOD, "id": "d", "name": "D", "videos": [far_reel]},
            ]
        }
        out = verifier(state)
        self.assertEqual(set(out), {"verified", "rejected"})
        self.assertEqual([c["id"] for c in out["verified"]], ["a"])
        self.assertEqual(
            {r["id"]: r["reason"] for r in out["rejected"]},
            {"b": "venue not found", "c": "closed right now", "d": "not where the reel claims"},
        )

    def test_keeps_earlier_rejections(self):
        earlier = {"id": "x", "name": "X", "reason": "closed right now"}
        out = verifier({"ranked": [{**GOOD, "open_now": False}], "rejected": [earlier]})
        self.assertEqual([r["id"] for r in out["rejected"]], ["x", "a"])

    def test_route(self):
        self.assertEqual(route({"verified": [GOOD], "replans": 0}), "feed")
        self.assertEqual(route({"verified": [], "replans": 0}), "replan")
        self.assertEqual(route({"verified": [], "replans": 1}), "replan")
        self.assertEqual(route({"verified": [], "replans": 2}), "give_up")


if __name__ == "__main__":
    unittest.main()
