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
        kept.append({**c, "outfit": outfit["id"]})
    return {"ranked": kept, "warnings": warnings}
