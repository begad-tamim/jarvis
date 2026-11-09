"""A simulated stock market for the zero-setup demo, tests and CI.

Prices follow a regime-switching random walk with occasional news-driven
breakouts (volume spike + a matching headline), so the demo shows every
part of JARVIS - scans, breakouts, AI theses, stops and targets - without an
API key. Everything here is FAKE data and the dashboard says so.
"""

from __future__ import annotations

import math
import random
import time

from ..models import Bar

# symbol -> (start price, drift per bar, volatility per bar, base volume)
DEFAULT_UNIVERSE: dict[str, tuple[float, float, float, float]] = {
    "SPY": (585.0, 0.00012, 0.0018, 4_000_000),
    "NVDA": (142.0, 0.00035, 0.0060, 9_000_000),
    "AAPL": (228.0, 0.00010, 0.0035, 3_500_000),
    "MSFT": (418.0, 0.00012, 0.0032, 1_800_000),
    "AMZN": (206.0, 0.00018, 0.0042, 3_000_000),
    "META": (590.0, 0.00020, 0.0050, 1_200_000),
    "GOOGL": (178.0, 0.00012, 0.0040, 2_200_000),
    "TSLA": (330.0, 0.00010, 0.0085, 6_000_000),
    "AMD": (138.0, 0.00005, 0.0065, 3_800_000),
    "AVGO": (172.0, 0.00025, 0.0055, 2_100_000),
    "NFLX": (880.0, 0.00015, 0.0045, 400_000),
    "PLTR": (62.0, 0.00040, 0.0090, 7_500_000),
    "JPM": (240.0, 0.00008, 0.0028, 1_100_000),
    "LLY": (790.0, -0.00005, 0.0045, 700_000),
    "COST": (930.0, 0.00010, 0.0025, 250_000),
    "XOM": (118.0, -0.00008, 0.0030, 1_900_000),
}

_HEADLINES_UP = [
    "{s} jumps after analysts raise price target on AI demand",
    "{s} surges as quarterly revenue beats estimates",
    "{s} rallies on new multi-year partnership announcement",
    "Options traders pile into {s} calls ahead of product event",
]
_HEADLINES_DOWN = [
    "{s} slides after guidance disappoints investors",
    "{s} falls as regulator opens inquiry",
    "{s} drops on report of slowing orders",
]


class SyntheticMarket:
    name = "synthetic"

    def __init__(self, seed: int = 7, bar_seconds: int = 300, warmup: int = 260, universe: dict | None = None):
        self.rng = random.Random(seed)
        self.bar_seconds = bar_seconds
        self.universe = dict(universe or DEFAULT_UNIVERSE)
        self.symbols = list(self.universe)
        self.bars: dict[str, list[Bar]] = {s: [] for s in self.symbols}
        self.news: list[dict] = []
        self._shock: dict[str, tuple[float, int]] = {}  # symbol -> (drift boost, bars left)
        self._trend: dict[str, float] = dict.fromkeys(self.universe, 0.0)  # persistent trend regime per symbol
        self._market_factor = 0.0
        start = time.time() - warmup * bar_seconds
        self._t = start
        for s, (p, *_rest) in self.universe.items():
            self.bars[s].append(Bar(start, p, p, p, p, self.universe[s][3]))
        for _ in range(warmup):
            self.step()

    # ------------------------------------------------------------------ simulation
    def step(self) -> None:
        self._t += self.bar_seconds
        mf = self.rng.gauss(0, 0.0012)
        self._market_factor = 0.85 * self._market_factor + mf
        for s in self.symbols:
            p0, drift, vol, base_v = self.universe[s]
            last = self.bars[s][-1]
            boost, left = self._shock.get(s, (0.0, 0))
            if left == 0 and s != "SPY" and self.rng.random() < 0.008:
                up = self.rng.random() < 0.65
                boost, left = ((0.004 if up else -0.004) * (1 + self.rng.random()), self.rng.randint(6, 18))
                tpl = self.rng.choice(_HEADLINES_UP if up else _HEADLINES_DOWN)
                self.news.append({"symbol": s, "headline": tpl.format(s=s), "t": self._t, "source": "SimWire"})
                self.news = self.news[-200:]
            if self.rng.random() < 0.012:  # trends persist for ~80 bars, then a new regime
                self._trend[s] = self.rng.choice([-1.0, -0.5, 0.0, 0.5, 1.0, 1.5]) * 0.00045
            beta = 1.0 if s != "SPY" else 0.6
            vol *= 0.62  # per-5-minute-bar volatility
            r = drift + self._trend[s] + boost + beta * self._market_factor * 0.5 + self.rng.gauss(0, vol)
            c = max(0.5, last.c * math.exp(r))
            o = last.c * math.exp(self.rng.gauss(0, vol * 0.25))
            wick = abs(self.rng.gauss(0, vol * 0.6))
            h = max(o, c) * (1 + wick)
            low = min(o, c) * (1 - abs(self.rng.gauss(0, vol * 0.6)))
            v = base_v * (1 + abs(self.rng.gauss(0, 0.35))) * (3.2 if left else 1.0)
            self.bars[s].append(Bar(self._t, round(o, 4), round(h, 4), round(low, 4), round(c, 4), round(v)))
            if len(self.bars[s]) > 2000:
                del self.bars[s][:500]
            if left:
                self._shock[s] = (boost * 0.92, left - 1)
            else:
                self._shock.pop(s, None)

    @property
    def now(self) -> float:
        return self._t

    # ------------------------------------------------------------------ DataFeed API
    def get_bars(self, symbols: list[str], limit: int = 200) -> dict[str, list[Bar]]:
        return {s: list(self.bars[s][-limit:]) for s in symbols if s in self.bars}

    def get_news(self, symbols: list[str], limit: int = 10) -> list[dict]:
        want = set(symbols)
        return [n for n in reversed(self.news) if n["symbol"] in want][:limit]

    def latest_price(self, symbol: str) -> float | None:
        b = self.bars.get(symbol)
        return b[-1].c if b else None
