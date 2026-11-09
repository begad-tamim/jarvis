import threading
import urllib.parse
import urllib.request

import pytest
from test_server import TOKEN, make_hub

from jarvis.channels import telegram as tg_mod
from jarvis.channels.telegram import TelegramBot
from jarvis.channels.voice import Voice, twilio_signature
from jarvis.server import serve


def test_twilio_signature_matches_documented_example():
    # https://www.twilio.com/docs/usage/security#validating-requests
    params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234", "From": "+12349013030", "To": "+18005551212"}
    assert twilio_signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", params) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


class FakeAssistant:
    def __init__(self):
        self.asked = []

    def ask(self, text, project=None, source="hud"):
        self.asked.append((text, source))
        return type("M", (), {"text": f"echo {text}"})()


def test_telegram_ignores_strangers(monkeypatch):
    sent = []
    monkeypatch.setattr(tg_mod, "request_json", lambda m, url, **kw: sent.append(kw["body"]))
    a = FakeAssistant()
    bot = TelegramBot("123:abc", "42", a)
    assert bot.handle({"message": {"chat": {"id": 666}, "text": "/brief"}}) is None and not a.asked and not sent
    assert bot.handle({"message": {"chat": {"id": 42}, "text": "how is orbit?"}}) == "echo how is orbit?"
    assert a.asked == [("how is orbit?", "telegram")] and sent[0]["chat_id"] == "42"


@pytest.fixture
def voice_srv(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token")
    monkeypatch.setenv("TWILIO_FROM", "+15550000000")
    monkeypatch.setenv("JARVIS_OWNER_PHONE", "+15551112222")
    monkeypatch.setenv("JARVIS_PUBLIC_URL", "https://jarvis.example.ts.net")
    v = Voice()
    hub = make_hub(voice=v)
    v.assistant = hub.assistant
    httpd = serve(hub, "127.0.0.1", 0, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", v
    httpd.shutdown()


def post_form(url, path, params, sig=None, host="jarvis.example.ts.net"):
    req = urllib.request.Request(url + path, data=urllib.parse.urlencode(params).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Host", host)
    if sig:
        req.add_header("X-Twilio-Signature", sig)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_voice_webhook_rejects_unsigned(voice_srv):
    url, _ = voice_srv
    assert post_form(url, "/voice/incoming", {"From": "+15551112222"})[0] == 403
    assert post_form(url, "/voice/incoming", {"From": "+15551112222"}, sig="forged")[0] == 403


def test_voice_call_in_conversation(voice_srv):
    url, v = voice_srv
    p = {"From": "+15551112222", "Direction": "inbound"}
    st, body = post_form(url, "/voice/incoming", p, twilio_signature("secret-token", v.public_url + "/voice/incoming", p))
    assert st == 200 and "<Gather" in body and "JARVIS here" in body
    p2 = {**p, "SpeechResult": "brief me"}
    st, body = post_form(url, "/voice/answer", p2, twilio_signature("secret-token", v.public_url + "/voice/answer", p2))
    assert st == 200 and "<Say" in body and "Anything else?" in body


def test_voice_blocks_other_callers(voice_srv):
    url, v = voice_srv
    p = {"From": "+19998887777", "Direction": "inbound"}
    st, body = post_form(url, "/voice/incoming", p, twilio_signature("secret-token", v.public_url + "/voice/incoming", p))
    assert "private" in body and "<Hangup/>" in body


def test_outbound_call_twiml_escapes_text(monkeypatch):
    monkeypatch.setenv("JARVIS_PUBLIC_URL", "")
    v = Voice()
    assert "&lt;script&gt;" in v.call_twiml("<script>") and "<Gather" not in v.call_twiml("hi")


def test_kill_switch_phones_the_owner(monkeypatch, tmp_path):
    from test_engine import demo_engine

    from jarvis.cli import build_hub
    from jarvis.config import Config
    from jarvis.journal import Journal
    from jarvis.projects import Registry

    for k, v in {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_FROM": "+1", "JARVIS_OWNER_PHONE": "+2"}.items():
        monkeypatch.setenv(k, v)
    calls = []
    monkeypatch.setattr(Voice, "call", lambda self, msg, to=None: calls.append(msg))
    _, e = demo_engine()
    e.tick()
    build_hub(Config(), engine=e, registry=Registry([]), journal=Journal(":memory:"))
    e.kill("drawdown limit")
    assert e.risk.state.killed and calls and "drawdown limit" in calls[0]
