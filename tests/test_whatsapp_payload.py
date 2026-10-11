import hashlib
import hmac
import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch


def load_whatsapp():
    """Import app.whatsapp without starting tracing: stub the two modules that would."""
    stubs = {
        "app.trace": types.ModuleType("app.trace"),
        "app.graph": types.ModuleType("app.graph"),
    }
    stubs["app.graph"].run_once = lambda context: {}
    with patch.dict(sys.modules, stubs):
        sys.modules.pop("app.whatsapp", None)
        module = importlib.import_module("app.whatsapp")
    sys.modules.pop("app.whatsapp", None)
    return module


def payload(message: dict) -> dict:
    return {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}


class WhatsAppPayloadTest(unittest.TestCase):
    wa = load_whatsapp()

    def test_text_and_location_messages_are_read(self):
        text = self.wa.incoming(payload({"from": "911234567890", "type": "text", "text": {"body": "hi"}}))
        self.assertEqual(text, [{"from": "911234567890", "text": "hi", "lat": None, "lng": None}])
        pin = self.wa.incoming(payload(
            {"from": "911234567890", "type": "location", "location": {"latitude": 15.4989, "longitude": 73.8313}}
        ))
        self.assertEqual((pin[0]["lat"], pin[0]["lng"]), (15.4989, 73.8313))

    def test_receipts_and_other_message_types_are_ignored(self):
        self.assertEqual(self.wa.incoming({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}), [])
        self.assertEqual(self.wa.incoming(payload({"from": "91", "type": "image"})), [])

    def test_delivery_failures_are_surfaced(self):
        failed = {"entry": [{"changes": [{"value": {"statuses": [{"status": "failed", "errors": [
            {"code": 131031, "title": "Business Account locked",
             "error_data": {"details": "Business account has been locked."}}]}]}}]}]}
        self.assertEqual(self.wa.delivery_failures(failed), ["131031 Business account has been locked."])
        self.assertEqual(self.wa.delivery_failures(payload({"from": "91", "type": "text", "text": {"body": "hi"}})), [])

    def test_twilio_messages_and_signature(self):
        form = {"From": "whatsapp:+911234567890", "Body": "hi", "Latitude": "15.4989", "Longitude": "73.8313"}
        self.assertEqual(self.wa.twilio_incoming(form),
                         {"from": "whatsapp:+911234567890", "text": "hi", "lat": 15.4989, "lng": 73.8313, "via": "twilio"})
        import base64
        url = "https://example.test/twilio"
        signed = url + "".join(k + form[k] for k in sorted(form))
        good = base64.b64encode(hmac.new(b"token", signed.encode(), hashlib.sha1).digest()).decode()
        with patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "token", "PUBLIC_URL": "https://example.test"}):
            self.assertTrue(self.wa.twilio_signature_ok(form, good))
            self.assertFalse(self.wa.twilio_signature_ok({**form, "Body": "changed"}, good))

    def test_signature_must_match_the_app_secret(self):
        raw = b'{"entry": []}'
        good = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"META_APP_SECRET": "secret"}):
            self.assertTrue(self.wa.signature_ok(raw, good))
            self.assertFalse(self.wa.signature_ok(raw + b" ", good))
            self.assertFalse(self.wa.signature_ok(raw, ""))
        with patch.dict(os.environ, {"META_APP_SECRET": ""}):
            self.assertFalse(self.wa.signature_ok(raw, good))


if __name__ == "__main__":
    unittest.main()
