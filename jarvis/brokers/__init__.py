"""Broker adapters. Anything with this shape can be traded by JARVIS."""

from __future__ import annotations

from typing import Protocol

from ..models import Account, Clock, OrderRequest, OrderResult, Position


class Broker(Protocol):
    name: str
    is_live: bool

    def get_account(self) -> Account: ...

    def get_positions(self) -> list[Position]: ...

    def get_clock(self) -> Clock: ...

    def submit_order(self, req: OrderRequest) -> OrderResult: ...

    def cancel_orders(self, symbol: str | None = None) -> int: ...

    def close_position(self, symbol: str) -> OrderResult | None: ...

    def open_orders(self) -> list[dict]: ...
