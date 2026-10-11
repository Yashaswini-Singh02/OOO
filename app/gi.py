"""Which crafts belong to which region, from data/gi_crafts.json.

This table is what lets the verifier check a shopping nudge against something other
than the model's opinion: a craft is only offered where the table ties it to the region.
"""
import json
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).resolve().parent.parent / "data" / "gi_crafts.json"


@lru_cache
def _table() -> dict:
    with open(TABLE) as f:
        return {k.lower(): v for k, v in json.load(f).items() if not k.startswith("_")}


def crafts_for(*regions: str) -> list:
    """Crafts tied to any of the given region names (district, city, state)."""
    table = _table()
    for region in regions:
        if not region:
            continue
        name = region.lower()
        # "North Goa" should find the "Goa" entry
        match = table.get(name) or next((v for k, v in table.items() if k in name.split()), None)
        if match:
            return match
    return []
