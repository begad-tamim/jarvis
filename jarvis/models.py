"""Core data types shared by every JARVIS module.

Plain dataclasses on purpose: no ORM, no pydantic, zero dependencies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Side = Literal["buy", "sell"]


@dataclass(slots=True)
class Bar:
    """One OHLCV candle. `t` is a unix timestamp (seconds, UTC)."""

    t: float
    o: float
    h: float
    l: float  # noqa: E741 - industry-standard name
    c: float
    v: float


@dataclass(slots=True)
class Account:
    equity: float
    cash: float
    buying_power: float
    last_equity: float | None = None  # broker's previous-close equity, if known
    daytrade_count: int = 0
    pattern_day_trader: bool = False
    status: str = "ACTIVE"
    currency: str = "USD"


@dataclass(slots=True)
class Position:
    symbol: str
    qty: float
    avg_price: float
    market_price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.market_price

    @property
    def unrealized_pl(self) -> float:
        return (self.market_price - self.avg_price) * self.qty

    @property
    def unrealized_plpc(self) -> float:
        return (self.market_price / self.avg_price - 1.0) if self.avg_price else 0.0


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: Side
    qty: float | None = None
    notional: float | None = None
    stop_loss: float | None = None  # broker-side protective stop (bracket)
    take_profit: float | None = None
    reason: str = ""
    client_order_id: str = field(default_factory=lambda: f"jarvis-{uuid.uuid4().hex[:20]}")


@dataclass(slots=True)
class OrderResult:
    id: str
    symbol: str
    side: Side
    qty: float | None
    status: str
    filled_avg_price: float | None = None
    notional: float | None = None
    submitted_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Clock:
    is_open: bool
    next_open: float | None = None
    next_close: float | None = None


@dataclass(slots=True)
class Signal:
    """Output of the quant engine for one symbol."""

    symbol: str
    score: float  # -100 (strong sell) .. +100 (strong buy)
    price: float
    atr: float
    rsi: float
    action: Literal["buy", "sell", "hold"]
    factors: dict[str, float]
    summary: str
    dollar_volume: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Thesis:
    """Output of the analyst (Claude, a local LLM, or the rules engine)."""

    symbol: str
    action: Literal["buy", "pass", "hold", "sell"]
    conviction: float  # 0..1
    thesis: str
    risks: list[str] = field(default_factory=list)
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Verdict:
    """The risk engine's ruling. The analyst proposes - this decides."""

    approved: bool
    rule: str  # which guardrail fired (or "ok")
    message: str
    qty: float = 0.0
    notional: float = 0.0
    stop: float | None = None
    target: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
