"""`jarvis` command line.

jarvis                      # start JARVIS: HUD + assistant (+ markets, phone, Telegram if configured)
jarvis demo                 # zero-setup tour: sample projects + simulated market + live HUD
jarvis ask "what's left before I ship?" -p orbit-api
jarvis brief                # daily briefing across every project (free, no model)
jarvis projects             # all projects with live git status
jarvis brains               # which brains (Claude Code, Codex, Gemini, Ollama...) are installed
jarvis call "deploy is green"   # JARVIS phones you (Twilio)
jarvis trade                # autonomous trading on Alpaca (paper by default)
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
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝  your AI command center v{v}
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


def build_hub(cfg: Config, *, engine=None, registry=None, brain=None, journal=None, demo: bool = False):
    from .assistant import Assistant
    from .brains import BrainError, make_brain
    from .bus import Bus
    from .channels.telegram import TelegramBot
    from .channels.voice import Voice
    from .journal import Journal
    from .projects import Registry, discover
    from .server import Hub

    bus = Bus()
    if engine is not None:
        engine.bus = bus
    if registry is None:
        registry = Registry(discover(cfg.project_roots, cfg.projects))
    if brain is None:
        try:
            brain = make_brain(cfg.brain, cfg.brain_model, cfg.allow_edits, cfg.brain_command)
        except BrainError as e:
            from .brains import OfflineBrain

            print(_c("yellow", f"! brain: {e}. Running offline."))
            brain = OfflineBrain()
    journal = journal or Journal(cfg.data_path / "assistant.db")
    voice = Voice()
    assistant = Assistant(registry, brain, journal, bus, engine, voice)
    voice.assistant = assistant
    tg = TelegramBot(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""), assistant)
    hub = Hub(assistant, bus, engine, voice, tg, __version__, demo)
    if engine is not None and voice.ready:
        prev_kill = engine.kill

        def kill_and_call(reason: str, liquidate: bool = True):
            res = prev_kill(reason, liquidate)
            voice.call(f"JARVIS here. The trading kill switch fired: {reason}. All orders are cancelled.")
            return res

        engine.kill = kill_and_call
    return hub


def _serve(hub, cfg: Config, open_browser: bool):
    from .server import load_or_create_token, serve

    token = os.environ.get("JARVIS_DASHBOARD_TOKEN") or load_or_create_token(cfg.data_path / "dashboard.token")
    httpd = serve(hub, cfg.dashboard_host, cfg.dashboard_port, token)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    shown = "127.0.0.1" if cfg.dashboard_host in ("0.0.0.0", "::") else cfg.dashboard_host  # noqa: S104 - display only
    url = f"http://{shown}:{cfg.dashboard_port}/#token={token}"
    print(f"  {_c('cyan', '◉ HUD')}       {url}")
    print(_c("dim", "              (the token in the link is your key - don't share it)"))
    if hub.telegram.ready:
        hub.telegram.start()
        print(f"  {_c('cyan', '◉ TELEGRAM')}  listening (only your chat id is answered)")
    if hub.voice.ready:
        print(f"  {_c('cyan', '◉ PHONE')}     JARVIS can call you" + (" · you can call JARVIS" if hub.voice.inbound_ready else ""))
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    return httpd


def _summary(hub) -> None:
    a = hub.assistant
    n = len(a.registry.projects)
    print(
        f"  {_c('cyan', '◉ BRAIN')}     {a.brain.label}"
        + (_c("dim", "  (read-only)") if not getattr(a.brain, "allow_edits", False) else _c("yellow", "  (can edit files)"))
    )
    print(f"  {_c('cyan', '◉ PROJECTS')}  {n} found" + (_c("dim", "  - add folders with project_roots in jarvis.toml") if not n else ""))


def _idle_forever() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(1):
        pass


def _run_loop(engine, tick: float, before_tick=None) -> None:
    stop = threading.Event()

    def _sig(*_):
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    engine.run(stop, tick, before_tick)


# ---------------------------------------------------------------------------- commands
def cmd_demo(args, cfg: Config) -> None:
    from . import demo_workspace
    from .brains import BrainError, OfflineBrain, make_brain
    from .brokers.paper import PaperBroker
    from .data.synthetic import SyntheticMarket
    from .engine import Engine
    from .journal import Journal
    from .projects import Registry, discover

    print(_c("dim", "  building the sample workspace (7 git repos) ..."))
    root = demo_workspace.build(fresh=args.fresh)
    registry = Registry(discover([str(root)]))
    try:
        brain = make_brain(args.brain, cfg.brain_model) if args.brain != "offline" else OfflineBrain()
    except BrainError as e:
        print(_c("yellow", f"! {e}. Using the offline brain."))
        brain = OfflineBrain()

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
    hub = build_hub(cfg, engine=engine, registry=registry, brain=brain, journal=Journal(":memory:"), demo=True)
    for pid, tasks in demo_workspace.SEED_TASKS.items():
        for t in tasks:
            hub.assistant.add_task(pid, t)
    for pid, notes in demo_workspace.SEED_NOTES.items():
        for n in notes:
            hub.assistant.add_note(pid, n)
    print(_c("cyan", BANNER.format(v=__version__)))
    print(f"  {_c('yellow', '◉ DEMO')}      sample workspace in {root} · simulated market · ${args.cash:,.0f} play money")
    _summary(hub)
    if brain.name == "offline":
        print(_c("dim", "              tip: `jarvis demo --brain auto` lets Claude Code / Codex / Gemini answer for real"))
    _serve(hub, cfg, not args.no_browser)
    print(_c("dim", "  Ctrl+C to stop.\n"))
    first = [True]

    def step():
        if first[0]:
            first[0] = False
            return
        market.step()

    _run_loop(engine, args.speed, step)


def _markets_engine(cfg: Config, quiet: bool = False):
    from .engine import Engine
    from .journal import Journal
    from .notify import Notifier

    broker, data = build_live_stack(cfg)
    journal = Journal(cfg.data_path / f"jarvis-{broker.name}.db")
    engine = Engine(cfg, broker, data, build_analyst(cfg), journal, Notifier())
    tag = _c("red", "◉ LIVE — REAL MONEY") if broker.is_live else _c("green", "◉ PAPER")
    acct = broker.get_account()
    print(f"  {tag}     {broker.name} · {len(cfg.universe)} symbols · equity ${acct.equity:,.2f} · analyst: {engine.analyst.name}")
    return engine


def cmd_up(args, cfg: Config) -> None:
    print(_c("cyan", BANNER.format(v=__version__)))
    want = "on" if args.markets else ("off" if args.no_markets else cfg.markets)
    key, secret = alpaca_keys()
    engine = _markets_engine(cfg) if want == "on" or (want == "auto" and key and secret) else None
    hub = build_hub(cfg, engine=engine)
    _summary(hub)
    if engine is None:
        print(f"  {_c('dim', '◉ MARKETS')}   off" + _c("dim", "  (add Alpaca keys to .env to enable autonomous trading)"))
    _serve(hub, cfg, not args.no_browser)
    print(_c("dim", "  Ctrl+C to stop.\n"))
    logging.getLogger().setLevel(logging.INFO if args.verbose else logging.WARNING)
    if engine is not None:
        _run_loop(engine, args.tick)
    else:
        _idle_forever()


def cmd_run(args, cfg: Config) -> None:
    print(_c("cyan", BANNER.format(v=__version__)))
    engine = _markets_engine(cfg)
    hub = build_hub(cfg, engine=engine)
    if not args.no_dashboard:
        _serve(hub, cfg, not args.no_browser)
    print(_c("dim", "  Ctrl+C to stop. Broker-side stops stay active after exit.\n"))
    logging.getLogger().setLevel(logging.INFO)
    _run_loop(engine, args.tick)


def _offline_hub(cfg: Config, brain_name: str | None = None):
    from .journal import Journal

    if brain_name:
        cfg.brain = brain_name
    return build_hub(cfg, journal=Journal(cfg.data_path / "assistant.db"))


def cmd_ask(args, cfg: Config) -> None:
    hub = _offline_hub(cfg, args.brain)
    a = hub.assistant
    pid = None
    if args.project:
        p = a.registry.get(args.project) or a.registry.find(args.project)
        if not p:
            _die(f"no project matches '{args.project}' (see `jarvis projects`)")
        pid = p.id
    print(_c("dim", f"  {a.brain.label}{' · ' + pid if pid else ''} thinking..."), file=sys.stderr)
    m = a.ask(" ".join(args.text), pid, "cli")
    print(m.text)
    print(_c("dim", f"\n  {m.brain}{f' · {m.ms / 1000:.1f}s' if m.ms else ''}"), file=sys.stderr)


def cmd_brief(args, cfg: Config) -> None:
    from .projects import briefing

    hub = _offline_hub(cfg, "offline")
    items = hub.assistant.registry.all(force=True)
    b = briefing(items, hub.assistant.open_tasks())
    print()
    print(f"  {_c('bold', 'JARVIS daily briefing')}  {_c('dim', time.strftime('%A %d %B'))}")
    print(f"  {b['headline']}\n")
    colors = {"attention": "red", "active": "yellow", "ready": "green", "quiet": "dim"}
    titles = {"attention": "NEEDS ATTENTION", "active": "IN PROGRESS", "ready": "READY", "quiet": "QUIET"}
    for k in ("attention", "active", "ready", "quiet"):
        if b["buckets"].get(k):
            print(f"  {_c(colors[k], titles[k])}")
            for it in b["buckets"][k]:
                print(f"    {it['name']:<22} {_c('dim', it['reason'])}")
    print(_c("dim", f"\n  {b['projects']} projects · {b['commits_7d']} commits this week · {b['todos']} TODOs in code\n"))


def cmd_projects(args, cfg: Config) -> None:
    hub = _offline_hub(cfg, "offline")
    items = hub.assistant.registry.all(force=True)
    if not items:
        print(f"No projects found in {', '.join(cfg.project_roots)}. Set project_roots in jarvis.toml.")
        return
    dot = {"attention": _c("red", "●"), "active": _c("yellow", "●"), "ready": _c("green", "●"), "quiet": _c("dim", "●")}
    for it in items:
        st = it["status"]
        print(
            f"  {dot.get(st['state'], '○')} {it['name']:<24} {st['branch'] or '-':<14} {_c('dim', it['last_commit_ago']):<20} {st['reason']}"
        )


def cmd_brains(args, cfg: Config) -> None:
    from .brains import detect, make_brain

    print()
    for b in detect():
        print(f"  {_c('green', '✔') if b['available'] else _c('dim', '·')} {b['name']:<12} {b['label']}")
    chosen = make_brain(cfg.brain, cfg.brain_model, cfg.allow_edits, cfg.brain_command) if cfg.brain != "cmd" or cfg.brain_command else None
    print(f'\n  brain = {cfg.brain!r} → {_c("cyan", chosen.label if chosen else "?")}   (set brain = "codex" etc. in jarvis.toml)\n')


def cmd_call(args, cfg: Config) -> None:
    from .channels.voice import Voice

    v = Voice()
    if not v.ready:
        _die("Twilio is not configured: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM and JARVIS_OWNER_PHONE in .env")
    v._call(" ".join(args.text) or "This is JARVIS. Everything is under control.", v.owner)
    print(_c("green", "✔ call requested"))


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
            warn('analyst=claude needs: pip install ".[ai]"')
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
    p = argparse.ArgumentParser(prog="jarvis", description="JARVIS - your AI command center: projects, any brain, phone, markets")
    p.add_argument("--config", "-c", help="path to jarvis.toml (default: ./jarvis.toml if present)")
    p.add_argument("--version", action="version", version=f"jarvis {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    up = sub.add_parser("up", help="start JARVIS (default)")
    up.add_argument("--markets", action="store_true", help="force the trading module on")
    up.add_argument("--no-markets", action="store_true")
    up.add_argument("--tick", type=float, default=60)
    up.add_argument("--no-browser", action="store_true")

    d = sub.add_parser("demo", help="sample projects + simulated market + live HUD, no keys needed")
    d.add_argument("--brain", default="offline", help="offline (default), auto, claude-code, codex, gemini, ollama")
    d.add_argument("--speed", type=float, default=1.2, help="seconds per simulated 5-min bar (default 1.2)")
    d.add_argument("--cash", type=float, default=100_000)
    d.add_argument("--seed", type=int, default=22)
    d.add_argument("--analyst", choices=["rules", "claude", "claude-code", "ollama"])
    d.add_argument("--fresh", action="store_true", help="rebuild the sample workspace")
    d.add_argument("--no-browser", action="store_true")

    ask = sub.add_parser("ask", help="ask JARVIS anything from the terminal")
    ask.add_argument("text", nargs="+")
    ask.add_argument("-p", "--project")
    ask.add_argument("--brain")
    sub.add_parser("brief", help="daily briefing across all projects (free)")
    sub.add_parser("projects", help="list projects with live git status")
    sub.add_parser("brains", help="show which brains are installed")
    c = sub.add_parser("call", help="have JARVIS phone you (Twilio)")
    c.add_argument("text", nargs="*")

    r = sub.add_parser("trade", aliases=["run"], help="trade autonomously on Alpaca (paper unless unlocked)")
    r.add_argument("--tick", type=float, default=60, help="seconds between ticks (default 60)")
    r.add_argument("--no-dashboard", action="store_true")
    r.add_argument("--no-browser", action="store_true")

    s = sub.add_parser("scan", help="rank the stock universe now")
    s.add_argument("--demo", action="store_true", help="use the simulated market")

    b = sub.add_parser("backtest", help="replay history through the real trading engine")
    b.add_argument("symbols", nargs="*")
    b.add_argument("--range", default="2y", help="Yahoo range: 1y, 2y, 5y… (default 2y)")
    b.add_argument("--cash", type=float, default=100_000)
    b.add_argument("--demo", action="store_true", help="synthetic data, no network")
    b.add_argument("--bars", type=int, default=600)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--json", help="write full results to this file")

    sub.add_parser("status", help="trading account, positions and open orders")
    k = sub.add_parser("kill", help="cancel all orders and flatten every position")
    k.add_argument("--yes", action="store_true")
    doc = sub.add_parser("doctor", help="check config, keys and security settings")
    doc.add_argument("--offline", action="store_true")

    args = p.parse_args(argv)
    if args.cmd is None:
        args = p.parse_args([*(argv or sys.argv[1:]), "up"])
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
    cmds = {
        "up": cmd_up, "demo": cmd_demo, "ask": cmd_ask, "brief": cmd_brief, "projects": cmd_projects, "brains": cmd_brains,
        "call": cmd_call, "trade": cmd_run, "run": cmd_run, "scan": cmd_scan, "backtest": cmd_backtest,
        "status": cmd_status, "kill": cmd_kill,
    }  # fmt: skip
    cmds[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
