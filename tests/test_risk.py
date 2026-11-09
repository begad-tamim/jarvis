import datetime as dt

from conftest import make_signal

from jarvis.models import Account, Position, Thesis
from jarvis.risk import RiskConfig, RiskEngine

NOW = 1_700_000_000.0


def engine(**kw):
    r = RiskEngine(RiskConfig(**kw))
    r.roll_day(Account(100_000, 100_000, 100_000), dt.datetime(2026, 1, 5, 10))
    return r


def test_sizes_by_risk_and_sets_stop(account):
    v = engine().check_entry(make_signal(price=100, atr=2), None, account, [], NOW)
    assert v.approved
    assert v.stop == 96.0 and v.target == 108.0  # 2xATR stop, 4xATR target
    # 1% risk / $4 stop distance = 250 sh, capped by 10% position = 100 sh
    assert v.qty == 100


def test_llm_cannot_increase_size(account):
    eng = engine()
    base = eng.check_entry(make_signal(), None, account, [], NOW).qty
    hyped = eng.check_entry(make_signal(), Thesis("NVDA", "buy", 1.0, "to the moon"), account, [], NOW).qty
    assert hyped <= base


def test_low_conviction_and_analyst_pass_are_vetoed(account):
    eng = engine()
    assert eng.check_entry(make_signal(), Thesis("NVDA", "buy", 0.3, "meh"), account, [], NOW).rule == "conviction"
    assert eng.check_entry(make_signal(), Thesis("NVDA", "pass", 0.9, "no"), account, [], NOW).rule == "analyst"


def test_guardrails(account):
    eng = engine(max_positions=1, blocklist=["GME"])
    held = [Position("AAPL", 10, 100, 100)]
    assert eng.check_entry(make_signal(), None, account, held, NOW).rule == "max_positions"
    assert eng.check_entry(make_signal("GME"), None, account, [], NOW).rule == "blocklist"
    assert eng.check_entry(make_signal(price=3), None, account, [], NOW).rule == "min_price"
    assert eng.check_entry(make_signal(dollar_volume=10), None, account, [], NOW).rule == "liquidity"
    assert eng.check_entry(make_signal(action="hold"), None, account, [], NOW).rule == "signal"


def test_never_uses_margin():
    eng = engine()
    poor = Account(equity=100_000, cash=500, buying_power=400_000)  # margin account
    v = eng.check_entry(make_signal(price=100), None, poor, [], NOW)
    assert v.notional <= 500


def test_daily_loss_halts_entries_but_not_tomorrow():
    eng = engine()
    down = Account(96_000, 96_000, 96_000)
    assert "daily loss" in eng.check_entry(make_signal(), None, down, [], NOW).message
    eng.roll_day(down, dt.datetime(2026, 1, 6, 10))
    assert eng.check_entry(make_signal(), None, down, [], NOW).approved


def test_drawdown_trips_kill_switch():
    eng = engine(max_daily_loss_pct=0.5)
    eng.state.peak_equity = 100_000
    halt = eng.check_halts(Account(84_000, 84_000, 84_000))
    assert eng.state.killed and "drawdown" in halt


def test_cooldown_and_pdt(account):
    eng = engine()
    eng.on_exit("NVDA", NOW)
    assert eng.check_entry(make_signal(), None, account, [], NOW + 60).rule == "cooldown"
    pdt = Account(10_000, 10_000, 10_000, daytrade_count=3)
    small = RiskEngine()
    small.roll_day(pdt, dt.datetime(2026, 1, 5, 10))
    assert small.check_entry(make_signal(), None, pdt, [], NOW).rule == "pdt"


def test_trailing_stop_only_moves_up():
    eng = engine()
    p = Position("NVDA", 10, 100, 100)
    eng.ensure_meta(p, 2.0, NOW)
    s0 = eng.state.meta["NVDA"]["stop"]
    p.market_price = 120
    assert eng.exit_reason(p, None, None, NOW) == ""
    s1 = eng.state.meta["NVDA"]["stop"]
    assert s1 > s0
    p.market_price = 110
    eng.exit_reason(p, None, None, NOW)
    assert eng.state.meta["NVDA"]["stop"] == s1
    p.market_price = s1 - 0.01
    assert "stop hit" in eng.exit_reason(p, None, None, NOW)
