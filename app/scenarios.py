"""Named demo inputs. A scenario is a JSON file in fixtures/ holding the context
and profile for one run, e.g. fixtures/goa_2day.json.
"""
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

DEFAULT = {
    "city": "Goa",
    "district": "North Goa",
    "lat": 15.4989,
    "lng": 73.8313,
    "now": "2026-10-10T09:00:00+05:30",
    "days_left": 2,
    "weather": "clear",
}


def load(name: str | None = None) -> dict:
    if name is None:
        return dict(DEFAULT)
    with open(FIXTURES / f"{name}.json") as f:
        return json.load(f)
