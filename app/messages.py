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
    choices: list = []  # [(label, key)] for "tell me about this one" buttons

    def __new__(cls, body: str, title: str = "", links=None, actions=False, ask_location=False,
                html="", photo=None, choices=None):
        self = super().__new__(cls, f"{title}\n{body}" if title else body)
        self.title, self.body = title, body
        self.links, self.actions, self.ask_location = links or [], actions, ask_location
        self.html = html or (f"<b>{escape(title)}</b>\n" if title else "") + escape(body)
        self.photo = photo
        self.choices = choices or []
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

VIDEO_LABEL = {"youtube": "▶️ Video", "instagram": "🎬 Reel"}


def _facts(nudge: dict) -> str:
    """One line of facts for plain-text channels."""
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


def _fact_lines(nudge: dict) -> list:
    """The same facts, one per line with an icon, so nothing wraps mid-fact on a narrow card."""
    lines = []
    if nudge.get("distance_m") is not None:
        lines.append(f"📍 {nudge['distance_m'] / 1000:.1f} km away")
    lines.append("🟢 Open now" if nudge.get("hours_verified") else "🕒 Opening hours not listed")
    if nudge.get("rating"):
        count = f" · {nudge['rating_count']:,} reviews" if nudge.get("rating_count") else ""
        lines.append(f"⭐ {nudge['rating']}{count}")
    if nudge.get("cost_inr"):
        lines.append(f"💸 About ₹{nudge['cost_inr']}")
    return lines


def _place_link(p: dict) -> str:
    return f'<a href="{escape(p["source"])}">{escape(p["name"])}</a>' if p.get("source") else escape(p["name"])


def _itinerary(nudge: dict) -> Reply:
    days = sorted({p["day"] for p in nudge["days"]})
    story, star = nudge.get("story") or {}, nudge.get("spotlight")
    themes = {int(k): v for k, v in (story.get("themes") or {}).items()}
    hooks = story.get("hooks") or {}
    title = f"{nudge.get('city') or 'Your trip'} in {len(days)} day{'s' if len(days) != 1 else ''}"

    plain, rich = [], [f"<b>{escape(title)}</b>"]
    if story.get("intro"):
        plain += [story["intro"], ""]
        rich += [escape(story["intro"])]
    for day in days:
        theme = f" · {themes[day]}" if themes.get(day) else ""
        plain.append(f"Day {day}{theme}:")
        rich += ["", f"🗓 <b>Day {day}{escape(theme)}</b>"]
        for p in (x for x in nudge["days"] if x["day"] == day):
            hook = hooks.get(p["id"]) or p.get("what") or ""
            rating = f"rated {p['rating']}" if p.get("rating") else ""
            plain.append(f"- {p['name']}" + (f" ({', '.join(x for x in (rating, hook) if x)})" if rating or hook else ""))
            line = "• " + _place_link(p) + (f"  ⭐ {p['rating']}" if p.get("rating") else "")
            rich.append(line + (f"\n   <i>{escape(hook)}</i>" if hook else ""))

    links = []
    if star:
        count = f", {star['rating_count']:,} reviews" if star.get("rating_count") else ""
        said = story.get("spotlight_line") or ""
        plain += ["", f"{star['label']}: {star['name']} (rated {star['rating']}{count}). {said}".strip()]
        rich += ["", f"✨ <b>{escape(star['label'])}:</b> {_place_link(star)}  ⭐ {star['rating']} ({star['rating_count']:,})"]
        if said:
            rich.append(f"   <i>{escape(said)}</i>")
        seen = set()
        for v in star.get("videos", []):
            if v["source"] not in seen:
                short = star["name"] if len(star["name"]) <= 18 else star["name"][:17] + "…"
                links.append((f"{'🎬 Reel' if v['source'] == 'instagram' else '▶️ Video'}: {short}", v["url"]))
                seen.add(v["source"])

    ask = "Want the full story on any of these? Tap one, or tell me what to swap."
    foot = "Every place here is real and currently operating."
    places = nudge["days"] + ([star] if star and star["id"] not in {p["id"] for p in nudge["days"]} else [])
    choices = [(p["name"], i) for i, p in enumerate(places)]
    return Reply(
        "\n".join(plain + ["", ask, foot]), title=title, links=links, choices=choices,
        html="\n".join(rich + ["", escape(ask), f"<i>{escape(foot)}</i>"]),
    )


def details_text(name: str, details: dict) -> Reply:
    """The practical follow-up for one place: hours, contact and what recent visitors wrote."""
    if not any(details.get(k) for k in ("hours", "website", "phone", "quotes")):
        return Reply(f"That's everything I could find on {name}.")
    plain, rich = [], [f"<b>More on {escape(name)}</b>"]
    if details.get("hours"):
        plain += ["Opening hours:"] + [f"  {h}" for h in details["hours"]]
        rich += ["", "🕒 <b>Opening hours</b>"] + [escape(h) for h in details["hours"]]
    contact = [x for x in (details.get("phone"), details.get("website")) if x]
    if contact:
        plain += ["Contact: " + " · ".join(contact)]
        rich += ["", "📞 " + "  ·  ".join(escape(x) for x in contact)]
    if details.get("quotes"):
        plain += ["What visitors wrote:"]
        rich += ["", "💬 <b>What visitors wrote</b>"]
        for q in details["quotes"]:
            tag = " · ".join(str(x) for x in (f"{q['rating']}/5" if q.get("rating") else "", q.get("when") or "") if x)
            plain.append(f'  "{q["text"]}" ({tag})')
            rich.append(f"<i>“{escape(q['text'])}”</i>" + (f"\n   {escape(tag)}" if tag else ""))
    return Reply("\n".join(plain), title=f"More on {name}", html="\n".join(rich))


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
    if nudge.get("kind") == "itinerary":
        return _itinerary(nudge)

    title = nudge["name"]
    if nudge.get("craft"):
        tag = "GI-registered" if nudge.get("gi_registered") else "traditional here"
        title = f"{nudge['name']} for {nudge['craft']} ({tag})"
    why = "; ".join(nudge.get("why", []))

    # plain text: one fact per line, no markup
    plain = [x for x in (nudge.get("what"), nudge.get("about")) if x]
    if nudge.get("reviews_say"):
        plain.append(f"Reviews say: {nudge['reviews_say']}")
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
    if nudge.get("reviews_say"):
        rich += ["", f"💬 <b>Reviews say:</b> {escape(nudge['reviews_say'])}"]
    rich += [""] + _fact_lines(nudge)
    if why:
        rich += ["", f"💡 <b>Why this:</b> {escape(why)}."]
    if nudge.get("outfit"):
        rich.append(f"👕 Wear your {escape(nudge['outfit'])}.")
    ask = "Want to know more about this place?"
    plain.append(ask)
    rich += ["", escape(ask)]
    if nudge.get("photo") and nudge.get("photo_by"):
        rich += ["", f"<i>📷 {escape(nudge['photo_by'])} · Google</i>"]  # photo credit, as Google asks

    links = [("🗺 Maps", nudge["source"])] if nudge.get("source") else []
    seen = set()
    for v in nudge.get("videos", []):
        label = VIDEO_LABEL.get(v["source"], "▶️ Video")
        if v["source"] == "instagram" and "/reel/" not in v["url"]:
            label = "📸 Instagram"
        kind = v["source"]  # one button per source keeps the row to three at most
        if kind not in seen:
            links.append((label, v["url"]))
            seen.add(kind)
    return Reply("\n".join(plain), title=title, links=links, actions=True, html="\n".join(rich),
                 photo=nudge.get("photo"))


def silence_text(result: dict) -> Reply:
    """Why nothing was suggested: the refusals, not an apology."""
    rejected = result.get("rejected", [])
    if not rejected:
        return Reply("Nothing nearby worth suggesting right now.")
    reasons = "; ".join(f"{r['name']} ({r['reason']})" for r in rejected[:3])
    return Reply(f"Ruled out: {reasons}.", title="Nothing passed my checks right now")
