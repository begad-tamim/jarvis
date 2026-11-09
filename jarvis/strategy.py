"""The quant brain: turns raw candles into a ranked, explainable score.

Seven factors, each normalised to [-1, 1] and weighted into a single
score in [-100, 100]. Every factor is reported back so both the LLM
analyst and the human on the dashboard can see *why* a stock scored high.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import indicators as ta
from .models import Bar, Signal

WEIGHTS: dict[str, float] = {
    "trend": 25,  # fast EMA vs slow EMA
    "structure": 10,  # price vs slow SMA
    "momentum": 20,  # rate of change
    "macd": 15,  # MACD histogram, ATR-normalised
    "rsi": 10,  # RSI sweet spot, overbought penalty
    "volume": 10,  # volume surge in the direction of the move
    "rel_strength": 10,  # outperformance vs the benchmark
}
assert sum(WEIGHTS.values()) == 100


@dataclass(slots=True)
class StrategyConfig:
    fast: int = 20
    slow: int = 50
    rsi_period: int = 14
    momentum_period: int = 10
    rs_period: int = 20
    entry_score: float = 45.0
    exit_score: float = -15.0
    rsi_overbought: float = 78.0
    min_bars: int = 60
    regime_filter: bool = True  # only buy when the benchmark is above its slow EMA
    risk_off_penalty: float = 20.0  # extra score required in a risk-off regime
    weights: dict[str, float] = field(default_factory=lambda: dict(WEIGHTS))


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def regime(bench_bars: list[Bar] | None, cfg: StrategyConfig) -> str:
    """'risk-on' when the benchmark trades above its slow EMA."""
    if not bench_bars or len(bench_bars) < cfg.slow:
        return "unknown"
    closes = [b.c for b in bench_bars]
    slow = ta.last(ta.ema(closes, cfg.slow))
    return "risk-on" if closes[-1] >= slow else "risk-off"


def score_symbol(
    symbol: str,
    bars: list[Bar],
    cfg: StrategyConfig | None = None,
    bench_bars: list[Bar] | None = None,
    market_regime: str = "unknown",
) -> Signal | None:
    cfg = cfg or StrategyConfig()
    if len(bars) < cfg.min_bars:
        return None
    closes = [b.c for b in bars]
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    vols = [b.v for b in bars]
    price = closes[-1]
    if price <= 0:
        return None

    ema_f = ta.last(ta.ema(closes, cfg.fast), price)
    ema_s = ta.last(ta.ema(closes, cfg.slow), price)
    sma_s = ta.last(ta.sma(closes, cfg.slow), price)
    atr_v = ta.last(ta.atr(highs, lows, closes, 14), price * 0.02) or price * 0.02
    rsi_v = ta.last(ta.rsi(closes, cfg.rsi_period), 50.0)
    mom = ta.last(ta.roc(closes, cfg.momentum_period))
    _, _, hist = ta.macd(closes)
    hist_v = ta.last(hist)

    # Volume surge: recent 3-bar average vs the prior 30 bars, signed by direction.
    recent = sum(vols[-3:]) / 3
    base = vols[-33:-3] or vols
    mean = sum(base) / len(base)
    sd = math.sqrt(sum((v - mean) ** 2 for v in base) / len(base)) or 1.0
    vol_z = (recent - mean) / sd
    direction = 1.0 if closes[-1] >= closes[-4] else -1.0

    rel = 0.0
    if bench_bars and len(bench_bars) > cfg.rs_period and len(closes) > cfg.rs_period:
        bc = [b.c for b in bench_bars]
        rel = (closes[-1] / closes[-1 - cfg.rs_period] - 1) - (bc[-1] / bc[-1 - cfg.rs_period] - 1)

    trend_pct = ema_f / ema_s - 1 if ema_s else 0.0
    if rsi_v >= cfg.rsi_overbought:
        rsi_f = -1.0
    elif rsi_v <= 30:
        rsi_f = -0.5  # falling knife: don't reward it
    else:
        rsi_f = _clip((rsi_v - 50) / 15)

    factors = {
        "trend": math.tanh(trend_pct * 60),
        "structure": math.tanh((price / sma_s - 1) * 25) if sma_s else 0.0,
        "momentum": math.tanh(mom * 18),
        "macd": math.tanh(hist_v / atr_v * 3) if atr_v else 0.0,
        "rsi": rsi_f,
        "volume": math.tanh(max(vol_z, 0) / 2) * direction,
        "rel_strength": math.tanh(rel * 15),
    }
    score = sum(cfg.weights.get(k, 0) * v for k, v in factors.items())
    score = round(max(-100.0, min(100.0, score)), 1)

    entry = cfg.entry_score + (cfg.risk_off_penalty if cfg.regime_filter and market_regime == "risk-off" else 0)
    if score >= entry and rsi_v < cfg.rsi_overbought:
        action = "buy"
    elif score <= cfg.exit_score:
        action = "sell"
    else:
        action = "hold"

    dollar_volume = sum(c * v for c, v in zip(closes[-20:], vols[-20:], strict=True)) / min(20, len(closes))
    summary = _explain(trend_pct, mom, rsi_v, vol_z, direction, rel, bench_bars is not None)
    return Signal(
        symbol=symbol,
        score=score,
        price=round(price, 4),
        atr=round(atr_v, 4),
        rsi=round(rsi_v, 1),
        action=action,
        factors={k: round(v, 3) for k, v in factors.items()},
        summary=summary,
        dollar_volume=round(dollar_volume, 2),
    )


def _explain(trend: float, mom: float, rsi_v: float, vol_z: float, direction: float, rel: float, has_bench: bool) -> str:
    parts = [
        f"EMA trend {'up' if trend >= 0 else 'down'} {trend * 100:+.1f}%",
        f"momentum {mom * 100:+.1f}%",
        f"RSI {rsi_v:.0f}",
    ]
    if vol_z > 1.0:
        parts.append(f"volume surge {vol_z:.1f}σ {'on buying' if direction > 0 else 'on selling'}")
    if has_bench:
        parts.append(f"{'outperforming' if rel >= 0 else 'lagging'} benchmark by {abs(rel) * 100:.1f}%")
    return ", ".join(parts)


def rank(
    universe: dict[str, list[Bar]],
    cfg: StrategyConfig | None = None,
    benchmark: str | None = None,
) -> tuple[list[Signal], str]:
    """Score every symbol, return signals sorted best-first plus the regime."""
    cfg = cfg or StrategyConfig()
    bench = universe.get(benchmark) if benchmark else None
    reg = regime(bench, cfg)
    out = []
    for sym, bars in universe.items():
        if sym == benchmark:
            continue
        s = score_symbol(sym, bars, cfg, bench, reg)
        if s:
            out.append(s)
    out.sort(key=lambda s: s.score, reverse=True)
    return out, reg
