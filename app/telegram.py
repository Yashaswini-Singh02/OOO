"""Telegram front end.

    python -m app.telegram

Uses long polling, so it needs no public address or tunnel: the bot asks Telegram for
new messages. Three kinds of input:

  text            -> the assistant understands it (budget, plan, shop, rate, ...)
  a location pin  -> one verified suggestion for right there
  live location   -> every update goes through the runtime's triggers and throttle,
                     so the bot speaks up on its own only when something is worth it
"""
import app.trace  # noqa: F401  MUST be the first import in every entry point

import base64
import html
import os
import threading
import time
from datetime import datetime, timezone

import requests
import urllib3.util.connection
from neatlogs import span

from app import assistant, messages
from app.assistant import Intent
from app.graph import run_once

# On networks where IPv6 to Telegram hangs, each new connection would stall for
# seconds before falling back. Use IPv4 for this process and reuse one connection.
urllib3.util.connection.HAS_IPV6 = False
SESSION = requests.Session()

API = "https://api.telegram.org/bot{token}/{method}"
POLL_S = 30
MAX_BODY = 4000  # Telegram caps a message at 4096 characters
MAX_CAPTION = 1024  # and a photo caption at 1024


def call(method: str, **params) -> dict:
    r = SESSION.post(
        API.format(token=os.environ["TELEGRAM_BOT_TOKEN"], method=method),
        json=params,
        timeout=POLL_S + 10,
    )
    body = r.json()
    if not body.get("ok"):
        # never print the URL: it contains the bot token
        raise RuntimeError(f"telegram {method} failed: {body.get('description')}")
    return body["result"]


# What each button under a suggestion means. Buttons skip the model: the intent is known.
BUTTONS = {
    "rate:5": ("❤️ Loved it", Intent(intent="rate", rating=5)),
    "rate:2": ("👎 Not for me", Intent(intent="rate", rating=2)),
    "save": ("🔖 Save for later", Intent(intent="save")),
    "more": ("🔄 Suggest another", Intent(intent="more")),
    "info": ("ℹ️ Tell me more", Intent(intent="info")),
    "videos": ("🎥 More videos", Intent(intent="videos")),
}
PHOTO_MAX_SIDE = 1280  # outfit photos are read once; a mid-size copy is plenty
REF = "|"  # "save|3f9a1c2e": the action, then the card it is for
ABOUT = "about:"  # "about:3" opens the card of the fourth place in the last plan


def incoming(update: dict) -> dict | None:
    """Reduce a Telegram update to {chat, kind, text, lat, lng, now}, or None to ignore it.
    Live-location updates arrive as edits to the original location message.
    """
    button = update.get("callback_query")
    if button:
        data, _, ref = button.get("data", "").partition(REF)
        if "message" not in button:
            return None
        if data in BUTTONS:
            label, intent = BUTTONS[data]
        elif data.startswith(ABOUT) and data[len(ABOUT):].isdigit():
            label, intent = "Tell me about it", Intent(intent="about", index=int(data[len(ABOUT):]))
        else:
            return None
        return {
            "chat": button["message"]["chat"]["id"],
            "kind": "button",
            "callback_id": button["id"],
            "intent": intent,
            "ref": ref,
            "now": datetime.now().astimezone().isoformat(timespec="seconds"),
            "text": label,
            "lat": None,
            "lng": None,
        }
    message = update.get("message") or update.get("edited_message")
    if not message:
        return None
    stamp = message.get("edit_date") or message["date"]
    out = {
        "chat": message["chat"]["id"],
        "now": datetime.fromtimestamp(stamp, timezone.utc).astimezone().isoformat(timespec="seconds"),
        "text": message.get("text", ""),
        "lat": None,
        "lng": None,
    }
    if message.get("photo"):
        # Telegram offers several sizes; take the largest that is not needlessly big
        sizes = sorted(message["photo"], key=lambda x: x.get("width", 0))
        fit = [x for x in sizes if max(x.get("width", 0), x.get("height", 0)) <= PHOTO_MAX_SIDE] or sizes[:1]
        return {**out, "kind": "photo", "file_id": fit[-1]["file_id"], "text": message.get("caption", "")}
    location = message.get("location")
    if location:
        live = "live_period" in location or "edited_message" in update
        return {**out, "kind": "live" if live else "pin",
                "lat": location["latitude"], "lng": location["longitude"]}
    if out["text"]:
        return {**out, "kind": "text"}
    return None


def render(reply: str) -> dict:
    """Telegram send parameters for a reply: bold title, link buttons, quick actions."""
    text = getattr(reply, "html", "") or html.escape(str(reply))
    params = {"text": text[:MAX_BODY], "parse_mode": "HTML", "disable_web_page_preview": True}
    rows = []
    if getattr(reply, "links", None):
        links = [{"text": label, "url": url} for label, url in reply.links]
        # a card's own links share a row; a longer list wraps two to a row so labels stay readable
        rows += [links] if len(links) <= 3 else [links[i:i + 2] for i in range(0, len(links), 2)]
    if getattr(reply, "actions", False):
        ref, hide = getattr(reply, "ref", ""), getattr(reply, "hide", ())
        # each button names its own card, so tapping one on an older card acts on that place
        actions = [{"text": label, "callback_data": f"{data}{REF}{ref}" if ref else data}
                   for data, (label, _) in BUTTONS.items() if data not in hide]
        rows += [actions[i:i + 2] for i in range(0, len(actions), 2)]
    # one place per row, so a long name is shown whole
    rows += [[{"text": messages.short(label, 48), "callback_data": f"{ABOUT}{key}"}]
             for label, key in getattr(reply, "choices", [])]
    if rows:
        params["reply_markup"] = {"inline_keyboard": rows}
    elif getattr(reply, "ask_location", False):
        params["reply_markup"] = {
            "keyboard": [[{"text": "📍 Share my location", "request_location": True}]],
            "resize_keyboard": True,
        }
    return params


def deliver(chat: int, reply: str) -> None:
    """Send a reply, with its picture when it has one. The text and buttons ride as the
    photo's caption when they fit; otherwise the photo goes first and the text follows.
    A picture that fails to send never costs the traveller the suggestion itself.
    """
    params = render(reply)
    photo = getattr(reply, "photo", None)
    if photo:
        try:
            if len(params["text"]) <= MAX_CAPTION:
                call("sendPhoto", chat_id=chat, photo=photo, caption=params["text"], parse_mode="HTML",
                     **({"reply_markup": params["reply_markup"]} if "reply_markup" in params else {}))
                return
            call("sendPhoto", chat_id=chat, photo=photo)
        except RuntimeError as e:
            print(f"photo not sent: {e}")
    call("sendMessage", chat_id=chat, **params)


def download(file_id: str) -> str:
    """Fetch a file the traveller sent, as base64. It is held in memory for one model call
    and never written to disk.
    """
    path = call("getFile", file_id=file_id)["file_path"]
    r = SESSION.get(f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{path}", timeout=30)
    if not r.ok:
        raise RuntimeError(f"telegram file download failed: {r.status_code}")
    return base64.b64encode(r.content).decode()


def allowed(chat: int) -> bool:
    """TELEGRAM_ALLOWED_CHATS is a comma-separated list of chat ids; unset means anyone."""
    listed = os.getenv("TELEGRAM_ALLOWED_CHATS", "").replace(" ", "")
    return not listed or str(chat) in listed.split(",")


@span(kind="WORKFLOW", name="telegram_message")
def respond(message: dict) -> str | None:
    """One update to at most one reply. Each update that does work is its own trace."""
    conn = assistant.user_db(f"telegram:{message['chat']}")
    try:
        if message["kind"] == "live":
            return assistant.on_live_location(conn, message["lat"], message["lng"], message["now"], run_once)
        chat = message["chat"]
        if message["kind"] == "photo":
            message = {**message, "image": download(message["file_id"]), "image_type": "image/jpeg"}
        return assistant.handle(
            conn, message, run_once, notify=lambda reply: deliver(chat, reply)
        )
    finally:
        conn.close()


TYPING_S = 4  # Telegram shows "typing..." for about five seconds per request


def keep_typing(chat: int, done: threading.Event) -> None:
    """Show "typing..." at once and keep it showing until the reply is ready."""
    while not done.is_set():
        try:
            call("sendChatAction", chat_id=chat, action="typing")
        except (requests.RequestException, RuntimeError):
            return  # cosmetic: never worth failing a reply over
        done.wait(TYPING_S)


def process(message: dict) -> None:
    if not allowed(message["chat"]):
        call("sendMessage", chat_id=message["chat"],
             text=f"This bot is private. Chat id {message['chat']} is not on its list.")
        return
    done = threading.Event()
    try:
        if message["kind"] == "button":
            call("answerCallbackQuery", callback_query_id=message["callback_id"])  # stops the button spinner
        if message["kind"] != "live":
            threading.Thread(target=keep_typing, args=(message["chat"], done), daemon=True).start()
        reply = respond(message)
    except Exception as e:  # the traveller should hear something even when a run fails
        print(f"error handling update: {type(e).__name__}: {e}")
        reply = "Something went wrong on my side. Try again in a moment."
    finally:
        done.set()
    if reply:
        deliver(message["chat"], reply)
    print(f"{message['now'][11:19]}  {message['kind']:<5} -> {'replied' if reply else 'stayed quiet'}")


def main() -> None:
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    me = call("getMe")
    call("setMyCommands", commands=[
        {"command": "start", "description": "What I can do"},
        {"command": "help", "description": "Things you can ask me"},
    ])
    print(f"@{me['username']} is listening. Open t.me/{me['username']} and send /start.")
    if not os.getenv("TELEGRAM_ALLOWED_CHATS"):
        print("TELEGRAM_ALLOWED_CHATS is not set: anyone who finds the bot can use it.")
    offset = None
    while True:
        try:
            updates = call("getUpdates", offset=offset, timeout=POLL_S,
                           allowed_updates=["message", "edited_message", "callback_query"])
        except (requests.RequestException, RuntimeError) as e:
            print(f"polling error, retrying: {type(e).__name__}")
            time.sleep(3)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            message = incoming(update)
            if message:
                process(message)


if __name__ == "__main__":
    main()
