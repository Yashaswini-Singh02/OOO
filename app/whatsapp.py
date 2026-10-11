"""WhatsApp front end. Two transports, each with its own webhook path:

    /whatsapp   Meta's WhatsApp Cloud API   (META_* settings)
    /twilio     Twilio's WhatsApp sandbox   (TWILIO_* settings)

    python -m app.whatsapp          # listens on :8000

Each incoming message is acknowledged at once and the reply is sent through the
provider's API from a background thread, because a graph run can take longer than a
webhook's time limit. A reply goes back through the transport the message came in on.
"""
import app.trace  # noqa: F401  MUST be the first import in every entry point

import base64
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests
from neatlogs import span

from app import assistant
from app.graph import run_once

PORT = int(os.getenv("PORT", "8000"))
PATH = "/whatsapp"
TWILIO_PATH = "/twilio"
MAX_BODY = 4000  # WhatsApp text messages are capped at 4096 characters
TWILIO_MAX_BODY = 1500  # Twilio caps WhatsApp messages near 1600 characters
META_SETTINGS = ("META_ACCESS_TOKEN", "META_PHONE_NUMBER_ID", "META_APP_SECRET", "META_VERIFY_TOKEN")
TWILIO_SETTINGS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "PUBLIC_URL")


def signature_ok(raw: bytes, header: str) -> bool:
    """Meta signs each POST: 'sha256=' + HMAC-SHA256 of the raw body with the app secret."""
    secret = os.getenv("META_APP_SECRET", "")
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(expected, header)


def incoming(payload: dict) -> list:
    """Pull the traveller's messages out of a webhook payload as {from, text, lat, lng}.
    Delivery and read receipts arrive on the same webhook and carry no messages.
    """
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                location = m.get("location", {})
                if m.get("type") not in ("text", "location"):
                    continue
                out.append(
                    {
                        "from": m["from"],
                        "text": m.get("text", {}).get("body", ""),
                        "lat": location.get("latitude"),
                        "lng": location.get("longitude"),
                    }
                )
    return out


def delivery_failures(payload: dict) -> list:
    """Meta accepts a send and reports later, on this webhook, if delivery failed."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for status in change.get("value", {}).get("statuses", []):
                for e in status.get("errors", []):
                    detail = (e.get("error_data") or {}).get("details") or e.get("title", "")
                    out.append(f"{e.get('code')} {detail}")
    return out


def send(to: str, body: str) -> None:
    """Send one WhatsApp text message through the Graph API."""
    version = os.getenv("META_GRAPH_VERSION", "v23.0")
    r = requests.post(
        f"https://graph.facebook.com/{version}/{os.environ['META_PHONE_NUMBER_ID']}/messages",
        headers={"Authorization": f"Bearer {os.environ['META_ACCESS_TOKEN']}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body[:MAX_BODY], "preview_url": True},
        },
        timeout=20,
    )
    if not r.ok:
        print(f"whatsapp send failed: {r.status_code} {r.text[:300]}")


def twilio_signature_ok(params: dict, header: str) -> bool:
    """Twilio signs each request: HMAC-SHA1 over the public URL plus the sorted form fields."""
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    url = os.getenv("PUBLIC_URL", "").rstrip("/") + TWILIO_PATH
    payload = url + "".join(k + params[k] for k in sorted(params))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest())
    return bool(token) and hmac.compare_digest(expected.decode(), header)


def twilio_incoming(form: dict) -> dict:
    return {
        "from": form["From"],  # "whatsapp:+91..."
        "text": form.get("Body", ""),
        "lat": float(form["Latitude"]) if form.get("Latitude") else None,
        "lng": float(form["Longitude"]) if form.get("Longitude") else None,
        "via": "twilio",
    }


def send_twilio(to: str, body: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, os.environ["TWILIO_AUTH_TOKEN"]),
        data={"From": os.environ["TWILIO_WHATSAPP_FROM"], "To": to, "Body": body[:TWILIO_MAX_BODY]},
        timeout=20,
    )
    if not r.ok:
        print(f"twilio send failed: {r.status_code} {r.text[:300]}")


@span(kind="WORKFLOW", name="whatsapp_message")
def respond(message: dict) -> str:
    """One incoming message to one reply. Each message is its own trace."""
    conn = assistant.user_db(message["from"])
    try:
        return assistant.handle(
            conn,
            {
                "text": message["text"],
                "lat": message["lat"],
                "lng": message["lng"],
                "now": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            run_once,
        )
    finally:
        conn.close()


def process(message: dict) -> None:
    try:
        reply = respond(message)
    except Exception as e:  # the traveller should hear something even when a run fails
        print(f"error handling message: {type(e).__name__}: {e}")
        reply = "Something went wrong on my side. Try again in a moment."
    (send_twilio if message.get("via") == "twilio" else send)(message["from"], reply)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Webhook verification: Meta sends a challenge and expects it echoed back."""
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        ok = (
            url.path == PATH
            and q.get("hub.mode") == "subscribe"
            and hmac.compare_digest(q.get("hub.verify_token", ""), os.getenv("META_VERIFY_TOKEN", ""))
        )
        if not ok:
            self.send_error(403)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(q.get("hub.challenge", "").encode())

    def do_POST(self):
        path = urlparse(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if path == TWILIO_PATH:
            form = {k: v[0] for k, v in parse_qs(raw.decode(), keep_blank_values=True).items()}
            if not twilio_signature_ok(form, self.headers.get("X-Twilio-Signature", "")):
                self.send_error(403, "bad signature")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.end_headers()
            self.wfile.write(b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>')
            threading.Thread(target=process, args=(twilio_incoming(form),), daemon=True).start()
            return
        if path != PATH:
            self.send_error(404)
            return
        if not signature_ok(raw, self.headers.get("X-Hub-Signature-256", "")):
            self.send_error(403, "bad signature")
            return

        self.send_response(200)
        self.end_headers()
        try:
            payload = json.loads(raw)
            messages = incoming(payload)
        except (ValueError, KeyError, AttributeError):
            return
        for problem in delivery_failures(payload):
            print(f"whatsapp delivery failed: {problem}")
        for message in messages:
            threading.Thread(target=process, args=(message,), daemon=True).start()

    def log_message(self, fmt, *args):  # keep the console to our own lines
        pass


if __name__ == "__main__":
    meta = all(os.getenv(name) for name in META_SETTINGS)
    twilio = all(os.getenv(name) for name in TWILIO_SETTINGS)
    if not (meta or twilio):
        raise SystemExit("set either the META_* or the TWILIO_* settings in .env (see .env.example)")
    print(f"listening on :{PORT}  meta {PATH}: {'on' if meta else 'off'}  twilio {TWILIO_PATH}: {'on' if twilio else 'off'}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
