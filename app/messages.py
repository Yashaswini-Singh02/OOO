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
    ref: str = ""  # which remembered card the action buttons belong to
    hide: tuple = ()  # action buttons to leave off, e.g. ("videos",) when there are none to show

    def __new__(cls, body: str, title: str = "", links=None, actions=False, ask_location=False,
                html="", photo=None, choices=None):
        self = super().__new__(cls, f"{title}\n{body}" if title else body)
        self.title, self.body = title, body
        self.links, self.actions, self.ask_location = links or [], actions, ask_location
        self.html = html or (f"<b>{escape(title)}</b>\n" if title else "") + escape(body)
        self.photo = photo
        self.choices = choices or []
        return self


def rest(reply: str) -> Reply:
    """A reply without its first line, for when the buddy has already said that part in
    its own words. Buttons and the picture are kept.
    """
    def drop(text: str) -> str:
        return "\n".join(text.split("\n")[1:]).strip()

    return Reply(drop(str(reply)), links=getattr(reply, "links", None), actions=getattr(reply, "actions", False),
                 ask_location=getattr(reply, "ask_location", False), html=drop(getattr(reply, "html", "")),
                 photo=getattr(reply, "photo", None), choices=getattr(reply, "choices", None))


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

# how a kind of place reads in a sentence: "you're into sunsets"
UNCOUNTED = {"art", "food", "heritage", "nature", "photography", "shopping"}
SPOKEN = {"indoor": "indoor places", "swim": "swimming", "beach": "beaches"}


def spoken(tag: str) -> str:
    return SPOKEN.get(tag) or (tag if tag in UNCOUNTED else f"{tag}s")


def trip_text(place: str, days: int, knows_taste: bool, knows_packing: bool) -> Reply:
    """Confirms where the traveller is, then offers what to do next: only the steps that
    are still open, so someone who already told us their tastes is not asked again.
    """
    got = f"Got it: you're in {place} for {days} more day{'s' if days != 1 else ''}."
    steps = [
        ("🗺️", f'"plan {days} day{"s" if days != 1 else ""} in {place}" for a full itinerary'),
        ("🍽️", '"somewhere to eat" or "a cafe nearby" for one place right now'),
        ("🛍️", '"what should I buy here?" for what the town is known for'),
    ]
    if not knows_taste:
        steps.append(("❤️", 'tell me your taste, e.g. "I love sunsets" or "I don\'t like museums"'))
    if not knows_packing:
        steps.append(("🧳", "tell me what you're packing, or send outfit photos"))
    foot = "Or share your location and I'll find what's closest and open."
    plain = [got, "", "What next?"] + [f"- {s}" for _, s in steps] + ["", foot]
    rich = [escape(got), "", "<b>What next?</b>"] + [f"{i} {escape(s)}" for i, s in steps] + ["", f"<i>{escape(foot)}</i>"]
    return Reply("\n".join(plain), html="\n".join(rich), ask_location=True)


VIDEO_LABEL ={"youtube": "▶️ Video", "instagram": "🎬 Reel"}


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


SHORTLIST_KEY = 100  # button keys from here up open a place in the last shortlist, not the plan


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
    if nudge.get("ask_packing"):
        ask += ("\n\nWhat are you packing? Tell me, or send photos of your outfits, and I'll match "
                "each one to the spot where it'll look best in pictures.")
    foot = "Every place here is real and currently operating."
    places = nudge["days"] + ([star] if star and star["id"] not in {p["id"] for p in nudge["days"]} else [])
    choices = [(p["name"], i) for i, p in enumerate(places)]
    return Reply(
        "\n".join(plain + ["", ask, foot]), title=title, links=links, choices=choices,
        html="\n".join(rich + ["", escape(ask), f"<i>{escape(foot)}</i>"]),
    )


def shortlist_text(nudge: dict) -> Reply:
    """Two or three verified places for something the traveller loves, each with a line on
    why and a button to open it, closing with a question about what they want next.
    """
    story = nudge.get("story") or {}
    lines = story.get("lines") or {}
    kinds = " and ".join(spoken(k) for k in nudge.get("kinds", [])) or "places"
    ask = story.get("ask") or f"Which one first? Or tell me what's next: food, a full plan, or more {kinds}?"
    plain, rich = [], []
    for i, p in enumerate(nudge["places"], 1):
        facts = [f"⭐ {p['rating']}"] if p.get("rating") else []
        if p.get("distance_m") is not None:
            facts.append(f"{p['distance_m'] / 1000:.1f} km")
        if p.get("reel"):
            facts.append("on Instagram too")
        tail = f" · {' · '.join(facts)}" if facts else ""
        why = lines.get(p["id"]) or p.get("summary") or ""
        plain.append(f"{i}. {p['name']}{tail}" + (f"\n   {why}" if why else ""))
        rich += [f"{i}. <b>{_place_link(p)}</b>{escape(tail)}"] + ([f"   <i>{escape(why)}</i>"] if why else []) + [""]
    return Reply("\n".join(plain + ["", ask]), html="\n".join(rich + [escape(ask)]),
                 choices=[(p["name"], SHORTLIST_KEY + i) for i, p in enumerate(nudge["places"])])


def pairings_text(pairings: list, outfits: list, places: list, videos: dict | None = None,
                  ask: str = "", photo: str | None = None) -> Reply:
    """Where each packed outfit would photograph best, with a shot idea, reels of the place
    and a button to open it. Styling advice, worded as a suggestion. `ask` closes the message
    with the next thing the buddy offers.
    """
    by_outfit, by_place = {o["id"]: o for o in outfits}, {p["id"]: p for p in places}
    plain, rich, links, choices = [], ["<b>Where each outfit will shine</b>"], [], []
    for x in pairings:
        outfit, place = by_outfit[x["outfit_id"]], by_place[x["place_id"]]
        why = f": {x['why']}" if x.get("why") else ""
        plain.append(f"- {outfit['name']} at {place['name']}{why}")
        rich += ["", f"👗 <b>{escape(outfit['name'])}</b> → {_place_link(place)}"]
        if x.get("why"):
            rich.append(f"   <i>{escape(x['why'])}</i>")
        if x.get("pose"):
            plain.append(f"  Shot idea: {x['pose']}")
            rich.append(f"   📸 {escape(x['pose'])}")
        if place["name"] not in [c[0] for c in choices]:
            choices.append((place["name"], SHORTLIST_KEY + len(choices)))
            seen = set()
            for v in (videos or {}).get(place["id"], []):
                if v["source"] not in seen and len(links) < 4:  # one reel and one video per place
                    seen.add(v["source"])
                    links.append((f"{VIDEO_LABEL.get(v['source'], '▶️ Video')}: {short(place['name'], 22)}", v["url"]))
    foot = "These are my styling picks, so overrule me freely."
    tail_plain, tail_rich = ["", foot], ["", f"<i>{escape(foot)}</i>"]
    if ask:
        tail_plain += ["", ask]
        tail_rich += ["", escape(ask)]
    return Reply("\n".join(plain + tail_plain), title="Where each outfit will shine", html="\n".join(rich + tail_rich),
                 links=links, choices=choices, photo=photo)


def videos_text(name: str, videos: list) -> Reply:
    """Every video and reel that passed the checks for one place, as a list of links."""
    if not videos:
        return Reply(f"I couldn't find any videos that are really about {name}, so I'd rather show none.")
    plain, rich = [], [f"<b>Watch {escape(name)}</b>"]
    for kind, label, icon in (("instagram", "Reels", "🎬"), ("youtube", "Videos and vlogs", "▶️")):
        some = [v for v in videos if v.get("source") == kind]
        if not some:
            continue
        plain.append(f"{label}:")
        rich += ["", f"{icon} <b>{label}</b>"]
        for v in some:
            title = (v.get("title") or "Watch")[:70]
            plain.append(f"- {title}: {v['url']}")
            rich.append(f'• <a href="{escape(v["url"])}">{escape(title)}</a>')
    return Reply("\n".join(plain), title=f"Watch {name}", html="\n".join(rich))


def profile_text(profile: dict) -> Reply:
    """What the buddy remembers about this traveller."""
    rows = [
        ("🧭", "Trip", profile.get("trip")),
        ("❤️", "You like", ", ".join(profile.get("likes", []))),
        ("🚫", "You skip", ", ".join(profile.get("dislikes", []))),
        ("📝", "You told me", "; ".join(profile.get("notes", []))),
        ("🧳", "Packed", ", ".join(profile.get("outfits", []))),
        ("🔖", "Saved", ", ".join(profile.get("saved", []))),
        ("💸", "Budget", profile.get("budget")),
    ]
    known = [(icon, label, value) for icon, label, value in rows if value]
    if not known:
        return Reply("Nothing yet. Tell me where you're going, what you like and what you're packing.")
    plain = [f"{label}: {value}" for _, label, value in known]
    rich = ["<b>What I remember about you</b>", ""] + [f"{icon} <b>{label}:</b> {escape(str(value))}" for icon, label, value in known]
    foot = "Send /forget to wipe all of this."
    return Reply("\n".join(plain + ["", foot]), title="What I remember about you",
                 html="\n".join(rich + ["", f"<i>{escape(foot)}</i>"]))


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


def outfit_label(outfit_id: str) -> str:
    """A packed outfit as the traveller would call it. Ids are the name with hyphens, cut at
    40 characters, so the last word of a long one may be a fragment and is dropped.
    """
    words = [w for w in outfit_id.split("-") if w]
    if words and words[-1].isdigit():  # "-2" marks a second outfit with the same name
        words = words[:-1]
    if len("-".join(words)) >= 39 and len(words) > 1:
        words = words[:-1]
    while len(words) > 1 and words[-1] in {"with", "and", "of", "in", "to", "a", "the"}:
        words = words[:-1]  # a name cut short should not end on a joining word
    return " ".join(words).split(" with ")[0] or outfit_id


def _plan(nudge: dict) -> Reply:
    plain = [
        f"Day {p['day']}: {p['name']} at {p['at']}"
        + (f", wear your {outfit_label(p['outfit'])}." if p.get("outfit") else ".")
        for p in nudge["days"]
    ]
    plain += [f"Avoid: {a}" for a in nudge.get("avoid", [])]
    rich = ["<b>Here's how to fit it in</b>", ""]
    for p in nudge["days"]:
        rich.append(f"🗓 <b>Day {p['day']}</b>  ·  {escape(p['name'])}")
        rich.append(f"      🕒 {escape(str(p['at']))}"
                    + (f"  ·  👕 {escape(outfit_label(p['outfit']))}" if p.get("outfit") else ""))
    for a in nudge.get("avoid", []):
        rich += ["", f"⚠️ <b>Avoid:</b> {escape(a)}"]
    return Reply("\n".join(plain), title="Here's how to fit it in", html="\n".join(rich))


FILLER = {"over", "from", "this", "that", "with", "their", "there", "people", "though", "while", "which",
          "about", "into", "also", "very", "place", "visitors", "reviewers", "come", "enjoy"}
REPEAT = 0.6  # share of a line's words already said above it, beyond which it is left out


def repeats(line: str, said: str) -> bool:
    """True when a line mostly says again what the card has already said."""
    def words(text: str) -> set:
        bare = (w.strip(".,;:!?'\"()").lower() for w in text.split())
        return {w[:6] for w in bare if len(w) > 3 and w not in FILLER}

    mine = words(line)
    return bool(mine) and len(mine & words(said)) / len(mine) >= REPEAT


def short(label: str, limit: int = 30) -> str:
    """A button label that fits, cut at a word and marked as cut, never mid-word."""
    if len(label) <= limit:
        return label
    return label[:limit].rsplit(" ", 1)[0].rstrip(" ,-") + "…"


def nudge_text(nudge: dict) -> Reply:
    if nudge.get("kind") == "plan":
        return _plan(nudge)
    if nudge.get("kind") == "itinerary":
        return _itinerary(nudge)
    if nudge.get("kind") == "shortlist":
        return shortlist_text(nudge)

    title = nudge["name"]
    if nudge.get("craft"):
        tag = "GI-registered" if nudge.get("gi_registered") else "traditional here"
        title = f"{nudge['name']} for {nudge['craft']} ({tag})"
    why = " ".join(nudge.get("why", []))

    # plain text: one fact per line, no markup
    say = nudge.get("say")  # the buddy's own words; when present they replace the plain reasons
    if nudge.get("reviews_say") and repeats(nudge["reviews_say"], f"{say or ''} {nudge.get('about') or ''}"):
        nudge = {**nudge, "reviews_say": ""}
    plain = [x for x in (nudge.get("what"), say, nudge.get("about")) if x]
    if nudge.get("reviews_say"):
        plain.append(f"Reviews say: {nudge['reviews_say']}")
    plain.append(_facts(nudge))
    if why and not say:
        plain.append(why)
    if nudge.get("outfit"):
        plain.append(f"Wear your {outfit_label(nudge['outfit'])}.")

    # formatted: bold title, italic type, description, then the facts and the reason.
    # No address here: the Maps button already takes the traveller to it.
    rich = [f"<b>{escape(title)}</b>"]
    if nudge.get("what"):
        rich.append(f"<i>{escape(nudge['what'])}</i>")
    if say:
        rich += ["", escape(say)]
    if nudge.get("about"):
        rich += ["", escape(nudge["about"])]
    if nudge.get("reviews_say"):
        rich += ["", f"💬 <b>Reviews say:</b> {escape(nudge['reviews_say'])}"]
    rich += [""] + _fact_lines(nudge)
    if why and not say:
        rich += ["", f"💡 {escape(why)}"]
    if nudge.get("outfit"):
        rich.append(f"👕 Wear your {escape(outfit_label(nudge['outfit']))}.")
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
    reply = Reply("\n".join(plain), title=title, links=links, actions=True, html="\n".join(rich),
                  photo=nudge.get("photo"))
    reply.ref = nudge.get("ref") or ""
    # "More videos" is only worth a button when there are more than the card already links to
    reply.hide = () if len(nudge.get("videos", [])) > len(seen) else ("videos",)
    return reply


def silence_text(result: dict) -> Reply:
    """Why nothing was suggested: the refusals, not an apology."""
    rejected = result.get("rejected", [])
    if not rejected:
        return Reply("Nothing nearby worth suggesting right now.")
    reasons = "; ".join(f"{r['name']} ({r['reason']})" for r in rejected[:3])
    return Reply(f"Ruled out: {reasons}.", title="Nothing passed my checks right now")
