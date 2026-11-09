"""The HUD dashboard: a tiny, locked-down HTTP server (stdlib only).

Security model
--------------
- Binds to 127.0.0.1 by default. Exposing it is an explicit choice.
- Every /api call needs a bearer token (random per install, stored 0600).
  It travels in the Authorization header - never a cookie - so other
  websites can't ride your session (no CSRF).
- Host header allow-list blocks DNS-rebinding attacks.
- Strict CSP, no third-party scripts, no inline event handlers.
- Request bodies are capped and parsed as JSON only.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import queue
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.server")
WEB = Path(__file__).parent / "web"
MAX_BODY = 4096

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
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


def make_handler(engine: Any, token: str, allowed_hosts: set[str]):
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

        # ---------------------------------------------------------- GET
        def do_GET(self) -> None:  # noqa: N802
            if not self._guard():
                return
            path = self.path.split("?", 1)[0]
            if path == "/api/snapshot":
                return self._json(200, engine.snapshot())
            if path == "/api/stream":
                return self._stream()
            if path == "/api/health":
                return self._json(200, {"ok": True, "status": engine.status})
            name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            f = (WEB / name).resolve()
            if not f.is_file() or WEB.resolve() not in f.parents:
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            data = f.read_bytes()
            self._headers(200, ctype, {"Content-Length": str(len(data))})
            self.wfile.write(data)

        def _stream(self) -> None:
            q = engine.subscribe()
            try:
                self._headers(200, "text/event-stream", {"Connection": "keep-alive"})
                self.wfile.write(f"event: snapshot\ndata: {json.dumps(engine.snapshot(), default=str)}\n\n".encode())
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
                engine.unsubscribe(q)

        # ---------------------------------------------------------- POST
        def do_POST(self) -> None:  # noqa: N802
            if not self._guard():
                return
            if (self.headers.get("Content-Type") or "").split(";")[0] != "application/json":
                return self._json(415, {"error": "application/json required"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            path = self.path.split("?", 1)[0]
            if path == "/api/kill":
                return self._json(
                    200, engine.kill(str(body.get("reason") or "kill switch pressed on dashboard")[:200], bool(body.get("liquidate", True)))
                )
            if path == "/api/pause":
                engine.pause()
                return self._json(200, {"ok": True})
            if path == "/api/resume":
                engine.resume()
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

    return Handler


def serve(engine: Any, host: str, port: int, token: str) -> ThreadingHTTPServer:
    allowed = {"127.0.0.1", "localhost", "::1"}
    if host not in ("127.0.0.1", "localhost", "::1"):
        allowed |= {host}
        log.warning("Dashboard bound to %s - make sure this network is trusted.", host)
    httpd = ThreadingHTTPServer((host, port), make_handler(engine, token, allowed))
    httpd.daemon_threads = True
    return httpd
