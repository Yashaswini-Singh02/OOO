"""Turn graph output into chat messages.

A Reply is the text plus what a richer client can do with it: link buttons, quick
actions, and a request for the traveller's location. It is a str, so a plain-text
channel can send it as is and ignore the rest.
"""


class Reply(str):
    title: str = ""  # first line, shown in bold where the channel supports it
    links: list = []  # [(label, url)] for link buttons
    actions: bool = False  # offer "loved it / not for me / save" under the message
    ask_location: bool = False  # offer a one-tap "share my location" button

    def __new__(cls, body: str, title: str = "", links=None, actions=False, ask_location=False):
        self = super().__new__(cls, f"{title}\n{body}" if title else body)
        self.title, self.body = title, body
        self.links, self.actions, self.ask_location = links or [], actions, ask_location
        return self


WELCOME = Reply(
    "I suggest one thing worth doing near you, and only after checking it's real and open.\n\n"
    "Send me your location to start. You can also just tell me things:\n"
    '- "I\'m in Goa for 3 more days"\n'
    '- "my trip budget is 15000"\n'
    '- "spent 850 on food"\n'
    '- "I want a trek and a sunset"\n'
    '- "what should I buy here?"\n\n'
    "Share your live location and I'll speak up on my own when something is worth it.",
    title="Hi, I'm Outofoffice.",
    ask_location=True,
)

VIDEO_LABEL = {"youtube": "Watch video", "instagram": "See reel"}


def _facts(nudge: dict) -> str:
    facts = []
    if nudge.get("distance_m") is not None:
        facts.append(f"{nudge['distance_m'] / 1000:.1f} km away")
    facts.append("open now" if nudge.get("hours_verified") else "opening hours unknown")
    if nudge.get("rating"):
        count = f" ({nudge['rating_count']:,} reviews)" if nudge.get("rating_count") else ""
        facts.append(f"rated {nudge['rating']}{count}")
    if nudge.get("cost_inr"):
        facts.append(f"about ₹{nudge['cost_inr']}")
    return " · ".join(facts)


def nudge_text(nudge: dict) -> Reply:
    if nudge.get("kind") == "plan":
        lines = [f"Day {p['day']}: {p['name']} at {p['at']}, wear your {p['outfit']}." for p in nudge["days"]]
        lines += [f"Avoid: {a}" for a in nudge.get("avoid", [])]
        return Reply("\n".join(lines), title="Here's how to fit it in")

    title = nudge["name"]
    if nudge.get("craft"):
        tag = "GI-registered" if nudge.get("gi_registered") else "traditional here"
        title = f"{nudge['name']} for {nudge['craft']} ({tag})"

    lines = []
    what = " · ".join(x for x in (nudge.get("what"), nudge.get("address")) if x)
    if what:
        lines.append(what)
    if nudge.get("summary"):
        lines.append(nudge["summary"])
    lines.append(_facts(nudge))
    if nudge.get("why"):
        lines.append("Why this: " + "; ".join(nudge["why"]) + ".")
    if nudge.get("outfit"):
        lines.append(f"Wear your {nudge['outfit']}.")
    if nudge.get("ruled_out"):
        lines.append(f"Ruled out on the way: {nudge['ruled_out']}.")

    links = [("Open in Maps", nudge["source"])] if nudge.get("source") else []
    seen = set()
    for v in nudge.get("videos", []):
        label = VIDEO_LABEL.get(v["source"], "Watch video")
        if label not in seen:  # one button per kind keeps the row short
            links.append((label, v["url"]))
            seen.add(label)
    return Reply("\n".join(lines), title=title, links=links, actions=True)


def silence_text(result: dict) -> Reply:
    """Why nothing was suggested: the refusals, not an apology."""
    rejected = result.get("rejected", [])
    if not rejected:
        return Reply("Nothing nearby worth suggesting right now.")
    reasons = "; ".join(f"{r['name']} ({r['reason']})" for r in rejected[:3])
    return Reply(f"Ruled out: {reasons}.", title="Nothing passed my checks right now")
