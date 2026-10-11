from app.state import RoveState

# Which budget category a candidate's cost is charged to.
CATEGORY = {"place": "activities", "event": "activities", "shop": "shopping"}


def remaining(budget: dict, category: str) -> float | None:
    """Money left in a category, or None when no allocation was set for it."""
    alloc = budget.get("alloc", {})
    if category not in alloc:
        return None
    return alloc[category] - budget.get("spent", {}).get(category, 0)


def budget(state: RoveState) -> dict:
    b = state.get("budget", {})
    warnings = list(state.get("warnings", []))

    for category, allocated in b.get("alloc", {}).items():
        spent = b.get("spent", {}).get(category, 0)
        if spent > allocated:
            warnings.append(f"overspent on {category}: ₹{spent:g} of ₹{allocated:g}")

    kept = []
    for c in state.get("ranked", []):
        left = remaining(b, CATEGORY.get(c.get("kind"), "activities"))
        if left is None or c.get("cost_inr", 0) <= left:
            kept.append(c)
    return {"ranked": kept, "warnings": warnings}
