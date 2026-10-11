"""Turn graph output into chat messages.

A Reply is the text plus what a richer client can do with it: a formatted version,
link buttons, quick actions, and a request for the traveller's location. It is a str,
so a plain-text channel can send it as is and ignore the rest.
"""
from html import escape


class Reply(str):
    title: str = ""  # first line, shown in bold where the channel supports it
    links: list = []  # [(label, url)] for link buttons
    actions: bool = False  # offer "loved it / not for me / save" under the message
    ask_location: bool = False  # offer a one-tap "share my location" button
    html: str = ""  # the same message with bold, italics and emoji, for channels that render HTML
    photo: str | None = None  # address of a picture to show with the message

    def __new__(cls, body: str, title: str = "", links=None, actions=False, ask_location=False,
                html="", photo=None):
        self = super().__new__(cls, f"{title}\n{body}" if title else body)
        self.title, self.body = title, body
        self.links, self.actions, self.ask_location = links or [], actions, ask_location
        self.html = html or (f"<b>{escape(title)}</b>\n" if title else "") + escape(body)
        self.photo = photo
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

VIDEO_LABEL = {"youtube": "▶️ Watch video", "instagram": "🎬 See reel"}


def _facts(nudge: dict, rich: bool = False) -> str:
    pin, open_, unknown, star, money = ("📍 ", "🟢 ", "🕒 ", "⭐ ", "💸 ") if rich else ("", "", "", "rated ", "")
    facts = []
    if nudge.get("distance_m") is not None:
        facts.append(f"{pin}{nudge['distance_m'] / 1000:.1f} km away")
    facts.append(f"{open_}Open now" if nudge.get("hours_verified") else f"{unknown}Hours unknown")
    if nudge.get("rating"):
        count = nudge.get("rating_count")
        facts.append(f"{star}{nudge['rating']}" + (f" ({count:,}{'' if rich else ' reviews'})" if count else ""))
    if nudge.get("cost_inr"):
        facts.append(f"{money}about ₹{nudge['cost_inr']}")
    text = "  ·  ".join(facts) if rich else " · ".join(facts)
    return text if rich else text.replace("Open now", "open now").replace("Hours unknown", "opening hours unknown")


def _plan(nudge: dict) -> Reply:
    plain = [f"Day {p['day']}: {p['name']} at {p['at']}, wear your {p['outfit']}." for p in nudge["days"]]
    plain += [f"Avoid: {a}" for a in nudge.get("avoid", [])]
    rich = ["<b>Here's how to fit it in</b>", ""]
    for p in nudge["days"]:
        rich.append(f"🗓 <b>Day {p['day']}</b>  ·  {escape(p['name'])}")
        rich.append(f"      🕒 {escape(str(p['at']))}  ·  👕 {escape(str(p['outfit']))}")
    for a in nudge.get("avoid", []):
        rich += ["", f"⚠️ <b>Avoid:</b> {escape(a)}"]
    return Reply("\n".join(plain), title="Here's how to fit it in", html="\n".join(rich))


def nudge_text(nudge: dict) -> Reply:
    if nudge.get("kind") == "plan":
        return _plan(nudge)

    title = nudge["name"]
    if nudge.get("craft"):
        tag = "GI-registered" if nudge.get("gi_registered") else "traditional here"
        title = f"{nudge['name']} for {nudge['craft']} ({tag})"
    why = "; ".join(nudge.get("why", []))

    # plain text: one fact per line, no markup
    plain = [x for x in (nudge.get("what"), nudge.get("about")) if x]
    plain.append(_facts(nudge))
    if why:
        plain.append(f"Why this: {why}.")
    if nudge.get("outfit"):
        plain.append(f"Wear your {nudge['outfit']}.")

    # formatted: bold title, italic type, description, then the facts and the reason.
    # No address here: the Maps button already takes the traveller to it.
    rich = [f"<b>{escape(title)}</b>"]
    if nudge.get("what"):
        rich.append(f"<i>{escape(nudge['what'])}</i>")
    if nudge.get("about"):
        rich += ["", escape(nudge["about"])]
    rich += ["", _facts(nudge, rich=True)]
    if why:
        rich.append(f"💡 <b>Why this:</b> {escape(why)}.")
    if nudge.get("outfit"):
        rich.append(f"👕 Wear your {escape(nudge['outfit'])}.")
    if nudge.get("photo") and nudge.get("photo_by"):
        rich += ["", f"<i>📷 {escape(nudge['photo_by'])} · Google</i>"]  # photo credit, as Google asks

    links = [("🗺 Open in Maps", nudge["source"])] if nudge.get("source") else []
    seen = set()
    for v in nudge.get("videos", []):
        label = VIDEO_LABEL.get(v["source"], "▶️ Watch video")
        if v["source"] == "instagram" and "/reel/" not in v["url"]:
            label = "📸 See on Instagram"
        if label not in seen:  # one button per kind keeps the row short
            links.append((label, v["url"]))
            seen.add(label)
    return Reply("\n".join(plain), title=title, links=links, actions=True, html="\n".join(rich),
                 photo=nudge.get("photo"))


def silence_text(result: dict) -> Reply:
    """Why nothing was suggested: the refusals, not an apology."""
    rejected = result.get("rejected", [])
    if not rejected:
        return Reply("Nothing nearby worth suggesting right now.")
    reasons = "; ".join(f"{r['name']} ({r['reason']})" for r in rejected[:3])
    return Reply(f"Ruled out: {reasons}.", title="Nothing passed my checks right now")
