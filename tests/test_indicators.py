import math

from jarvis import indicators as ta


def test_sma_basic():
    assert ta.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_seeds_with_sma():
    out = ta.ema([1, 2, 3, 4, 5, 6], 3)
    assert out[2] == 2.0 and math.isclose(out[3], 3.0) and math.isclose(out[5], 5.0)


def test_rsi_bounds_and_extremes():
    up = [float(i) for i in range(1, 40)]
    assert ta.last(ta.rsi(up)) == 100.0
    down = up[::-1]
    assert ta.last(ta.rsi(down)) == 0.0
    flat = [10.0] * 40
    assert ta.last(ta.rsi(flat)) == 50.0


def test_atr_positive():
    h = [10 + i * 0.1 + 1 for i in range(30)]
    lo = [10 + i * 0.1 - 1 for i in range(30)]
    c = [10 + i * 0.1 for i in range(30)]
    assert ta.last(ta.atr(h, lo, c)) > 1.9


def test_macd_lengths():
    vals = [100 + math.sin(i / 5) * 5 for i in range(80)]
    line, sig, hist = ta.macd(vals)
    assert len(line) == len(sig) == len(hist) == 80
    assert hist[-1] is not None


def test_roc_and_zscore():
    assert math.isclose(ta.roc([100, 110], 1)[1], 0.10)
    z = ta.zscore([1, 1, 1, 1, 5], 5)
    assert z[-1] > 1.9
