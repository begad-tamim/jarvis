"""Local paper broker: instant fills at the last price plus slippage.

Used by the demo and the backtester. No network, no account needed.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable

from ..models import Account, Clock, OrderRequest, OrderResult, Position


class PaperBroker:
    name = "paper-sim"
    is_live = False

    def __init__(
        self,
        price_fn: Callable[[str], float | None],
        cash: float = 100_000.0,
        slippage_bps: float = 2.0,
        clock_fn: Callable[[], float] = time.time,
    ):
        self._price = price_fn
        self._clock = clock_fn
        self.cash = cash
        self.start_cash = cash
        self.slippage = slippage_bps / 10_000
        self.positions: dict[str, list[float]] = {}  # symbol -> [qty, avg]
        self.fills: list[dict] = []
        self._ids = itertools.count(1)

    def _px(self, symbol: str) -> float:
        p = self._price(symbol)
        if p is None or p <= 0:
            raise ValueError(f"no price for {symbol}")
        return p

    def get_account(self) -> Account:
        mv = sum(q * self._px(s) for s, (q, _) in self.positions.items())
        eq = self.cash + mv
        # last_equity=None: the risk engine snapshots equity at the first tick of each day instead.
        return Account(equity=eq, cash=self.cash, buying_power=self.cash, last_equity=None)

    def get_positions(self) -> list[Position]:
        return [Position(s, q, a, self._px(s)) for s, (q, a) in self.positions.items()]

    def get_clock(self) -> Clock:
        return Clock(is_open=True)

    def open_orders(self) -> list[dict]:
        return []

    def submit_order(self, req: OrderRequest) -> OrderResult:
        px = self._px(req.symbol)
        if req.side == "buy":
            fill = px * (1 + self.slippage)
            qty = req.qty if req.qty is not None else (req.notional or 0) / fill
            cost = qty * fill
            if qty <= 0 or cost > self.cash + 1e-6:
                return OrderResult(str(next(self._ids)), req.symbol, "buy", qty, "rejected")
            self.cash -= cost
            q0, a0 = self.positions.get(req.symbol, [0.0, 0.0])
            q1 = q0 + qty
            self.positions[req.symbol] = [q1, (q0 * a0 + qty * fill) / q1]
        else:
            fill = px * (1 - self.slippage)
            q0, a0 = self.positions.get(req.symbol, [0.0, 0.0])
            qty = min(req.qty if req.qty is not None else q0, q0)
            if qty <= 0:
                return OrderResult(str(next(self._ids)), req.symbol, "sell", 0, "rejected")
            self.cash += qty * fill
            if q0 - qty <= 1e-9:
                self.positions.pop(req.symbol, None)
            else:
                self.positions[req.symbol] = [q0 - qty, a0]
        oid = str(next(self._ids))
        self.fills.append({"id": oid, "symbol": req.symbol, "side": req.side, "qty": qty, "price": fill, "t": self._clock()})
        return OrderResult(oid, req.symbol, req.side, round(qty, 6), "filled", round(fill, 4), round(qty * fill, 2))

    def cancel_orders(self, symbol: str | None = None) -> int:
        return 0

    def close_position(self, symbol: str) -> OrderResult | None:
        if symbol not in self.positions:
            return None
        return self.submit_order(OrderRequest(symbol, "sell", qty=self.positions[symbol][0], reason="close"))
