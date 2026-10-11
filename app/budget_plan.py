"""Budget planning for one traveller: split a total into categories, and report where
it stands. The split below is a fixed default; it is the seam where an external
planner (cfo.ai) can supply the allocation instead.
"""
import calendar
from datetime import datetime

CATEGORIES = ["food", "activities", "shopping", "transport", "other"]
DEFAULT_SPLIT = {"food": 0.30, "activities": 0.35, "shopping": 0.15, "transport": 0.15, "other": 0.05}


def split(total: float) -> dict:
    return {category: round(total * share) for category, share in DEFAULT_SPLIT.items()}


def days_remaining(period: str, days_left: int, now: datetime) -> int:
    if period == "month":
        return calendar.monthrange(now.year, now.month)[1] - now.day + 1
    return max(days_left, 1)


def status(budget: dict, period: str, days_left: int, now: datetime) -> str:
    alloc, spent = budget.get("alloc", {}), budget.get("spent", {})
    if not alloc:
        return 'No budget set yet. Tell me one, e.g. "my trip budget is 15000".'
    lines = []
    for category, allocated in alloc.items():
        used = spent.get(category, 0)
        lines.append(f"{category}: ₹{used:g} of ₹{allocated:g}" + ("  (over)" if used > allocated else ""))
    left = sum(alloc.values()) - sum(spent.values())
    if left < 0:
        head = f"₹{-left:,.0f} over budget this {period}."
    else:
        days = days_remaining(period, days_left, now)
        head = (
            f"₹{left:,.0f} left this {period}, about ₹{left / days:,.0f} a day "
            f"for {days} more day{'s' if days != 1 else ''}."
        )
    return head + "\n" + "\n".join(lines)
