from app.state import RoveState

# Fabrics that are wrong for an activity. Linen on the Dudhsagar trek is the demo case.
UNSUITABLE = {
    "trek": {"linen", "silk", "wool"},
    "beach": {"silk", "wool"},
}
# Fabric to reach for first when it is packed.
PREFERRED = {
    "trek": "quick-dry",
    "sunset": "linen",
}


WORSHIP = ("temple", "mandir", "mosque", "masjid", "church", "gurdwara", "gurudwara", "dargah", "shrine",
           "synagogue", "monastery", "place_of_worship")


def is_worship(place: dict) -> bool:
    """A temple, mosque, church or other place of worship, going by its type and name."""
    text = " ".join([place.get("name") or "", place.get("what") or "", *place.get("types", []),
                     *place.get("tags", [])]).lower()
    return any(w in text for w in WORSHIP)


def pick_outfit(activity: str, wardrobe: list) -> dict | None:
    suitable = [w for w in wardrobe if w["fabric"] not in UNSUITABLE.get(activity, set())]
    for w in suitable:
        if w["fabric"] == PREFERRED.get(activity):
            return w
    return suitable[0] if suitable else None


def wardrobe(state: RoveState) -> dict:
    packed = state.get("wardrobe", [])
    warnings = list(state.get("warnings", []))
    if not packed:
        # nothing recorded as packed: no constraint to apply
        return {"ranked": state.get("ranked", []), "warnings": warnings}

    kept = []
    for c in state.get("ranked", []):
        outfit = pick_outfit(c.get("activity", ""), packed)
        if outfit is None:
            warnings.append(f"nothing packed suits {c['name']} ({c.get('activity')})")
            continue
        # what to wear to a temple is a matter of covered shoulders and knees, not of style
        kept.append({**c, "outfit": None if is_worship(c) else outfit["id"]})
    return {"ranked": kept, "warnings": warnings}
