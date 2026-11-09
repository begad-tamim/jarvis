"""The JARVIS hub: one tiny, locked-down HTTP server (stdlib only) for every screen.

Security model
--------------
- Binds to 127.0.0.1 by default. Exposing it (phone over Tailscale, Twilio
  webhooks) is an explicit, documented choice.
- Every /api call needs a bearer token (random per install, stored 0600).
  It travels in the Authorization header - never a cookie - so other
  websites can't ride your session (no CSRF).
- /voice/* webhooks carry no token; instead each request must be signed by
  Twilio (HMAC-SHA1 of the exact URL + params with your auth token) and come
  from your own phone number.
- Host header allow-list blocks DNS-rebinding attacks.
- Strict CSP, no third-party scripts, no inline event handlers.
- Request bodies are capped. Projects are addressed by id, never by path.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import secrets
import urllib.parse
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.server")
WEB = Path(__file__).parent / "web"
MAX_BODY = 16_384
# Static files are an explicit allow-list: no path from the request ever touches the filesystem.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/markets.js": ("markets.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; manifest-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
}


def load_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        tok = path.read_text().strip()
        if len(tok) >= 32:
            return tok
    tok = secrets.token_urlsafe(32)
    path.write_text(tok)
    path.chmod(0o600)
    return tok


@dataclass
class Hub:
    """Everything the server can reach. `engine` and `voice` are optional modules."""

    assistant: Any
    bus: Any
    engine: Any = None
    voice: Any = None
    telegram: Any = None
    version: str = ""
    demo: bool = False

    def state(self) -> dict[str, Any]:
        from .brains import detect

        a = self.assistant
        return {
            "version": self.version,
            "demo": self.demo,
            "brain": {"name": a.brain.name, "label": a.brain.label},
            "brains": detect(),
            "channels": {
                "telegram": bool(self.telegram and self.telegram.ready),
                "call_out": bool(self.voice and self.voice.ready),
                "call_in": bool(self.voice and self.voice.inbound_ready),
            },
            "markets": self.engine is not None,
            "active": a.active,
            "busy": a.busy,
            "briefing": a.briefing(),
            "projects": a.registry.all(),
            "messages": a.recent(),
        }


def make_handler(hub: Hub, token: str, allowed_hosts: set[str]):
    engine = hub.engine

    class Handler(BaseHTTPRequestHandler):
        server_version = "JARVIS"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet, and never log query strings
            log.debug("%s %s", self.command, self.path.split("?")[0])

        # ---------------------------------------------------------- helpers
        def _headers(self, status: int, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()

        def _json(self, status: int, obj: Any) -> None:
            body = json.dumps(obj, default=str).encode()
            self._headers(status, "application/json", {"Content-Length": str(len(body))})
            self.wfile.write(body)

        def _xml(self, body: str) -> None:
            data = body.encode()
            self._headers(200, "text/xml; charset=utf-8", {"Content-Length": str(len(data))})
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in allowed_hosts

        def _authed(self) -> bool:
            h = self.headers.get("Authorization", "")
            return h.startswith("Bearer ") and hmac.compare_digest(h[7:].encode(), token.encode())

        def _guard(self) -> bool:
            if not self._host_ok():
                self._json(403, {"error": "host not allowed"})
                return False
            if self.path.startswith("/api/") and not self._authed():
                self._json(401, {"error": "missing or bad token"})
                return False
            return True

        def _query(self) -> dict[str, str]:
            return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

        def _body(self) -> bytes | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._json(413, {"error": "body too large"})
                return None
            return self.rfile.read(n) if n else b""

        # ---------------------------------------------------------- GET
        def do_GET(self) -> None:  # noqa: N802
            if not self._guard():
                return
            path = self.path.split("?", 1)[0]
            if path == "/api/hub":
                return self._json(200, hub.state())
            if path == "/api/project":
                view = hub.assistant.project_view(self._query().get("id", ""))
                return self._json(200, view) if view else self._json(404, {"error": "unknown project"})
            if path == "/api/stream":
                return self._stream()
            if path == "/api/health":
                return self._json(200, {"ok": True, "markets": engine.status if engine else None})
            if path == "/api/snapshot":
                return self._json(200, engine.snapshot()) if engine else self._json(404, {"error": "markets module is off"})
            asset = STATIC.get(path)
            if asset is None or not (WEB / asset[0]).is_file():
                return self._json(404, {"error": "not found"})
            data = (WEB / asset[0]).read_bytes()
            self._headers(200, asset[1], {"Content-Length": str(len(data))})
            self.wfile.write(data)

        def _stream(self) -> None:
            q = hub.bus.subscribe()
            try:
                self._headers(200, "text/event-stream", {"Connection": "keep-alive"})
                if engine is not None:
                    self.wfile.write(f"event: snapshot\ndata: {json.dumps(engine.snapshot(), default=str)}\n\n".encode())
                self.wfile.write(b"event: hello\ndata: {}\n\n")
                self.wfile.flush()
                while True:
                    try:
                        kind, payload = q.get(timeout=15)
                        msg = f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
                    except queue.Empty:
                        msg = ": keep-alive\n\n"
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                hub.bus.unsubscribe(q)

        # ---------------------------------------------------------- POST
        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                return self._json(403, {"error": "host not allowed"})
            path = self.path.split("?", 1)[0]
            if path in ("/voice/incoming", "/voice/answer"):
                return self._voice(path)
            if not self._guard():
                return
            if (self.headers.get("Content-Type") or "").split(";")[0] != "application/json":
                return self._json(415, {"error": "application/json required"})
            raw = self._body()
            if raw is None:
                return
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "bad json"})
            a = hub.assistant
            pid = str(body.get("project") or "") or None
            if path == "/api/ask":
                try:
                    src = body.get("source") if body.get("source") in ("hud", "phone", "voice") else "hud"
                    return self._json(200, asdict(a.ask(str(body.get("text") or ""), pid, src)))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
            if path == "/api/active":
                a.active = pid if a.registry.get(pid) else None
                hub.bus.publish("active", {"project": a.active})
                return self._json(200, {"active": a.active})
            if path == "/api/note" and a.registry.get(pid) and str(body.get("text") or "").strip():
                return self._json(200, a.add_note(pid, str(body["text"])))
            if path == "/api/task" and a.registry.get(pid) and str(body.get("text") or "").strip():
                return self._json(200, a.add_task(pid, str(body["text"])))
            if path == "/api/task/toggle" and a.registry.get(pid):
                return self._json(200, a.toggle_task(pid, str(body.get("id") or "")))
            if path == "/api/call":
                if not (hub.voice and hub.voice.ready):
                    return self._json(409, {"error": "phone calls are not configured (see README: JARVIS can call you)"})
                hub.voice.call(str(body.get("text") or "This is JARVIS checking in.")[:500])
                return self._json(200, {"ok": True})
            if engine is not None:
                if path == "/api/kill":
                    reason = str(body.get("reason") or "kill switch pressed on the HUD")[:200]
                    return self._json(200, engine.kill(reason, bool(body.get("liquidate", True))))
                if path == "/api/pause":
                    engine.pause()
                    return self._json(200, {"ok": True})
                if path == "/api/resume":
                    engine.resume()
                    return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

        def _voice(self, path: str) -> None:
            v = hub.voice
            if not (v and v.inbound_ready):
                return self._json(404, {"error": "not found"})
            if (self.headers.get("Content-Type") or "").split(";")[0] != "application/x-www-form-urlencoded":
                return self._json(415, {"error": "form body required"})
            raw = self._body()
            if raw is None:
                return
            params = {k: vals[0] for k, vals in urllib.parse.parse_qs(raw.decode(errors="replace"), keep_blank_values=True).items()}
            if not v.verify(self.path, params, self.headers.get("X-Twilio-Signature", "")):
                log.warning("rejected unsigned voice webhook")
                return self._json(403, {"error": "bad signature"})
            return self._xml(v.webhook(path, params))

    return Handler


def serve(hub: Hub, host: str, port: int, token: str) -> ThreadingHTTPServer:
    allowed = {"127.0.0.1", "localhost", "::1"}
    if host not in ("127.0.0.1", "localhost", "::1"):
        allowed |= {host}
        log.warning("HUD bound to %s - make sure this network is trusted.", host)
    extra = os.environ.get("JARVIS_ALLOWED_HOSTS", "")
    allowed |= {h.strip().lower() for h in extra.split(",") if h.strip()}
    if hub.voice is not None and hub.voice.public_url:
        allowed.add((urllib.parse.urlsplit(hub.voice.public_url).hostname or "").lower())
    httpd = ThreadingHTTPServer((host, port), make_handler(hub, token, allowed))
    httpd.daemon_threads = True
    return httpd
