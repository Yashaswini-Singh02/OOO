import unittest

from app.nodes.budget import budget


class BudgetTest(unittest.TestCase):
    def test_drops_what_breaks_the_remaining_allocation(self):
        state = {
            "budget": {"alloc": {"activities": 4000}, "spent": {"activities": 1000}},
            "ranked": [
                {"name": "falls", "kind": "place", "cost_inr": 3500},
                {"name": "fort", "kind": "place", "cost_inr": 0},
                {"name": "walk", "kind": "event", "cost_inr": 3000},
            ],
        }
        out = budget(state)
        self.assertEqual(set(out), {"ranked", "warnings"})
        self.assertEqual([c["name"] for c in out["ranked"]], ["fort", "walk"])
        self.assertEqual(out["warnings"], [])

    def test_category_without_allocation_is_unconstrained(self):
        state = {
            "budget": {"alloc": {"activities": 100}, "spent": {}},
            "ranked": [{"name": "feni", "kind": "shop", "cost_inr": 600}],
        }
        self.assertEqual(len(budget(state)["ranked"]), 1)

    def test_warns_on_overspend(self):
        state = {"budget": {"alloc": {"food": 1000}, "spent": {"food": 1250}}, "ranked": []}
        self.assertEqual(budget(state)["warnings"], ["overspent on food: ₹1250 of ₹1000"])


if __name__ == "__main__":
    unittest.main()
