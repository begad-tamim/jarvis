"""Technical indicators in pure Python.

Every function takes a list of floats and returns a list of the same length,
padded with `None` where the indicator isn't defined yet. No numpy, no pandas,
so JARVIS installs in a second and runs on a Raspberry Pi.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Series = list[float | None]


def sma(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be > 0")
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> Series:
    """Wilder's RSI."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period

    def _rsi(g: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / loss)

    out[period] = _rsi(avg_g, avg_l)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = _rsi(avg_g, avg_l)
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    """Returns (macd_line, signal_line, histogram)."""
    ef, es = ema(values, fast), ema(values, slow)
    line: Series = [(f - s) if f is not None and s is not None else None for f, s in zip(ef, es, strict=True)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_part = ema([v for v in line[start:] if v is not None], signal)
    sig: Series = [None] * start + sig_part
    hist: Series = [(m - s) if m is not None and s is not None else None for m, s in zip(line, sig, strict=True)]
    return line, sig, hist


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Series:
    """Wilder's Average True Range."""
    n = len(closes)
    out: Series = [None] * n
    if n <= period:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    prev = sum(trs[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def bollinger(values: Sequence[float], period: int = 20, mult: float = 2.0) -> tuple[Series, Series, Series]:
    mid = sma(values, period)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        m = mid[i]
        assert m is not None
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / period)
        upper[i], lower[i] = m + mult * sd, m - mult * sd
    return lower, mid, upper


def roc(values: Sequence[float], period: int) -> Series:
    """Rate of change as a fraction (0.05 == +5%)."""
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        out[i] = (values[i] / base - 1.0) if base else None
    return out


def zscore(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        sd = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
        out[i] = (values[i] - mean) / sd if sd > 0 else 0.0
    return out


def last(series: Series, default: float = 0.0) -> float:
    for v in reversed(series):
        if v is not None:
            return v
    return default
