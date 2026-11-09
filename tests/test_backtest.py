from jarvis.backtest import run_backtest
from jarvis.config import Config
from jarvis.data.synthetic import SyntheticMarket


def test_backtest_runs_and_reports():
    m = SyntheticMarket(seed=11, bar_seconds=86400, warmup=300)
    cfg = Config()
    cfg.universe = [s for s in m.symbols if s != "SPY"]
    cfg.lookback = 120
    cfg.risk.min_dollar_volume = 0
    r = run_backtest(m.get_bars(m.symbols, 300), cfg, cash=50_000)
    assert r.start_equity == 50_000 and r.end_equity > 0
    assert 0 <= r.max_drawdown < 1 and r.trades > 0
    assert len(r.equity) > 200
