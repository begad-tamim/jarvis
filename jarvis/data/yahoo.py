"""Free daily candles from Yahoo Finance's public chart endpoint.

Used for keyless backtests. Unofficial and rate limited - fine for research,
use Alpaca data for anything that trades.
"""

from __future__ import annotations

from ..http import request_json
from ..models import Bar

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"


class YahooData:
    name = "yahoo"

    def __init__(self, range_: str = "2y", interval: str = "1d"):
        self.range, self.interval = range_, interval

    def get_bars(self, symbols: list[str], limit: int = 600) -> dict[str, list[Bar]]:
        out: dict[str, list[Bar]] = {}
        for s in symbols:
            j = request_json("GET", URL.format(sym=s), params={"range": self.range, "interval": self.interval})
            res = (j.get("chart", {}).get("result") or [None])[0]
            if not res:
                continue
            q = res["indicators"]["quote"][0]
            bars = []
            for t, o, h, lo, c, v in zip(res.get("timestamp", []), q["open"], q["high"], q["low"], q["close"], q["volume"], strict=False):
                if None in (o, h, lo, c):
                    continue
                bars.append(Bar(float(t), o, h, lo, c, float(v or 0)))
            out[s] = bars[-limit:]
        return out

    def get_news(self, symbols: list[str], limit: int = 10) -> list[dict]:
        return []
