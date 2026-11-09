"""Tiny stdlib HTTP client with retries, timeouts and secret-safe errors."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "jarvis-trading-agent/1.0 (+https://github.com/begad-tamim/jarvis-trading-agent)"


class HTTPError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    body: Any = None,
    timeout: float = 15.0,
    retries: int = 3,
) -> Any:
    parts = urllib.parse.urlsplit(url)
    local = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError("only https URLs (or http to localhost) are allowed")
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)  # noqa: S310 - scheme checked above
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:300]
            try:
                msg = json.loads(text).get("message", text)
            except (ValueError, AttributeError):
                msg = text
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = float(e.headers.get("Retry-After") or 0) or (2**attempt + random.random())
                time.sleep(min(wait, 30))
                last_exc = HTTPError(e.code, msg)
                continue
            raise HTTPError(e.code, msg) from None  # never chain: the request may carry auth headers
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(2**attempt + random.random())
                continue
    raise ConnectionError(f"{method} {urllib.parse.urlsplit(url).netloc} failed: {last_exc}")
