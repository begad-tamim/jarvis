"""Alpaca Trading API v2 adapter (paper by default).

Live trading needs BOTH `mode = "live"` in config AND the environment variable
JARVIS_LIVE_TRADING=I_UNDERSTAND_THE_RISKS. The base URL is chosen in code
from the mode - it is never read from user input - so a typo can't silently
send paper-tested logic to a real-money account.
"""

from __future__ import annotations

import datetime as dt
import math
import urllib.parse

from ..http import HTTPError, request_json
from ..models import Account, Clock, OrderRequest, OrderResult, Position

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


def _f(x: object, default: float = 0.0) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _ts(s: str | None) -> float | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def _price(p: float) -> str:
    # Alpaca rejects sub-penny prices above $1.
    return f"{p:.2f}" if p >= 1 else f"{p:.4f}"


class AlpacaBroker:
    name = "alpaca"

    def __init__(self, key_id: str, secret: str, live: bool = False):
        if not key_id or not secret:
            raise ValueError("Alpaca keys missing: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
        self.is_live = live
        self.base = LIVE_URL if live else PAPER_URL
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self.name = "alpaca-live" if live else "alpaca-paper"

    def __repr__(self) -> str:
        return f"AlpacaBroker(live={self.is_live})"

    def _req(self, method: str, path: str, **kw):
        return request_json(method, self.base + path, headers=self._headers, **kw)

    # ------------------------------------------------------------------ reads
    def get_account(self) -> Account:
        a = self._req("GET", "/v2/account")
        return Account(
            equity=_f(a.get("equity")),
            cash=_f(a.get("cash")),
            buying_power=_f(a.get("non_marginable_buying_power") or a.get("cash")),
            last_equity=_f(a.get("last_equity")) or None,
            daytrade_count=int(a.get("daytrade_count") or 0),
            pattern_day_trader=bool(a.get("pattern_day_trader")),
            status=a.get("status", ""),
            currency=a.get("currency", "USD"),
        )

    def get_positions(self) -> list[Position]:
        return [
            Position(p["symbol"], _f(p["qty"]), _f(p["avg_entry_price"]), _f(p.get("current_price")))
            for p in self._req("GET", "/v2/positions")
            if p.get("side", "long") == "long"
        ]

    def get_clock(self) -> Clock:
        c = self._req("GET", "/v2/clock")
        return Clock(bool(c.get("is_open")), _ts(c.get("next_open")), _ts(c.get("next_close")))

    def open_orders(self) -> list[dict]:
        return self._req("GET", "/v2/orders", params={"status": "open", "limit": 200, "nested": "true"})

    # ------------------------------------------------------------------ writes
    def submit_order(self, req: OrderRequest) -> OrderResult:
        body: dict = {
            "symbol": req.symbol,
            "side": req.side,
            "type": "market",
            "client_order_id": req.client_order_id,
        }
        whole = req.qty is not None and req.qty >= 1 and math.isclose(req.qty, round(req.qty))
        if req.side == "buy" and whole and req.stop_loss:
            # Bracket order: the protective stop lives AT THE BROKER, so the
            # downside stays capped even if this process or laptop dies.
            body.update(qty=str(int(round(req.qty or 0))), time_in_force="gtc", order_class="bracket" if req.take_profit else "oto")
            body["stop_loss"] = {"stop_price": _price(req.stop_loss)}
            if req.take_profit:
                body["take_profit"] = {"limit_price": _price(req.take_profit)}
        elif req.qty is not None:
            # Fractional orders must be DAY market orders; JARVIS manages their stop.
            body.update(qty=f"{req.qty:.6f}".rstrip("0").rstrip("."), time_in_force="day" if not whole else "gtc")
        elif req.notional is not None:
            body.update(notional=f"{req.notional:.2f}", time_in_force="day")
        else:
            raise ValueError("order needs qty or notional")
        o = self._req("POST", "/v2/orders", body=body, retries=0)  # never auto-retry an order POST
        return OrderResult(
            id=o["id"],
            symbol=o["symbol"],
            side=o["side"],
            qty=_f(o.get("qty")) or None,
            status=o.get("status", ""),
            filled_avg_price=_f(o.get("filled_avg_price")) or None,
            notional=_f(o.get("notional")) or None,
        )

    def cancel_orders(self, symbol: str | None = None) -> int:
        if symbol is None:
            r = self._req("DELETE", "/v2/orders")
            return len(r or [])
        n = 0
        for o in self.open_orders():
            if o.get("symbol") == symbol:
                try:
                    self._req("DELETE", f"/v2/orders/{urllib.parse.quote(o['id'])}", retries=1)
                    n += 1
                except HTTPError:
                    pass
        return n

    def close_position(self, symbol: str) -> OrderResult | None:
        # Bracket legs reserve the shares, so cancel them first.
        self.cancel_orders(symbol)
        try:
            o = self._req("DELETE", f"/v2/positions/{urllib.parse.quote(symbol)}", retries=1)
        except HTTPError as e:
            if e.status == 404:
                return None
            raise
        return OrderResult(o["id"], o["symbol"], o["side"], _f(o.get("qty")) or None, o.get("status", ""))
