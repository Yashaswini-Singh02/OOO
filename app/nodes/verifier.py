from neatlogs import span

from app.geo import distance_m
from app.nodes.planner import day_conflict
from app.state import RoveState

# A reel that places a venue further than this from where it really is gets refused.
MAX_REEL_MISMATCH_M = 50_000


def refusal(c: dict) -> str | None:
    """Why this candidate must not reach the feed, or None if it may."""
    if not c.get("exists"):
        return "venue not found"
    if not c.get("open_now"):
        return "closed right now"
    for video in c.get("videos", []):
        off_by = distance_m(c["lat"], c["lng"], video["claimed_lat"], video["claimed_lng"])
        if off_by > MAX_REEL_MISMATCH_M:
            return "not where the reel claims"
    return None


@span(kind="GUARDRAIL", name="verifier")
def verifier(state: RoveState) -> dict:
    ok, bad = [], []
    for c in state.get("ranked", []):
        reason = refusal(c)
        if reason:
            bad.append({**c, "reason": reason})
        else:
            ok.append(c)
    out = {"verified": ok, "rejected": state.get("rejected", []) + bad}
    if state.get("wants"):
        out.update(check_plan(state, ok))
    return out


def check_plan(state: RoveState, ok: list) -> dict:
    """Second layer for planned runs: every item verified, every want covered, every day feasible."""
    by_id = {c["id"]: c for c in ok}
    plan = state.get("plan", [])
    new_conflicts = []
    for day in sorted({p["day"] for p in plan}):
        conflict = day_conflict([by_id[p["id"]] for p in plan if p["day"] == day and p["id"] in by_id])
        if conflict:
            new_conflicts.append({**conflict, "day": day})
    all_verified = all(p["id"] in by_id for p in plan)
    all_covered = {p["activity"] for p in plan} >= set(state["wants"])
    return {
        "conflicts": state.get("conflicts", []) + new_conflicts,
        "plan_ok": bool(plan) and all_verified and all_covered and not new_conflicts,
    }


def route(state: RoveState) -> str:
    passed = state.get("plan_ok") if state.get("wants") else state.get("verified")
    if passed:
        return "feed"
    if state.get("replans", 0) < 2:
        return "replan"
    return "give_up"
