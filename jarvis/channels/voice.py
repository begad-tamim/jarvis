"""Phone calls with JARVIS, both ways, through Twilio (optional, bring your own account).

- JARVIS calls you: `jarvis call "build is green"`, "call me" in chat, or an
  automatic call when the trading kill switch fires.
- You call JARVIS: point your Twilio number's voice webhook at
  {JARVIS_PUBLIC_URL}/voice/incoming and just talk. Speech goes to the same
  assistant as the HUD and Telegram, and the answer is read back to you.

Security: every webhook must carry a valid X-Twilio-Signature (HMAC-SHA1 with
your auth token), and only JARVIS_OWNER_PHONE is allowed to talk to it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
from typing import Any
from xml.sax.saxutils import escape

from ..http import request_json

log = logging.getLogger("jarvis.voice")
VOICE = "Polly.Brian"  # a calm British voice, very on-brand
GREETING = "JARVIS here. What do you need?"


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's documented scheme: base64(HMAC-SHA1(token, url + sorted key/value pairs))."""
    payload = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def say(text: str) -> str:
    return f'<Say voice="{VOICE}">{escape(text[:1500])}</Say>'


def gather(action: str, prompt: str = "") -> str:
    inner = say(prompt) if prompt else ""
    return f'<Gather input="speech" action="{escape(action)}" method="POST" speechTimeout="auto" language="en-US">{inner}</Gather>'


def twiml(*parts: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(parts) + "</Response>"


class Voice:
    def __init__(self, assistant: Any = None):
        e = os.environ
        self.sid = e.get("TWILIO_ACCOUNT_SID", "")
        self.token = e.get("TWILIO_AUTH_TOKEN", "")
        self.from_ = e.get("TWILIO_FROM", "")
        self.owner = e.get("JARVIS_OWNER_PHONE", "")
        self.public_url = e.get("JARVIS_PUBLIC_URL", "").rstrip("/")
        self.assistant = assistant

    @property
    def ready(self) -> bool:
        return bool(self.sid and self.token and self.from_ and self.owner)

    @property
    def inbound_ready(self) -> bool:
        return self.ready and self.public_url.startswith("https://")

    # ------------------------------------------------------------------ outbound
    def call(self, message: str, to: str | None = None) -> None:
        if not self.ready:
            raise RuntimeError("Twilio is not configured")
        threading.Thread(target=self._call, args=(message, to or self.owner), daemon=True).start()

    def call_twiml(self, message: str) -> str:
        if self.inbound_ready:  # let you answer back on the same call
            return twiml(say(message), gather(f"{self.public_url}/voice/answer", "Anything else?"), say("Goodbye."))
        return twiml(say(message))

    def _call(self, message: str, to: str) -> None:
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        try:
            request_json(
                "POST", f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json",
                headers={"Authorization": f"Basic {auth}"}, form={"To": to, "From": self.from_, "Twiml": self.call_twiml(message)},
                retries=0, timeout=20,
            )  # fmt: skip
            log.info("call placed")
        except Exception as e:  # noqa: BLE001
            log.warning("call failed: %s", e)

    # ------------------------------------------------------------------ inbound webhooks
    def verify(self, path_and_query: str, params: dict[str, str], signature: str) -> bool:
        if not self.token or not self.public_url or not signature:
            return False
        expected = twilio_signature(self.token, self.public_url + path_and_query, params)
        return hmac.compare_digest(expected, signature)

    def webhook(self, path: str, params: dict[str, str]) -> str:
        """Return TwiML for /voice/incoming and /voice/answer. Caller must have verified the signature."""
        caller = params.get("From", "")
        if self.owner and caller != self.owner and params.get("Direction", "inbound") == "inbound":
            return twiml(say("Sorry, this line is private."), "<Hangup/>")
        action = f"{self.public_url}/voice/answer"
        if path == "/voice/incoming":
            return twiml(gather(action, GREETING), say("I didn't catch that. Goodbye."))
        speech = (params.get("SpeechResult") or "").strip()
        if not speech:
            return twiml(gather(action, "Sorry, say that again?"))
        if speech.lower().strip(" .!") in ("bye", "goodbye", "that's all", "thanks jarvis", "thank you", "nothing", "no"):
            return twiml(say("Anytime. Goodbye."), "<Hangup/>")
        reply = self.assistant.ask(speech, source="call").text if self.assistant else "My assistant is offline."
        return twiml(say(reply), gather(action, "Anything else?"), say("Goodbye."))
