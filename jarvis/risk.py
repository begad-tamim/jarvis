"""The risk engine: hard guardrails that no model output can override.

Design rule: the LLM *proposes*, this module *decides*. Position size,
stops, exposure, loss limits and the kill switch are all computed here in
plain code, from broker data, so a hallucinating or prompt-injected model
can never size a trade, skip a stop, or trade past a loss limit.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Account, Position, Signal, Thesis, Verdict


@dataclass(slots=True)
class RiskConfig:
    max_position_pct: float = 0.10  # max % of equity in a single name
    max_positions: int = 8
    max_gross_exposure_pct: float = 0.95  # never use margin: cap total long exposure
    risk_per_trade_pct: float = 0.01  # equity lost if the stop is hit
    stop_atr_mult: float = 2.0
    take_profit_atr_mult: float = 4.0  # 2:1 reward/risk by default
    trailing_atr_mult: float = 2.5
    max_daily_loss_pct: float = 0.03  # halt new entries for the day
    max_drawdown_pct: float = 0.15  # from peak equity -> kill switch
    max_trades_per_day: int = 20  # new entries per day
    min_price: float = 5.0  # no penny stocks
    min_dollar_volume: float = 1_000_000.0
    min_conviction: float = 0.55
    cooldown_minutes: int = 60  # no re-entry right after an exit (anti-churn)
    max_order_notional: float = 0.0  # 0 = no extra cap
    allow_fractional: bool = True
    pdt_protection: bool = True  # block entries that could trip the US pattern-day-trader rule
    blocklist: list[str] = field(default_factory=list)


@dataclass
class PositionMeta:
    """What JARVIS remembers about a position that the broker doesn't."""

    stop: float
    target: float | None
    high_water: float
    atr: float
    opened_at: float
    reason: str = ""
    broker_protected: bool = False  # a stop order also lives at the broker


@dataclass
class RiskState:
    day: str = ""
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    trades_today: int = 0
    halted_reason: str = ""  # daily halt (auto-clears next day)
    killed: bool = False  # kill switch (manual reset only)
    killed_reason: str = ""
    last_exit: dict[str, float] = field(default_factory=dict)
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> RiskState:
        st = cls()
        for k, v in (d or {}).items():
            if hasattr(st, k):
                setattr(st, k, v)
        return st


def _round_price(p: float) -> float:
    return round(p, 2) if p >= 1 else round(p, 4)


class RiskEngine:
    def __init__(self, cfg: RiskConfig | None = None, state: RiskState | None = None):
        self.cfg = cfg or RiskConfig()
        self.state = state or RiskState()

    # ------------------------------------------------------------------ day/halts
    def roll_day(self, account: Account, now: dt.datetime) -> None:
        day = now.date().isoformat()
        if self.state.day != day:
            self.state.day = day
            self.state.day_start_equity = account.last_equity or account.equity
            self.state.trades_today = 0
            self.state.halted_reason = ""
        self.state.peak_equity = max(self.state.peak_equity, account.equity)

    def daily_pl_pct(self, account: Account) -> float:
        base = self.state.day_start_equity or account.equity
        return account.equity / base - 1 if base else 0.0

    def drawdown_pct(self, account: Account) -> float:
        peak = self.state.peak_equity or account.equity
        return 1 - account.equity / peak if peak else 0.0

    def check_halts(self, account: Account) -> str:
        """Returns '' when trading may continue, otherwise the reason."""
        if self.state.killed:
            return self.state.killed_reason or "kill switch engaged"
        dd = self.drawdown_pct(account)
        if dd >= self.cfg.max_drawdown_pct:
            self.kill(f"max drawdown {dd:.1%} >= {self.cfg.max_drawdown_pct:.0%} from peak")
            return self.state.killed_reason
        day = self.daily_pl_pct(account)
        if day <= -self.cfg.max_daily_loss_pct:
            self.state.halted_reason = f"daily loss {day:.2%} hit the {self.cfg.max_daily_loss_pct:.0%} limit - no new entries today"
        return self.state.halted_reason

    def kill(self, reason: str) -> None:
        self.state.killed = True
        self.state.killed_reason = reason

    def reset_kill(self) -> None:
        self.state.killed = False
        self.state.killed_reason = ""

    # ------------------------------------------------------------------ entries
    def check_entry(
        self,
        signal: Signal,
        thesis: Thesis | None,
        account: Account,
        positions: list[Position],
        now: float,
    ) -> Verdict:
        c, sym, price = self.cfg, signal.symbol, signal.price

        def veto(rule: str, msg: str) -> Verdict:
            return Verdict(False, rule, msg)

        halt = self.check_halts(account)
        if halt:
            return veto("halted", halt)
        if sym.upper() in {b.upper() for b in c.blocklist}:
            return veto("blocklist", f"{sym} is on the blocklist")
        if signal.action != "buy":
            return veto("signal", f"quant score {signal.score:+.0f} is not a buy")
        if thesis is not None and thesis.action != "buy":
            return veto("analyst", f"analyst passed: {thesis.thesis[:120]}")
        if thesis is not None and thesis.conviction < c.min_conviction:
            return veto("conviction", f"conviction {thesis.conviction:.2f} < {c.min_conviction:.2f}")
        if price < c.min_price:
            return veto("min_price", f"${price:.2f} is below the ${c.min_price:.2f} floor")
        if signal.dollar_volume and signal.dollar_volume < c.min_dollar_volume:
            return veto("liquidity", f"avg dollar volume ${signal.dollar_volume:,.0f} too thin")
        if any(p.symbol == sym for p in positions):
            return veto("duplicate", f"already holding {sym}")
        if len(positions) >= c.max_positions:
            return veto("max_positions", f"{len(positions)}/{c.max_positions} slots used")
        if self.state.trades_today >= c.max_trades_per_day:
            return veto("max_trades", f"{self.state.trades_today} trades today (limit {c.max_trades_per_day})")
        last_exit = self.state.last_exit.get(sym)
        if last_exit and now - last_exit < c.cooldown_minutes * 60:
            return veto("cooldown", f"exited {sym} {int((now - last_exit) / 60)}m ago (cooldown {c.cooldown_minutes}m)")
        if c.pdt_protection and account.equity < 25_000 and account.daytrade_count >= 3:
            return veto("pdt", f"{account.daytrade_count} day trades in 5 days on a <$25k account")

        equity = account.equity
        atr = signal.atr if signal.atr > 0 else price * 0.02
        stop_dist = atr * c.stop_atr_mult
        stop = price - stop_dist
        if stop <= 0:
            return veto("stop", "stop would be below zero")
        target = price + atr * c.take_profit_atr_mult if c.take_profit_atr_mult > 0 else None

        # Size by risk: lose at most risk_per_trade_pct of equity if the stop is hit.
        risk_qty = (equity * c.risk_per_trade_pct) / stop_dist
        cap_qty = (equity * c.max_position_pct) / price
        exposure = sum(p.market_value for p in positions)
        room = max(0.0, equity * c.max_gross_exposure_pct - exposure)
        cash_room = max(0.0, min(account.cash, account.buying_power)) * 0.98  # never borrow
        qty = min(risk_qty, cap_qty, room / price, cash_room / price)
        if c.max_order_notional > 0:
            qty = min(qty, c.max_order_notional / price)
        if thesis is not None:  # conviction can only shrink a position, never grow it
            qty *= max(0.5, min(1.0, thesis.conviction))
        qty = math.floor(qty * 1e4) / 1e4 if c.allow_fractional else float(math.floor(qty))
        notional = qty * price
        if qty <= 0 or notional < 1.0:
            return veto("size", "no room: position would be under $1 after risk limits")

        return Verdict(
            True,
            "ok",
            f"risk {c.risk_per_trade_pct:.1%} of equity, stop {c.stop_atr_mult}×ATR",
            qty=qty,
            notional=round(notional, 2),
            stop=_round_price(stop),
            target=_round_price(target) if target else None,
        )

    def on_entry(self, signal: Signal, verdict: Verdict, now: float, reason: str, broker_protected: bool) -> None:
        self.state.trades_today += 1
        self.state.meta[signal.symbol] = asdict(
            PositionMeta(
                stop=verdict.stop or signal.price * 0.95,
                target=verdict.target,
                high_water=signal.price,
                atr=signal.atr,
                opened_at=now,
                reason=reason[:300],
                broker_protected=broker_protected,
            )
        )

    def on_exit(self, symbol: str, now: float) -> None:
        # Exits never count toward the daily limit: reducing risk is always allowed.
        self.state.last_exit[symbol] = now
        self.state.meta.pop(symbol, None)

    # ------------------------------------------------------------------ exits
    def ensure_meta(self, pos: Position, atr: float | None, now: float) -> dict[str, Any]:
        """Adopt positions JARVIS didn't open (or forgot after a restart)."""
        m = self.state.meta.get(pos.symbol)
        if m is None:
            a = atr or pos.avg_price * 0.02
            m = asdict(
                PositionMeta(
                    stop=_round_price(min(pos.avg_price, pos.market_price) - a * self.cfg.stop_atr_mult),
                    target=None,
                    high_water=max(pos.avg_price, pos.market_price),
                    atr=a,
                    opened_at=now,
                    reason="adopted existing position",
                )
            )
            self.state.meta[pos.symbol] = m
        return m

    def exit_reason(self, pos: Position, signal: Signal | None, thesis: Thesis | None, now: float) -> str:
        """Returns a human reason to sell, or '' to hold. Also ratchets the trailing stop."""
        m = self.ensure_meta(pos, signal.atr if signal else None, now)
        px = pos.market_price
        if px > m["high_water"]:
            m["high_water"] = px
            trail = _round_price(px - m["atr"] * self.cfg.trailing_atr_mult)
            if trail > m["stop"]:
                m["stop"] = trail  # stops only ever move up
        if px <= m["stop"]:
            return f"stop hit at ${px:.2f} (stop ${m['stop']:.2f}, {pos.unrealized_plpc:+.1%})"
        if m.get("target") and px >= m["target"]:
            return f"target reached ${px:.2f} (+{pos.unrealized_plpc:.1%})"
        if signal and signal.action == "sell":
            return f"quant score collapsed to {signal.score:+.0f}: {signal.summary}"
        if thesis and thesis.action == "sell" and thesis.conviction >= self.cfg.min_conviction:
            return f"analyst exit ({thesis.source}): {thesis.thesis[:140]}"
        return ""
