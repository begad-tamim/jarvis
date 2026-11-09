from jarvis.models import Bar
from jarvis.strategy import StrategyConfig, rank, score_symbol


def _bars(closes, vol=1e6):
    return [Bar(i * 60.0, c, c * 1.01, c * 0.99, c, vol) for i, c in enumerate(closes)]


def test_uptrend_scores_higher_than_downtrend():
    up = _bars([100 * 1.004**i for i in range(120)])
    down = _bars([100 * 0.996**i for i in range(120)])
    su, sd = score_symbol("UP", up), score_symbol("DN", down)
    assert su.score > 30 > sd.score
    assert sd.action in ("sell", "hold")


def test_needs_min_bars():
    assert score_symbol("X", _bars([100] * 10)) is None


def test_factor_breakdown_is_exposed():
    s = score_symbol("UP", _bars([100 * 1.003**i for i in range(120)]))
    assert set(s.factors) == {"trend", "structure", "momentum", "macd", "rsi", "volume", "rel_strength"}
    assert all(-1 <= v <= 1 for v in s.factors.values())
    assert "RSI" in s.summary


def test_overbought_blocks_buy():
    s = score_symbol("HOT", _bars([100 * 1.02**i for i in range(120)]), StrategyConfig(rsi_overbought=70))
    assert s.rsi >= 70 and s.action != "buy"


def test_rank_sorts_and_skips_benchmark(market):
    sigs, regime = rank(market.get_bars(market.symbols, 200), benchmark="SPY")
    assert regime in ("risk-on", "risk-off")
    assert "SPY" not in {s.symbol for s in sigs}
    assert [s.score for s in sigs] == sorted((s.score for s in sigs), reverse=True)
