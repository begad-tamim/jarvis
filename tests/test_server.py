import json
import threading
import urllib.error
import urllib.request

import pytest
from test_engine import demo_engine

from jarvis.assistant import Assistant
from jarvis.brains import OfflineBrain
from jarvis.bus import Bus
from jarvis.journal import Journal
from jarvis.projects import Registry
from jarvis.server import Hub, serve

TOKEN = "t" * 43


def make_hub(engine=None, registry=None, voice=None):
    bus = Bus()
    if engine is not None:
        engine.bus = bus
    a = Assistant(registry or Registry([]), OfflineBrain(), Journal(":memory:"), bus, engine, voice)
    return Hub(a, bus, engine, voice, None, "test")


@pytest.fixture
def srv():
    m, e = demo_engine()
    e.tick()
    httpd = serve(make_hub(e), "127.0.0.1", 0, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", e
    httpd.shutdown()


def call(url, token=TOKEN, method="GET", body=None, host=None, ctype="application/json"):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", ctype)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_api_requires_token(srv):
    url, _ = srv
    assert call(url + "/api/snapshot", token=None)[0] == 401
    assert call(url + "/api/snapshot", token="wrong")[0] == 401
    st, _, body = call(url + "/api/snapshot")
    assert st == 200 and "account" in json.loads(body)


def test_dns_rebinding_blocked(srv):
    url, _ = srv
    assert call(url + "/api/snapshot", host="evil.example.com")[0] == 403


def test_security_headers_and_static(srv):
    url, _ = srv
    st, h, body = call(url + "/", token=None)
    assert st == 200 and b"JARVIS" in body
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["X-Frame-Options"] == "DENY"
    assert call(url + "/../server.py", token=None)[0] == 404


def test_kill_requires_json_and_works(srv):
    url, e = srv
    assert call(url + "/api/kill", method="POST", body={}, ctype="text/plain")[0] == 415
    st, _, _ = call(url + "/api/kill", method="POST", body={"reason": "test"})
    assert st == 200 and e.risk.state.killed
    call(url + "/api/resume", method="POST", body={})
    assert not e.risk.state.killed


def test_hub_state_and_ask(srv):
    url, _ = srv
    st, _, body = call(url + "/api/hub")
    j = json.loads(body)
    assert st == 200 and j["markets"] is True and j["brain"]["name"] == "offline"
    st, _, body = call(url + "/api/ask", method="POST", body={"text": "markets"})
    assert st == 200 and "Equity $" in json.loads(body)["text"]
    assert call(url + "/api/ask", method="POST", body={"text": "  "})[0] == 400
    assert call(url + "/api/ask", method="POST", body=["not", "a", "dict"])[0] == 400


def test_body_cap(srv):
    url, _ = srv
    assert call(url + "/api/ask", method="POST", body={"text": "x" * 20000})[0] == 413


def test_microphone_allowed_for_voice_mode(srv):
    url, _ = srv
    _, h, _ = call(url + "/", token=None)
    assert "microphone=(self)" in h["Permissions-Policy"]
