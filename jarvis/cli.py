"""`jarvis` command line.

jarvis demo                 # zero-setup: simulated market + live HUD
jarvis run                  # autonomous trading on Alpaca (paper by default)
jarvis scan                 # rank the universe right now
jarvis backtest NVDA AAPL   # replay history through the real engine
jarvis status               # account, positions, risk state
jarvis doctor               # config + security checks
jarvis kill                 # cancel everything and flatten
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
import webbrowser

from . import __version__
from .config import Config, alpaca_keys, live_unlocked, load_config, load_dotenv

BANNER = r"""
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝  autonomous trading agent v{v}
"""

C = {
    "cyan": "\033[96m",
    "green": "\033[92m",
    "red": "\033[91m",
    "yellow": "\033[93m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "end": "\033[0m",
}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = dict.fromkeys(C, "")


def _c(color: str, s: str) -> str:
    return f"{C[color]}{s}{C['end']}"


def _die(msg: str) -> None:
    print(_c("red", f"✖ {msg}"), file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------- builders
def build_live_stack(cfg: Config):
    from .brokers.alpaca import AlpacaBroker
    from .data.alpaca_data import AlpacaData

    key, secret = alpaca_keys()
    if not key or not secret:
        _die(
            "Alpaca keys missing. Copy .env.example to .env and add ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY\n  (free paper account: https://app.alpaca.markets/signup) - or try `jarvis demo` first."
        )
    live = cfg.mode == "live"
    if live and not live_unlocked():
        _die(
            'mode = "live" also needs JARVIS_LIVE_TRADING=I_UNDERSTAND_THE_RISKS in the environment.\n  Run on paper first. Real money can be lost.'
        )
    return AlpacaBroker(key, secret, live=live), AlpacaData(key, secret, cfg.timeframe)


def build_analyst(cfg: Config):
    from .analyst import RulesAnalyst, make_analyst

    try:
        return make_analyst(cfg.analyst, cfg.analyst_model, cfg.analyst_effort)
    except RuntimeError as e:
        print(_c("yellow", f"! {e}. Falling back to the rules analyst."))
        return RulesAnalyst()


def _serve(engine, cfg: Config, open_browser: bool):
    from .server import load_or_create_token, serve

    token = os.environ.get("JARVIS_DASHBOARD_TOKEN") or load_or_create_token(cfg.data_path / "dashboard.token")
    httpd = serve(engine, cfg.dashboard_host, cfg.dashboard_port, token)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    shown = "127.0.0.1" if cfg.dashboard_host in ("0.0.0.0", "::") else cfg.dashboard_host  # noqa: S104 - display only
    url = f"http://{shown}:{cfg.dashboard_port}/#token={token}"
    print(f"  {_c('cyan', '◉ HUD')}      {url}")
    print(_c("dim", "             (the token in the link is your key - don't share it)"))
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    return httpd


def _run_loop(engine, tick: float, before_tick=None) -> None:
    stop = threading.Event()

    def _sig(*_):
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    engine.run(stop, tick, before_tick)


# ---------------------------------------------------------------------------- commands
def cmd_demo(args, cfg: Config) -> None:
    from .brokers.paper import PaperBroker
    from .data.synthetic import SyntheticMarket
    from .engine import Engine
    from .journal import Journal

    market = SyntheticMarket(seed=args.seed)
    cfg.mode = "demo"
    cfg.universe = [s for s in market.symbols if s != "SPY"]
    cfg.benchmark = "SPY"
    cfg.scan_interval_sec = market.bar_seconds
    cfg.analyst_interval_sec = market.bar_seconds * 4
    cfg.risk.cooldown_minutes = 90
    cfg.risk.max_daily_loss_pct = 0.05
    cfg.risk.max_trades_per_day = 60  # a simulated 'day' passes in minutes
    cfg.risk.min_dollar_volume = 0
    if args.analyst:
        cfg.analyst = args.analyst
    if cfg.analyst != "rules":
        cfg.analyst_interval_sec = market.bar_seconds * 15  # LLM calls take seconds; don't call every bar
    broker = PaperBroker(market.latest_price, cash=args.cash, clock_fn=lambda: market.now)
    engine = Engine(cfg, broker, market, build_analyst(cfg), Journal(":memory:"), clock=lambda: market.now)
    engine.think("DEMO MODE: simulated market and play money. Nothing here is real.", "warn")
    print(_c("cyan", BANNER.format(v=__version__)))
    print(f"  {_c('yellow', '◉ DEMO')}     simulated market · ${args.cash:,.0f} play money · analyst: {engine.analyst.name}")
    _serve(engine, cfg, not args.no_browser)
    print(_c("dim", "  Ctrl+C to stop.\n"))
    first = [True]

    def step():
        if first[0]:
            first[0] = False
            return
        market.step()

    _run_loop(engine, args.speed, step)


def cmd_run(args, cfg: Config) -> None:
    from .engine import Engine
    from .journal import Journal
    from .notify import Notifier

    broker, data = build_live_stack(cfg)
    journal = Journal(cfg.data_path / f"jarvis-{broker.name}.db")
    engine = Engine(cfg, broker, data, build_analyst(cfg), journal, Notifier())
    print(_c("cyan", BANNER.format(v=__version__)))
    tag = _c("red", "◉ LIVE — REAL MONEY") if broker.is_live else _c("green", "◉ PAPER")
    print(f"  {tag}    {broker.name} · {len(cfg.universe)} symbols · {cfg.timeframe} · analyst: {engine.analyst.name}")
    acct = broker.get_account()
    print(f"  {_c('cyan', '◉ ACCOUNT')}  equity ${acct.equity:,.2f} · cash ${acct.cash:,.2f} · status {acct.status}")
    if not args.no_dashboard:
        _serve(engine, cfg, not args.no_browser)
    print(_c("dim", "  Ctrl+C to stop. Broker-side stops stay active after exit.\n"))
    logging.getLogger().setLevel(logging.INFO)
    _run_loop(engine, args.tick)


def cmd_scan(args, cfg: Config) -> None:
    from .strategy import rank

    if args.demo:
        from .data.synthetic import SyntheticMarket

        data = SyntheticMarket()
        syms = data.symbols
    else:
        _, data = build_live_stack(cfg)
        syms = list(dict.fromkeys(cfg.universe + [cfg.benchmark]))
    sigs, reg = rank(data.get_bars(syms, cfg.lookback), cfg.strategy, cfg.benchmark)
    print(
        f"\n  regime: {_c('green' if reg == 'risk-on' else 'yellow', reg)}   ({len(sigs)} symbols, {cfg.timeframe if not args.demo else 'simulated'})\n"
    )
    print(f"  {'SYM':<6} {'SCORE':>6}  {'PRICE':>10}  {'RSI':>4}  SIGNAL  WHY")
    for s in sigs:
        col = "green" if s.action == "buy" else "red" if s.action == "sell" else "dim"
        bar = "█" * int(abs(s.score) / 10)
        print(
            f"  {s.symbol:<6} {s.score:>+6.0f}  {s.price:>10,.2f}  {s.rsi:>4.0f}  {_c(col, f'{s.action.upper():<6}')}  {_c('dim', s.summary[:80])}  {_c(col, bar)}"
        )
    print()


def cmd_backtest(args, cfg: Config) -> None:
    from .backtest import run_backtest

    syms = [s.upper() for s in args.symbols] or cfg.universe[:10]
    bench = cfg.benchmark
    if args.demo:
        from .data.synthetic import SyntheticMarket

        m = SyntheticMarket(seed=args.seed, bar_seconds=86400, warmup=args.bars)
        bars = m.get_bars(m.symbols, args.bars)
        syms = [s for s in m.symbols if s != "SPY"]
        bench = "SPY"
        src = "synthetic"
    else:
        from .data.yahoo import YahooData

        print(_c("dim", f"  downloading {len(syms) + 1} daily series from Yahoo Finance…"))
        bars = YahooData(range_=args.range).get_bars(syms + [bench])
        src = f"Yahoo daily, {args.range}"
    cfg.universe, cfg.benchmark = syms, bench
    cfg.lookback = 120
    cfg.risk.min_dollar_volume = 0
    t0 = time.time()

    def prog(k, n):
        print(f"\r  replaying bar {k}/{n}", end="", flush=True)

    r = run_backtest(bars, cfg, cash=args.cash, progress=prog)
    print(f"\r  replayed {len(r.equity)} bars in {time.time() - t0:.1f}s ({src})" + " " * 10)

    def pct(x):
        return _c("green" if x >= 0 else "red", f"{x:+.2%}")

    rows = [
        ("Total return", pct(r.total_return)),
        (f"Benchmark ({bench} buy & hold)", pct(r.benchmark_return)),
        ("CAGR", pct(r.cagr)),
        ("Sharpe", f"{r.sharpe:.2f}"),
        ("Sortino", f"{r.sortino:.2f}"),
        ("Max drawdown", _c("red", f"{-r.max_drawdown:.2%}")),
        ("Closed trades", str(r.trades)),
        ("Win rate", f"{r.win_rate:.1%}"),
        ("Profit factor", f"{r.profit_factor:.2f}"),
        ("Final equity", f"${r.end_equity:,.2f}"),
    ]
    print()
    for k, v in rows:
        print(f"  {k:<32} {v}")
    print(_c("dim", "\n  Past performance does not predict future results. Backtests ignore fees, taxes and many real frictions.\n"))
    if args.json:
        import json

        with open(args.json, "w") as f:
            json.dump({"summary": r.summary(), "equity": r.equity, "trades": r.trade_log}, f, default=str)
        print(f"  wrote {args.json}")


def cmd_status(args, cfg: Config) -> None:
    broker, _ = build_live_stack(cfg)
    a = broker.get_account()
    clk = broker.get_clock()
    print(f"\n  {broker.name} · market {'OPEN' if clk.is_open else 'closed'}")
    print(f"  equity ${a.equity:,.2f}   cash ${a.cash:,.2f}   day trades (5d) {a.daytrade_count}\n")
    for p in broker.get_positions():
        col = "green" if p.unrealized_pl >= 0 else "red"
        print(f"  {p.symbol:<6} {p.qty:>10g} @ {p.avg_price:>9,.2f}  now {p.market_price:>9,.2f}  {_c(col, f'{p.unrealized_plpc:+.2%}')}")
    for o in broker.open_orders():
        print(
            _c(
                "dim",
                f"  open order: {o.get('side')} {o.get('qty') or o.get('notional')} {o.get('symbol')} {o.get('type')} {o.get('status')}",
            )
        )
    print()


def cmd_kill(args, cfg: Config) -> None:
    broker, _ = build_live_stack(cfg)
    if not args.yes:
        ans = input(f"Cancel ALL orders and close ALL positions on {broker.name}? type 'kill' to confirm: ")
        if ans.strip() != "kill":
            print("aborted")
            return
    n = broker.cancel_orders()
    closed = [p.symbol for p in broker.get_positions() if broker.close_position(p.symbol)]
    print(_c("red", f"✖ cancelled {n} orders, closed {len(closed)} positions: {', '.join(closed) or '-'}"))


def cmd_doctor(args, cfg: Config, env_warnings: list[str]) -> None:
    ok = lambda s: print(f"  {_c('green', '✔')} {s}")  # noqa: E731
    warn = lambda s: print(f"  {_c('yellow', '!')} {s}")  # noqa: E731
    print()
    ok(f"Python {sys.version.split()[0]} · jarvis {__version__} · zero required dependencies")
    ok(f"mode = {cfg.mode} · analyst = {cfg.analyst} · {len(cfg.universe)} symbols")
    for w in env_warnings:
        warn(w)
    key, secret = alpaca_keys()
    (ok if key and secret else warn)(
        "Alpaca keys " + ("found in environment" if key and secret else "missing (needed for run/scan/status)")
    )
    if cfg.mode == "live":
        (ok if live_unlocked() else warn)(
            "live trading " + ("UNLOCKED" if live_unlocked() else "locked (set JARVIS_LIVE_TRADING to unlock)")
        )
    if cfg.analyst == "claude":
        try:
            import anthropic  # noqa: F401

            ok("anthropic SDK installed")
        except ImportError:
            warn('analyst=claude needs: pip install "jarvis-trading-agent[ai]"')
    if cfg.dashboard_host not in ("127.0.0.1", "localhost", "::1"):
        warn(f"dashboard binds to {cfg.dashboard_host} - reachable from your network")
    else:
        ok("dashboard is local-only (127.0.0.1)")
    r = cfg.risk
    ok(
        f"risk: {r.risk_per_trade_pct:.1%}/trade · max {r.max_position_pct:.0%}/position · {r.max_daily_loss_pct:.0%} daily stop · {r.max_drawdown_pct:.0%} kill-switch drawdown"
    )
    if key and secret and not args.offline:
        try:
            broker, _ = build_live_stack(cfg)
            a = broker.get_account()
            ok(f"connected to {broker.name}: equity ${a.equity:,.2f}, status {a.status}")
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            warn(f"broker connection failed: {e}")
    print()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="jarvis", description="JARVIS - autonomous AI stock trading agent")
    p.add_argument("--config", "-c", help="path to jarvis.toml (default: ./jarvis.toml if present)")
    p.add_argument("--version", action="version", version=f"jarvis {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="simulated market + live dashboard, no keys needed")
    d.add_argument("--speed", type=float, default=1.2, help="seconds per simulated 5-min bar (default 1.2)")
    d.add_argument("--cash", type=float, default=100_000)
    d.add_argument("--seed", type=int, default=22)
    d.add_argument("--analyst", choices=["rules", "claude", "claude-code", "ollama"])
    d.add_argument("--no-browser", action="store_true")

    r = sub.add_parser("run", help="trade autonomously on Alpaca (paper unless unlocked)")
    r.add_argument("--tick", type=float, default=60, help="seconds between ticks (default 60)")
    r.add_argument("--no-dashboard", action="store_true")
    r.add_argument("--no-browser", action="store_true")

    s = sub.add_parser("scan", help="rank the universe now")
    s.add_argument("--demo", action="store_true", help="use the simulated market")

    b = sub.add_parser("backtest", help="replay history through the real engine")
    b.add_argument("symbols", nargs="*")
    b.add_argument("--range", default="2y", help="Yahoo range: 1y, 2y, 5y… (default 2y)")
    b.add_argument("--cash", type=float, default=100_000)
    b.add_argument("--demo", action="store_true", help="synthetic data, no network")
    b.add_argument("--bars", type=int, default=600)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--json", help="write full results to this file")

    sub.add_parser("status", help="account, positions and open orders")
    k = sub.add_parser("kill", help="cancel all orders and flatten every position")
    k.add_argument("--yes", action="store_true")
    doc = sub.add_parser("doctor", help="check config, keys and security settings")
    doc.add_argument("--offline", action="store_true")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    env_warnings = load_dotenv()
    try:
        cfg = load_config(args.config)
    except (ValueError, FileNotFoundError) as e:
        _die(f"config: {e}")
    if args.cmd == "doctor":
        return cmd_doctor(args, cfg, env_warnings)
    for w in env_warnings:
        print(_c("yellow", f"! {w}"))
    {"demo": cmd_demo, "run": cmd_run, "scan": cmd_scan, "backtest": cmd_backtest, "status": cmd_status, "kill": cmd_kill}[args.cmd](
        args, cfg
    )


if __name__ == "__main__":
    main()
