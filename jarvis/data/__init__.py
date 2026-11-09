"""Market data providers."""

from __future__ import annotations

from typing import Protocol

from ..models import Bar


class DataFeed(Protocol):
    name: str

    def get_bars(self, symbols: list[str], limit: int = 200) -> dict[str, list[Bar]]: ...

    def get_news(self, symbols: list[str], limit: int = 10) -> list[dict]: ...
