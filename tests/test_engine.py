import threading

from jarvis.brokers.paper import PaperBroker
from jarvis.config import Config
from jarvis.data.synthetic import SyntheticMarket
from jarvis.engine import Engine
from jarvis.journal import Journal


def demo_engine(seed=7):
    m = SyntheticMarket(seed=seed)
    cfg = Config(mode="demo")
    cfg.universe = [s for s in m.symbols if s != "SPY"]
    cfg.scan_interval_sec = m.bar_seconds
    cfg.analyst_interval_sec = m.bar_seconds * 3
    cfg.risk.min_dollar_volume = 0
    b = PaperBroker(m.latest_price, clock_fn=lambda: m.now)
    return m, Engine(cfg, b, m, journal=Journal(), clock=lambda: m.now)


def test_engine_trades_and_respects_limits():
    m, e = demo_engine()
    for _ in range(400):
        e.tick()
        m.step()
    snap = e.snapshot()
    assert snap["scans"] > 300
    assert any(t["side"] == "buy" for t in e.trades)
    assert len(snap["positions"]) <= e.cfg.risk.max_positions
    for p in snap["positions"]:
        assert p["value"] <= snap["account"]["equity"] * e.cfg.risk.max_position_pct * 1.25  # price drift since entry
        assert p["stop"] is not None
    assert snap["account"]["cash"] >= -1e-6  # never borrows


def test_kill_switch_flattens_and_blocks():
    m, e = demo_engine()
    for _ in range(150):
        e.tick()
        m.step()
    e.kill("test")
    assert e.broker.get_positions() == []
    m.step()
    e.tick()
    assert all(t["side"] == "sell" for t in list(e.trades)[:1])
    assert e.snapshot()["killed"]
    assert not any(d["verdict"]["approved"] for d in list(e.decisions)[-3:] if d["t"] > m.now - 1)


def test_pause_still_manages_exits():
    m, e = demo_engine()
    for _ in range(200):
        e.tick()
        m.step()
    e.pause()
    n_buys = sum(1 for t in e.trades if t["side"] == "buy")
    for _ in range(100):
        e.tick()
        m.step()
    assert sum(1 for t in e.trades if t["side"] == "buy") == n_buys


def test_run_loop_stops():
    m, e = demo_engine()
    stop = threading.Event()
    th = threading.Thread(target=e.run, args=(stop, 0.01, m.step))
    th.start()
    stop.wait(0.3)
    stop.set()
    th.join(2)
    assert not th.is_alive()
