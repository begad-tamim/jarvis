"""Alpaca Market Data v2 (free IEX feed works with a free account)."""

from __future__ import annotations

import datetime as dt

from ..http import request_json
from ..models import Bar

DATA_URL = "https://data.alpaca.markets"

_TF_MINUTES = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 1440}


def _ts(s: str) -> float:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class AlpacaData:
    name = "alpaca"

    def __init__(self, key_id: str, secret: str, timeframe: str = "15Min", feed: str = "iex"):
        if timeframe not in _TF_MINUTES:
            raise ValueError(f"timeframe must be one of {sorted(_TF_MINUTES)}")
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self.timeframe = timeframe
        self.feed = feed

    def __repr__(self) -> str:  # never leak keys into logs
        return f"AlpacaData(timeframe={self.timeframe!r}, feed={self.feed!r})"

    def get_bars(self, symbols: list[str], limit: int = 200) -> dict[str, list[Bar]]:
        minutes = _TF_MINUTES[self.timeframe]
        # Markets trade ~6.5h/day, 5 days/week: look back far enough to fill `limit` bars.
        days = max(5, int(limit * minutes / 390 * 7 / 5 * 1.6) + 3) if minutes < 1440 else int(limit * 1.6) + 10
        start = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: dict[str, list[Bar]] = {s: [] for s in symbols}
        token = None
        for _ in range(50):
            j = request_json(
                "GET",
                f"{DATA_URL}/v2/stocks/bars",
                headers=self._headers,
                params={
                    "symbols": ",".join(symbols),
                    "timeframe": self.timeframe,
                    "start": start,
                    "limit": 10000,
                    "feed": self.feed,
                    "adjustment": "split",
                    "page_token": token,
                },
            )
            for sym, rows in (j.get("bars") or {}).items():
                out.setdefault(sym, []).extend(Bar(_ts(r["t"]), r["o"], r["h"], r["l"], r["c"], r.get("v", 0)) for r in rows)
            token = j.get("next_page_token")
            if not token:
                break
        return {s: b[-limit:] for s, b in out.items() if b}

    def get_news(self, symbols: list[str], limit: int = 10) -> list[dict]:
        try:
            j = request_json(
                "GET",
                f"{DATA_URL}/v1beta1/news",
                headers=self._headers,
                params={"symbols": ",".join(symbols), "limit": min(50, limit), "sort": "desc"},
                retries=1,
            )
        except Exception:  # noqa: BLE001 - news is optional context, never fatal
            return []
        out = []
        for n in j.get("news", []):
            for s in n.get("symbols", []):
                if s in symbols:
                    out.append({"symbol": s, "headline": n.get("headline", ""), "t": _ts(n["created_at"]), "source": n.get("source", "")})
                    break
        return out[:limit]
