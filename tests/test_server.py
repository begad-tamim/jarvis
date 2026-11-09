import json
import threading
import urllib.error
import urllib.request

import pytest
from test_engine import demo_engine

from jarvis.server import serve

TOKEN = "t" * 43


@pytest.fixture
def srv():
    m, e = demo_engine()
    e.tick()
    httpd = serve(e, "127.0.0.1", 0, TOKEN)
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
