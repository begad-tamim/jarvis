"""Backtester: replays history through the *same* Engine, RiskEngine and
strategy used live - no separate "backtest logic" that can drift from reality.

The LLM analyst is intentionally not used here: models have read the news
that happened after your backtest start date, which is look-ahead bias.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .analyst import RulesAnalyst
from .brokers.paper import PaperBroker
from .config import Config
from .engine import Engine
from .journal import Journal
from .models import Bar


class ReplayFeed:
    name = "replay"

    def __init__(self, bars: dict[str, list[Bar]], warmup: int):
        self.all = bars
        self.master = max(bars.values(), key=len)
        self.i = warmup
        self.idx = {s: 0 for s in bars}

    @property
    def now(self) -> float:
        return self.master[self.i].t

    def _upto(self, s: str) -> list[Bar]:
        b, t = self.all[s], self.now
        j = self.idx[s]
        while j < len(b) and b[j].t <= t:
            j += 1
        self.idx[s] = j
        return b[:j]

    def price(self, s: str) -> float | None:
        if s not in self.all:
            return None
        b = self._upto(s)
        return b[-1].c if b else None

    def get_bars(self, symbols: list[str], limit: int = 200) -> dict[str, list[Bar]]:
        return {s: self._upto(s)[-limit:] for s in symbols if s in self.all}

    def get_news(self, symbols: list[str], limit: int = 10) -> list[dict]:
        return []

    def advance(self) -> bool:
        if self.i + 1 >= len(self.master):
            return False
        self.i += 1
        return True


@dataclass
class BacktestResult:
    start: float
    end: float
    start_equity: float
    end_equity: float
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    trades: int
    win_rate: float
    profit_factor: float
    exposure_bars: float
    benchmark_return: float
    equity: list[tuple[float, float]]
    trade_log: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k not in ("equity", "trade_log")}
        return d


def _metrics(eq: list[tuple[float, float]], periods_per_year: float) -> tuple[float, float, float, float]:
    vals = [e for _, e in eq]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals)) if vals[i - 1]]
    if len(rets) < 2:
        return 0.0, 0.0, 0.0, 0.0
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    down = [r for r in rets if r < 0]
    dsd = math.sqrt(sum(r * r for r in down) / len(rets)) if down else 0.0
    sharpe = mean / sd * math.sqrt(periods_per_year) if sd else 0.0
    sortino = mean / dsd * math.sqrt(periods_per_year) if dsd else 0.0
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    years = (eq[-1][0] - eq[0][0]) / (365.25 * 86400)
    cagr = (vals[-1] / vals[0]) ** (1 / years) - 1 if years > 0.05 and vals[0] > 0 else 0.0
    return sharpe, sortino, mdd, cagr


def run_backtest(
    bars: dict[str, list[Bar]],
    cfg: Config,
    cash: float = 100_000.0,
    warmup: int | None = None,
    progress: Any = None,
) -> BacktestResult:
    warmup = warmup or max(cfg.strategy.min_bars, cfg.strategy.slow) + 5
    feed = ReplayFeed(bars, warmup)
    broker = PaperBroker(feed.price, cash=cash, clock_fn=lambda: feed.now)
    cfg.scan_interval_sec = 0
    cfg.analyst_interval_sec = 0
    eng = Engine(cfg, broker, feed, RulesAnalyst(), Journal(":memory:"), clock=lambda: feed.now)
    eng.think = lambda *a, **k: None  # type: ignore[method-assign]  # quiet
    n = len(feed.master) - warmup
    k = 0
    while True:
        eng.tick()
        k += 1
        if progress and k % 25 == 0:
            progress(k, n)
        if not feed.advance():
            break
    eq = list(eng.equity_points)
    gaps = sorted(eq[i][0] - eq[i - 1][0] for i in range(1, len(eq))) or [86400]
    bar = gaps[len(gaps) // 2] or 86400
    ppy = 252 if bar >= 86400 * 0.9 else 252 * 6.5 * 3600 / bar
    sharpe, sortino, mdd, cagr = _metrics(eq, ppy)
    closed = [t for t in eng.trades if t.get("side") == "sell"]
    gains = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    bench = bars.get(cfg.benchmark)
    bench_ret = 0.0
    if bench and len(bench) > warmup:
        bench_ret = bench[-1].c / bench[warmup].c - 1
    return BacktestResult(
        start=eq[0][0],
        end=eq[-1][0],
        start_equity=cash,
        end_equity=eq[-1][1],
        total_return=eq[-1][1] / cash - 1,
        cagr=cagr,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=mdd,
        trades=len(closed),
        win_rate=(sum(1 for t in closed if t["pnl"] > 0) / len(closed)) if closed else 0.0,
        profit_factor=(gains / losses) if losses else (math.inf if gains else 0.0),
        exposure_bars=0.0,
        benchmark_return=bench_ret,
        equity=eq,
        trade_log=list(reversed(eng.trades)),
    )
