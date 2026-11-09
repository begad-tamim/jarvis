"""The autonomous trading loop.

    every tick:   sync account -> enforce halts -> manage exits (stops, targets, trailing)
    every scan:   pull candles -> score universe -> analyst review -> risk verdict -> execute

Exits are managed on every tick even when paused or halted: protecting open
positions never stops. Only *new risk* is gated.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .analyst import RulesAnalyst
from .config import Config
from .journal import Journal
from .models import Account, OrderRequest, Position, Signal, Thesis
from .notify import Notifier
from .risk import RiskEngine, RiskState
from .strategy import rank

log = logging.getLogger("jarvis.engine")

try:
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001  # pragma: no cover - tzdata missing (some Windows installs)
    NY = dt.UTC


class Engine:
    def __init__(
        self,
        cfg: Config,
        broker: Any,
        data: Any,
        analyst: Any = None,
        journal: Journal | None = None,
        notifier: Notifier | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.broker = broker
        self.data = data
        self.analyst = analyst or RulesAnalyst()
        self.journal = journal or Journal()
        self.notifier = notifier or Notifier()
        self.clock = clock
        self.risk = RiskEngine(cfg.risk, RiskState.from_dict(self.journal.get("risk_state")))
        self.lock = threading.RLock()
        self.status = "booting"
        self.market_open = False
        self.regime = "unknown"
        self.paused = False
        self.signals: list[Signal] = []
        self.theses: dict[str, tuple[float, Thesis]] = {}
        self.market_view = ""
        self.thoughts: deque[dict] = deque(maxlen=150)
        self.trades: deque[dict] = deque(maxlen=200)
        self.decisions: deque[dict] = deque(maxlen=60)
        self.equity_points: deque[tuple[float, float]] = deque(self.journal.equity_curve(1500), maxlen=1500)
        self.account: Account | None = None
        self.positions: list[Position] = []
        self.last_scan = 0.0
        self.last_analyst = 0.0
        self.scans = 0
        self.start_equity: float | None = self.journal.get("start_equity")
        self._subs: list[queue.Queue] = []
        self._error_streak = 0
        self._veto_seen: dict[tuple[str, str], float] = {}
        for e in reversed(self.journal.events("trade", 200)):
            self.trades.appendleft(e)

    # ------------------------------------------------------------------ events
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self._subs:
                self._subs.remove(q)

    def _publish(self, kind: str, payload: Any) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait((kind, payload))
            except queue.Full:
                pass

    def think(self, text: str, kind: str = "info", symbol: str | None = None) -> None:
        t = {"t": self.clock(), "kind": kind, "text": text, "symbol": symbol}
        self.thoughts.append(t)
        log.info("%s%s", f"[{symbol}] " if symbol else "", text)
        self._publish("thought", t)

    # ------------------------------------------------------------------ controls
    def pause(self) -> None:
        with self.lock:
            self.paused = True
            self.think("Paused by operator. Open positions are still protected.", "warn")

    def resume(self) -> None:
        with self.lock:
            self.paused = False
            if self.risk.state.killed:
                self.risk.reset_kill()
                self.think("Kill switch reset by operator.", "warn")
            self.think("Resumed. Back on watch.", "ok")
            self._save()

    def kill(self, reason: str = "manual kill switch", liquidate: bool = True) -> dict:
        with self.lock:
            self.risk.kill(reason)
            self.think(f"KILL SWITCH: {reason}. Cancelling orders{' and flattening' if liquidate else ''}.", "alert")
            cancelled = self.broker.cancel_orders()
            closed = []
            if liquidate:
                for p in self.broker.get_positions():
                    self._exit(p, f"kill switch: {reason}")
                    closed.append(p.symbol)
            self.notifier.send(f"JARVIS kill switch: {reason}. Closed: {', '.join(closed) or 'nothing'}")
            self.journal.log("kill", {"reason": reason, "closed": closed, "cancelled": cancelled})
            self._save()
            return {"cancelled": cancelled, "closed": closed}

    # ------------------------------------------------------------------ loop
    def run(self, stop: threading.Event, tick_seconds: float = 60.0, before_tick: Callable[[], None] | None = None) -> None:
        self.think(f"Online. {self.broker.name} · {len(self.cfg.universe)} symbols · analyst: {self.analyst.name}.", "ok")
        while not stop.is_set():
            try:
                if before_tick:
                    before_tick()
                self.tick()
                self._error_streak = 0
            except Exception as e:  # noqa: BLE001 - the loop must survive transient API failures
                self._error_streak += 1
                log.exception("tick failed")
                self.think(f"Tick failed ({type(e).__name__}: {str(e)[:120]}). Retrying.", "error")
                if self._error_streak >= 10 and not self.risk.state.killed:
                    self.pause()
            stop.wait(tick_seconds * min(8, 2 ** max(0, self._error_streak - 1)) if self._error_streak else tick_seconds)
        self.think("Shutting down. Broker-side stops stay active.", "warn")

    def tick(self) -> None:
        with self.lock:
            now = self.clock()
            clock = self.broker.get_clock()
            self.market_open = clock.is_open
            account = self.broker.get_account()
            positions = self.broker.get_positions()
            self.account, self.positions = account, positions
            if self.start_equity is None:
                self.start_equity = account.equity
                self.journal.set("start_equity", account.equity)
            self.risk.roll_day(account, dt.datetime.fromtimestamp(now, NY))
            was_killed = self.risk.state.killed
            halt = self.risk.check_halts(account)
            if self.risk.state.killed and not was_killed:
                self._save()
                self.kill(self.risk.state.killed_reason)
                return
            self.equity_points.append((now, round(account.equity, 2)))
            self.journal.record_equity(account.equity, account.cash, now)

            if not clock.is_open:
                self.status = "market closed"
                self._publish("snapshot", self.snapshot())
                return

            scan_due = now - self.last_scan >= self.cfg.scan_interval_sec
            sigmap = {s.symbol: s for s in self.signals}
            news: list[dict] = []
            if scan_due:
                self.status = "scanning"
                syms = list(dict.fromkeys(self.cfg.universe + [p.symbol for p in positions] + [self.cfg.benchmark]))
                bars = self.data.get_bars(syms, self.cfg.lookback)
                self.signals, self.regime = rank(bars, self.cfg.strategy, self.cfg.benchmark)
                sigmap = {s.symbol: s for s in self.signals}
                self.last_scan = now
                self.scans += 1
                buys = [s for s in self.signals if s.action == "buy"]
                top = ", ".join(f"{s.symbol} {s.score:+.0f}" for s in self.signals[:3])
                held_syms = {p.symbol for p in positions}
                fresh_buys = [s.symbol for s in buys if s.symbol not in held_syms]
                if fresh_buys or self.scans % 6 == 1:
                    lead = f"{len(fresh_buys)} new setups: {', '.join(fresh_buys[:4])}." if fresh_buys else "No new setups."
                    self.think(f"Scan #{self.scans}: {len(self.signals)} symbols, regime {self.regime}. Leaders {top}. {lead}", "scan")
                news = self.data.get_news(syms, 30)

            # ---- exits: always, even when paused/halted
            for p in positions:
                th = self.theses.get(p.symbol, (0, None))[1]
                reason = self.risk.exit_reason(p, sigmap.get(p.symbol), th if th and th.action == "sell" else None, now)
                if reason:
                    self._exit(p, reason)
            if self.cfg.flatten_eod and clock.next_close and clock.next_close - now < 600:
                for p in self.broker.get_positions():
                    self._exit(p, "end-of-day flatten")
            positions = self.broker.get_positions()
            self.positions = positions

            # ---- entries
            if scan_due and not halt and not self.paused:
                held = {p.symbol for p in positions}
                cands = [s for s in self.signals if s.action == "buy" and s.symbol not in held][: self.cfg.max_candidates]
                if cands:
                    self._review(cands, positions, sigmap, news, account, now)
                    for s in cands:
                        self._consider(s, now)
            elif scan_due and halt:
                self.think(f"New entries blocked: {halt}", "warn")

            self.status = "paused" if self.paused else ("halted" if halt else "watching")
            self._save()
            self._publish("snapshot", self.snapshot())

    # ------------------------------------------------------------------ steps
    def _review(self, cands: list[Signal], positions: list[Position], sigmap: dict, news: list[dict], account: Account, now: float) -> None:
        fresh = self.cfg.analyst_interval_sec
        missing = [s for s in cands if now - self.theses.get(s.symbol, (0, None))[0] > fresh]
        due = now - self.last_analyst >= fresh or (missing and now - self.last_analyst >= min(120, fresh))
        if not missing or not due:
            return
        self.status = "thinking"
        self.think(f"Asking the {self.analyst.name} analyst about {', '.join(s.symbol for s in cands)}…", "think")
        t0 = time.time()
        view, theses = self.analyst.review(cands, [(p, sigmap.get(p.symbol)) for p in positions], news, self.regime, account.equity)
        self.last_analyst = now
        if view:
            self.market_view = view
        for th in theses:
            self.theses[th.symbol] = (now, th)
            if th.action in ("buy", "sell"):
                self.think(f"{th.action.upper()} at {th.conviction:.0%} conviction. {th.thesis}", "thesis", th.symbol)
        self.journal.log("analyst", {"view": view, "theses": [t.to_dict() for t in theses], "secs": round(time.time() - t0, 1)})

    def _consider(self, s: Signal, now: float) -> None:
        account = self.broker.get_account()
        positions = self.broker.get_positions()
        th = self.theses.get(s.symbol, (0, None))[1]
        v = self.risk.check_entry(s, th, account, positions, now)
        decision = {
            "t": now,
            "symbol": s.symbol,
            "score": s.score,
            "price": s.price,
            "thesis": th.to_dict() if th else None,
            "verdict": v.to_dict(),
        }
        self.decisions.append(decision)
        self.journal.log("decision", decision, s.symbol, now)
        if not v.approved:
            key = (s.symbol, v.rule)
            if now - self._veto_seen.get(key, 0) > 1800:  # don't repeat the same veto for 30 min
                self._veto_seen[key] = now
                self.think(f"Vetoed by risk [{v.rule}]: {v.message}", "veto", s.symbol)
            return
        qty = v.qty
        stop = take = None
        if self.cfg.broker_side_stops and qty >= 1 and getattr(self.broker, "name", "").startswith("alpaca"):
            qty = float(math.floor(qty))  # brackets need whole shares
            stop, take = v.stop, v.target
        req = OrderRequest(s.symbol, "buy", qty=qty, stop_loss=stop, take_profit=take, reason=(th.thesis if th else s.summary))
        r = self.broker.submit_order(req)
        if r.status in ("rejected", "canceled"):
            self.think(f"Order rejected by broker ({r.status}).", "error", s.symbol)
            return
        px = r.filled_avg_price or s.price
        self.risk.on_entry(s, v, now, req.reason, broker_protected=stop is not None)
        trade = {
            "t": now, "side": "buy", "symbol": s.symbol, "qty": round(qty, 4), "price": round(px, 2),
            "notional": round(qty * px, 2), "stop": v.stop, "target": v.target, "order_id": r.id,
            "status": r.status, "reason": req.reason[:240], "by": th.source if th else "quant",
        }  # fmt: skip
        self.trades.appendleft(trade)
        self.journal.log("trade", trade, s.symbol, now)
        prot = " · stop at broker" if stop else ""
        self.think(f"BOUGHT {qty:g} @ ${px:,.2f} (${qty * px:,.0f}). Stop ${v.stop}, target ${v.target}{prot}.", "buy", s.symbol)
        self.notifier.send(f"🟢 JARVIS bought {qty:g} {s.symbol} @ ${px:,.2f} · stop ${v.stop} · {req.reason[:140]}")

    def _exit(self, p: Position, reason: str) -> None:
        now = self.clock()
        r = self.broker.close_position(p.symbol)
        if r is None:
            return
        px = r.filled_avg_price or p.market_price
        pnl = (px - p.avg_price) * p.qty
        self.risk.on_exit(p.symbol, now)
        self.theses.pop(p.symbol, None)
        trade = {
            "t": now, "side": "sell", "symbol": p.symbol, "qty": round(p.qty, 4), "price": round(px, 2),
            "notional": round(p.qty * px, 2), "pnl": round(pnl, 2), "pnl_pct": round(px / p.avg_price - 1, 4) if p.avg_price else 0,
            "order_id": r.id, "status": r.status, "reason": reason[:240],
        }  # fmt: skip
        self.trades.appendleft(trade)
        self.journal.log("trade", trade, p.symbol, now)
        self.think(
            f"SOLD {p.qty:g} @ ${px:,.2f} · P&L {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f}. {reason}",
            "sell" if pnl >= 0 else "loss",
            p.symbol,
        )
        self.notifier.send(f"{'🔵' if pnl >= 0 else '🔴'} JARVIS sold {p.symbol} @ ${px:,.2f} · P&L ${pnl:+,.2f} · {reason[:140]}")

    def _save(self) -> None:
        self.journal.set("risk_state", self.risk.state.to_dict())

    # ------------------------------------------------------------------ view
    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            a = self.account
            r = self.cfg.risk
            meta = self.risk.state.meta
            closed = [t for t in self.trades if t.get("side") == "sell" and "pnl" in t]
            wins = [t for t in closed if t["pnl"] > 0]
            exposure = sum(p.market_value for p in self.positions)
            eq = a.equity if a else 0.0
            day_pl = (eq - self.risk.state.day_start_equity) if a and self.risk.state.day_start_equity else 0.0
            pts = list(self.equity_points)
            if len(pts) > 600:
                step = len(pts) / 600
                pts = [pts[int(i * step)] for i in range(600)] + [pts[-1]]
            return {
                "t": self.clock(),
                "mode": self.cfg.mode,
                "broker": self.broker.name,
                "live": bool(getattr(self.broker, "is_live", False)),
                "analyst": self.analyst.name,
                "status": self.status,
                "market_open": self.market_open,
                "regime": self.regime,
                "paused": self.paused,
                "killed": self.risk.state.killed,
                "halted": self.risk.state.halted_reason or self.risk.state.killed_reason,
                "scans": self.scans,
                "account": {
                    "equity": round(eq, 2),
                    "cash": round(a.cash, 2) if a else 0,
                    "start_equity": self.start_equity,
                    "total_return_pct": round(eq / self.start_equity - 1, 5) if a and self.start_equity else 0,
                    "day_pl": round(day_pl, 2),
                    "day_pl_pct": round(self.risk.daily_pl_pct(a), 5) if a else 0,
                    "drawdown_pct": round(self.risk.drawdown_pct(a), 5) if a else 0,
                    "exposure": round(exposure, 2),
                    "exposure_pct": round(exposure / eq, 4) if eq else 0,
                },
                "risk": {
                    "daily_loss_limit": r.max_daily_loss_pct,
                    "max_drawdown": r.max_drawdown_pct,
                    "max_positions": r.max_positions,
                    "max_trades": r.max_trades_per_day,
                    "trades_today": self.risk.state.trades_today,
                    "risk_per_trade": r.risk_per_trade_pct,
                    "max_position_pct": r.max_position_pct,
                },
                "positions": [
                    {
                        "symbol": p.symbol,
                        "qty": round(p.qty, 4),
                        "avg": round(p.avg_price, 2),
                        "price": round(p.market_price, 2),
                        "value": round(p.market_value, 2),
                        "pl": round(p.unrealized_pl, 2),
                        "plpc": round(p.unrealized_plpc, 4),
                        "stop": meta.get(p.symbol, {}).get("stop"),
                        "target": meta.get(p.symbol, {}).get("target"),
                        "reason": meta.get(p.symbol, {}).get("reason", ""),
                    }
                    for p in sorted(self.positions, key=lambda p: -p.market_value)
                ],
                "signals": [s.to_dict() for s in self.signals[:20]],
                "theses": [th.to_dict() for _, th in sorted(self.theses.values(), key=lambda x: -x[0])[:8]],
                "market_view": self.market_view,
                "thoughts": list(self.thoughts)[-60:],
                "trades": list(self.trades)[:40],
                "decisions": list(self.decisions)[-20:],
                "equity": pts,
                "stats": {
                    "closed": len(closed),
                    "wins": len(wins),
                    "win_rate": round(len(wins) / len(closed), 3) if closed else None,
                    "realized_pl": round(sum(t["pnl"] for t in closed), 2),
                },
            }
