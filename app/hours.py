"""Opening hours, for specs simple enough to trust: "24/7", "09:00-18:00",
and the same with a leading day range such as "Mo-Fr 09:00-18:00".
Anything else returns None (unknown), never a guess.
"""
import re
from datetime import datetime

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
SPEC = re.compile(r"^(?:(\w\w)(?:-(\w\w))?\s+)?(\d\d):(\d\d)-(\d\d):(\d\d)$")


def window(spec: str, now: datetime) -> tuple[float, float] | None:
    """Today's (open, close) in hours, or None if the spec is unknown or excludes today."""
    m = SPEC.match(spec.strip())
    if not m:
        return None
    first, last, h1, m1, h2, m2 = m.groups()
    if first:
        if first not in DAYS or (last and last not in DAYS):
            return None
        lo, hi = DAYS.index(first), DAYS.index(last or first)
        today = now.weekday()
        in_range = lo <= today <= hi if lo <= hi else (today >= lo or today <= hi)
        if not in_range:
            return (0.0, 0.0)
    return int(h1) + int(m1) / 60, int(h2) + int(m2) / 60


def is_open(spec: str | None, now: datetime) -> bool | None:
    if not spec:
        return None
    if spec.strip() == "24/7":
        return True
    w = window(spec, now)
    if w is None:
        return None
    return w[0] <= now.hour + now.minute / 60 < w[1]


def minutes_since_opening(spec: str | None, now: datetime) -> float | None:
    """How long ago the place opened today; None if unknown or not open."""
    if not spec or not is_open(spec, now):
        return None
    w = window(spec, now)
    if w is None:
        return None
    return (now.hour + now.minute / 60 - w[0]) * 60
