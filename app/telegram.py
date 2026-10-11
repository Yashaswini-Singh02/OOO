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

import html
import os
import time
from datetime import datetime, timezone

import requests
import urllib3.util.connection
from neatlogs import span

from app import assistant
from app.assistant import Intent
from app.graph import run_once

# On networks where IPv6 to Telegram hangs, each new connection would stall for
# seconds before falling back. Use IPv4 for this process and reuse one connection.
urllib3.util.connection.HAS_IPV6 = False
SESSION = requests.Session()

API = "https://api.telegram.org/bot{token}/{method}"
POLL_S = 30
MAX_BODY = 4000  # Telegram caps a message at 4096 characters


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
    "rate:5": ("Loved it", Intent(intent="rate", rating=5)),
    "rate:2": ("Not for me", Intent(intent="rate", rating=2)),
    "save": ("Save for later", Intent(intent="save")),
}


def incoming(update: dict) -> dict | None:
    """Reduce a Telegram update to {chat, kind, text, lat, lng, now}, or None to ignore it.
    Live-location updates arrive as edits to the original location message.
    """
    button = update.get("callback_query")
    if button:
        if button.get("data") not in BUTTONS or "message" not in button:
            return None
        return {
            "chat": button["message"]["chat"]["id"],
            "kind": "button",
            "callback_id": button["id"],
            "intent": BUTTONS[button["data"]][1],
            "now": datetime.now().astimezone().isoformat(timespec="seconds"),
            "text": BUTTONS[button["data"]][0],
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
    title, body = getattr(reply, "title", ""), getattr(reply, "body", str(reply))
    text = (f"<b>{html.escape(title)}</b>\n" if title else "") + html.escape(body)
    params = {"text": text[:MAX_BODY], "parse_mode": "HTML", "disable_web_page_preview": True}
    rows = []
    if getattr(reply, "links", None):
        rows.append([{"text": label, "url": url} for label, url in reply.links])
    if getattr(reply, "actions", False):
        rows.append([{"text": label, "callback_data": data} for data, (label, _) in BUTTONS.items()])
    if rows:
        params["reply_markup"] = {"inline_keyboard": rows}
    elif getattr(reply, "ask_location", False):
        params["reply_markup"] = {
            "keyboard": [[{"text": "Share my location", "request_location": True}]],
            "resize_keyboard": True,
        }
    return params


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
        return assistant.handle(
            conn, message, run_once, notify=lambda text: call("sendMessage", chat_id=chat, text=text)
        )
    finally:
        conn.close()


def process(message: dict) -> None:
    if not allowed(message["chat"]):
        call("sendMessage", chat_id=message["chat"],
             text=f"This bot is private. Chat id {message['chat']} is not on its list.")
        return
    try:
        if message["kind"] == "button":
            call("answerCallbackQuery", callback_query_id=message["callback_id"])  # stops the button spinner
        if message["kind"] != "live":
            # shows "typing..." in the chat at once, while the message is being understood
            call("sendChatAction", chat_id=message["chat"], action="typing")
        reply = respond(message)
    except Exception as e:  # the traveller should hear something even when a run fails
        print(f"error handling update: {type(e).__name__}: {e}")
        reply = "Something went wrong on my side. Try again in a moment."
    if reply:
        call("sendMessage", chat_id=message["chat"], **render(reply))
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
